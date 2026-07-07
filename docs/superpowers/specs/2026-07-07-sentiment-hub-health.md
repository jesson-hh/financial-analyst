# 情绪中台②③:统一情绪 store + 数据健康总闸设计

- 日期:2026-07-07
- 缘起:数据端体检(2026-07-06-data-seams-audit.md)T3/T4/T5;中台①(datafeed/live_client)已交付,用户「做 2 和 3」。
- 红线:准确不幻觉;诚实降级;**展示型绝不回写 v4/picks/blend/seats 信号**(rescore/rerank 既有红线不变);PIT 回测链零改动;新文件全 append-only 月轮转防无界膨胀。

## ② 统一情绪 store(收编 T3/T4:LLM 判读 4 点零共享 + 情绪文件三家三 schema)

### 数据文件(`var/sentiment/`,月轮转)
- `judgments-YYYYMM.jsonl`:每行一条个股当日情绪判读。`{ts, date, code, tag, read, score, as_of, source}`(tag∈利好/中性/利空;score∈+1/0/−1;source∈rescore/news_search;**同 (date,code) 多行取最后一条**;判过无新闻=写 tag=null 行,读回 None 区别于"从未判")。
- `market-YYYYMM.jsonl`:每行一次大盘消息面判读。`{ts, date, market_read, market_tilt, as_of, source}`(latest wins)。

### 读写模块 `guanlan_v2/datafeed/sentiment.py`
- `read_judgments(codes, day=None) -> {code: {tag,read,score}|None}`(只返当日出现过的 code;None=判过无新闻)
- `read_judgment(code, day=None) -> {...}|None`
- `write_judgments(day, rows:{code:{tag,read,score}|None}, *, as_of, source)`
- `latest_market(day=None) -> {market_read, market_tilt, as_of, ts}`(无→全 None)
- `write_market(day, market_read, market_tilt, as_of, source)`
- `read_summary(code=None, day=None) -> {date, market:{...}, judgment:{...}|None}`

### 三消费方 rewire(各守其职,共享同一 store)
1. **rescore**(`screen/rescore.py`,读+写,成本敏感):`_load_news_cache`→`read_judgments(pool,day)`;`_append_news_cache`→`write_judgments`+`write_market`;**修 market 全命中空转缺陷(评审真机坐实 rescore.py:163-173)**:`news_scores` 末尾若 `stats.market_read is None`(全缓存命中/LLM 未跑)→ 读 `latest_market(day)` 回填,rerank 的大盘上下文不再空转。
2. **news_search**(`console/tools.py::news_search_impl`,写透,保持现拉新鲜):LLM 成功后把本票判读 + 大盘 read/tilt **写进 store**(不读,不改现拉/展示头条行为)—— 让 news_search 的判读被 rescore/ww_sentiment 复用,消除同日同票口径分裂。
3. **rerank**(`screen/rerank.py`):零改动,经 rescore 修好的 market 自动受益。

### 新工具 `ww_sentiment`(只读,零 LLM)
`sentiment_impl(code="", date="")` 读共享 store:返当日本票判读(tag/read/score)+ 大盘 read/tilt,不触发 LLM。与 `ww_news_search`(每调必真 LLM 拉头条)的能力差=「查我们今天已判的口径」vs「现在去重判」。缺则诚实「今日未判读,可用 ww_news_search 现判」。

## ③ 数据健康总闸(收编 T5:新鲜度治理缺失)

### 聚合模块 `guanlan_v2/datafeed/health.py`
`collect_data_health() -> {ok, generated_at, overall:{status, stale, missing}, items:{...}}`,逐项防御(读失败→status:missing+note,绝不崩):
| item | 源 | 关键字段 |
|---|---|---|
| v4_ranking | strategy.ranking_date/load_v4_ranking | date, rows, stale_days |
| regen_scheduler | screen.api._REGEN_SCHED | enabled, last_auto_ts |
| dl_ensemble | `strategy/vendor/artifacts/v4_dl_provenance.json` | date, active, sources[{model_id,active,stale_days,lookahead}], any_stale |
| stock_basic | `get_data_paths().parquet_root/tushare_stock_basic.parquet` mtime | age_days |
| tencent_live_cache | `<parquet_root>/../live/tencent/manifest_latest.json` | run_at, age_hours |
| pit_store | `get_data_paths().pit_store_root/_meta.json` | cal_end, news_date_max, age_days |
- 每 item 带 `status∈fresh|stale|missing`(自然日阈值,以 health.py 常量为准:v4>3·stock_basic>5·DL 任一活跃源超4或全断供**或 provenance date 自身超4**·tencent>24h·pit>3);`overall.status` 只取**数据项**最差(运维项 `_OPS_ITEMS={regen_scheduler}` 不参与,免 opt-in 未开把 overall 拉成 unknown),`stale`/`missing` 列问题项。
- **评审修**:DL status 除 per-source 冻结 stale_days 外,必用 `_age_days(prov.date)` 兜底(regen 停摆时 per-source stale_days 冻结在 0 会误报 fresh,真机坐实);`ww_sentiment` date 入参归一(容 dashless 20260707)。

### 路由 + 工具
- `GET /data/health`(薄壳,`guanlan_v2/health/api.py` 或并入现有;asyncio.to_thread)。
- `ww_data_health` 工具(只读):格式化 content = overall + 各项 status 一行。

## 四点同步
新增 2 工具(ww_sentiment + ww_data_health)→ 计数 **51/76/55 → 53/78/57**:specs(本文)+ CONSOLE_ALLOWED(WW_TOOL_TABLE 注册)+ `_SYSTEM_PROMPT` roster + 守护(test_console_tools 613/619/620/1084/1086 + test_guanlan_mcp 13/71/100)。glmcp README 计数句同步。

## 测试
- sentiment.py:读写往返/None 区别于缺席/月轮转/latest wins/market latest;rescore market 全命中回填修(桩 read_judgments 全命中 + latest_market 有值 → stats.market_read 非 None);news_search 写透(桩 LLM 成功 → store 落判读);ww_sentiment 命中/缺席。
- health.py:各 item 齐全/缺文件降级/阈值 status/overall 取最差;ww_data_health content。
- 全量 pytest 绿;真机:rescore 与 ww_news_search 写同一 store、ww_sentiment 读回;ww_data_health 返真新鲜度(regen enabled/DL stale/pit cal_end)。

## 非目标
不迁移 macro pulse 的 snapshots.jsonl(Δ24h 依赖其历史,风险高;情绪统一由 market store 承接);中台①的其余快赢(read_v4_ranking 单一化/news_marks 路径进配置/mainline 收敛)另批;T7 双写另议。
