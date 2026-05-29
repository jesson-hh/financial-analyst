# 量化工作台 UI (SP-C.2–C.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 C.1 的 6 个直连因子 REST 端点接成一个独立可用的 `/quant.html` 量化工作台 (4 模式: 因子库&详情 / 炼因子 / 多因子合成 / 研究档案)，并补 2 个小后端口使其成为 炼→存→评→合成→归档→迭代 自包含闭环。

**Architecture:** 后端在 `buddy/server.py build_app()` 加 `POST /factor/save` + 给 `/factor/report`、`/factor/compose` 加 `archive`/`note` 字段 (复用现成 `UserFactorStore.add` / `ResearchArchive.append`)。前端新建独立页 `ui/quant.html` + `ui/quant.jsx` (由设计稿 `%TEMP%\fa_design_5\quant.jsx` 就地改造: 删 mock + ChatPanel + 5 处无数据源块，控件驱动直连 REST)，主 `app.jsx` 只加一个导航链接。

**Tech Stack:** Python / FastAPI / pytest TestClient (后端)；React 18 + babel-standalone 浏览器内编译 + tokens.css (前端，无构建步)；node `@babel/standalone` 编译校验 + Playwright/Chrome MCP 浏览器实测。

**关键纪律 (每个前端任务必守):**
- 改任何 `.jsx` → bump 对应 html 的 `?v=` cache-buster (全 script tag)，否则浏览器拿旧版。
- 交付前 node `@babel/standalone` 实编译 `quant.jsx`/`app.jsx`，语法错=整页白屏。
- 中文字符串用「」，不要用 ASCII 双引号当内容 (会被当字符串结束)。
- 直连 `window.GUANLAN_BACKEND` (`http://127.0.0.1:9999`)；本机探测 `trust_env=False` 绕 Clash。
- 关键控件 `opacity:1`，不靠 hover 暴露。
- **后端自检**用控制端稳定 Bash (`D:\app\miniconda` python, pandas 2.3.3) 复跑 pytest，不轻信 subagent "passed"；不污染注册表 (用 `unregister`，不用 `_clear_registry_for_tests`)。

---

## File Structure

**后端 (修改):**
- `src/financial_analyst/buddy/server.py` — 加 `SaveReq` 模型；`ReportReq`/`ComposeReq` 加 `archive`/`note`；加 `POST /factor/save` 端点；`/factor/report`、`/factor/compose` 成功后非致命归档。
- `tests/test_factor_rest.py` — 扩 5 个测试 (save / report-archive / compose-archive / archive-非致命)。

**前端 (新建):**
- `src/financial_analyst/ui/quant.html` — 独立入口页 (仿 index.html)，载 quant.jsx 渲染 `<QuantApp/>`。
- `src/financial_analyst/ui/quant.jsx` — 工作台单文件应用 (由设计稿就地改造)。

**前端 (修改):**
- `src/financial_analyst/ui/app.jsx` — TopBar (`:1264`) 加「🔬 量化工作台」链接 → 开 `quant.html`。
- `src/financial_analyst/ui/index.html` — bump `?v=` (app.jsx 改动)。

---

## Phase 1 — 后端 2 处补口 (TDD)

> 运行测试统一: 在 `G:\financial-analyst` 下 `python -m pytest tests/test_factor_rest.py -v` (用装有 financial-analyst 的环境；控制端用 `D:\app\miniconda` python 复核)。复用文件已有 `_patch_data(monkeypatch)` fixture (monkeypatch `resolve_universe_codes`+loader) 和 tmp `$FINANCIAL_ANALYST_HOME` 模式。

### Task 1: `POST /factor/save` — 炼出的因子入库

**Files:**
- Modify: `src/financial_analyst/buddy/server.py` (加 `SaveReq` 于其它 *Req 旁 `:73-93`；加端点于 `/factor/list` 之后 `:1212` 前 `return app`)
- Test: `tests/test_factor_rest.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_factor_rest.py` 末尾追加 (复用文件顶部已 import 的 `TestClient`/`build_app`)：

```python
# 7. POST /factor/save — 炼出的因子入库 (tmp home 隔离 UserFactorStore)
# ──────────────────────────────────────────────────────────────────────
def test_save_endpoint_persists_and_registers(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    client = TestClient(build_app())
    r = client.post("/factor/save", json={
        "name": "usr_rev5", "expr": "rank(-delta(close,5))",
        "description": "5 日反转", "kpis": {"rank_ic": 0.021, "state": "一般"}})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "usr_rev5"
    assert body["expr"] == "rank(-delta(close,5))"
    assert "created" in body
    # 入库后出现在 /factor/list 的 user
    lst = client.get("/factor/list").json()
    assert any(u["name"] == "usr_rev5" for u in lst["user"])


def test_save_endpoint_dedupes_name(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    client = TestClient(build_app())
    a = client.post("/factor/save", json={"name": "dup", "expr": "rank(close)"}).json()
    b = client.post("/factor/save", json={"name": "dup", "expr": "rank(volume)"}).json()
    assert a["name"] == "dup"
    assert b["name"] != "dup"   # 自动加后缀，不覆盖
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_factor_rest.py::test_save_endpoint_persists_and_registers -v`
Expected: FAIL (404 — `/factor/save` 未定义)

- [ ] **Step 3: 加 `SaveReq` 模型**

在 `server.py` `ComposeReq` 定义后 (`:93` 附近) 加：

```python
class SaveReq(BaseModel):
    name: str
    expr: str
    description: str = ""
    parsed: list = []
    kpis: dict = {}
```

- [ ] **Step 4: 加端点**

在 `factor_list_ep` 之后、`return app` (`:1214`) 之前加：

```python
    @app.post("/factor/save")
    async def factor_save_ep(req: SaveReq):
        """把炼出的因子入库 (持久化 + 注册，立即可被 /factor/report 评 + 出现在 /factor/list)。"""
        try:
            from financial_analyst.factors.forge import UserFactorStore
            entry = UserFactorStore().add({
                "name": req.name, "family": "user", "expr": req.expr,
                "description": req.description, "parsed": req.parsed, "kpis": req.kpis})
            return _jsonable(entry)
        except Exception as exc:
            return JSONResponse(status_code=500,
                                content={"error": f"{type(exc).__name__}: {exc}"})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_factor_rest.py::test_save_endpoint_persists_and_registers tests/test_factor_rest.py::test_save_endpoint_dedupes_name -v`
Expected: PASS (2 passed)

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/server.py tests/test_factor_rest.py
git -C G:\financial-analyst commit -m "feat(rest): POST /factor/save — persist+register forged factor"
```

### Task 2: `/factor/report` 归档记录 (archive + note)

**Files:**
- Modify: `server.py` (`ReportReq` `:73`；`factor_report_ep` `:1062`)
- Test: `tests/test_factor_rest.py`

- [ ] **Step 1: 写失败测试**

```python
# 8. /factor/report archive=true → 写一条 report 到 tmp 档案; archive=false → 不写
# ──────────────────────────────────────────────────────────────────────
def test_report_archive_records_run(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    _patch_data(monkeypatch)
    client = TestClient(build_app())
    client.post("/factor/report", json={
        "expr_or_name": "rank(-delta(close,5))", "archive": True, "note": "t1"})
    runs = client.get("/factor/archive").json()["runs"]
    assert len(runs) == 1 and runs[0]["kind"] == "report" and runs[0]["note"] == "t1"


def test_report_archive_off_records_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    _patch_data(monkeypatch)
    client = TestClient(build_app())
    client.post("/factor/report", json={
        "expr_or_name": "rank(-delta(close,5))", "archive": False})
    assert client.get("/factor/archive").json()["runs"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_factor_rest.py::test_report_archive_records_run -v`
Expected: FAIL (runs 为空 — 端点还没归档)

- [ ] **Step 3: `ReportReq` 加字段**

把 `server.py:73-78` 的 `ReportReq` 改为 (末尾加两行)：

```python
class ReportReq(BaseModel):
    expr_or_name: str
    universe: str = "csi300_active"
    freq: str = "month"
    start: Optional[str] = None
    end: Optional[str] = None
    archive: bool = True
    note: str = ""
```

- [ ] **Step 4: 端点内非致命归档**

把 `factor_report_ep` (`:1074-1075`) 的 `rpt = ...; return _jsonable(...)` 改为：

```python
            rpt = _eval_mod.factor_report(req.expr_or_name, cfg)
            if req.archive and getattr(rpt, "status", "") == "ok":
                try:
                    from financial_analyst.factors.research import (
                        ResearchArchive, record_from_report)
                    ResearchArchive().append(record_from_report(rpt, note=req.note))
                except Exception:
                    pass  # 归档失败不拖垮报告主体
            return _jsonable(_asdict(rpt))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_factor_rest.py::test_report_archive_records_run tests/test_factor_rest.py::test_report_archive_off_records_nothing -v`
Expected: PASS (2 passed)

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/server.py tests/test_factor_rest.py
git -C G:\financial-analyst commit -m "feat(rest): /factor/report opt-in archive recording (default on)"
```

### Task 3: `/factor/compose` 归档记录 + 归档非致命

**Files:**
- Modify: `server.py` (`ComposeReq` `:87`；`factor_compose_ep` `:1110`)
- Test: `tests/test_factor_rest.py`

- [ ] **Step 1: 写失败测试**

```python
# 9. /factor/compose archive=true → 写一条 compose; 归档异常 → 不拖垮主体 (仍 200)
# ──────────────────────────────────────────────────────────────────────
def test_compose_archive_records_run(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    _patch_data(monkeypatch)
    client = TestClient(build_app())
    r = client.post("/factor/compose", json={
        "members": ["rank(-delta(close,5))", "rank(close)"],
        "method": "equal", "archive": True, "note": "c1"})
    assert r.status_code == 200
    runs = client.get("/factor/archive").json()["runs"]
    assert any(x["kind"] == "compose" for x in runs)


def test_compose_archive_failure_is_non_fatal(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    _patch_data(monkeypatch)
    def _boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(
        "financial_analyst.factors.research.ResearchArchive.append", _boom)
    client = TestClient(build_app())
    r = client.post("/factor/compose", json={
        "members": ["rank(-delta(close,5))", "rank(close)"],
        "method": "equal", "archive": True})
    assert r.status_code == 200   # 归档炸了，报告主体仍返回
    assert r.json().get("status") == "ok"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_factor_rest.py::test_compose_archive_records_run -v`
Expected: FAIL (无 compose 记录)

- [ ] **Step 3: `ComposeReq` 加字段**

把 `server.py:87-92` 的 `ComposeReq` 末尾加两行：

```python
class ComposeReq(BaseModel):
    members: list
    method: str = "lgbm"
    universe: str = "csi300_active"
    freq: str = "month"
    train_frac: float = 0.6
    archive: bool = True
    note: str = ""
```

- [ ] **Step 4: 端点内非致命归档**

把 `factor_compose_ep` (`:1125-1127`) 的 `res = ...; return _jsonable(...)` 改为：

```python
            res = _compose_mod.compose_factors(
                req.members, cfg, method=req.method, train_frac=req.train_frac)
            if req.archive and getattr(res, "status", "") == "ok":
                try:
                    from financial_analyst.factors.research import (
                        ResearchArchive, record_from_compose)
                    ResearchArchive().append(record_from_compose(res, note=req.note))
                except Exception:
                    pass
            return _jsonable(_asdict(res))
```

- [ ] **Step 5: 跑全套确认通过 + 不回归**

Run: `python -m pytest tests/test_factor_rest.py -v`
Expected: PASS (C.1 原有 17 测 + 本阶段新增 6 测全绿)

- [ ] **Step 6: 全量后端回归 (控制端 miniconda)**

Run: `python -m pytest tests/ -q`
Expected: 无新增失败 (server/buddy/factors 套件不回归)。若有失败先排查是否环境 (litellm/mcp 缺) 而非代码。

- [ ] **Step 7: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/server.py tests/test_factor_rest.py
git -C G:\financial-analyst commit -m "feat(rest): /factor/compose opt-in archive recording (non-fatal)"
```

---

## Phase 2 — 前端脚手架 (就地改造设计稿)

> 启服务用于浏览器实测: 后端 `financial-analyst serve --port 9999`；前端 `cd src/financial_analyst/ui; python -m http.server 5173`。浏览器开 `http://127.0.0.1:5173/quant.html`。babel 编译校验脚本见 Task 4 Step 4。

### Task 4: 脚手架 — quant.html + 拷贝设计稿 + 改根组件 + 编译校验工具

**Files:**
- Create: `src/financial_analyst/ui/quant.html`
- Create: `src/financial_analyst/ui/quant.jsx` (从设计稿拷贝)
- Create: `src/financial_analyst/ui/_compile_check.js` (临时校验脚本，最后删)

- [ ] **Step 1: 拷贝设计稿为起点**

```bash
copy "%TEMP%\fa_design_5\quant.jsx" "G:\financial-analyst\src\financial_analyst\ui\quant.jsx"
```

(若 `%TEMP%\fa_design_5` 已不存在: 设计稿是视觉参考，本计划后续每个组件的改造都按 *函数名* 引用并给出具体改动，可据此重建；但优先用拷贝。)

- [ ] **Step 2: 根组件改名 + 导出 window.QuantApp**

编辑 `ui/quant.jsx`:
- 把 `function App() {` 改为 `function QuantApp() {`。
- 删掉文件末尾 `ReactDOM.createRoot(document.getElementById('root')).render(<App />);` 这一行，换成：

```javascript
window.QuantApp = QuantApp;
```

- [ ] **Step 3: 建 quant.html**

写 `ui/quant.html` (仿 index.html，独立入口；注意 `?v=` 与 index.html 各自独立)：

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>观澜 · 量化研究工作台</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600;700&family=Noto+Serif+SC:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600&display=swap" />
<link rel="stylesheet" href="tokens.css" />
<style>
  html, body, #root { height: 100%; margin: 0; }
  body { overflow-x: auto; overflow-y: hidden; }
  #root { min-width: 1200px; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  .hover-row:hover { background: rgba(28,24,20,0.04); }
  .hover-pill:hover { background: rgba(28,24,20,0.06); }
  *::-webkit-scrollbar { width: 8px; height: 8px; }
  *::-webkit-scrollbar-thumb { background: rgba(28,24,20,0.12); }
  select { appearance: none; -webkit-appearance: none; }
</style>
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous"></script>
</head>
<body>
<div id="root"></div>
<script>
  window.GUANLAN_BACKEND = 'http://127.0.0.1:9999';
</script>
<script type="text/babel" src="quant.jsx?v=20260529-1"></script>
<script type="text/babel" data-presets="env,react">
  ReactDOM.createRoot(document.getElementById('root')).render(<QuantApp />);
</script>
</body>
</html>
```

- [ ] **Step 4: 建编译校验脚本 + 跑 (防白屏)**

写 `ui/_compile_check.js`:

```javascript
// node ui/_compile_check.js  —  babel 编译 quant.jsx/app.jsx，语法错=白屏
const fs = require('fs');
const Babel = require('@babel/standalone');
for (const f of process.argv.slice(2)) {
  try {
    Babel.transform(fs.readFileSync(f, 'utf8'), { presets: ['env', 'react'] });
    console.log('OK  ', f);
  } catch (e) { console.error('FAIL', f, '\n', e.message); process.exit(1); }
}
```

Run (在 ui/ 目录):
```bash
npm i @babel/standalone@7.29.0
node _compile_check.js quant.jsx
```
Expected: `OK   quant.jsx` (拷贝来的设计稿应能编译)

- [ ] **Step 5: 浏览器冒烟**

启后端 :9999 + `http.server` :5173，浏览器开 `/quant.html`。用 Playwright/Chrome MCP: 断言 `#root` 非空 (非白屏)，能看到 TopBar「觀瀾 · 量化研究」+ 设计稿 mock 内容。

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.html src/financial_analyst/ui/quant.jsx
git -C G:\financial-analyst commit -m "feat(ui): standalone quant.html scaffold from design draft (mock, compiling)"
```

### Task 5: 删 mock + 死组件 + 加数据层/三态/工具 + TopBar 改 4 模式

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx`

- [ ] **Step 1: 删 mock 数据与无数据源组件**

在 `ui/quant.jsx` 删除以下 (按函数/常量名整段删，连同其调用处)：
- 常量: `LIBS`, `FACTORS`；函数 `genICSeries`, `genEquity`, `genDecile`, `genPicks` 及任何 `buildAlchemyFormula`/`alchemyKpis`/`buildUserFactorEntry`/`SEED_CHAT` 等 mock 助手。
- 组件: `ChatPanel`, `Avatar` (若仅 ChatPanel/AlchemyCard 用，保留待 Task 8 复用则不删——见下), `ToolChain`, `SignalChart`, `SignalPopover`, `SignalStats`, `SignalLegend`, `CrowdingBox`, `ExposureBars`, `CorrList`, `PicksTable`。
- `FactorDetail` 内的「信号回放 · 焦点」段、「风险归因 · 拥挤度 · 相关性」折叠段 (调用上面已删组件的 JSX)。
- 保留待复用: `AlchemyCard`(Task 8 改造)、`AlchemyParamEditor`(删)、`AlchemySpark`(保留)、`Avatar`(Task 8 用)、图表 `ICChart`/`EquityChart`/`DecileChart`、`Kpi`/`Panel`/`Pill`/`Segmented`/`ChartTip`/`MiniSparkline`/`LegendInline`。

- [ ] **Step 2: 加 REST 数据层 + 三态 + 格式化工具**

在 `ui/quant.jsx` 顶部 (`const { useState, ... } = React;` 之后) 加：

```javascript
// ───────── 直连 REST 数据层 ─────────
const API = window.GUANLAN_BACKEND || '';

async function q(path, opts) {
  const res = await fetch(API + path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok && (!body || body.error)) {
    throw new Error((body && body.error) || ('HTTP ' + res.status));
  }
  return body;
}
const getJSON = (path) => q(path);
const postJSON = (path, payload) =>
  q(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

// useAsync: 手动触发的异步请求 → {data, loading, error, run, reset}
function useAsync() {
  const [state, setState] = useState({ data: null, loading: false, error: null });
  const run = useCallback(async (fn) => {
    setState({ data: null, loading: true, error: null });
    try { const data = await fn(); setState({ data, loading: false, error: null }); return data; }
    catch (e) { setState({ data: null, loading: false, error: e.message || String(e) }); }
  }, []);
  const reset = useCallback(() => setState({ data: null, loading: false, error: null }), []);
  return { ...state, run, reset };
}

// null/undefined/NaN → 「—」; 数字按位
const n2 = (v, d = 2) => (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) ? '—' : (typeof v === 'number' ? v.toFixed(d) : v);
const pct = (v, d = 2) => (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) ? '—' : (v * 100).toFixed(d) + '%';

// ───────── 三态小组件 ─────────
function Loading({ label = '加载中…' }) {
  return <div className="mono" style={{ padding: 24, fontSize: 12, color: 'var(--ink-3)', textAlign: 'center' }}>⏳ {label}</div>;
}
function Empty({ label = '暂无数据' }) {
  return <div className="serif" style={{ padding: 24, fontSize: 13, color: 'var(--ink-3)', textAlign: 'center' }}>{label}</div>;
}
function ErrorBox({ error }) {
  return <div className="mono" style={{ padding: 16, fontSize: 12, color: 'var(--yin)', border: '1px solid var(--line)', background: 'rgba(28,24,20,0.03)' }}>✗ {error}</div>;
}
const POOLS = ['csi300', 'csi500', 'csi800', 'all'];
const POOL_DEFAULT = 'csi300_active';
```

- [ ] **Step 3: TopBar 改 4 模式 + 删 mock 行情/时钟**

把 `TopBar` 的 `nav` 数组改为 4 模式，并删掉 mock 指数 ticker + 假「交易中 14:17」块 (整段 `<div className="mono" ...>{[{n:'上证'...}]}</div>` 到时钟 `</header>` 前)：

```javascript
      <nav style={{ display: 'flex', alignItems: 'center', gap: 0, marginLeft: 28 }}>
        {[
          { k: 'lib',     l: '因子库 & 详情' },
          { k: 'forge',   l: '炼因子' },
          { k: 'compose', l: '多因子合成' },
          { k: 'archive', l: '研究档案' },
        ].map(t => (
          <button key={t.k} onClick={() => onMode(t.k)} className="hover-pill" style={{
            padding: '6px 12px', border: 'none', background: 'transparent',
            fontFamily: 'var(--serif)', fontSize: 12.5,
            color: mode === t.k ? 'var(--ink)' : 'var(--ink-2)',
            borderBottom: mode === t.k ? '2px solid var(--yin)' : '2px solid transparent',
            cursor: 'pointer',
          }}>{t.l}</button>
        ))}
      </nav>
      <div style={{ flex: 1 }} />
      <a href="app.html" className="mono hover-link" style={{ fontSize: 11, color: 'var(--ink-3)', textDecoration: 'none' }}>← 返回对话</a>
```

(注: `app.html` 链接见 Task 12 —— 主 app 入口若仍是 `index.html` 则改这里的 href 为 `index.html`。)

- [ ] **Step 4: QuantApp 改成 4 模式路由 (空体)**

把 `QuantApp` 函数体替换为：

```javascript
function QuantApp() {
  const [mode, setMode] = useState('lib');
  return (
    <div className="paper-bg" style={{ width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)' }}>
      <TopBar mode={mode} onMode={setMode} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        {mode === 'lib' && <LibraryMode />}
        {mode === 'forge' && <ForgeMode />}
        {mode === 'compose' && <ComposeMode />}
        {mode === 'archive' && <ArchiveMode />}
      </div>
    </div>
  );
}
function LibraryMode() { return <Empty label="因子库 (待接)" />; }
function ForgeMode() { return <Empty label="炼因子 (待接)" />; }
function ComposeMode() { return <Empty label="多因子合成 (待接)" />; }
function ArchiveMode() { return <Empty label="研究档案 (待接)" />; }
```

- [ ] **Step 5: 编译 + 浏览器验证 (4 模式可切换, 无白屏)**

Run: `node _compile_check.js quant.jsx` → `OK`
浏览器 `/quant.html`: 断言无白屏，4 个 TopBar 按钮可切换，各显占位 Empty。

- [ ] **Step 6: bump + 提交**

把 `quant.html` 的 `quant.jsx?v=20260529-1` 改 `-2`。
```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "refactor(ui): strip mock + add REST data layer + 4-mode shell"
```

### Task 6: 图表组件适配真实数据

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (`ICChart`/`EquityChart`/`DecileChart`)

- [ ] **Step 1: `ICChart` 接真 IC 序列 + 删假 t-stat**

`ICChart` 现签名 `({ series })` 其中 series 是数字数组。改为接受日期标签并去除虚构 t-stat：
- 签名改 `function ICChart({ series, dates })`。
- 删 `monthLabel` 函数 + 用它的 x 轴 label，改为按 `dates` 头/中/尾取 3 个 (空则用 `第1/N/M 期`)：
```javascript
        {(() => {
          const idxs = dates && dates.length ? [0, Math.floor(dates.length/2), dates.length-1] : [];
          const lbl = (i) => dates && dates[i] ? String(dates[i]).slice(2,10) : `第${i+1}`;
          return idxs.map((di, k, arr) => {
            const x = pad.l + (w - pad.l - pad.r) * (k / (arr.length - 1));
            return <text key={di} x={x} y={h - 6} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{lbl(di)}</text>;
          });
        })()}
```
- tooltip 里删 `tStat`/`stars` (虚构)，只留 IC 值 + 日期：把 tooltip body 改为显示 `monthLabel(hover.idx)` → `dates[hover.idx]` 和 IC 值两行 (删「t-stat」「显著性」两行)。

- [ ] **Step 2: `EquityChart` 接真净值 + 真基准**

- 签名改 `function EquityChart({ series, dates, benchmark })` (series/benchmark 都是数字数组，benchmark 可空)。
- 删第 `bench = series.map((_, i) => 1 + ...)` 假基准行，改 `const bench = benchmark && benchmark.length === series.length ? benchmark : null;`
- `benchPath`/tooltip 里 bench 相关用 `bench &&` 守卫 (无基准则不画基准线、tooltip 不显基准/超额)。
- 删 `dayLabel` 的 mock months，改用 `dates`：x 轴 label 用 `dates` 头/中/尾 (同 ICChart 写法)；tooltip 日期用 `dates[hover.idx]`。

- [ ] **Step 3: `DecileChart` 网格线自适应**

- 签名保留 `({ bars, active, onToggle })`，bars 由调用方传 `group_ann_return.map(v => v*100)` (百分比)。
- 把硬编码网格线 `[-10, -5, 5, 10]` 改为按 max 派生：
```javascript
        {[-max, -max/2, max/2, max].map((v) => {
          const y = mid - (v / max) * ((h - pad.t - pad.b) / 2);
          return (
            <g key={v}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke="var(--line-soft)" strokeDasharray="2 3" />
              <text x={pad.l - 4} y={y + 3} fontSize="9" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">{v.toFixed(0)}%</text>
            </g>
          );
        })}
```

- [ ] **Step 4: 编译校验**

Run: `node _compile_check.js quant.jsx`
Expected: `OK` (此时图表暂无调用方，仅验证语法)

- [ ] **Step 5: bump + 提交**

`quant.html` `?v=` → `-3`。
```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "refactor(ui): adapt IC/Equity/Decile charts for real data + derived axes"
```

---

## Phase 3 — 4 模式接真端点

### Task 7: C.2 因子库 & 详情

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (`LibraryMode` + 新 `FactorReportView`)

- [ ] **Step 1: 写 `FactorReportView` (C.2 详情 + C.4a 复用)**

加组件 (两档分离: IC 体检 / 组合回测)：

```javascript
function FactorReportView({ report }) {
  if (!report) return null;
  if (report.status && report.status !== 'ok') {
    return <ErrorBox error={`评测未完成 · ${report.status}${report.error ? ' · ' + report.error : ''}`} />;
  }
  const ic = report.ic || {}, qt = report.quantile || {}, pf = report.portfolio || {}, ch = report.characteristics || {};
  const icDates = (ic.ic_series || []).map(p => p[0]);
  const icVals = (ic.ic_series || []).map(p => p[1]);
  const navVals = (pf.nav_series || []).map(p => p[1]);
  const navDates = (pf.nav_series || []).map(p => p[0]);
  const benchVals = (pf.benchmark_nav || []).map(p => p[1]);
  const decile = (qt.group_ann_return || []).map(v => v * 100);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {(report.warnings || []).length > 0 && (
        <div className="mono" style={{ fontSize: 10, color: 'var(--jin)' }}>⚠ {report.warnings.join(' · ')}</div>
      )}
      <Panel title={<><span>IC 体检</span><span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 8 }}>秒级 · 截面相关</span></>}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 0, border: '1px solid var(--line-soft)' }}>
          <Kpi label="IC 均值" value={n2(ic.ic_mean, 4)} />
          <Kpi label="ICIR" value={n2(ic.icir, 2)} />
          <Kpi label="RankIC" value={n2(ic.rank_ic_mean, 4)} />
          <Kpi label="RankICIR" value={n2(ic.rank_icir, 2)} />
          <Kpi label="t-stat" value={n2(ic.ic_tstat, 2)} />
          <Kpi label="IC 胜率" value={pct(ic.ic_win_rate)} />
          <Kpi label="覆盖度" value={pct(ch.coverage)} />
          <Kpi label="半衰期" value={ch.half_life >= 0 ? n2(ch.half_life, 0) : '—'} />
        </div>
        <div style={{ marginTop: 10 }}>{icVals.length ? <ICChart series={icVals} dates={icDates} /> : <Empty label="无 IC 序列" />}</div>
      </Panel>
      <Panel title={<><span>组合回测</span><span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 8 }}>分钟级 · 十分位等权多空</span></>}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 0, border: '1px solid var(--line-soft)' }}>
          <Kpi label="年化" value={pct(pf.ann_return)} />
          <Kpi label="Sharpe" value={n2(pf.sharpe, 2)} />
          <Kpi label="最大回撤" value={pct(pf.max_drawdown)} />
          <Kpi label="Calmar" value={n2(pf.calmar, 2)} />
          <Kpi label="波动率" value={pct(pf.volatility)} />
          <Kpi label="换手" value={pct(pf.turnover)} />
          <Kpi label="胜率" value={pct(pf.win_rate)} />
          <Kpi label="多空价差" value={pct(qt.long_short_spread)} />
        </div>
        <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 320px' }}>{navVals.length ? <EquityChart series={navVals} dates={navDates} benchmark={benchVals} /> : <Empty label="无净值" />}</div>
          <div style={{ flex: '1 1 320px' }}>{decile.length ? <DecileChart bars={decile} /> : <Empty label="无十分位" />}</div>
        </div>
      </Panel>
    </div>
  );
}
```

- [ ] **Step 2: 写 `LibraryMode` (左库导航 + 右详情)**

```javascript
function LibraryMode() {
  const [list, setList] = useState({ registered: [], user: [] });
  const [benchRows, setBenchRows] = useState([]);
  const [pool, setPool] = useState('csi300');
  const [family, setFamily] = useState('alpha101');
  const [sel, setSel] = useState('');       // 选中的因子名
  const [expr, setExpr] = useState('');     // 或手输表达式
  const rpt = useAsync();
  const benchA = useAsync();

  useEffect(() => { getJSON('/factor/list').then(setList).catch(() => {}); }, []);
  const loadBench = () => benchA.run(() => getJSON(`/factor/bench?universe=${pool}&family=${family}`).then(b => { setBenchRows(b.rows || []); return b; }));
  const runReport = (target) => {
    const t = target || expr || sel;
    if (!t) return;
    rpt.run(() => postJSON('/factor/report', { expr_or_name: t, universe: pool === 'csi300' ? POOL_DEFAULT : pool }));
  };
  const icByName = {}; benchRows.forEach(r => { icByName[r.name] = r; });

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      {/* 左: 因子库导航 */}
      <aside style={{ width: 300, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ padding: 12, borderBottom: '1px solid var(--line-soft)', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Segmented value={family} onChange={setFamily} options={[{v:'alpha101',l:'Alpha101'},{v:'gtja191',l:'GTJA191'},{v:'qlib158',l:'Qlib158'},{v:'user',l:'我的'}]} />
          <button onClick={loadBench} className="hover-pill" style={{ fontSize: 11, padding: '4px 8px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer' }}>批量 IC ↻</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {benchA.loading && <Loading label="批量 IC 计算中…" />}
          {(family === 'user' ? list.user : list.registered.filter(r => !family || r.family === family)).map(f => (
            <div key={f.name} className="hover-row" onClick={() => { setSel(f.name); setExpr(''); runReport(f.name); }}
              style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', background: sel === f.name ? 'rgba(28,24,20,0.05)' : 'transparent' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <code className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>{f.name}</code>
                {icByName[f.name] && <span className={'mono ' + (icByName[f.name].rank_ic >= 0 ? 'up' : 'down')} style={{ fontSize: 10 }}>{n2(icByName[f.name].rank_ic, 3)}</span>}
              </div>
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.formula || f.expr || ''}</div>
            </div>
          ))}
          {!(family === 'user' ? list.user : list.registered).length && <Empty label="暂无因子" />}
        </div>
      </aside>
      {/* 右: 详情 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <input value={expr} onChange={e => setExpr(e.target.value)} placeholder="输入白名单表达式, 如 rank(-delta(close,5))"
            style={{ flex: '1 1 280px', padding: '6px 10px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }} />
          <Segmented value={pool} onChange={setPool} options={POOLS.map(p => ({ v: p, l: p }))} />
          <button onClick={() => runReport()} disabled={rpt.loading} className="hover-pill"
            style={{ padding: '6px 14px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
            {rpt.loading ? '运行中…' : '运行评测'}
          </button>
        </div>
        {rpt.loading && <Loading label="组合回测中 (小池秒级 / 大池分钟级)…" />}
        {rpt.error && <ErrorBox error={rpt.error} />}
        {rpt.data && <FactorReportView report={rpt.data} />}
        {!rpt.data && !rpt.loading && !rpt.error && <Empty label="选左侧因子 / 输表达式 → 运行评测" />}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 编译校验**

Run: `node _compile_check.js quant.jsx` → `OK`

- [ ] **Step 4: 浏览器实测 C.2**

启 :9999 + :5173 → `/quant.html` 因子库模式: 左库列出真因子 (registered 非空)；点「批量 IC」出横条数值；点一个因子 / 输 `rank(-delta(close,5))` + 运行评测 → 右出 IC 体检 + 组合回测两档 + 图。验冷池/错表达式出 ErrorBox。

- [ ] **Step 5: bump + 提交**

`?v=` → `-4`。
```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "feat(ui): C.2 factor library + detail (list/bench/report, two-tier)"
```

### Task 8: C.3 炼因子 (ForgeCard 重建)

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (改 `AlchemyCard` → 真 forge 契约；写 `ForgeMode`)

- [ ] **Step 1: 把 `AlchemyCard` 重建为真 forge 契约**

`AlchemyCard` 现是客户端调参 mock。改为消费 `ForgeResult` + 触发存库。保留视觉外壳 (「炼」角章、原话→解析→公式→速测 分层、暖色)，删 param 滑块/`buildAlchemyFormula`/`alchemyKpis` 逻辑。替换整个 `AlchemyCard` 为：

```javascript
function ForgeCard({ result, onSave, saved, saving }) {
  // result: ForgeResult + quick_ic. 失败态 (compile_ok=false / out_of_vocab) 友好显示。
  if (!result) return null;
  const { idea, expr, parsed, name, rationale, compile_ok, error, out_of_vocab, quick_ic } = result;
  return (
    <div style={{ background: 'var(--paper)', border: '1.5px solid var(--ink)', position: 'relative', boxShadow: '6px 6px 0 -2px var(--paper-3)', maxWidth: 640 }}>
      <div style={{ position: 'absolute', top: -1, right: -1, width: 30, height: 30, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, fontWeight: 500, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>炼</div>
      <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'baseline', gap: 8, paddingRight: 38 }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 500 }}>经验 → 因子</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>α-FORGE</span>
      </div>
      {/* 原话 */}
      <div style={{ padding: '12px 14px 10px' }}>
        <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>原话 · 想法</div>
        <div className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.72, fontStyle: 'italic', paddingLeft: 12, borderLeft: '2px solid var(--jin)' }}>{idea}</div>
      </div>
      {out_of_vocab && <div className="mono" style={{ padding: '0 14px 12px', fontSize: 11, color: 'var(--jin)' }}>当前只支持价量/估值/股息/规模/换手类因子；ROE/财报/事件暂不支持 (B.2)。</div>}
      {!compile_ok && !out_of_vocab && <div style={{ padding: '0 14px 12px' }}><ErrorBox error={error || '生成失败'} /></div>}
      {compile_ok && (
        <>
          {rationale && (
            <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px' }}>
              <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>推理 · LLM</div>
              <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.6 }}>{rationale}</div>
            </div>
          )}
          <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px', background: 'rgba(28,24,20,0.04)' }}>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>因子公式 · {name || 'usr_factor'}</div>
            <pre style={{ margin: 0, fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{expr}</pre>
          </div>
          <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px' }}>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 8 }}>速测 IC{quick_ic ? '' : ' · 跳过'}</div>
            {quick_ic ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 0, border: '1px solid var(--line-soft)' }}>
                <Kpi label="RankIC" value={n2(quick_ic.rank_ic, 4)} />
                <Kpi label="RankICIR" value={n2(quick_ic.rank_ir, 2)} />
                <Kpi label="判定" value={quick_ic.state || '—'} />
              </div>
            ) : <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>无速测结果</span>}
          </div>
          <div style={{ borderTop: '1px solid var(--line)', padding: '8px 12px' }}>
            <button onClick={() => onSave && onSave(result)} disabled={saved || saving} style={{
              width: '100%', padding: '7px 10px', background: saved ? 'transparent' : 'var(--ink)', color: saved ? 'var(--dai)' : 'var(--paper)',
              border: saved ? '1px solid var(--dai)' : 'none', fontFamily: 'var(--serif)', fontSize: 12, cursor: saved ? 'default' : 'pointer' }}>
              {saved ? '✓ 已入库 · 可在因子库引用' : saving ? '入库中…' : '存入因子库 ↗'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 写 `ForgeMode`**

```javascript
function ForgeMode() {
  const [idea, setIdea] = useState('');
  const [pool, setPool] = useState('csi300');
  const forge = useAsync();
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const runForge = () => { if (!idea.trim()) return; setSaved(false); forge.run(() => postJSON('/factor/forge', { idea, universe: pool === 'csi300' ? POOL_DEFAULT : pool, quick_eval: true })); };
  const save = async (r) => {
    setSaving(true);
    try { await postJSON('/factor/save', { name: r.name, expr: r.expr, description: r.rationale, parsed: r.parsed, kpis: r.quick_ic || {} }); setSaved(true); }
    catch (e) { alert('入库失败: ' + e.message); }
    finally { setSaving(false); }
  };
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 24, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
      <div style={{ width: '100%', maxWidth: 640, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <textarea value={idea} onChange={e => setIdea(e.target.value)} rows={3}
          placeholder="用一句话描述你的因子想法, 如: 5 日反转 / 量价背离 / 低换手高股息"
          style={{ padding: '10px 12px', border: '1px solid var(--line)', fontFamily: 'var(--sans)', fontSize: 13, background: 'var(--paper)', resize: 'vertical' }} />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Segmented value={pool} onChange={setPool} options={POOLS.map(p => ({ v: p, l: p }))} />
          <span style={{ flex: 1 }} />
          <button onClick={runForge} disabled={forge.loading} className="hover-pill"
            style={{ padding: '7px 16px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
            {forge.loading ? '炼制中…' : '炼因子 ⚗'}
          </button>
        </div>
      </div>
      {forge.loading && <Loading label="LLM 炼因子 + 速测中…" />}
      {forge.error && <ErrorBox error={forge.error} />}
      {forge.data && <ForgeCard result={forge.data} onSave={save} saved={saved} saving={saving} />}
    </div>
  );
}
```

- [ ] **Step 3: 编译校验**

Run: `node _compile_check.js quant.jsx` → `OK`

- [ ] **Step 4: 浏览器实测 C.3**

`/quant.html` 炼因子: 输「5 日反转」→ 炼因子 → 出 expr + 推理 + 速测 IC；点「存入因子库」→ 成功；切到因子库模式选「我的」family → 新因子在列且可评测。验 out_of_vocab (输「ROE 连续上修」) 出友好提示。

- [ ] **Step 5: bump + 提交**

`?v=` → `-5`。
```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "feat(ui): C.3 forge card (real /factor/forge + /factor/save loop)"
```

### Task 9: C.4a 多因子合成

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (`ComposeMode`)

- [ ] **Step 1: 写 `ComposeMode`**

```javascript
function ComposeMode() {
  const [list, setList] = useState({ registered: [], user: [] });
  const [members, setMembers] = useState([]);   // 选中成员名/表达式
  const [draft, setDraft] = useState('');
  const [method, setMethod] = useState('lgbm');
  const [pool, setPool] = useState('csi300');
  const [trainFrac, setTrainFrac] = useState(0.6);
  const comp = useAsync();
  useEffect(() => { getJSON('/factor/list').then(setList).catch(() => {}); }, []);
  const allNames = [...list.registered.map(r => r.name), ...list.user.map(u => u.name)];
  const addMember = (m) => { if (m && !members.includes(m)) setMembers([...members, m]); };
  const run = () => { if (members.length < 2) return; comp.run(() => postJSON('/factor/compose', { members, method, universe: pool === 'csi300' ? POOL_DEFAULT : pool, train_frac: trainFrac })); };
  const res = comp.data;
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        <input list="members-dl" value={draft} onChange={e => setDraft(e.target.value)}
          placeholder="选/输因子名或表达式" style={{ flex: '1 1 240px', padding: '6px 10px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }} />
        <datalist id="members-dl">{allNames.map(n => <option key={n} value={n} />)}</datalist>
        <button onClick={() => { addMember(draft.trim()); setDraft(''); }} className="hover-pill" style={{ padding: '6px 10px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 12 }}>+ 加成员</button>
        <Segmented value={method} onChange={setMethod} options={[{v:'equal',l:'等权'},{v:'ic_weighted',l:'IC加权'},{v:'linear',l:'线性'},{v:'lgbm',l:'LGBM'}]} />
        <Segmented value={pool} onChange={setPool} options={POOLS.map(p => ({ v: p, l: p }))} />
        <button onClick={run} disabled={comp.loading || members.length < 2} className="hover-pill"
          style={{ padding: '6px 14px', border: 'none', background: members.length < 2 ? 'var(--line)' : 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          {comp.loading ? '合成中…' : '合成评测'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
        {members.map(m => (
          <span key={m} className="mono" style={{ fontSize: 11, padding: '3px 8px', border: '1px solid var(--line)', display: 'flex', gap: 6, alignItems: 'center' }}>
            {m}<span onClick={() => setMembers(members.filter(x => x !== m))} style={{ cursor: 'pointer', color: 'var(--yin)', opacity: 1 }}>×</span>
          </span>
        ))}
        {members.length < 2 && <span className="serif" style={{ fontSize: 12, color: 'var(--ink-3)' }}>至少选 2 个成员</span>}
      </div>
      {comp.loading && <Loading label="OOS 训练/测试中…" />}
      {comp.error && <ErrorBox error={comp.error} />}
      {res && res.status && res.status !== 'ok' && <ErrorBox error={`合成未完成 · ${res.status}${res.error ? ' · ' + res.error : ''}`} />}
      {res && res.status === 'ok' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Panel title={<>合成结论 · <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{res.method} · train {res.n_train_dates} / test {res.n_test_dates}</span></>}>
            <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.6, marginBottom: 8 }}>{res.verdict}</div>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 220px' }}>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginBottom: 4 }}>权重</div>
                {Object.entries(res.weights || {}).map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><code className="mono">{k}</code><span className="mono">{n2(v, 3)}</span></div>
                ))}
              </div>
              <div style={{ flex: '1 1 320px' }}>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginBottom: 4 }}>成员 OOS 对比</div>
                <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
                  <thead><tr style={{ color: 'var(--ink-3)' }}><td>成员</td><td style={{ textAlign: 'right' }}>RankIC</td><td style={{ textAlign: 'right' }}>Sharpe</td></tr></thead>
                  <tbody>{(res.member_oos || []).map(m => (
                    <tr key={m.name}><td><code className="mono">{m.name}</code></td><td className="mono" style={{ textAlign: 'right' }}>{n2(m.rank_ic, 3)}</td><td className="mono" style={{ textAlign: 'right' }}>{n2(m.sharpe, 2)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          </Panel>
          <div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', margin: '4px 0 8px' }}>综合分 OOS 评测</div>
            <FactorReportView report={res.composite} />
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 编译校验**

Run: `node _compile_check.js quant.jsx` → `OK`

- [ ] **Step 3: 浏览器实测 C.4a**

`/quant.html` 多因子合成: 加 2 个成员 (如 `rank(-delta(close,5))` + `rank(close)`) → method=等权 → 合成评测 → 出权重 + verdict + 成员对比表 + 综合分 OOS 报告 (复用详情图)。验 members<2 时按钮禁用。

- [ ] **Step 4: bump + 提交**

`?v=` → `-6`。
```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "feat(ui): C.4a compose (members/method/OOS + reuse FactorReportView)"
```

### Task 10: C.4b 研究档案

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (`ArchiveMode`)

- [ ] **Step 1: 写 `ArchiveMode`**

```javascript
function ArchiveMode() {
  const listA = useAsync();
  const [target, setTarget] = useState('');
  const [cmp, setCmp] = useState([]);     // 选中待对比的两个 run id
  const cmpA = useAsync();
  useEffect(() => { listA.run(() => getJSON('/factor/archive')); }, []);
  const runs = (listA.data && listA.data.runs) || (target && listA.data && listA.data.history) || [];
  const rows = target ? ((listA.data && listA.data.history) || []) : runs;
  const loadTarget = (t) => { setTarget(t); listA.run(() => getJSON(`/factor/archive?target=${encodeURIComponent(t)}`)); };
  const reset = () => { setTarget(''); setCmp([]); cmpA.reset(); listA.run(() => getJSON('/factor/archive')); };
  const toggleCmp = (id) => { const next = cmp.includes(id) ? cmp.filter(x => x !== id) : [...cmp, id].slice(-2); setCmp(next); if (next.length === 2) cmpA.run(() => getJSON(`/factor/archive?compare=${next[0]},${next[1]}`)); };
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
        <span className="serif" style={{ fontSize: 14, color: 'var(--ink)' }}>研究档案 {target && <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>· {target} 历史</span>}</span>
        {(target || cmp.length) ? <button onClick={reset} className="hover-pill" style={{ fontSize: 11, padding: '3px 8px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer' }}>← 全部</button> : null}
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>勾选 2 条对比</span>
      </div>
      {listA.loading && <Loading />}
      {listA.error && <ErrorBox error={listA.error} />}
      {cmpA.data && (
        <Panel title="对比 diff">
          <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
            <tbody>{Object.entries(cmpA.data).map(([k, v]) => (
              <tr key={k}><td className="mono" style={{ color: 'var(--ink-3)' }}>{k}</td><td className="mono">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td></tr>
            ))}</tbody>
          </table>
        </Panel>
      )}
      {!listA.loading && !rows.length && <Empty label="研究档案为空 · 在因子库/合成里跑评测会自动归档" />}
      <div style={{ marginTop: 12 }}>
        {rows.map(r => (
          <div key={r.id} className="hover-row" style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 10px', borderBottom: '1px solid var(--line-soft)' }}>
            <input type="checkbox" checked={cmp.includes(r.id)} onChange={() => toggleCmp(r.id)} />
            <code className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', width: 60 }}>{r.id}</code>
            <span className="mono" style={{ fontSize: 10, padding: '1px 5px', background: r.kind === 'compose' ? 'var(--yin)' : 'var(--dai)', color: 'var(--paper)' }}>{r.kind}</span>
            <code className="mono hover-link" onClick={() => loadTarget(r.target)} style={{ fontSize: 12, color: 'var(--ink)', cursor: 'pointer' }}>{r.target}</code>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flex: 1 }}>{r.timestamp} · {r.universe}/{r.freq} {r.note ? '· ' + r.note : ''}</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{Object.entries(r.metrics || {}).slice(0, 3).map(([k, v]) => `${k}=${n2(v, 3)}`).join('  ')}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 编译校验**

Run: `node _compile_check.js quant.jsx` → `OK`

- [ ] **Step 3: 浏览器实测 C.4b**

先在因子库/合成各跑 1 次 (archive 默认 on) → 切研究档案: 出 runs 列表；点 target 看 history；勾 2 条出 compare diff。空态文案正确 (新 tmp home)。

- [ ] **Step 4: bump + 提交**

`?v=` → `-7`。
```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "feat(ui): C.4b research archive (list/history/compare)"
```

---

## Phase 4 — 集成与回归

### Task 11: 删临时编译脚本

- [ ] **Step 1: 删 `_compile_check.js`** (改用 npx 一行命令，避免污染 ui/)

```bash
del "G:\financial-analyst\src\financial_analyst\ui\_compile_check.js"
```
后续编译校验改用: `node -e "const B=require('@babel/standalone'),fs=require('fs');B.transform(fs.readFileSync('quant.jsx','utf8'),{presets:['env','react']});console.log('OK')"`

- [ ] **Step 2: 提交**

```bash
git -C G:\financial-analyst add -A src/financial_analyst/ui/
git -C G:\financial-analyst commit -m "chore(ui): drop temp compile-check script"
```

### Task 12: 主 app 加「量化工作台」入口链接

**Files:**
- Modify: `src/financial_analyst/ui/app.jsx` (TopBar `:1264`，🩺探活 pill 附近 `:1385`)
- Modify: `src/financial_analyst/ui/index.html` (bump `?v=`)

- [ ] **Step 1: 加链接**

在 `app.jsx` TopBar 的 🩺探活 `<span onClick={runDiag} ...>` 之前 (`:1385` 上方) 插入：

```javascript
      <a href="quant.html" title="量化研究工作台 · 因子评测/炼因子/合成/档案" className="mono hover-pill"
         style={{ fontSize: 11, color: 'var(--ink-2)', textDecoration: 'none', padding: '3px 8px', border: '1px solid var(--line)', cursor: 'pointer' }}>
        🔬 量化工作台
      </a>
```

(同时确认 `quant.jsx` TopBar 里「← 返回对话」的 href 指向主 app 实际入口文件名: 若主入口是 `index.html` 则把 Task 5 Step 3 写的 `href="app.html"` 改为 `href="index.html"`。)

- [ ] **Step 2: bump 主 app cache-buster**

把 `index.html` 三个 script tag 的 `?v=20260528-8` 全改为 `?v=20260529-1`。

- [ ] **Step 3: 编译校验 app.jsx (防白屏)**

Run (在 ui/): `node -e "const B=require('@babel/standalone'),fs=require('fs');B.transform(fs.readFileSync('app.jsx','utf8'),{presets:['env','react']});console.log('OK')"`
Expected: `OK`

- [ ] **Step 4: 浏览器验证主 app 链接**

`/index.html` (或主入口): TopBar 出「🔬 量化工作台」，点击跳 `/quant.html`；工作台「← 返回对话」跳回。两页都不白屏。

- [ ] **Step 5: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/ui/app.jsx src/financial_analyst/ui/index.html
git -C G:\financial-analyst commit -m "feat(ui): link to quant workbench from main app TopBar"
```

### Task 13: 全量回归 + 端到端闭环实测

- [ ] **Step 1: 后端全量 (控制端 miniconda)**

Run: `python -m pytest tests/ -q`
Expected: 无新增失败 (含 test_factor_rest.py 全绿)。

- [ ] **Step 2: 两个 jsx 编译校验**

Run (在 ui/): 分别 babel transform `quant.jsx` 与 `app.jsx` → 均 `OK`。

- [ ] **Step 3: 端到端闭环浏览器实测**

启 :9999 + :5173。在 `/quant.html` 走通整圈:
1. 炼因子: 输想法 → 炼 → 存入因子库 (成功)。
2. 因子库: 「我的」family 见新因子 → 运行评测 → 出双档报告。
3. 合成: 新因子 + 一个内置 → 合成 → 出 OOS + verdict。
4. 研究档案: 见到上面 report + compose 两条自动归档；勾 2 条出 diff。
5. 三态: 冷池/错表达式/members<2/out_of_vocab 各出对应空/错提示，无白屏、无 mock 残留。

- [ ] **Step 4: 收尾自检 (项目硬规则)**

- 确认无 `_compile_check.js` 残留、无 mock 常量残留 (grep `quant.jsx` 无 `LIBS`/`FACTORS`/`genICSeries`)。
- 按 financial-analyst 项目规则更新经验沉淀 (memories / strategy 不适用本仓，但若有 UI 模式值得记，走 reference_guanlan_ui 同源更新)。
- `git -C G:\financial-analyst status` 确认工作区干净 (除既有 `config/loaders.yaml`、`data/` 等会话前已存在的未跟踪项)。

- [ ] **Step 5: 最终提交 (如有零散改动)**

```bash
git -C G:\financial-analyst add -A src/financial_analyst/ui/
git -C G:\financial-analyst commit -m "test(ui): end-to-end quant workbench loop verified"
```

---

## Self-Review (作者已过一遍)

**Spec 覆盖:** 4 模式 (C.2 Task7 / C.3 Task8 / C.4a Task9 / C.4b Task10) ✓；2 后端口 (save Task1 / report-archive Task2 / compose-archive Task3) ✓；5 校正 (两档=Task7 FactorReportView；删信号回放/拥挤度/风格/相关性/持仓=Task5 Step1；档案一等=Task10；池名 csi300/500/800/all=Task5 Step2 POOLS) ✓；无 mock=Task5 ✓；主 app 链接=Task12 ✓；前端纪律 (cache-buster 每任务 bump、babel 校验每任务、Clash 直连=quant.html GUANLAN_BACKEND) ✓。

**类型一致:** `useAsync()→{data,loading,error,run,reset}`、`FactorReportView({report})`、`ForgeCard({result,onSave,saved,saving})`、`q/getJSON/postJSON`、`n2/pct/POOLS/POOL_DEFAULT`、`Loading/Empty/ErrorBox` 跨任务一致引用。图表签名改动 (`ICChart({series,dates})`/`EquityChart({series,dates,benchmark})`/`DecileChart({bars})`) 在 Task6 定义、Task7 `FactorReportView` 按此调用 ✓。

**已知取舍:** 设计稿若 `%TEMP%` 失效则按函数名重建 (Task4 Step1 注明)；前端无 JS 单测框架，校验=babel 编译 + 浏览器实测 (符合本仓现状)。
