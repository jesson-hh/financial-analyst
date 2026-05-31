# ETF 深度分析 — 子项目 A:数据层 设计 (2026-05-30)

**Goal:** 给 financial-analyst 加一套 ETF 数据层(对照 A 股个股数据基建),让 `ETFLoader` 能读出单只 ETF 的全维度数据(价格 / NAV / 折溢价 / 持仓构成 / 申赎-份额流 / 费率-基准 / 跟踪误差),为后续子项目 B(ETF 深度分析报告)供数。

**Architecture:** 专用 ETF 命名空间(A1)。ETF 价格走 pytdx(免 token)→ 专属 qlib bin `cn_data_etf`;基金指标走 Tushare `fund_*`(有效 token)→ `etf_*.parquet`;实时折溢价走 akshare `fund_etf_spot_em`(免 token)→ `etf_spot.parquet`。全部复用 fa 现有传输层(`TushareLoader._query` / `pytdx_kline` / `bin_writer.safe_merge_write`),数据写进共享 `stock_data/` 树。loader/updater/CLI 落在 **financial-analyst**(与现有 stock 数据基建同位)。

**Tech stack:** Python;fa `data/loaders/`(BaseLoader 模式)+ `data/updaters/`(直连增量模式)+ `data_cli.py`(typer)+ `config/universes/`;Tushare HTTP `fund_*`、pytdx、akshare;qlib bin + parquet。

---

## 已锁定决策(用户 2026-05-30 确认)

1. **全量对照,分阶段**:先做子项目 A(数据层),再做 B(分析层)。本 spec 只覆盖 A。
2. **ETF 池 ~100-300 只**:主流流动(宽基 + 主要行业/主题),非全 2624。
3. **数据源**:pytdx 价格 + Tushare `fund_*`(有效 token)+ akshare 折溢价。
4. **A1 专用命名空间**:`cn_data_etf` + `etf_*.parquet` + `ETFLoader` + `etf.txt` universe + `fa data update-etf`。
5. **单进程写入**:所有 bin 写入单进程 `safe_merge_write`(吸取本会话并行损坏日历的事故教训,绝不并发写日历)。

---

## 终态落盘布局

```
G:/stocks/stock_data/
├── cn_data_etf/                      # ETF 价格 qlib bin (新)
│   ├── calendars/day.txt             # 复制 cn_data 的交易日历 (ETF 同交易日)
│   ├── instruments/all.txt           # = ETF 池 (SH/SZ 前缀码 + 起止日期)
│   └── features/{code}/              # 如 sh510300/
│       └── {open,high,low,close,volume,amount}.day.bin
└── parquet/
    ├── etf_basic.parquet             # 主表 (费率/基准/类型/解析后的跟踪指数码)
    ├── etf_nav.parquet               # NAV 历史 (unit/accum/adj/net_asset)
    ├── etf_share.parquet             # 日份额 (→ 申赎流 + AUM)
    ├── etf_holdings.parquet          # 季度持仓 (top holdings)
    ├── etf_div.parquet               # 分红
    ├── etf_index.parquet             # 跟踪指数日线 (算跟踪误差用)
    └── etf_spot.parquet              # akshare 实时快照 (每次覆盖: IOPV/折价率/份额/AUM)

G:/financial-analyst/
├── config/universes/etf.txt          # ETF 池 (新)
└── src/financial_analyst/data/
    ├── loaders/etf.py                # ETFLoader (新)
    └── updaters/etf_price.py / etf_fund.py / etf_spot.py   # 三条管线 (新)
```

`get_data_paths()` 增加 `etf_qlib_uri` 解析(默认 `<data_root>/cn_data_etf`)。`config/loaders.yaml` 可加 `provider_uri.etf` 显式 override(缺省走默认)。

---

## 组件设计

### ① ETF 池 `config/universes/etf.txt`

- 构建脚本 `build_etf_universe`(fa 内,可重跑):
  1. akshare `fund_etf_spot_em()`(~1486 只,带 AUM/换手)→ 按 AUM 降序 + 换手过滤,取 top ~200。
  2. ∪ 关键保底清单(宽基 510300/510500/510050/512100/588000/588080/159915/159949/159919 + 主要行业/主题:证券512880/医药/半导体/军工/新能源/红利/黄金 等)。
  3. 去重 → 写 SH/SZ 前缀码,一行一个。
- `data/universe.py` 的 `resolve_universe_codes` 增加 `etf` label(读 `universes/etf.txt`)。
- 每只解析跟踪指数:`fund_basic.benchmark`(自由文本)模糊匹配 `index_basic` 的 `ts_code` → 存进 `etf_basic.index_code`(匹配不上留空,跟踪误差降级跳过)。

### ② 三条数据管线 + parquet schema

**价格管线** `updaters/etf_price.py`(pytdx,免 token):
- 复用 `pytdx_kline` 拉取逻辑;新增 `update_etf_daily_batch(etf_provider_uri, codes)`,**单进程**,`safe_merge_write` 写 `cn_data_etf`。
- 日历:复用 `cn_data/calendars/day.txt`(ETF 同交易日);ETF 池写 `cn_data_etf/instruments/all.txt`。
- ⚠ pytdx `qlib_code_to_pytdx` 对裸 5xx/15x 报错 → 码必须 SH/SZ 前缀(universe 已前缀)。

**基金指标管线** `updaters/etf_fund.py`(Tushare `fund_*`,有效 token + `Accept-Encoding: identity`):

| parquet | Tushare API | 关键列 |
|---|---|---|
| etf_basic | `fund_basic(market=E)` 筛池 | ts_code, name, management, m_fee, c_fee, benchmark, **index_code**(解析), fund_type, invest_type, list_date |
| etf_nav | `fund_nav` | ts_code, nav_date, unit_nav, accum_nav, adj_nav, net_asset |
| etf_share | `fund_share` | ts_code, trade_date, fd_share |
| etf_holdings | `fund_portfolio` | ts_code, end_date(季), symbol, mkv, stk_mkv_ratio |
| etf_div | `fund_div` | ts_code, ex_date, div_cash |
| etf_index | `index_daily`(对 etf_basic.index_code 去重) | index_code, trade_date, close, pct_chg |

> **单位**:NAV 元;net_asset/fd_share 等以 Tushare 文档为准(实现时核对 + 在 loader 注释标注;参考 data_contract.md 的单位约定)。

### ③ 实时折溢价管线 `updaters/etf_spot.py`(akshare `fund_etf_spot_em`,免 token):
- etf_spot.parquet(每次覆盖):ts_code, asof, iopv, premium_discount_pct(折价率), shares(最新份额), aum(总市值), turnover。

### ④ `ETFLoader`(`data/loaders/etf.py`)— B 层消费接口

```
fetch_etf_quote(code, start, end, freq='day')      # cn_data_etf bin → OHLCV DataFrame
fetch_etf_nav(code, start, end)                     # etf_nav.parquet
fetch_etf_premium_discount(code)                    # 历史: price/unit_nav-1; 实时: etf_spot 折价率
fetch_etf_holdings(code)                            # etf_holdings 最新季; 指数ETF 用 index_constituents 补 top10
fetch_etf_flow(code, lookback)                      # Δfd_share→申赎净额, AUM=fd_share×unit_nav 趋势
fetch_etf_meta(code)                                # etf_basic: 费率/基准/index_code/类型
fetch_tracking_error(code, window)                  # NAV 日收益 vs etf_index 日收益的 std (年化)
```
- 复用 `qlib_binary` 的 bin 读取(指向 `cn_data_etf`);parquet 走 `get_data_paths().parquet_root`。
- 码格式转换复用现有 `_to_tushare_code`(510300.SH)/ `_qlib_to_tdx` / `_code_to_dir`(sh510300)。

### ⑤ CLI `fa data update-etf`

- `data_cli.py` 新 typer 子命令(镜像 `update`):`--codes`(默认读 etf.txt)、`--skip-fund`、`--skip-spot`、`--n-daily`。
- 流程(**单进程**):价格(pytdx)→ 基金指标(Tushare fund_*)→ 实时快照(akshare)→ `last_update.mark_updated('etf')`。

### ⑥ token / 传输

- Tushare `fund_*` 用**有效 token**:当前 `.env` 的 `TUSHARE_TOKEN` 失效(40101)、有效 token 硬编码在 `scripts/incremental_update_tushare.py`。**修法**:把有效 token 配进 `.env`/`FA_TUSHARE_TOKEN`,ETF updater 从 env 读,**不再硬编码**(顺带治理这个味道)。
- `fund_*` 大响应加 `Accept-Encoding: identity`(绕 brotli `ContentDecodingError`)。
- 价格 pytdx 免 token;实时 akshare 免 token。

### ⑦ 测试

- fa 有 pytest:单测 mock Tushare/akshare/pytdx,验 ETFLoader 各方法 + parquet schema + 码格式转换(SH510300↔510300.SH↔sh510300)。
- 冒烟:`fa data update-etf --codes SH510300` 实拉,断言 bin 可读 + etf_basic/nav/spot 有该码行 + premium_discount 数值合理。

---

## 非目标 (Non-goals)

- **子项目 B(分析层)**:tier-1/2 ETF agent、`etf-deep-dive.yaml`、ETF memory、`fa etf-report` —— A 完工后单独 spec。
- 实时/盘中 PCF 申赎篮子、in-kind 篮子文件(Tushare/akshare 免费层没有)。
- 主动型 ETF 的逐日全持仓(只有季度 `fund_portfolio`;指数 ETF 用 index_constituents 补 top10 近似)。
- 官方跟踪误差披露(自行从 NAV vs index 计算)。
- 不动 stock 的 cn_data / 现有 stock 数据(ETF 完全独立命名空间)。

## 风险

| 风险 | 缓解 |
|---|---|
| Tushare token 失效/限流 | 用有效 token 配 env;fund_* 加 identity 头;失败重试 |
| benchmark→index_code 模糊匹配不准 | 匹配不上则跳过跟踪误差(降级),不阻塞;可人工维护映射表 |
| 季度持仓滞后 | 指数 ETF 用 index_constituents 补;主动 ETF 标注"持仓截至 {季}" |
| pytdx ETF 码格式 | universe 强制 SH/SZ 前缀;转换函数单测覆盖 |
| 写入并发损坏(本会话事故) | **单进程** safe_merge_write;ETF 日历复制自 cn_data 只读不并发 append |
| ETF net_asset/share 单位 | 实现时核对 Tushare 文档,loader 注释标注,跟 data_contract 对齐 |
