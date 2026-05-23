#!/usr/bin/env bash
# ============================================================
#  financial-analyst + GuanLan UI 一键启动 (macOS / Linux)
#  - 检查 .env 存在
#  - 检查 venv 可用
#  - 起 buddy SSE 后端 (port 9999, nohup + PID file)
#  - 起 GuanLan 前端 (port 5173, nohup + PID file)
#  - 打开浏览器
#  关闭: ./stop.sh
# ============================================================
set -e

cd "$(dirname "$0")"

echo
echo "=== financial-analyst + GuanLan UI ==="
echo

# ── 1. 环境检查 ─────────────────────────────────────────────
if [[ ! -f ".env" ]]; then
    echo "[ERROR] .env 不存在. 先 cp .env.example .env 并填入 API key."
    echo "        详见 docs/setup/zero_to_report.md"
    exit 1
fi

if [[ ! -x ".venv/bin/financial-analyst" ]]; then
    echo "[ERROR] .venv 不存在或未装 financial-analyst."
    echo "        先: python -m venv .venv && source .venv/bin/activate && pip install -e .[serve]"
    exit 1
fi

# fa_ui_ready 在 Linux/macOS 默认路径不一定相同, 看环境
FA_UI_DIR="${FA_UI_DIR:-G:/stocks/fa_ui_ready}"
if [[ ! -f "$FA_UI_DIR/index.html" ]]; then
    echo "[ERROR] $FA_UI_DIR 不存在. 设环境变量 FA_UI_DIR 指向 fa_ui_ready 目录."
    exit 1
fi
echo "[OK]    .env, venv, fa_ui_ready 都在 ($FA_UI_DIR)"

# ── 2. 端口检查 ─────────────────────────────────────────────
if lsof -ti:9999 >/dev/null 2>&1; then
    echo "[WARN]  端口 9999 已被占用. 先跑 ./stop.sh"
    exit 1
fi
if lsof -ti:5173 >/dev/null 2>&1; then
    echo "[WARN]  端口 5173 已被占用. 先跑 ./stop.sh"
    exit 1
fi

# ── 3. 启后端 ───────────────────────────────────────────────
echo "[BOOT]  启动 buddy SSE 后端 (port 9999)..."
mkdir -p .run
nohup .venv/bin/financial-analyst serve --port 9999 \
    > .run/serve.log 2>&1 &
echo $! > .run/serve.pid

# 等后端起来 (最多 10s)
for i in $(seq 1 20); do
    if curl -sf http://127.0.0.1:9999/health >/dev/null 2>&1; then
        echo "[OK]    后端就绪 (http://127.0.0.1:9999/health)"
        break
    fi
    sleep 0.5
    if [[ $i -eq 20 ]]; then
        echo "[ERROR] 后端 10s 内没起来. 看 .run/serve.log"
        exit 1
    fi
done

# ── 4. 启前端 ───────────────────────────────────────────────
echo "[BOOT]  启动 GuanLan UI 前端 (port 5173)..."
(cd "$FA_UI_DIR" && nohup python -m http.server 5173 \
    > "$OLDPWD/.run/ui.log" 2>&1 &
 echo $! > "$OLDPWD/.run/ui.pid")
sleep 1
echo "[OK]    前端就绪."

# ── 5. 打开浏览器 ───────────────────────────────────────────
echo "[OPEN]  http://localhost:5173"
if command -v open >/dev/null 2>&1; then
    open http://localhost:5173            # macOS
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:5173        # Linux
else
    echo "        请手动开浏览器到 http://localhost:5173"
fi

echo
echo "============================================================"
echo " ✓ 启动完成. PID 文件: .run/serve.pid, .run/ui.pid"
echo " ✓ 日志: .run/serve.log, .run/ui.log"
echo
echo " 关闭: ./stop.sh"
echo "============================================================"
echo
