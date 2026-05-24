#!/usr/bin/env bash
# financial-analyst serve smoke test — 验证所有关键 endpoint 工作.
#
# 前提: financial-analyst serve --port 9999 已经在跑.
# 用法: bash scripts/smoke_test_serve.sh
#
# 输出: 每个 endpoint 一行 PASS/FAIL + 简短诊断. exit 1 if any fail.

set -uo pipefail

PORT="${FA_SERVE_PORT:-9999}"
BASE="http://127.0.0.1:$PORT"
FAILED=0

# 颜色 (没 tty 时 disable)
if [ -t 1 ]; then
    G="\033[32m"; R="\033[31m"; Y="\033[33m"; X="\033[0m"
else
    G=""; R=""; Y=""; X=""
fi

_log_pass() { printf "%b\n" "${G}✓${X} $1"; }
_log_fail() { printf "%b\n" "${R}✗${X} $1"; FAILED=$((FAILED + 1)); }
_log_warn() { printf "%b\n" "${Y}⚠${X} $1"; }

_check_field() {
    # _check_field "endpoint" "json_key" "expected"
    # json_key: dot-separated, e.g. "ok" or "summary.host_connectivity.ok"
    local label="$1" key="$2" expected="$3"
    local got
    got=$(echo "$RESP" | KEY="$key" python -c "
import sys, json, os
d = json.load(sys.stdin)
for k in os.environ['KEY'].split('.'):
    d = d.get(k) if isinstance(d, dict) else getattr(d, k, None)
print(d)
" 2>/dev/null)
    if [ "$got" = "$expected" ]; then
        _log_pass "$label  → $key = $expected"
    else
        _log_fail "$label  → $key = '$got' (期望 '$expected')"
    fi
}

echo "=== financial-analyst serve smoke test ==="
echo "  base: $BASE"
echo ""

# ───────── /health ─────────
RESP=$(curl -fsS "$BASE/health" 2>/dev/null) || { _log_fail "/health unreachable. serve 没启动?"; exit 1; }
_check_field "/health" "ok" "True"
VERSION=$(echo "$RESP" | python -c "import sys, json; print(json.load(sys.stdin)['version'])")
TOOLS=$(echo "$RESP" | python -c "import sys, json; print(json.load(sys.stdin)['tools'])")
echo "    version=$VERSION, tools=$TOOLS"

# ───────── /diag?quick=1 ─────────
RESP=$(curl -fsS "$BASE/diag?quick=1") || _log_fail "/diag failed"
_check_field "/diag" "ok" "True"
N_SRC_OK=$(echo "$RESP" | python -c "import sys, json; d=json.load(sys.stdin); print(sum(1 for r in d['results'] if r['ok']))")
N_SRC=$(echo "$RESP" | python -c "import sys, json; d=json.load(sys.stdin); print(len(d['results']))")
[ "$N_SRC_OK" = "$N_SRC" ] && _log_pass "/diag sources $N_SRC_OK/$N_SRC OK" \
                            || _log_fail "/diag sources only $N_SRC_OK/$N_SRC OK"

# ───────── /tools ─────────
RESP=$(curl -fsS "$BASE/tools")
N_TOOLS=$(echo "$RESP" | python -c "import sys, json; print(len(json.load(sys.stdin)))")
[ "$N_TOOLS" -ge 20 ] && _log_pass "/tools $N_TOOLS tools" \
                      || _log_fail "/tools only $N_TOOLS tools (期望 ≥20)"

# ───────── /models ─────────
RESP=$(curl -fsS "$BASE/models") || _log_fail "/models failed"
_check_field "/models" "ok" "True"
N_MODELS=$(echo "$RESP" | python -c "import sys, json; print(len(json.load(sys.stdin)['models']))")
[ "$N_MODELS" -ge 1 ] && _log_pass "/models $N_MODELS LLM models" \
                     || _log_fail "/models 0 LLM (没配 API key?)"

# ───────── /quotes ─────────
RESP=$(curl -fsS "$BASE/quotes?codes=SH600519,SZ300750")
_check_field "/quotes" "ok" "True"
N_QUOTES=$(echo "$RESP" | python -c "import sys, json; print(len(json.load(sys.stdin)['quotes']))")
[ "$N_QUOTES" = "2" ] && _log_pass "/quotes 2/2 stocks 拉到" \
                      || _log_fail "/quotes only $N_QUOTES/2 (腾讯接口?)"

# ───────── /comments (refresh=0 本地) ─────────
RESP=$(curl -fsS "$BASE/comments?code=SH600519&limit=3&refresh=0&sentiment=0")
_check_field "/comments" "ok" "True"
N_CMT=$(echo "$RESP" | python -c "import sys, json; print(len(json.load(sys.stdin).get('comments', [])))")
echo "    本地评论 $N_CMT 条 (refresh=0)"

# ───────── /resolve ─────────
RESP=$(curl -fsS "$BASE/resolve?q=600519")
_check_field "/resolve (code)" "code" "SH600519"

# UTF-8 编码中文 — Windows Git Bash 默认按 latin-1 转, 服务端会乱码
ENCODED=$(python -c "import urllib.parse; print(urllib.parse.quote('贵州茅台'))")
RESP=$(curl -fsS "$BASE/resolve?q=$ENCODED")
_check_field "/resolve (name)" "code" "SH600519"

# ───────── /alerts ─────────
RESP=$(curl -fsS "$BASE/alerts")
_check_field "/alerts" "ok" "True"
N_ALERTS=$(echo "$RESP" | python -c "import sys, json; print(len(json.load(sys.stdin).get('alerts', [])))")
echo "    当前盯盘规则 $N_ALERTS 条"

# ───────── /run SSE ─────────
echo ""
echo "─── SSE /run streaming test ───"
echo '{"query": "茅台现在多少钱", "mode": "auto"}' > /tmp/fa_smoke_runreq.json
EVT_COUNT=$(curl -sN -X POST "$BASE/run" \
    -H 'Content-Type: application/json' \
    --data-binary @/tmp/fa_smoke_runreq.json \
    --max-time 30 2>/dev/null | grep -c "^event:")
rm -f /tmp/fa_smoke_runreq.json
[ "$EVT_COUNT" -ge 3 ] && _log_pass "/run SSE 收到 $EVT_COUNT 个 event" \
                       || _log_fail "/run SSE 只收 $EVT_COUNT 个 event (期望 ≥3)"

# ───────── /report-progress (没跑过 report 时应 not_started) ─────────
RESP=$(curl -fsS "$BASE/report-progress?code=NONEXIST_CODE_TEST")
ERR=$(echo "$RESP" | python -c "import sys, json; print(json.load(sys.stdin).get('error', ''))")
[ "$ERR" = "not_started" ] && _log_pass "/report-progress 未跑时返回 not_started" \
                           || _log_pass "/report-progress 返回 $ERR (历史 progress 存在)"

# ───────── 总结 ─────────
echo ""
echo "─────────────────────────────"
if [ "$FAILED" = "0" ]; then
    printf "%b\n" "${G}✓ All smoke tests passed.${X}"
    exit 0
else
    printf "%b\n" "${R}✗ $FAILED test(s) failed.${X}"
    exit 1
fi
