# guanlan-v2 数据契约 (Phase 0)

guanlan_v2 不持有数据，全部**只读引用** stock 数据面，经单入口解析：
`from financial_analyst.data.paths import get_data_paths`

## 脐带 (唯一依赖)
- 行情 qlib bin：`get_data_paths().qlib_day` / `.qlib_5min` (= stock_data/cn_data, cn_data_5min)
- parquet：`.parquet_root` (= stock_data/parquet)
- 新闻+F10：`.news_data_root` (= G:/stocks/news_data，含 tdx_f10/{code}/)
- pit_store：`.pit_store_root`  ·  ETF：`.qlib_etf` (= stock_data/cn_data_etf)
- 优先级：环境变量 FA_* > loaders.yaml > ~/.financial-analyst/data > G:/stocks dev fallback

## 实时 vs EOD 数据来源 (ultracode 验证澄清, 2026-06-04)
脐带(get_data_paths → stock_data)覆盖的是 **EOD / 因子 / 财务 / 新闻 / 5min bin**。
**盘中实时行情**走的是**引擎内的腾讯实时源** (`TencentQuoteCollector` → qt.gtimg.cn),**不是 stock_data**:
- `/quotes`、`realtime_quote` 工具 → 腾讯实时 (盘中墙需要实时, 这是有意设计)。
- 代价: 外部网络依赖 — 腾讯不可达时 `/quotes` 返回 502 (诚实失败, 非吞错)。
- Phase 1 若要更稳: 可加 stock_data 收盘价兜底, 并在 `/diag` 暴露该外部依赖。
- 5min/日线/因子等 **不走腾讯**, 仍是 stock_data bin (经脐带)。

## 因子库 (factorlib · P1, 2026-06-04)
`guanlan_v2/factorlib/`(仓内, cards/seats 在仓后端先例)落基础/自挖因子。**不持有数据**:
- 因子**求值借引擎 primitive**(import `financial_analyst.factors` 的 expr / compile_factor / AlphaSpec),数据**全经 `get_data_paths`**(本机 qlib bin),零硬编码、零复制 stock_data。
- 因子**注册进引擎运行期 zoo registry**(import 注入, 不改 engine/ 源码)→ 既有 `/factor/list`(引擎 buddy server 提供) 自动含, `/factor/report` 可评。
- 基础因子从 `G:/stocks/results/factor_mining/*.txt` **译写迁移**(Qlib-DSL → 引擎 zoo-DSL, 不兼容故须译, 非复制);只读 stocks, 不改 stocks。
- 详见 [factor_library.md](factor_library.md)(位置/结构/迁移台账/口径/加因子配方)。

## 铁律
- 零硬编码数据路径 (全走 get_data_paths)。
- 不复制 stock_data (几十 GB)，只引用。
- 不 push 含私有 KB 的内容到任何公开 origin。

## 已知 config 漂移 (在 G:/stocks/config.py，研究端；此处仅登记，guanlan_v2 不依赖它)
- FINANCIAL_DIR=stock_data/financial 不存在 (真财务在 parquet/financial + tdx_finance)
- MIN5_DATA_DIR=stock_data/csi300_5min 不存在
- FACTORS_DIR=G:/stocks/factors 不存在 (真因子数据在 strategy/factors)
guanlan_v2 经 get_data_paths 访问，不碰这些常量，故不受影响。

## 原始采集源 (留 stock，guanlan_v2 不直接读)
- G:/stocks/股票历史数据（每周更新） (72.65GB raw 分钟/日K)，G:/stocks/ETF（每周更新）(5.08GB)
  — import 脚本的源，重建 bin 用。
