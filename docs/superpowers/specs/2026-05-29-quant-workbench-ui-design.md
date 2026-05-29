# 量化工作台 UI 设计 · SP-C.2–C.4

> 状态: 待用户 review
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-C (工作台UI) 的第 2-4 块 — 把 C.1 直连端点接成真页面

## 目标

把 C.1 已就绪的 6 个直连 REST 端点接成一个**独立可用的「量化研究工作台」页面** (`/quant.html`)，覆盖 因子库浏览 + 详情评测 (C.2) / 炼因子 (C.3) / 多因子合成 + 研究档案 (C.4)，在工作台内形成 **炼 → 存 → 评 → 合成 → 归档 → 迭代** 的自包含闭环。同时校正设计稿 (`quant.jsx`) 的 5 处无数据源/错框架问题。

## 背景与定位

C (工作台UI) 拆分: C.1 直连 REST ✅ → **C.2 因子库&详情 → C.3 炼因子卡 → C.4 合成+档案 (本文)**。

### 现状基线 (已勘察)

- **6 端点已就绪** (`buddy/server.py:1062-1212`): `POST /factor/report` (ReportReq) / `POST /factor/forge` (ForgeReq, +quick_ic) / `POST /factor/compose` (ComposeReq, members<2→400) / `GET /factor/archive?target=&compare=` / `GET /factor/bench?universe=&family=&since=&until=&max_codes=` / `GET /factor/list?family=`。响应均经 `_jsonable` (NaN/Inf/numpy 标量→null)。
- **活页 UI = `src/financial_analyst/ui/`** (canonical)。`launch_cli._ui_dir()` 解析 (`$FA_UI_DIR` > `<pkg>/ui/` > `packaging/src-tauri/ui/`) + `python -m http.server` 服务 (launch_cli:540)。`index.html` 载 react/react-dom/babel-standalone + `tokens.css` + `shared.jsx`/`agent-adapter.jsx`/`app.jsx` (渲染 `<ObservatoryApp/>`)，`window.GUANLAN_BACKEND='http://127.0.0.1:9999'`，全 script tag 带 `?v=20260528-8` cache-buster。**app.jsx 无视图/页面路由** (单一 chat SPA，`ObservatoryApp` @457)。`G:\stocks\fa_ui_ready` 与 `packaging/src-tauri/ui` 是 **stale 镜像** (不动)。
- **设计稿 `%TEMP%\fa_design_5\quant.jsx`** (107KB，全 mock，5 条 review 未改) = 视觉参考，含 TopBar / ChatPanel / AlchemyCard / QuantWorkbench(FactorStrip/FactorList/FactorDetail) / 图表基元(ICChart/EquityChart/DecileChart/Kpi/Panel) / **待删** SignalChart·SignalPopover·SignalStats·SignalLegend(信号回放)·CrowdingBox(拥挤度)·ExposureBars(风格暴露)·CorrList(相关性)·PicksTable(持仓)。

### 渲染契约 (端点响应 dataclass 字段，asdict 后)

- **FactorReport** (`/factor/report`): `meta{factor,family,universe,freq,start,end,n_dates,n_codes,fwd_days}` · `ic{ic_mean,ic_std,icir,ic_tstat,ic_win_rate,rank_ic_mean,rank_icir,ic_series:[[date,v]],ic_decay:[[h,ic,rankic]]}` · `quantile{n_groups,group_ann_return:[],group_nav:[[]],monotonicity,long_short_spread}` · `portfolio{nav_series:[[date,v]],benchmark_nav?,ann_return,sharpe,max_drawdown,volatility,turnover,win_rate,calmar}` · `characteristics{coverage,autocorr_1,half_life,top_group_turnover}` · `warnings[]` · `status` · `error`。**注: 无逐股 picks 列表** (故设计稿 PicksTable 无数据源)。
- **ForgeResult** (`/factor/forge`): `{idea,expr,parsed:[],name,rationale,compile_ok,error,out_of_vocab}` + 端点附加 `quick_ic` (dict|null, 含 rank_ic/rank_ir/hit_rate/state)。
- **ComposeResult** (`/factor/compose`): `{method,members,weights:{},train_frac,n_train_dates,n_test_dates,composite:FactorReport|null,member_oos:[{name,rank_ic,sharpe}],verdict,warnings,status,error}`。
- **RunRecord** (`/factor/archive`): `{id,timestamp,kind:"report"|"compose",target,formula,universe,freq,start,end,metrics:{},note,tags:[]}`；`compare(a,b)→diff dict`，`history(target)→[RunRecord]`，无参→`{runs:[...]}`。
- **bench rows** (`/factor/bench`): `{rows:[{name,family,ic,rank_ic,ir,rank_ir,hit_rate,state}]}`。
- **list** (`/factor/list`): `{registered:[{name,family,formula}],user:[{name,expr,kpis,...}]}`。

### 已勘察的 2 处后端缺口 (底层 API 现成)

1. **炼出的因子无 REST 存库**: 只有 agent 工具 `alpha_forge` 经 `UserFactorStore().add(...)` 入库 (tools.py:1452)。`add(entry)` 会**持久化 + 自动注册** (`register_one` → compile_factor + register family="user")，并返回带唯一名/created 的 entry。
2. **REST report/compose 不写档案**: 只有 agent 工具带 `archive=true` 经 `ResearchArchive().append(record_from_report/compose(...))` (tools.py:1524/1625)。REST 端点直调 eval/compose 模块，跳过归档 → C.4b 档案屏会空。

### 已锁定决策 (本次 brainstorm，2 次确认)

- **独立 `/quant.html` 页** (非并入 app.jsx)：app.jsx 无路由结构 + 2300 行单文件白屏风险高 + 设计稿本就独立 app → 适配最少、回归风险最低。主 app 只加一个导航链接。
- **控件驱动** (非 chat/agent 驱动)：工作台直连 6 个 REST 端点 (秒级~分钟级同步)，对话/agent 留在主 app。
- **自包含闭环**：补 `POST /factor/save` + 给 REST report/compose 加 archive 记录 (默认开 + 可填 note)，使工作台不依赖聊天即可走完整闭环 (正中愿景 + 校正④)。
- **无 mock**：删尽设计稿假数据，一律真端点 + 空/加载/错误三态诚实显示。

## 范围

### 做
1. **后端 2 处补口** (复用现成函数，每处 1-3 行)：`POST /factor/save` + report/compose 的 archive 记录。
2. **前端**: 新建 `ui/quant.html` + `ui/quant.jsx` (控件驱动 4 模式，从设计稿改造)。
3. **主 app**: `app.jsx` 加一个「量化工作台」入口链接 (+ bump `index.html ?v=`)。
4. 后端 TestClient 单测 + 前端 babel 编译验证 + 浏览器实测。

### 不做
- 并入 app.jsx 的视图路由 (本次独立页)。
- 同步 `fa_ui_ready` / `packaging/src-tauri/ui` 两个 stale 镜像。
- 拥挤度 / 风格暴露 / 相关性 / 信号回放 / 逐股持仓表 (无数据源)。
- 个股选股 (`/screen`，出 C 范围)。
- 异步作业队列 / SSE (沿用同步快默认)；鉴权；图片/vision。

## 后端补口 (server.py build_app 内)

1. **`POST /factor/save`** — body `SaveReq{name, expr, description?, parsed?, kpis?}` → `UserFactorStore().add({"name":name,"family":"user","expr":expr,"description":description,"parsed":parsed,"kpis":kpis})` → 返回 entry (含唯一名 + created)。重名自动加后缀 (`_unique_name`)。存后**立即**可被 `/factor/report <name>` 评测 + 出现在 `/factor/list` user。内部异常→500。
2. **archive 记录** — `ReportReq`/`ComposeReq` 加 `archive: bool = True` + `note: str = ""`。`/factor/report` 成功且 status=="ok" 后 `try: ResearchArchive().append(record_from_report(rpt, note=req.note)) except Exception: pass` (**非致命**，归档失败不拖垮报告主体)；`/factor/compose` 同理用 `record_from_compose`。→ 工作台每次评测/合成自动进档案，喂 C.4b 迭代屏。

## 前端架构 (`/quant.html`，standalone)

- **新文件 `ui/quant.html`**: 仿 `index.html` (react/react-dom/babel-standalone + `tokens.css` + `window.GUANLAN_BACKEND` + 全 script tag `?v=` cache-buster)，载 `quant.jsx`，渲染 `<QuantApp/>`。`#root` min-width 同 index。
- **新文件 `ui/quant.jsx`**: 从设计稿 `quant.jsx` 改造 —
  - **删**: 全部 mock (`LIBS`/`FACTORS`/`genICSeries`/`genEquity`/`genDecile`/`genPicks`)；`ChatPanel`；`SignalChart`/`SignalPopover`/`SignalStats`/`SignalLegend`；`CrowdingBox`/`ExposureBars`/`CorrList`/`PicksTable`。
  - **保留/复用**: `AlchemyCard`(+`AlchemyParamEditor`/`AlchemySpark`)、`ICChart`/`EquityChart`/`DecileChart`、`Kpi`/`Panel`/`Pill`/`Segmented`/`ChartTip`/`MiniSparkline`，配色走 `tokens.css`。
  - **新增**: `QuantApp` (TopBar 4 模式状态机 + 顶层数据层) + REST helper (`q(path, opts)` = `fetch(window.GUANLAN_BACKEND + path)`，trust 直连，三态)。
- **主 app 改动 (最小)**: `app.jsx` TopBar 加「量化工作台」链接 (`window.open('quant.html')` 或同窗导航)；bump `index.html ?v=`。

## 模式 → 端点 → 渲染 (4 模式)

| 模式 (TopBar) | 块 | 端点 | 渲染 |
|---|---|---|---|
| **因子库 & 详情** | C.2 | `GET /factor/list` ⊕ `GET /factor/bench`；`POST /factor/report` | 左导航 + 右详情 (下) |
| **炼因子** | C.3 | `POST /factor/forge`；`POST /factor/save` | AlchemyCard + 存库 |
| **多因子合成** | C.4a | `POST /factor/compose` | 权重 + 复用 FactorReportView + 成员对比 + verdict |
| **研究档案** | C.4b | `GET /factor/archive` | runs / history / compare diff |

- **C.2 左·因子库导航**: `/factor/list` (registered+user 按 family 分组) ⊕ `/factor/bench` (按 name join 出 rank_ic/rank_ir/state 横条；bench 是批量作业 → 带 loading)。支持选已注册名 / user 因子 / 直接输白名单表达式。
- **C.2 右·详情** (`/factor/report`，带 universe/freq/start/end + archive 默认 true，经显式「运行评测」按钮触发 + loading)：
  - **IC 体检子块** (语义档1): `ic.{ic_mean,icir,ic_tstat,ic_win_rate,rank_ic_mean,rank_icir}` + `ICChart(ic_series)` + IC 衰减 (`ic_decay`)。
  - **组合回测子块** (语义档2，视觉分开): `quantile.group_ann_return→DecileChart` (+monotonicity/long_short_spread)；`portfolio.nav_series→EquityChart` (+benchmark_nav)；`portfolio.{ann_return,sharpe,max_drawdown,volatility,turnover,win_rate,calmar}→Kpi`；`characteristics.{coverage,autocorr_1,half_life,top_group_turnover}`。
  - `warnings` 显示；`status≠ok`→空/错态。
- **C.3 炼因子**: 想法 textarea → `/factor/forge{idea,universe,quick_eval:true}` → **AlchemyCard**: `rationale`(推理链) + `expr` + `parsed`(token) + `compile_ok`/`error`/`out_of_vocab` 态 + `quick_ic`(RankIC/RankICIR/state)。`[存入因子库]` → `/factor/save{name:fr.name,expr:fr.expr,description:fr.rationale,parsed:fr.parsed,kpis:quick_ic}` → 成功提示 + 刷新左库列表。
- **C.4a 合成**: 从库/表达式选 **≥2** 成员 + 方法 `Segmented(equal/ic_weighted/linear/lgbm)` + universe/freq/train_frac → `/factor/compose` → `weights`(每成员条) + `composite`(**复用 `<FactorReportView>`** 渲染 OOS 报告) + `member_oos` 表(name/rank_ic/sharpe) + `verdict` + `n_train_dates`/`n_test_dates`。members<2 → 400 友好提示。
- **C.4b 研究档案**: 无参→`{runs}` 列表 (id/timestamp/kind/target/关键 metrics/note/tags)；`?target=`→history 趋势；`?compare=a,b`→metrics diff。即"评测→改进→再评测"闭环屏。

**复用**: FactorReport 渲染抽成 `<FactorReportView report={...}/>`，C.2 详情 + C.4a composite 共用。

## 5 条校正落地

① **两档分离**: 库横条 = 批量 IC 扫描 (`/bench`，带 loading)；详情把 *IC 体检子块* 与 *组合回测子块* 视觉分开各标档位；报告经显式「运行评测」+ loading (小池秒级、大池分钟级，不假装即时)。
② **删信号回放** (买卖箭头) — 截面因子无单股买卖序列；截面归因改用 DecileChart + IC 衰减。
③ **删拥挤度 / 风格暴露 / 相关性** — 无数据源 (PicksTable 同样无源，一并删)。
④ **研究档案升一等模式** (`/factor/archive`)。
⑤ **池名 csi300 / csi500 / csi800 / all** (对齐后端 `resolve_universe_codes`)；交互默认 `csi300_active`。

## 数据 / 三态 / 无 mock

- **加载**: 每个 fetch 转圈/骨架；bench/report/compose 是慢档必显式 loading。
- **空**: list/archive/bench 空 → 「暂无 …」诚实文案，非空白。
- **错**: 业务 `status≠ok` (empty_universe/load_error/compute_error/fit_error/too_few_factors) 或 forge `compile_ok=false`/`out_of_vocab` → 渲染可读提示；HTTP 500 `{error}` → 错误条。
- **NaN/Inf**: 后端 `_jsonable` 已转 null，前端遇 null 显「—」。
- 删尽设计稿 mock，不留任何假数据源。

## 前端纪律 (硬性，见 reference_guanlan_ui / feedback_browser_jsx_cache)

- 改任何 `.jsx` → bump `quant.html ?v=` (新页) / `index.html ?v=` (主 app 链接改动时) 全 script tag。
- babel 浏览器内编译: 交付前 **node `@babel/standalone` 实编译** `quant.jsx` + `app.jsx` 验证无语法错 (防整页白屏)；中文串用「」不用 ASCII 双引号。
- 直连 `:9999` (GUANLAN_BACKEND)，localhost 探测 `trust_env=False` 绕 Clash 502。
- 关键控件 `opacity:1` 不靠 hover。
- 本会话 Playwright/Chrome MCP 实测每个模式 (`#root` 非白屏 + 各 fetch 真数据/三态)。

## 测试

**后端** (pytest TestClient，扩 `tests/test_factor_rest.py`，tmp `$FINANCIAL_ANALYST_HOME` 隔离 store/archive):
1. `POST /factor/save` (tmp store) → 200 + entry；重名加后缀；存后 `/factor/list` user 含之 + `/factor/report <name>` 可评。
2. `/factor/report` archive=true (tmp home) → 跑后 archive list 多一条 report；archive=false → 不写。
3. `/factor/compose` archive=true → 多一条 compose。
4. 归档失败非致命: monkeypatch `append` 抛 → 报告主体仍 200。
5. 不污染注册表 (用 `unregister`，**不用** `_clear_registry_for_tests`)；自检用稳定 Bash (miniconda pandas 2.3.3) 复跑关键套件，不轻信 subagent "passed"。

**前端**:
6. node `@babel/standalone` 编译 `quant.jsx` / `app.jsx` → 无错。
7. 浏览器 (Playwright/Chrome): 启 backend `:9999` + `http.server` 服 `ui/`，逐模式实测: 库列表/bench 横条出真数据；详情跑真 report 出图；forge 出 expr+quick_ic 且存库刷新；compose ≥2 出 OOS+对比+verdict；档案出 runs/趋势/diff。空/错态各验一次 (冷池 / members<2 / out_of_vocab)。

## 验收标准 (DoD)

- `/quant.html` 独立可跑，4 模式全部走真端点，无 mock 残留。
- 闭环走通: forge → save (入库即用) → report → compose → archive → compare，全在工作台内。
- 5 条校正全部落地 (无信号回放/拥挤度/风格/相关性/持仓表)。
- 后端 2 处补口 + 单测全绿；C.1 现有测试 + 全量 server/buddy 测试不回归；不污染注册表；无新依赖。
- `quant.jsx` / `app.jsx` node-babel 编译通过 (无白屏)；浏览器实测 4 模式 + 三态；cache-buster 已 bump。
- 主 app 加「量化工作台」入口链接。
