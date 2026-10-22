#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=================================================="
echo "    ✨ 启动 OmniCleaner · Mac 本地去重工具"
echo "=================================================="

# Check if port 8990 is occupied and verify it belongs to app.py before killing
PID=$(lsof -ti :8990 2>/dev/null || true)
if [ -n "$PID" ]; then
    PROC_CMD=$(ps -p $PID -o command= 2>/dev/null || true)
    if [[ "$PROC_CMD" == *"app.py"* ]]; then
        echo "发现已有运行中的 OmniCleaner 实例 (PID: $PID)，正在重启..."
        kill -9 $PID 2>/dev/null || true
        sleep 0.5
    else
        echo "⚠️ 端口 8990 已被其他进程占用 (PID: $PID: $PROC_CMD)，无法自动终止。"
        echo "请先手动释放 8990 端口后重试。"
        read -p "按回车键退出..."
        exit 1
    fi
fi

# Ensure python3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ 未检测到 Python 3。请先在终端运行 'xcode-select --install' 或安装 Python 3。"
    read -p "按回车键退出..."
    exit 1
fi

python3 app.py &
APP_PID=$!

sleep 1
open "http://127.0.0.1:8990"

echo "✅ 服务已在本地启动: http://127.0.0.1:8990"
echo "💡 提示：使用完毕后在终端按下 Ctrl+C 即可关闭服务。"

wait $APP_PID
