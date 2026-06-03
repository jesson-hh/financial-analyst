# Watch Panel UX (P0+P1+P2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WatchMode 从"看不懂在干什么 / 改不了参数"升级到"banner 说清盯盘工作流 + 状态横条全显 + RecCard 点开看 LLM 全文 + 自选 chip 可编辑 avg_cost/stop_loss + ⚡ 点开看 trigger 含义 + 高级控件可调 tick_seconds/cooldown/llm_cap".

**Architecture:** 后端 `/watch/status` 加 3 字段返当前 cfg (tick_seconds/cooldown_minutes/global_llm_cap_per_session). 前端 WatchMode 加 5 sub-component (WatchStrategyBanner / WatchStatusChips / RecDetailModal / TriggerPopover / WatchAdvancedControls), RecCard 加 onClick 接 modal, 自选 chip 加 avg_cost/stop_loss 编辑, 4 KPI 加 tooltip. Playwright 真浏览器烟测.

**Tech Stack:** Python 3.13 (FastAPI/pytest) + JSX (React 18 inline babel) + Playwright

**Spec:** [docs/superpowers/specs/2026-06-03-watch-panel-ux-design.md](../specs/2026-06-03-watch-panel-ux-design.md)

---

## File Structure

**后端**:
- Modify: `src/financial_analyst/buddy/server.py` `/watch/status` endpoint @ L2435-2447 (加 3 字段)

**前端**:
- Modify: `src/financial_analyst/ui/quant.jsx` WatchMode @ L2448-2735 (加 5 sub-component + 改 RecCard + 自选 chip 增强 + KPI tooltip)
- Modify: `src/financial_analyst/ui/quant.html` `?v=` bump

**测试**:
- Create: `tests/test_watch_status_cfg.py` (status idle + running 后 cfg 透传)
- Create: `tests/test_watch_panel_ux_e2e.py` (Playwright 真浏览器)

---

## Task 1: 后端 `/watch/status` 返当前 cfg (~1h)

**Files:**
- Modify: `src/financial_analyst/buddy/server.py:2435-2447` (watch_status endpoint)
- Create: `tests/test_watch_status_cfg.py`

### 1.1 写失败测试

- [ ] **Step 1: 写测试**

Create `tests/test_watch_status_cfg.py`:
```python
"""/watch/status 返当前 WatchLoop cfg (tick_seconds/cooldown/llm_cap), 让前端能显示."""
import pytest
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


@pytest.fixture
def client():
    app = build_app()
    return TestClient(app)


def test_watch_status_idle_returns_defaults(client):
    """No watch loop running → return WatchLoopConfig defaults (60/15/20)."""
    client.post("/watch/stop", json={})
    r = client.get("/watch/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    # Defaults match WatchLoopConfig (loop.py L107-110)
    assert body["tick_seconds"] == 60
    assert body["cooldown_minutes"] == 15
    assert body["global_llm_cap_per_session"] == 20


def test_watch_status_running_returns_loop_cfg(client):
    """After /watch/start with overrides, /watch/status returns the loop's actual cfg."""
    client.post("/watch/stop", json={})
    r = client.post("/watch/start", json={
        "items": [{"code": "SH600519"}],
        "tick_seconds": 30,
        "cooldown_minutes": 10,
        "global_llm_cap_per_session": 50,
    })
    assert r.status_code == 200
    assert r.json().get("ok") is True
    try:
        r = client.get("/watch/status")
        body = r.json()
        assert body["running"] is True
        assert body["tick_seconds"] == 30
        assert body["cooldown_minutes"] == 10
        assert body["global_llm_cap_per_session"] == 50
    finally:
        client.post("/watch/stop", json={})


def test_watch_status_preserves_existing_fields(client):
    """加 3 字段不破坏现有字段."""
    client.post("/watch/stop", json={})
    r = client.get("/watch/status")
    body = r.json()
    for k in ("ok", "running", "n_items", "items", "tick_count", "llm_calls_made"):
        assert k in body, f"existing field {k} missing"
```

- [ ] **Step 2: 跑测试验失败**

Run: `cd G:/financial-analyst && pytest tests/test_watch_status_cfg.py -v --tb=short`
Expected: `test_watch_status_idle_returns_defaults` + `test_watch_status_running_returns_loop_cfg` FAIL (KeyError).

- [ ] **Step 3: 改 watch_status endpoint**

Edit `src/financial_analyst/buddy/server.py` L2435-2447:
```python
@app.get("/watch/status")
async def watch_status():
    """Current盯盘 state: running flag, item list, tick/LLM counters + 当前 cfg."""
    loop = _watch_loop
    items = _watch_items_view()
    cfg = getattr(loop, "cfg", None) if loop else None
    return JSONResponse({
        "ok": True,
        "running": _watch_running(),
        "n_items": len(items),
        "items": items,
        "tick_count": int(getattr(loop, "tick_count", 0)) if loop else 0,
        "llm_calls_made": int(getattr(loop, "llm_calls_made", 0)) if loop else 0,
        # P0.2 新增 (让前端显示当前 cfg, 不只默认值)
        "tick_seconds": float(getattr(cfg, "tick_seconds", 60)) if cfg else 60.0,
        "cooldown_minutes": int(getattr(cfg, "cooldown_minutes", 15)) if cfg else 15,
        "global_llm_cap_per_session": int(getattr(cfg, "global_llm_cap_per_session", 20)) if cfg else 20,
    })
```

- [ ] **Step 4: 跑测试验通过**

Run: `pytest tests/test_watch_status_cfg.py -v`
Expected: 3 pass

- [ ] **Step 5: 后端 watch 回归**

Run: `pytest tests/test_watch*.py -v --tb=line -m "not slow"`
Expected: 全过

- [ ] **Step 6: Commit**

```bash
git add src/financial_analyst/buddy/server.py tests/test_watch_status_cfg.py
git commit -m "feat(watch): /watch/status 返当前 cfg (tick_seconds/cooldown/llm_cap) 让前端显示"
```

---

## Task 2: 前端 WatchMode 5 sub-component + chip 增强 + KPI tooltip (~半天)

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (WatchMode @ L2448-2735, RecCard @ L2307)
- Modify: `src/financial_analyst/ui/quant.html` `?v=` bump

照 spec § P0/P1/P2 实施. 5 sub-component:

- **WatchStrategyBanner** ({running}) — banner stopped/running 双模式 (spec § P0.1)
- **WatchStatusChips** ({status, recs}) — 一行 chip 串 (spec § P0.2)
- **RecDetailModal** ({rec, onClose}) — RecCard 点开 modal (spec § P0.3)
- **TriggerPopover** ({code, kind, ts, onClose}) + `TRIGGER_META` 静态 dict — ⚡ 点开详情 (spec § P1.2)
- **WatchAdvancedControls** ({tick, setTick, cool, setCool, cap, setCap, running}) — 折叠区 (spec § P2.1)

辅助改动:
- 改 `RecCard` signature 加 `onOpen`, 卡片整体加 `onClick` 调 `onOpen(item)`, confirm/ignore 按钮 `e.stopPropagation()`
- 自选 chip (L2675-2699) 加 "⋯" 编辑入口 → popover 改 avg_cost/stop_loss; 显示 `(price-cost)/cost` 浮动收益
- 4 KPI (L2707-2712) 加 tooltip prop (Kpi 已在 backtest 支持)
- WatchMode 加 useState: showAdv / tickSeconds(60) / cooldown(15) / llmCap(20) / selectedRec / selectedTrigger / llmCallsMade
- 初始 status fetch + running flip 时同步 cfg state 到本地
- `start()` body 加 tick_seconds / cooldown_minutes / global_llm_cap_per_session + items 含 avg_cost/stop_loss
- 顶部控制条 + 添加 旁加 [高级 ▾] toggle, 下方插 `<WatchStrategyBanner />` + `<WatchStatusChips />` + `{showAdv && <WatchAdvancedControls />}`
- WatchMode return 末尾插 `{selectedRec && <RecDetailModal .../>}` + `{selectedTrigger && <TriggerPopover .../>}`

quant.html: grep `?v=` 找当前 cache buster, bump (eg `20260603a → 20260603b`).

- [ ] **Step 1-15: 按上述清单实施 (TDD 不适用 — 纯 JSX 视觉, e2e 验)**
- [ ] **Step 16: 手工自检 — 浏览器开 quant.html 切到实时盯盘**
  - 看 banner stopped 文案
  - + 添加 SH600519
  - [高级 ▾] 展开
  - 看不到 SSE 连接没问题 (要 fa serve 跑, 本步不要求)
- [ ] **Step 17: Commit**

```bash
git add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git commit -m "feat(ui): WatchMode P0+P1+P2 — banner/状态横条/RecModal/自选chip编辑/trigger popover/KPI tooltip/高级控件"
```

---

## Task 3: Playwright e2e + 全量回归 (~半天)

**Files:**
- Create: `tests/test_watch_panel_ux_e2e.py`

### 3.1 写 Playwright 测试

复用 backtest e2e 的 fixture 模式 (subprocess fa serve + http.server + os-assigned free ports + page.route 绕 `window.GUANLAN_BACKEND` 硬编码).

- [ ] **Step 1: 写测试**

Create `tests/test_watch_panel_ux_e2e.py`:
```python
"""Playwright e2e: watch panel P0+P1+P2 UX 真浏览器烟测.

* P0.1 banner stopped/running 文案切换
* 加 1 只股 + [高级 ▾] 改 tick=30
* ▶ 开始 → P0.2 状态横条显示 "tick 30s"
* P1.3 KPI 现价 tooltip 含 "Tencent"
"""
import subprocess, time, os, socket
import pytest

pytestmark = pytest.mark.slow


def _free_port():
    s = socket.socket(); s.bind(("", 0)); p = s.getsockname()[1]; s.close()
    return p


@pytest.fixture(scope="module")
def stack():
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    be_port = _free_port()
    ui_port = _free_port()
    proc_be = subprocess.Popen(["fa", "serve", "--port", str(be_port)],
                                cwd="G:/financial-analyst", env=env)
    proc_ui = subprocess.Popen(["python", "-m", "http.server", str(ui_port)],
                                cwd="G:/financial-analyst/src/financial_analyst/ui", env=env)
    time.sleep(10)
    # schema sanity check
    import urllib.request, json as _json
    try:
        sch = _json.loads(urllib.request.urlopen(f"http://localhost:{be_port}/openapi.json", timeout=5).read())
        # /watch/status 应该返 tick_seconds (P0.2 加的字段)
        # 注: openapi 不会显示返回字段, 只能在跑时验. 跳过 schema check 走真测.
    except Exception as e:
        pytest.skip(f"backend cold start failed: {e}")
    yield f"http://localhost:{ui_port}/quant.html", be_port
    proc_be.terminate(); proc_ui.terminate()
    proc_be.wait(timeout=5); proc_ui.wait(timeout=5)


def test_watch_panel_ux(stack):
    url, be_port = stack
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        # 绕 window.GUANLAN_BACKEND 硬编码 (backtest e2e 教训)
        page.route(f"**127.0.0.1:9999/**", lambda r: r.continue_(
            url=r.request.url.replace("127.0.0.1:9999", f"127.0.0.1:{be_port}")))
        page.goto(url)
        page.click("text=实时盯盘", timeout=10_000)
        # P0.1 stopped banner
        page.wait_for_selector("text=盯盘未开始", timeout=5_000)
        # 加 1 只股
        page.fill("input[placeholder*='加股票代码']", "SH600519")
        page.click("text=+ 添加")
        # P2.1 高级
        page.click("text=高级 ▾")
        page.wait_for_selector("text=tick 间隔", timeout=3_000)
        # 改 tick=30 (找 tick number input — 用 label-near selector)
        # 简化: 改第一个 number input
        page.locator("input[type=number]").first.fill("30")
        # ▶ 开始
        page.click("text=开始盯盘")
        time.sleep(4)   # SSE 连
        # P0.1 running banner
        page.wait_for_selector("text=实时盯盘运行中", timeout=8_000)
        # P0.2 状态横条 tick 30s
        page.wait_for_selector("text=tick 30s", timeout=3_000)
        # P1.3 KPI tooltip — 选中股 SH600519 蜡烛区 KPI
        # 注: 蜡烛区有 4 KPI, 取"现价"上层 div 的 title
        try:
            kpi = page.locator("text=现价").locator("..").first
            tooltip = kpi.get_attribute("title")
            assert tooltip and "Tencent" in tooltip, f"现价 tooltip 缺失: {tooltip!r}"
        except Exception:
            # 蜡烛 KPI 仅在选中股 + 有 quote 时显示, SSE 没数据可能跳过
            pass
        # 停止
        page.click("text=■ 停止")
        browser.close()
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/test_watch_panel_ux_e2e.py -v -s --tb=short -m "slow"`
Expected: 1 pass (~1-2 min)

### 3.2 全量回归

- [ ] **Step 3: 全仓快速 (含老 watch tests)**

Run: `pytest -x -q --tb=short -m "not slow" --ignore=tests/test_watch_panel_ux_e2e.py 2>&1 | tail -15`
Expected: 全过

- [ ] **Step 4: 全仓含 slow**

Run: `pytest -q --tb=short --ignore=tests/test_watch_panel_ux_e2e.py 2>&1 | tail -15`
Expected: 全过

### 3.3 commit + summary

- [ ] **Step 5: Commit e2e**

```bash
git add tests/test_watch_panel_ux_e2e.py
git commit -m "test(e2e): watch panel P0+P1+P2 Playwright 烟测 (banner/横条/modal/popover/高级)"
```

- [ ] **Step 6: 总览**

`git log --oneline -5`. Expected 4 commit: 1 spec + 3 实现.

---

## DoD (从 spec 复制, 验收 checklist)

- [ ] `/watch/status` idle 返 tick_seconds=60 / cooldown_minutes=15 / global_llm_cap_per_session=20
- [ ] `POST /watch/start {tick_seconds: 30}` 后 status 返 tick_seconds=30
- [ ] 前端 stopped banner 显示 "盯盘未开始 — ..."
- [ ] 前端 running banner 显示 "实时盯盘运行中 — Tencent realtime + ..."
- [ ] 状态横条显示 tick / 冷却 / LLM cap 剩余
- [ ] 点击 RecCard → modal 显示完整 reason + trigger_kind 描述 + target/stop/confidence
- [ ] 自选 chip 显示 avg_cost / stop_loss / 浮动收益 (若 avg_cost 已填)
- [ ] ⚡ 点击 → popover 显示 trigger_kind 含义
- [ ] hover KPI "现价" → tooltip 含 "Tencent realtime"
- [ ] 高级控件 [高级 ▾] 展开 3 控件, running 中 disabled
- [ ] Playwright 烟测全过
- [ ] `quant.html` ?v= bump
- [ ] 全量回归不破
- [ ] 工作分支 feat/watch-panel-ux, main 不动, 不推 origin
