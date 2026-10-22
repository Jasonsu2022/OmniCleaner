# OmniCleaner — Free Duplicate File Finder for Mac

<p align="center">
  <img src="https://img.shields.io/badge/Platform-macOS-black?style=flat-square&logo=apple" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Dependencies-0%20(Pure%20Stdlib)-brightgreen?style=flat-square" alt="Zero Dependencies">
  <img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" alt="License">
</p>

> **English:** Free, open-source duplicate file finder for macOS — safely clean duplicate photos, music, and videos on external drives and NAS with one-click undo and guaranteed original retention.  
> **中文：** 免费开源的 macOS 本地重复文件清理工具 —— 针对移动硬盘与 NAS 中的海量照片、音乐及视频深度去重，每组强制保留原件，移入系统废纸篓且支持一键撤回。

---

## 🎯 Who is it for? / 适用人群

- 📷 **Photographers & Creators**: Organize hundreds of gigabytes or terabytes of duplicate RAW/JPEG/PNG/HEIC media across backup hard drives.
- 🎵 **Music & Audio Collectors**: Deduplicate messy audio tracks across overlapping music albums, OSTs, and download directories.
- 💾 **External Drive & NAS Users**: Safely declutter massive multi-volume archives without slow network re-uploads or cloud fees.
- 💡 **CleanMyMac & Gemini 2 Alternative Seekers**: Users tired of annual software subscriptions who need a transparent, local-first Mac deduplication tool.

---

## ✨ Features / 核心特色

1. 🛡️ **Guaranteed Original Retention (Hard-Locked)**  
   OmniCleaner implements a dual-layer check in both the UI and backend API to ensure that **at least one unselected original file in every duplicate group is retained and verified on disk before any deletion executes**.
2. 🎯 **Targeted Directory Priority Cleanup** *(Killer Feature)*  
   Have two overlapping folders (e.g. organized `Music Albums` vs messy `Soundtracks`)? Specify folder preference keywords to bulk-select copies inside the secondary directory while ensuring the primary directory remains untouched.
3. 🗑️ **Native macOS Trash + Twin-File 1-Click Restore**  
   Files are moved to the native macOS Finder Trash via AppleScript (`(POSIX file p) as alias`). A local manifest records the surviving twin file for each trashed path, allowing one-click reconstruction back to original folder locations.
   > **Note on Undo:** The one-click restore feature recovers files by copying the verified surviving duplicate back to the original file path. The original trashed files remain in your macOS Trash until emptied, and can also be manually restored via Finder's "Put Back". Restored files recreated by copying may not preserve original creation timestamps (birthtime) or Finder metadata tags.
4. ⚡ **3-Tier High-Performance Comparison**  
   Optimized for massive archives (tested with **27,000+ duplicate groups / 37MB+ index cache**):
   - *Stage 1:* File size clustering + Inode deduplication (skips hardlinks & symlinks).
   - *Stage 2:* Dual-sample head/tail 8KB SHA-256 (filters out 99% of same-format non-duplicates with near-zero disk I/O).
   - *Stage 3:* Full SHA-256 hash comparison using Python standard hashlib with OpenSSL acceleration.
5. 📦 **Zero External Dependencies**  
   Built entirely on the standard Python 3 library (`http.server`, `hashlib`, `subprocess`, `secrets`) and modern Vanilla JS/CSS. **No `pip install`, no `node_modules`, no Electron bloat**. Double-click `.command` to launch in your browser.
6. 🔍 **Instant Drag-and-Drop via macOS Spotlight**  
   Drop any external APFS/HFS+ disk folder directly onto the drop zone. Integrated macOS Spotlight (`mdfind`) indexing resolves deep folder paths in under 0.05 seconds.

---

## 📊 Comparison / 与常见软件对比

| Feature / 特性 | **OmniCleaner** | Gemini 2 | CleanMyMac | dupeGuru |
| :--- | :---: | :---: | :---: | :---: |
| **Price (价格)** | **Free ($0)** | Paid / Subscription | Paid / Subscription | **Free ($0)** |
| **Open Source (开源透明)** | **Yes (MIT)** | No | No | Yes (GPL) |
| **Guaranteed 1 Original Lock (强制保全原件)** | **Yes (Dual-Locked)** | – | – | Yes (Reference files) |
| **Targeted Directory Cleanup (定向目录偏好)** | **Yes** | – | – | Yes (Reference folders) |
| **1-Click Restore / Undo (一键撤回放回)** | **Yes (Twin-Copy)** | – | – | No |
| **Native macOS Trash (原生移入废纸篓)** | **Yes** | Yes | Yes | Yes |
| **Handles 20K+ Groups (支持万级重复项)** | **Yes** | Yes | Yes | Yes |

---

## 🚀 Quick Start / 快速上手

### Method A: Download Release (Recommended for most Mac users)
1. Download the latest `OmniCleaner-v1.0.0.zip` from [Releases](../../releases).
2. Unzip the file.
3. Double-click `启动OmniCleaner.command`.
   > *Note for macOS Gatekeeper:* If macOS displays *"unidentified developer"*, simply **Right-click (Control-click) `启动OmniCleaner.command` ➔ Open ➔ Click "Open"**.  
   > *(If your Mac does not have Python 3 yet, macOS will automatically prompt you to install Apple Command Line Tools with one click).*
4. Your default browser will immediately open `http://127.0.0.1:8990`.

### Method B: Via Terminal / Git Clone
```bash
# 1. Clone repository
git clone https://github.com/Jasonsu2022/OmniCleaner.git
cd OmniCleaner

# 2. Run with standard Python 3
./run.sh
```

---

## ⚙️ How It Works / 工作原理

### 1. Smart Original Scoring Heuristics
When duplicates are found, OmniCleaner intelligently scores and ranks files to identify the most likely true original:
- **Priority 1 (Heaviest Penalty):** Files containing duplicate/copy markers (e.g. `副本`, `备份`, ` copy`, `Copy of`, ` (1)`, ` 1`) receive a -1,000,000 penalty.
- **Priority 2 (Timestamp):** Earlier creation/modification dates score higher.
- **Priority 3 (Organization):** Deeper file structure depth scores higher (organized folders preferred over root dumps).

### 2. Safety Lock
Every duplicate group strictly retains at least 1 file. The frontend checkbox for the sole surviving file is disabled with a safety lock, and the backend HTTP handler verifies on disk that an unselected file exists and matches size before executing any deletion.

---

## ⚠️ Known Limitations / 已知限制

1. **User Interface Language**: The web interface is currently in Chinese (English localization is planned for future versions).
2. **Exact Duplicate Content Only**: OmniCleaner identifies byte-for-byte exact matches using SHA-256 hashes. It does **not** perform AI visual similarity comparisons (such as camera burst shots or differing compression ratios).
3. **Bundle Protection**: Internal bundle packages (`.photoslibrary`, `.musiclibrary`, `.lrlibrary`, `.fcpbundle`, `.imovielibrary`, `.app`) are strictly excluded from recursive file walks to prevent accidental corruption of database files.
4. **Network Shares (SMB/NAS)**: On network volumes that do not support macOS native Trash (`.Trashes`), files may be deleted directly by Finder (though they can still be recovered via the one-click duplicate copy restore feature as long as a surviving copy exists on disk).
5. **Spotlight Index Dependency for Drag-and-Drop**: Instant path resolution relies on macOS Spotlight. If an external drive has Spotlight indexing disabled (`mdutil -i off`), you can use the built-in Native Folder Picker dialog (`choose folder`) or paste the folder path directly.

---

## ❓ FAQ (Frequently Asked Questions)

### How do I find duplicate photos on an external hard drive on Mac?
Drag any folder from your external volume (e.g. `/Volumes/MyPassport/Photos`) directly into the OmniCleaner dropzone, or click **"📁 原生访达选择"** to pick multiple directories. Click **"🚀 开始全量比对扫描"**. OmniCleaner uses three-tier hashing to quickly identify identical files across different folders without scanning unnecessary file contents.

### Is there a free alternative to Gemini 2 or CleanMyMac for finding duplicates?
Yes! OmniCleaner is completely free and open-source under the MIT license. It provides a clean, responsive web interface, native macOS Trash integration, and directory preference cleanup without subscriptions, ads, or telemetry.

### Will it accidentally delete my original files?
No. OmniCleaner implements a strict **single original retention mechanism**:
1. At least one original copy in every group is hard-locked from being selected.
2. Before any deletion, the backend server confirms on disk that an unselected file exists and matches the original size; otherwise, deletion is aborted.
3. Deleted files go to the macOS native Trash and can also be undone with one click via the **"↩️ 撤回上一批删除"** button.

### Does it upload my files or scan data?
**Never.** OmniCleaner runs 100% locally on `127.0.0.1:8990`. There is zero telemetry, zero analytics, and zero network communication outside your local machine.

### Does it find visually similar photos?
No. OmniCleaner focuses on exact hash-level duplicate files (identical bytes). Comparing exact SHA-256 byte hashes prevents false positives between distinct photos taken in the same sequence.

---

## 常见问题 (中文 FAQ)

### Mac 移动硬盘上的重复照片怎么免费清理？
直接将移动硬盘中的文件夹拖入 OmniCleaner 网页端界面，或点击“原生访达选择”挑选文件夹。点击“开始全量比对扫描”，系统会自动进行分级哈希比对，快速找出跨目录的重复照片与视频。

### 有没有免费替代 CleanMyMac 或 Gemini 2 的工具？
OmniCleaner 专为解决商业软件订阅昂贵、操作黑盒的痛点而开发，100% 免费开源，无需安装任何第三方库或 Python 扩展包，双击脚本即可在浏览器中获得清爽的去重体验。

### 会不会误删唯一的照片原件？
系统具有双重防护卡口：
1. 每组重复照片系统都会通过算法推荐最优原件，并强制锁死至少一份有效文件，不可打勾删除。
2. 执行删除前，后端会核验磁盘上保留的原件是否存在且大小一致，若异常则终止删除。
3. 删除操作调用系统 AppleScript 移入 macOS 废纸篓，并生成孪生恢复索引，支持一键撤回重建。
> **关于撤回机制说明：** 一键撤回功能通过将同组中经过校验的保留原件复制回原路径来重建文件。被删除的副本文件仍保存在 macOS 系统废纸篓中，用户亦可在废纸篓中手动右键“放回原处”。通过复制重建的文件可能无法保留原本的文件创建时间（birthtime）和访达标签（Finder tags）。

### 我的个人文件或数据会被上传到云端吗？
**绝不会。** OmniCleaner 是完全的 Local-First 本地工具，服务仅监听本地回环地址 `127.0.0.1`，无任何外部数据上报或网络上传。

---

## 📄 License

This project is licensed under the [MIT License](LICENSE) - see the [LICENSE](LICENSE) file for details.  
Copyright (c) 2026 **Jason Su**.
