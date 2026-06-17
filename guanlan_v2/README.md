# guanlan_v2 — 后端壳

把 `financial_analyst` 引擎和 V2 多模块前端缝在一起的 FastAPI。不实现业务逻辑,不持有数据本体。

> **2026-06-04:引擎已 fork 进本仓库 `engine/`,guanlan-v2 自此自包含。** 后端不再 import 外部 `G:/fa-watch-wt/src`,而是 import 仓库内 `engine/financial_analyst`;配置用仓库内 `config/`;**只有数据仍外部**(经 `get_data_paths` → `G:/stocks`)。详见下「引擎来源」。

## server.py 三段

### 1. `_ensure_engine_importable() -> str`
让 `financial_analyst` 可 import,且 **`GUANLAN_FA_SRC` 权威**:
- 把 `GUANLAN_FA_SRC`(默认 `G:/fa-watch-wt/src`)**prepend 到 `sys.path`,在 `import financial_analyst` 之前**。
- 为什么:本机有一个 editable 安装的旧分支 `financial_analyst`(主分支),缺 recipe 层、`/watch/market_status`、`/watch/signal_pack`。不 prepend 就会拿到旧引擎。prepend 后,worktree 引擎(merge-stocks)胜出。
- 返回 `financial_analyst.__file__` 供启动日志核对;若解析到的不在 `fa_src` 下,发 warning。

### 2. `create_app() -> FastAPI`
- `from financial_analyst.buddy.server import build_app; app = build_app()` —— 引擎全部真实端点(/run、/factor/*、/watch/*、/concepts、/upload、/quotes…),真数据。
- `app.mount("/ui", StaticFiles(directory=ui, html=True))` —— 服务前端;`html=True` 让 `/ui/` 命中 `index.html`(→ 重定向到研究图谱首页)。**StaticFiles 递归服务子目录**,所以 `/ui/graph/...`、`/ui/_shared/...` 自动可达。
- `@app.get("/")` 重定向到 `/ui/`(best-effort:若引擎已注册 `/`,那个先匹配,无害)。
- 工厂函数(非仅模块级),方便测试用 TestClient 构造全新 app。

### 3. `main(host, port)`
`uvicorn.run(app, ...)`,host/port 可经 `GUANLAN_HOST`/`GUANLAN_PORT` 覆盖。`python server.py` 直接跑即调它。

## 引擎来源

**仓库内 `engine/financial_analyst/`** —— 2026-06-04 从 `G:/fa-watch-wt/src`(`financial-analyst` 的 merge-stocks worktree)**fork 进来**(246 .py + `_resources/`)。`_FA_SRC_DEFAULT = <repo>/engine`,启动日志应打 `engine source: …/guanlan-v2/engine/financial_analyst/__init__.py`。它有:recipe 层(个股速览等确定性配方)、`/watch/*` 盯盘、`/watch/market_status`、`/watch/signal_pack`、EOD 桥。

> **与上游脱钩**:fa-watch-wt 的后续修复不会自动流入,需手动 backport。设 `GUANLAN_FA_SRC=G:/fa-watch-wt/src` 可指回上游 A/B(在它被删之前)。

## 不在这里做的事
- 不加业务端点(加到引擎 `engine/financial_analyst/buddy/server.py`)。
- **不拷数据本体**:`stock_data`/`news_data`(几十 GB)留在 `G:/stocks`,经引擎 `get_data_paths` 只读引用(`config/loaders.yaml` 已指向)。
- 不把真 `.env`(密钥)入库:引擎从 `os.environ` 读 `DEEPSEEK_API_KEY` 等(见根 `.env.example`)。
