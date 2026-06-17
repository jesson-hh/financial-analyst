# 研报系统接口

对话模块「深度研报」的完整接口契约 —— 基于引擎 `engine/financial_analyst/buddy/`(`tools.py` 工具定义 + `server.py` 端点)的真实实现整理(2026-06-04)。

## 0. 研报内容范围(2026-06-04 改版)

研报走 **技术面/资金面 + 市场环境** 视角,**剥离因子/量化**:
- **个股 swarm(`config/swarm/stock-deep-dive.yaml`)**:删 `factor-computer`/`quant-analyst`/`model-predictor`;加 `market-scanner`(大盘)/`mainline-classifier`(主线)/`morning-brief-writer`(早盘),喂 technical-analyst + report-writer。
- **ETF swarm(`etf-deep-dive.yaml`)**:本就无因子;加同样 3 个市场 agent。
- **report-writer / etf-report-writer 模板**(在 `memories/<agent>/report_template.md`):删「量化模型」维度 + 「量化共识」节;加「市场环境(大盘·主线·早盘)」节;技术面提核心(均线/量价/**筹码集中/股东户数/主力成本**)。report-writer 输出层指令把上游量化措辞改述为技术/资金/估值语言,正文不出现「因子/IC/模型面」。
- **验证(2026-06-04 真跑)**:ETF 100% 无因子 + 含市场环境;个股含市场环境/技术面/筹码、无量化共识节(`fundamental-analyst` 底层仍是因子-IC 评分,故其市值分层规则解释偶有 1 行残留,由输出指令兜底,未硬拆其 349 行回测 playbook)。
- 「筹码集中」等技术/资金面分析**不需要量化(因子)层** —— 用价量 + 股东户数(F10)直接算。

## 1. 研报类型(生成工具)

每种研报是 agent 经 `/run` 调用的一个**工具**;完成后工具的 `side_effect.md_path` 由 server 转成 SSE `report {path}` 事件,正文 `.md` 落到 `out/`。

| 研报类型 | 工具 `name` | 参数 | 耗时 `cost_hint` | 需确认 | 输出文件 |
|----------|-------------|------|------------------|--------|----------|
| 个股深度研报 | `run_report` | `code`(必填,Qlib 格式 `SH600519`/`SZ002594`/`BJ430489`)· `asof`(可选 `YYYY-MM-DD`,默认今日) | `minutes`(5–8 分钟) | ✅ `confirm_required` | `out/{CODE}_{date}.md` |
| ETF 深度研报 | `run_etf_report` | `code`(必填,ETF 5/15 开头 `510300`/`SH510300`/`159915`)· `asof`(可选) | `minutes` | ✅ | `out/{CODE}_{date}.md` |
| 盘前晨报 | `morning_brief` | 无 | `seconds` | ✗ | `out/morning_brief_{date}.md` |
| 主线雷达 | `mainline_radar` | 无 | `seconds` | ✗ | `out/mainline_{date}.md` |
| 海外雷达 | `overseas_radar` | 无 | `seconds` | ✗ | `out/overseas_*.md` |

- 个股/ETF 研报是多智能体流水线(多空辩论 + 风控官否决),输出评级/目标价/止损/仓位;生成期间写 `out/{CODE}_progress.json` 供进度轮询。
- `run_report` / `run_etf_report` 标了 `confirm_required` → 前端弹 y/n/a 确认(`mode` 决定是否真拦)。

## 2. 存储位置

- 研报目录 = `_project_root() / "out"`。`_project_root()` 解析顺序:`$FINANCIAL_ANALYST_HOME` → 含 `pyproject.toml`/`memories/` 的仓库根 → cwd。
- **自包含构建下** `_project_root()` = `G:/guanlan-v2`(引擎在 `engine/financial_analyst`,仓库根有 `pyproject.toml`)→ **研报落 `G:/guanlan-v2/out/`**。
- 每份深度研报伴随:`{CODE}_progress.json`(多智能体进度)、可能的 `.html` 渲染版。
- 38 篇历史研报已于 2026-06-04 从 `G:/financial-analyst/out` 迁入 `G:/guanlan-v2/out`(含 .json/.html 共 145 文件)。
- `out/` 在 `.gitignore`(研报是生成物,不入库)。如需让研报落回旧位置,设 `FINANCIAL_ANALYST_HOME=G:/financial-analyst`。

## 3. 访问接口(HTTP / SSE)

| 接口 | 方法 | 入参 | 返回 | 用途 |
|------|------|------|------|------|
| `/run` | POST · SSE | `{query, mode, model, session_id, context}` | SSE `report` 事件 `{path}` | 跑研报工具,完成时推 `.md` 绝对路径 |
| `/report` | GET | `?path=<out/ 下的 .md 绝对路径>` | `{ok, text}` / 404 | 抓研报全文(**白名单**:仅限 `_project_root()/out` 下的 `.md`) |
| `/report-progress` | GET | `?code=CODE` | 未开始 `{ok:false, error:"not_started", agents:{}, total:0, done:0, running:0, pending:0}`;进行中 `{ok:true, agents:{…}, total, done, running, pending}` | 轮询多智能体生成进度 |

> SSE `report` 事件由 server 在工具 `tool_result.side_effect.md_path` 存在时发出(见 `server.py` `/run` 的 `forward()`)。

## 4. 前端流(对话模块)

- `agent-adapter.jsx`:SSE `report` → `onReport({path})`。
- `app.jsx` `startAgent.onReport`:`fetch('/report?path=…')` 抓全文 → 填 `ReportDrawer` + `save_report` 存成 transcript 卡片(关抽屉可重开)。`run_report` 工具的 `tool_done` 也兜底带全文。
- 生成中:`DeepReportProgress` 组件轮询 `/report-progress?code=`。
- 标题解析:研报首行 `# 名称 (CODE) — …` 提取标的;解析不到用文件名里的 code。

## 5. 数据来源

研报内容由引擎多智能体读真数据生成:行情(腾讯实时)、EOD/因子/估值(`get_data_paths` → `G:/stocks/stock_data`)、新闻(`news_db` → `G:/stocks/news_data`)、LLM 综述(deepseek)。**研报本身不持有数据,只引用。**
