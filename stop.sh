#!/usr/bin/env bash
# ============================================================
#  关闭 financial-analyst + GuanLan UI (macOS / Linux)
# ============================================================
cd "$(dirname "$0")"

echo
echo "=== 关闭 financial-analyst + GuanLan UI ==="
echo

# 关后端 (按 PID file)
if [[ -f .run/serve.pid ]]; then
    PID=$(cat .run/serve.pid)
    if kill "$PID" 2>/dev/null; then
        echo "[OK]    后端 PID $PID 已关"
    else
        echo "[SKIP]  后端 PID $PID 已不在"
    fi
    rm -f .run/serve.pid
else
    echo "[SKIP]  没有 .run/serve.pid"
fi

# 关前端
if [[ -f .run/ui.pid ]]; then
    PID=$(cat .run/ui.pid)
    if kill "$PID" 2>/dev/null; then
        echo "[OK]    前端 PID $PID 已关"
    else
        echo "[SKIP]  前端 PID $PID 已不在"
    fi
    rm -f .run/ui.pid
else
    echo "[SKIP]  没有 .run/ui.pid"
fi

# 兜底: 按端口杀
for port in 9999 5173; do
    if pids=$(lsof -ti:$port 2>/dev/null); then
        for pid in $pids; do
            kill "$pid" 2>/dev/null && echo "[OK]    端口 $port 上的 PID $pid 已关"
        done
    fi
done

echo
echo "============================================================"
echo " ✓ 关闭完成."
echo "============================================================"
echo
