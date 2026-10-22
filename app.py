import os
import re
import sys
import json
import time
import secrets
import hashlib
import subprocess
import urllib.parse
import http.server
import socketserver
import threading
import unicodedata
from collections import defaultdict

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_RESULT_FILE = os.path.join(APP_DIR, 'scan_result.json')
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
CONFIG_EXAMPLE_FILE = os.path.join(APP_DIR, 'config.example.json')
TRASH_LOG_FILE = os.path.join(APP_DIR, 'trash_manifest.json')

# Session security token
SESSION_TOKEN = secrets.token_hex(16)

SKIP_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.localized'}
SKIP_DIR_NAMES = {'.git', '.Trash', '.Trashes', '.Spotlight-V100', '.fseventsd', 'System Volume Information'}
# macOS software bundle directories to protect from accidental internal modification
SKIP_BUNDLE_EXTS = ('.photoslibrary', '.photolibrary', '.musiclibrary', '.lrlibrary', '.fcpbundle', '.imovielibrary', '.app')

# High-confidence copy markers regex: 副本, 备份, copy, backup, (1)~(99), ' 1'~' 99', 'Copy of'
COPY_MARKER_REGEX = re.compile(r'副本|备份|\bcopy\b|\bbackup\b|\s*\([1-9]\d{0,1}\)|\s+[1-9]\d{0,1}(?=\.[^.]+$)|(?i:^copy\s+of\s+)|^副本\s+', re.IGNORECASE)

# Global scan status tracker
scan_state = {
    'status': 'idle', # idle, scanning, done, error
    'progress_message': '就绪',
    'total_files': 0,
    'current_step': '',
    'percent': 0,
    'error': None
}

scan_lock = threading.Lock()

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    if os.path.exists(CONFIG_EXAMPLE_FILE):
        try:
            with open(CONFIG_EXAMPLE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'target_folders': [],
        'exclude_folders': []
    }

def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)

def normalize_and_dedup_targets(targets):
    """Normalize paths, resolve realpath, and eliminate parent-child overlapping folders."""
    cleaned = []
    for t in targets:
        if not t:
            continue
        p = os.path.abspath(os.path.expanduser(t))
        if os.path.isdir(p):
            cleaned.append(p)
            
    cleaned = sorted(list(set(cleaned)), key=len)
    final_targets = []
    for p in cleaned:
        # Check if already covered by an existing parent
        is_covered = False
        for parent in final_targets:
            if p == parent or p.startswith(parent + os.sep):
                is_covered = True
                break
        if not is_covered:
            final_targets.append(p)
    return final_targets

def score_original(file_path, stat_info):
    """
    Score file for 'original' candidacy: Higher score = more likely to be the true original.
    User priority:
    1. Penalty for copy markers ('副本', ' (1)', ' copy', etc.) -> Priority 1 (highest)
    2. Earlier creation/modification date -> Priority 2
    3. Deeper directory depth (more sorted/organized) -> Priority 3
    """
    fname = os.path.basename(file_path)
    
    is_copy = bool(COPY_MARKER_REGEX.search(fname))
    score = -1000000.0 if is_copy else 0.0
    
    # 2. Earlier time (birthtime or mtime)
    try:
        btime = getattr(stat_info, 'st_birthtime', stat_info.st_mtime)
    except Exception:
        btime = stat_info.st_mtime
        
    age_seconds = max(0, 1900000000 - btime)
    score += (age_seconds / 100000.0)
    
    # 3. Directory depth: deeper = better organized (+100 pts per level)
    depth = len(file_path.split(os.sep))
    score += (depth * 100.0)
    
    return score

def trash_paths(paths):
    """Safely move paths to macOS Trash via AppleScript Finder delete with argv passing."""
    successes = []
    errors = []
    chunk_size = 40
    valid_paths = [p for p in paths if os.path.exists(p) and not os.path.isdir(p)]
    
    # AppleScript that accepts argv and converts to alias properly
    script = '''on run argv
        set outList to {}
        repeat with p in argv
            try
                set a to (POSIX file p) as alias
                tell application "Finder" to delete a
                set end of outList to "OK|" & p
            on error errMsg
                set end of outList to "ERR|" & p & "|" & errMsg
            end try
        end repeat
        set AppleScript's text item delimiters to linefeed
        return outList as text
    end run'''
    
    for i in range(0, len(valid_paths), chunk_size):
        chunk = valid_paths[i:i + chunk_size]
        cmd = ['osascript', '-e', script, *chunk]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        
        # Parse reported status
        reported_status = {}
        for line in proc.stdout.split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split('|', 2)
            if len(parts) >= 2:
                status, path = parts[0], parts[1]
                msg = parts[2] if len(parts) > 2 else ''
                reported_status[path] = (status, msg)
                
        # Check which actually moved
        for p in chunk:
            if not os.path.exists(p):
                successes.append(p)
            else:
                status, msg = reported_status.get(p, ('ERR', '未能移入废纸篓'))
                errors.append({'path': p, 'error': msg or '文件仍存在（可能无写入权限或被系统保护）'})
                
    return successes, errors

def record_trash_manifest(batch_id, twin_map):
    """Record batch twin mapping to trash_manifest.json for 100% reliable one-click restore."""
    history = []
    if os.path.exists(TRASH_LOG_FILE):
        try:
            with open(TRASH_LOG_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            history = []
            
    batch_entry = {
        'batch_id': batch_id,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(twin_map),
        'twin_map': twin_map # {trashed_path: surviving_twin_path}
    }
    history.append(batch_entry)
    if len(history) > 10:
        history = history[-10:]
        
    try:
        temp_log = TRASH_LOG_FILE + '.tmp'
        with open(temp_log, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(temp_log, TRASH_LOG_FILE)
    except Exception as e:
        print(f"Error saving trash manifest: {e}", file=sys.stderr)

def restore_last_trash_batch():
    """Put back items from the latest trash batch via APFS clone / twin restore. Only pop on success!"""
    if not os.path.exists(TRASH_LOG_FILE):
        return {'status': 'error', 'message': '没有可撤回的删除记录'}
        
    try:
        with open(TRASH_LOG_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
        
    if not history:
        return {'status': 'error', 'message': '撤回记录为空'}
        
    last_batch = history[-1]
    twin_map = last_batch.get('twin_map', {})
    
    import shutil
    restored = []
    failed = []
    
    for trashed_path, twin_path in twin_map.items():
        if os.path.exists(trashed_path):
            restored.append(trashed_path) # Already restored or exists
            continue
        if twin_path and os.path.exists(twin_path):
            try:
                os.makedirs(os.path.dirname(trashed_path), exist_ok=True)
                shutil.copy2(twin_path, trashed_path)
                restored.append(trashed_path)
                continue
            except Exception:
                pass
        failed.append(trashed_path)
        
    if len(restored) > 0:
        # If all items were restored, pop the batch; if partial, retain only the failed items for retry
        if len(failed) == 0:
            history.pop()
        else:
            last_batch['twin_map'] = {p: tw for p, tw in twin_map.items() if p in failed}
            last_batch['count'] = len(last_batch['twin_map'])

        try:
            temp_log = TRASH_LOG_FILE + '.tmp'
            with open(temp_log, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            os.replace(temp_log, TRASH_LOG_FILE)
        except Exception:
            pass

        # Sync scan_result.json so restored items reappear in the UI without a full re-scan
        if os.path.exists(SCAN_RESULT_FILE):
            try:
                with scan_lock:
                    with open(SCAN_RESULT_FILE, 'r', encoding='utf-8') as f:
                        sdata = json.load(f)
                    
                    twin_to_restored = defaultdict(list)
                    for rp in restored:
                        tw = twin_map.get(rp)
                        if tw:
                            twin_to_restored[tw].append(rp)
                    
                    existing_group_paths = set()
                    for g in sdata.get('scattered_groups', []):
                        existing_paths = {f['path'] for f in g['files']}
                        for p in existing_paths:
                            existing_group_paths.add(p)
                        for f in list(g['files']):
                            tw = f['path']
                            if tw in twin_to_restored:
                                for rp in twin_to_restored[tw]:
                                    if rp not in existing_paths and os.path.exists(rp):
                                        st = os.stat(rp)
                                        f_entry = {
                                            'path': rp,
                                            'dir': os.path.dirname(rp),
                                            'name': os.path.basename(rp),
                                            'size': g['size_bytes'],
                                            'size_formatted': g['size_formatted'],
                                            'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime)),
                                            'is_copy_marker': bool(COPY_MARKER_REGEX.search(os.path.basename(rp))),
                                            'score': score_original(rp, st),
                                            'is_original': False
                                        }
                                        g['files'].append(f_entry)
                                        existing_paths.add(rp)
                                        existing_group_paths.add(rp)
                        g['copy_count'] = len(g['files'])
                        g['wasted_bytes'] = g['size_bytes'] * (len(g['files']) - 1)
                        g['wasted_formatted'] = format_bytes(g['wasted_bytes'])

                    # Reconstruct groups for restored files whose twins were the sole survivors (pruned from groups)
                    for rp in restored:
                        tw = twin_map.get(rp)
                        if tw and os.path.exists(tw) and os.path.exists(rp):
                            if tw not in existing_group_paths and rp not in existing_group_paths:
                                sz = os.path.getsize(tw)
                                st_tw = os.stat(tw)
                                st_rp = os.stat(rp)
                                f_tw = {
                                    'path': tw,
                                    'dir': os.path.dirname(tw),
                                    'name': os.path.basename(tw),
                                    'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(st_tw.st_mtime)),
                                    'is_copy_marker': bool(COPY_MARKER_REGEX.search(os.path.basename(tw)))
                                }
                                f_rp = {
                                    'path': rp,
                                    'dir': os.path.dirname(rp),
                                    'name': os.path.basename(rp),
                                    'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(st_rp.st_mtime)),
                                    'is_copy_marker': bool(COPY_MARKER_REGEX.search(os.path.basename(rp)))
                                }
                                files_pair = [f_tw, f_rp]
                                files_pair.sort(key=lambda item: score_original(item['path'], os.stat(item['path'])), reverse=True)
                                
                                new_g = {
                                    'group_id': f"{sz}_{int(time.time())}",
                                    'size_bytes': sz,
                                    'size_formatted': format_bytes(sz),
                                    'wasted_bytes': sz,
                                    'wasted_formatted': format_bytes(sz),
                                    'copy_count': 2,
                                    'files': files_pair
                                }
                                sdata.get('scattered_groups', []).append(new_g)
                                existing_group_paths.add(tw)
                                existing_group_paths.add(rp)
                    
                    new_waste = sum(g['wasted_bytes'] for g in sdata.get('scattered_groups', []))
                    sdata['summary']['wasted_space_bytes'] = new_waste
                    sdata['summary']['wasted_space_formatted'] = format_bytes(new_waste)
                    sdata['summary']['total_duplicate_files'] = sum(g['copy_count'] for g in sdata.get('scattered_groups', []))
                    sdata['summary']['total_duplicate_file_groups'] = len(sdata.get('scattered_groups', []))
                    
                    temp_f = SCAN_RESULT_FILE + '.tmp'
                    with open(temp_f, 'w', encoding='utf-8') as f:
                        json.dump(sdata, f, ensure_ascii=False, indent=2)
                    os.replace(temp_f, SCAN_RESULT_FILE)
            except Exception as e:
                print(f"Error refreshing scan_result.json on restore: {e}", file=sys.stderr)
            
        return {
            'status': 'ok',
            'batch_id': last_batch.get('batch_id'),
            'restored_count': len(restored),
            'failed_count': len(failed),
            'restored_paths': restored
        }
    else:
        return {
            'status': 'error',
            'message': f'撤回失败：在磁盘上未能找到对应的 {len(failed)} 个原件孪生副本'
        }

def format_bytes(b):
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024**3):.2f} GB"
    elif b >= 1024 * 1024:
        return f"{b / (1024**2):.1f} MB"
    elif b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"

def dual_sample_hash(filepath, filesize):
    """
    Optimized 2nd-stage sample: head 8KB + tail 8KB.
    Eliminates 99% of same-camera/same-container false positives with near-zero I/O.
    For small files <= 16KB, digest entire file to ensure 100% exact correctness without boundary false duplicates.
    """
    h = hashlib.sha256()
    with open(filepath, 'rb') as fh:
        if filesize <= 16384:
            h.update(fh.read())
            return h.hexdigest()
        # Head 8KB
        h.update(fh.read(8192))
        # Tail 8KB
        fh.seek(filesize - 8192)
        h.update(fh.read(8192))
    return h.hexdigest()

def full_hash(filepath):
    """Apple Silicon accelerated SHA-256 (hardware crypto instruction)."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def run_scan(target_folders, exclude_folders):
    global scan_state
    with scan_lock:
        scan_state['status'] = 'scanning'
        scan_state['progress_message'] = '正在规范化扫描目录，排除重叠子文件夹...'
        scan_state['percent'] = 5
        scan_state['error'] = None

    try:
        # 1. Normalize targets to strictly avoid parent-child duplicate walks
        targets = normalize_and_dedup_targets(target_folders)
        exclude_set = {os.path.abspath(os.path.expanduser(p)) for p in exclude_folders}
        
        # 2. Collect all files with inode dedup (skip symlinks and hardlinks)
        size_map = defaultdict(list)
        stat_map = {}
        seen_inodes = set()
        total_files = 0

        for root_target in targets:
            if not os.path.exists(root_target):
                continue
            for root, dirs, files in os.walk(root_target):
                # Prune excluded dirs and dangerous bundles
                dirs[:] = [
                    d for d in dirs 
                    if os.path.abspath(os.path.join(root, d)) not in exclude_set 
                    and d not in SKIP_DIR_NAMES 
                    and not any(d.lower().endswith(ext) for ext in SKIP_BUNDLE_EXTS)
                ]
                
                for f in files:
                    if f.startswith('.') or f in SKIP_FILES or f.startswith('._'):
                        continue
                    fp = os.path.join(root, f)
                    try:
                        st = os.lstat(fp)
                        # Skip symlinks
                        if os.path.islink(fp):
                            continue
                        # Skip same inode (hardlinks / already scanned)
                        inode_key = (st.st_dev, st.st_ino)
                        if inode_key in seen_inodes:
                            continue
                        seen_inodes.add(inode_key)
                        
                        sz = st.st_size
                        if sz > 0:
                            size_map[sz].append(fp)
                            stat_map[fp] = st
                        total_files += 1
                    except OSError:
                        pass

        with scan_lock:
            scan_state['total_files'] = total_files
            scan_state['progress_message'] = f'已收集 {total_files} 个独立文件，执行第一阶段体积过滤...'
            scan_state['percent'] = 25

        # 3. Filter candidate sizes
        cand_sizes = {sz: fps for sz, fps in size_map.items() if len(fps) > 1}
        total_cand = sum(len(fps) for fps in cand_sizes.values())

        with scan_lock:
            scan_state['progress_message'] = f'发现 {total_cand} 个同尺寸文件，执行头尾 8KB 双段指纹采样...'
            scan_state['percent'] = 45

        partial_map = defaultdict(list)
        for sz, fps in cand_sizes.items():
            for fp in fps:
                try:
                    h = dual_sample_hash(fp, sz)
                    partial_map[(sz, h)].append(fp)
                except OSError:
                    pass

        # 4. Third-stage: Full SHA-256 for true collisions
        cand_partials = {k: fps for k, fps in partial_map.items() if len(fps) > 1}
        full_map = defaultdict(list)
        total_to_hash = sum(len(fps) for fps in cand_partials.values())

        with scan_lock:
            scan_state['progress_message'] = f'进行硬件加速全量 SHA-256 最终碰撞确认 ({total_to_hash} 个文件)...'
            scan_state['percent'] = 70

        for (sz, _), fps in cand_partials.items():
            if sz <= 16384:
                # <=16KB was already fully digested by dual_sample_hash
                for fp in fps:
                    full_map[(sz, _)].append(fp)
                continue
            for fp in fps:
                try:
                    fh = full_hash(fp)
                    full_map[(sz, fh)].append(fp)
                except OSError:
                    pass

        dup_groups = {k: fps for k, fps in full_map.items() if len(fps) > 1}

        with scan_lock:
            scan_state['progress_message'] = '应用 1>2>3 规则为原件打分并生成结构化清单...'
            scan_state['percent'] = 90

        # 5. Format groups and apply User Rules 1>2>3
        scattered_groups = []
        for (sz, h), fps in dup_groups.items():
            # Sort files in this group according to User Rules:
            # Rule 1: Non-copy filenames first
            # Rule 2: Older creation time first
            # Rule 3: Deeper directory level first
            sorted_fps = sorted(fps, key=lambda p: score_original(p, stat_map[p]), reverse=True)
            
            wasted = sz * (len(sorted_fps) - 1)
            file_items = []
            for p in sorted_fps:
                st = stat_map[p]
                try:
                    btime = getattr(st, 'st_birthtime', st.st_mtime)
                except Exception:
                    btime = st.st_mtime
                file_items.append({
                    'path': p,
                    'dir': os.path.dirname(p),
                    'name': os.path.basename(p),
                    'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(btime)),
                    'is_copy_marker': bool(COPY_MARKER_REGEX.search(os.path.basename(p)))
                })
                
            scattered_groups.append({
                'group_id': f"{sz}_{h[:16]}",
                'size_bytes': sz,
                'size_formatted': format_bytes(sz),
                'wasted_bytes': wasted,
                'wasted_formatted': format_bytes(wasted),
                'copy_count': len(sorted_fps),
                'files': file_items
            })

        scattered_groups.sort(key=lambda x: -x['size_bytes'])
        total_waste_bytes = sum(g['wasted_bytes'] for g in scattered_groups)

        count_large = sum(1 for g in scattered_groups if g['size_bytes'] >= 10*1024*1024)
        count_medium = sum(1 for g in scattered_groups if 1*1024*1024 <= g['size_bytes'] < 10*1024*1024)
        count_small = sum(1 for g in scattered_groups if g['size_bytes'] < 1*1024*1024)

        result = {
            'summary': {
                'target_folders': targets,
                'exclude_folders': exclude_folders,
                'total_scanned_files': total_files,
                'total_duplicate_file_groups': len(scattered_groups),
                'total_duplicate_files': sum(g['copy_count'] for g in scattered_groups),
                'wasted_space_bytes': total_waste_bytes,
                'wasted_space_formatted': format_bytes(total_waste_bytes),
                'count_large': count_large,
                'count_medium': count_medium,
                'count_small': count_small,
                'scanned_at': time.strftime('%Y-%m-%d %H:%M:%S')
            },
            'scattered_groups': scattered_groups
        }

        # Atomic write
        temp_file = SCAN_RESULT_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, SCAN_RESULT_FILE)

        with scan_lock:
            scan_state['status'] = 'done'
            scan_state['percent'] = 100
            scan_state['progress_message'] = f'扫描完成！发现 {len(scattered_groups)} 组重复文件，可释放 {format_bytes(total_waste_bytes)}'

    except Exception as e:
        with scan_lock:
            scan_state['status'] = 'error'
            scan_state['error'] = str(e)
            scan_state['progress_message'] = f'扫描出错: {str(e)}'
        print(f"Error during scan: {e}", file=sys.stderr)


class DupCleanerHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=APP_DIR, **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def check_host(self):
        """Security: Verify Host header to prevent DNS rebinding."""
        host = self.headers.get('Host', '')
        if host not in ('127.0.0.1:8990', 'localhost:8990'):
            self.send_json_response(403, {'status': 'error', 'message': 'Forbidden Host'})
            return False
        return True

    def check_csrf_and_token(self):
        """Security: Verify Host header, Origin header, and session token to prevent CSRF / malicious external pages."""
        if not self.check_host():
            return False
        
        origin = self.headers.get('Origin', '')
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.netloc not in ('127.0.0.1:8990', 'localhost:8990'):
                self.send_json_response(403, {'status': 'error', 'message': 'Forbidden Origin'})
                return False

        token = self.headers.get('X-Session-Token', '')
        if not secrets.compare_digest(token, SESSION_TOKEN):
            self.send_json_response(403, {'status': 'error', 'message': 'Invalid session token'})
            return False
        return True

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        
        # Token check on mutating API calls
        if not self.check_csrf_and_token():
            return

        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            req_data = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            req_data = {}

        if parsed.path == '/api/config':
            save_config(req_data)
            self.send_json_response(200, {'status': 'ok', 'config': req_data})
            return

        elif parsed.path == '/api/select-folder-dialog':
            # AppleScript: native multi-folder chooser
            ascript = '''
            tell application "Finder"
                activate
                set selFolders to choose folder with prompt "请选择要加入去重比对的文件夹 (支持按住 Command 或 Shift 多选):" with multiple selections allowed
                set outList to {}
                repeat with f in selFolders
                    set end of outList to POSIX path of f
                end repeat
                set AppleScript's text item delimiters to linefeed
                return outList as text
            end tell
            '''
            proc = subprocess.run(['osascript', '-e', ascript], capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                lines = [p.strip().rstrip('/') for p in proc.stdout.strip().split('\n') if p.strip()]
                self.send_json_response(200, {'status': 'ok', 'paths': lines})
            else:
                self.send_json_response(200, {'status': 'cancel'})
            return

        elif parsed.path == '/api/resolve-path':
            query = req_data.get('query', '').strip()
            queries = [q.strip() for q in req_data.get('queries', []) if q and q.strip()]
            if query and query not in queries:
                queries.append(query)

            resolved_paths = []

            # 1. Direct path check
            for q in list(queries):
                if os.path.exists(q) and os.path.isdir(q):
                    resolved_paths.append(os.path.abspath(q))

            # 2. PRIORITY: Check current Finder selection (Multi-folder drag & drop support!)
            ascript = '''
            tell application "Finder"
                try
                    set sel to selection
                    if sel is not {} then
                        set outList to {}
                        repeat with s in sel
                            set end of outList to POSIX path of (s as alias)
                        end repeat
                        set AppleScript's text item delimiters to linefeed
                        return outList as text
                    end if
                end try
                return ""
            end tell
            '''
            proc = subprocess.run(['osascript', '-e', ascript], capture_output=True, text=True)
            finder_paths = []
            if proc.returncode == 0 and proc.stdout.strip():
                finder_paths = [p.strip().rstrip('/') for p in proc.stdout.strip().split('\n') if p.strip()]

            if finder_paths:
                for fp in finder_paths:
                    if os.path.exists(fp) and os.path.isdir(fp):
                        if not queries:
                            # User dropped without item names (Finder fallback): include all selected folders!
                            resolved_paths.append(fp)
                        else:
                            # Match against query basenames or full names
                            for q in queries:
                                if os.path.basename(fp) == q or q in fp or q == fp:
                                    resolved_paths.append(fp)
                                    break
                # If queries had entries, but none matched finder_paths, include all finder_paths if it was a drag
                if not resolved_paths:
                    for fp in finder_paths:
                        if os.path.exists(fp) and os.path.isdir(fp):
                            resolved_paths.append(fp)

            # 3. Fast Spotlight (mdfind) lookup for any remaining queries (Instantaneous for any folder on Mac or external volumes!)
            matched_bases = {os.path.basename(p) for p in resolved_paths}
            remaining_queries = [q for q in queries if q not in matched_bases and not os.path.exists(q)]
            if remaining_queries:
                for q in remaining_queries:
                    try:
                        safe_q = q.replace("'", "\\'")
                        proc_md = subprocess.run(['mdfind', f"kMDItemFSName == '{safe_q}'"], capture_output=True, text=True, timeout=2)
                        if proc_md.returncode == 0 and proc_md.stdout.strip():
                            for line in proc_md.stdout.strip().split('\n'):
                                line = line.strip()
                                if line and os.path.exists(line) and os.path.isdir(line):
                                    resolved_paths.append(os.path.abspath(line))
                                    break
                    except Exception:
                        pass

            # 4. Fuzzy search in common macOS roots, external volumes & existing target folders
            matched_bases = {os.path.basename(p) for p in resolved_paths}
            remaining_queries = [q for q in queries if q not in matched_bases and not os.path.exists(q)]
            if remaining_queries:
                cfg = load_config()
                search_roots = [
                    os.path.expanduser('~/Desktop'),
                    os.path.expanduser('~/Downloads'),
                    os.path.expanduser('~/Documents'),
                    os.path.expanduser('~'),
                ]
                for tf in cfg.get('target_folders', []):
                    if os.path.isdir(tf) and tf not in search_roots:
                        search_roots.append(tf)

                if os.path.exists('/Volumes'):
                    try:
                        for v in os.listdir('/Volumes'):
                            vp = os.path.join('/Volumes', v)
                            if os.path.isdir(vp):
                                search_roots.append(vp)
                                try:
                                    for sub in os.listdir(vp):
                                        sub_p = os.path.join(vp, sub)
                                        if os.path.isdir(sub_p):
                                            search_roots.append(sub_p)
                                            # Depth 2
                                            try:
                                                for sub2 in os.listdir(sub_p):
                                                    sub2_p = os.path.join(sub_p, sub2)
                                                    if os.path.isdir(sub2_p):
                                                        search_roots.append(sub2_p)
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                    except Exception:
                        pass

                for q in remaining_queries:
                    for c in search_roots:
                        target = os.path.join(c, q)
                        if os.path.exists(target) and os.path.isdir(target):
                            resolved_paths.append(os.path.abspath(target))
                            break

            # Deduplicate preserving order
            unique_paths = []
            for p in resolved_paths:
                if p not in unique_paths:
                    unique_paths.append(p)

            if unique_paths:
                self.send_json_response(200, {
                    'status': 'ok',
                    'paths': unique_paths,
                    'path': unique_paths[0]
                })
            else:
                self.send_json_response(400, {'status': 'error', 'message': f'未找到路径: {query or queries}'})
            return

        elif parsed.path == '/api/start-scan':
            target_folders = req_data.get('target_folders', [])
            exclude_folders = req_data.get('exclude_folders', [])
            if not target_folders:
                self.send_json_response(400, {'status': 'error', 'message': '请至少添加一个目标文件夹'})
                return

            if scan_state['status'] == 'scanning':
                self.send_json_response(400, {'status': 'error', 'message': '扫描已在运行中，请勿重复启动'})
                return

            save_config({'target_folders': target_folders, 'exclude_folders': exclude_folders})

            threading.Thread(target=run_scan, args=(target_folders, exclude_folders), daemon=True).start()
            self.send_json_response(200, {'status': 'ok', 'message': '扫描已启动'})
            return

        elif parsed.path == '/api/open-folder':
            target_path = req_data.get('path', '')
            if target_path and os.path.exists(target_path) and os.path.isdir(target_path):
                subprocess.Popen(['open', target_path])
                self.send_json_response(200, {'status': 'ok', 'path': target_path})
            else:
                self.send_json_response(400, {'status': 'error', 'message': f'文件夹不存在: {target_path}'})
            return

        elif parsed.path == '/api/reveal-file':
            target_path = req_data.get('path', '')
            if target_path and os.path.exists(target_path):
                subprocess.Popen(['open', '-R', target_path])
                self.send_json_response(200, {'status': 'ok', 'path': target_path})
            else:
                self.send_json_response(400, {'status': 'error', 'message': f'文件不存在: {target_path}'})
            return

        elif parsed.path == '/api/trash-files':
            paths_to_trash = req_data.get('paths', [])
            if not paths_to_trash:
                self.send_json_response(400, {'status': 'error', 'message': '未提供待删除的文件列表'})
                return

            # HARD DEFENSE (Zero Data Loss):
            # 1. Paths must belong to current duplicate scan result
            # 2. In every duplicate group, at least ONE file MUST remain unselected!
            if not os.path.exists(SCAN_RESULT_FILE):
                self.send_json_response(400, {'status': 'error', 'message': '无法验证扫描清单，拒绝删除'})
                return

            try:
                with open(SCAN_RESULT_FILE, 'r', encoding='utf-8') as f:
                    sdata = json.load(f)
            except Exception as e:
                self.send_json_response(500, {'status': 'error', 'message': f'读取清单失败: {e}'})
                return

            trash_set_norm = {unicodedata.normalize('NFC', p) for p in paths_to_trash}
            valid_set_norm = set()
            twin_map = {}
            for g in sdata.get('scattered_groups', []):
                all_paths_in_group = [f['path'] for f in g.get('files', [])]
                all_paths_norm = [unicodedata.normalize('NFC', p) for p in all_paths_in_group]
                remaining_in_group = [p for p in all_paths_norm if p not in trash_set_norm]
                
                # Check rule: Group MUST retain at least 1 file!
                if len(all_paths_in_group) > 1 and len(remaining_in_group) == 0:
                    self.send_json_response(400, {
                        'status': 'error', 
                        'message': f"安全拦截：检测到重复组 [{os.path.basename(all_paths_in_group[0])}] 的所有副本都被勾选！系统拒绝将原件全部删除。"
                    })
                    return
                for p in all_paths_norm:
                    valid_set_norm.add(p)
                    
                # If this group contains files to be trashed, verify on disk that at least one unselected file exists and matches size!
                has_trash_in_group = any(p in trash_set_norm for p in all_paths_norm)
                if has_trash_in_group:
                    expected_size = g.get('size_bytes', 0)
                    verified_survivor = None
                    for f in g.get('files', []):
                        raw_p = f['path']
                        norm_p = unicodedata.normalize('NFC', raw_p)
                        if norm_p not in trash_set_norm:
                            try:
                                if os.path.exists(raw_p) and not os.path.isdir(raw_p):
                                    if os.stat(raw_p).st_size == expected_size:
                                        verified_survivor = raw_p
                                        break
                            except Exception:
                                pass

                    if not verified_survivor:
                        sample_name = os.path.basename(all_paths_in_group[0]) if all_paths_in_group else "未知文件"
                        self.send_json_response(400, {
                            'status': 'error',
                            'message': f"安全拦截：重复组 [{sample_name}] 中未被勾选的保留原件在磁盘上不存在或大小不符，拒绝执行删除以保护数据安全。"
                        })
                        return

                    # twin_map can ONLY point to the verified surviving file!
                    for p in all_paths_norm:
                        if p in trash_set_norm:
                            twin_map[p] = verified_survivor

            # Check if any path is outside the scan result (Defense against CSRF injection)
            for p in paths_to_trash:
                p_norm = unicodedata.normalize('NFC', p)
                if p_norm not in valid_set_norm:
                    self.send_json_response(400, {'status': 'error', 'message': f'非法请求：路径不在重复清单中: {p}'})
                    return

            successes, errors = trash_paths(paths_to_trash)

            # Record successfully trashed items and their twins for 100% reliable one-click restore
            if successes:
                actual_twin_map = {p: twin_map.get(unicodedata.normalize('NFC', p), '') for p in successes}
                batch_id = f"batch_{int(time.time())}"
                record_trash_manifest(batch_id, actual_twin_map)

            # Update scan_result.json atomically
            try:
                with scan_lock:
                    trashed_set_norm = {unicodedata.normalize('NFC', p) for p in successes}
                    new_groups = []
                    for g in sdata.get('scattered_groups', []):
                        remaining_files = [f for f in g['files'] if unicodedata.normalize('NFC', f['path']) not in trashed_set_norm]
                        if len(remaining_files) > 1:
                            g['files'] = remaining_files
                            g['copy_count'] = len(remaining_files)
                            g['wasted_bytes'] = g['size_bytes'] * (len(remaining_files) - 1)
                            w = g['wasted_bytes']
                            g['wasted_formatted'] = format_bytes(w)
                            new_groups.append(g)

                    sdata['scattered_groups'] = new_groups
                    sdata['summary']['total_duplicate_file_groups'] = len(new_groups)
                    sdata['summary']['total_duplicate_files'] = sum(g['copy_count'] for g in new_groups)
                    new_waste = sum(g['wasted_bytes'] for g in new_groups)
                    sdata['summary']['wasted_space_bytes'] = new_waste
                    sdata['summary']['wasted_space_formatted'] = format_bytes(new_waste)

                    temp_f = SCAN_RESULT_FILE + '.tmp'
                    with open(temp_f, 'w', encoding='utf-8') as f:
                        json.dump(sdata, f, ensure_ascii=False, indent=2)
                    os.replace(temp_f, SCAN_RESULT_FILE)
            except Exception as e:
                print(f"Error updating scan_result.json: {e}", file=sys.stderr)

            self.send_json_response(200, {
                'status': 'ok',
                'trashed_count': len(successes),
                'trashed_paths': successes,
                'errors': errors
            })
            return

        elif parsed.path == '/api/restore-trash':
            result = restore_last_trash_batch()
            if result.get('status') == 'ok':
                self.send_json_response(200, result)
            else:
                self.send_json_response(400, result)
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if not self.check_host():
            return
            
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/session':
            # Returns session token to frontend
            self.send_json_response(200, {'status': 'ok', 'token': SESSION_TOKEN})
            return
        elif parsed.path == '/api/status':
            global scan_state
            with scan_lock:
                self.send_json_response(200, scan_state)
            return
        elif parsed.path == '/api/config':
            self.send_json_response(200, load_config())
            return
        elif parsed.path == '/api/data':
            if os.path.exists(SCAN_RESULT_FILE):
                with open(SCAN_RESULT_FILE, 'r', encoding='utf-8') as f:
                    self.send_json_response(200, json.load(f))
            else:
                self.send_json_response(200, {'summary': None, 'scattered_groups': []})
            return
        elif parsed.path in ['/', '/index.html']:
            # Restrict static service to index.html only (Prevent source leak)
            self.path = '/index.html'
            super().do_GET()
            return
            
        self.send_response(404)
        self.end_headers()

    def send_json_response(self, code, data):
        payload = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    port = 8990
    server_address = ('127.0.0.1', port)
    httpd = socketserver.ThreadingTCPServer(server_address, DupCleanerHandler)
    print(f"✨ OmniCleaner running safely at: http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer terminated.")
        httpd.server_close()

if __name__ == '__main__':
    main()
