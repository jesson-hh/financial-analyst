# 观澜数据端衔接大体检(2026-07-06)

四路并行测绘(stocks 新两层契约 / 观澜新闻文本链 / 情绪打分链 / 行情沉淀链),36 条发现,全部带 file:line 证据。完整测绘 JSON 存于会话产物;本文是综合结论与整合方案。

## 一、stocks 侧新两层(事实基准)

- **实时接口层**:`src/data/live_sources.py` 统一门面 = **21 源(13 可用 + 8 planned:资金流/龙虎榜/解禁/两融/大宗/股东变化/分红/腾讯quote)**,统一信封 `LiveSourceResult{status:ok|planned|error, fetched_ts, items[13字段含 visible_ts]}`;正统 CLI = `scripts/probe_live_sources.py`;agent 别名皮 = `probe_a_stock_data.py`(38 别名);**旧 `probe_live_text_sources.py`(13源)降为实现层遗留**,短名全兼容(注册表双键 source_id+alias),未正式废弃但已不是 UI 登记入口。
- **沉淀层**:Qlib bin / Parquet / text_source / PIT / wiki + 新增 `stock_data/staging/live_sources`(`stage_live_sources.py --apply` 才写,`{source}/date=D/{run}.normalized.parquet+manifest.json`,含 content_hash 去重键)——**该目录今日实测尚不存在(--apply 从未跑过)**。
- 腾讯行情 live cache:`stock_data/live/tencent/*/latest.parquet+manifest_latest.json(available_ts/errors_count)`——**盘上停 07-01 22:20,名为 realtime 实为手动**,消费方必须读 manifest 做门。

## 二、体检结论:七大主题(按危害排序)

### T1|正统入口错位 day-one(High×3 路交叉确认)
观澜 `ww_live_text`(tools.py:1389 硬编码旧 13 源 CLI)与骑其上的 macro 打板温度(astock.py:13),在 stocks 拆层当天就错位:8 个新增源够不着、planned 状态不可见、异常信封更差(旧 CLI 异常=traceback 非零退出,新 CLI=status:error 干净 JSON)。旧 CLI 一旦收敛,ww_live_text+A股温度整条静默降级。

### T2|同一上游多路重复拉取、跨进程零共享节流(High)
- 东财新闻宇宙(个股新闻/快讯)在观澜有 **4 条互不复用的拉取实现**(engine akshare / engine opencli collector / stocks 旧探针 / news_marks 三路合并),喂 6 个消费入口。
- 腾讯行情 **6+ 处直拉** qt.gtimg.cn(seats/api.py:1623,1696,1795;buddy/server.py:837,1028,1220;watch/feed.py:62),与 stocks live cache 平行双拉、互不相认。
- 涨停池:macro 与 ww_live_text 各起子进程,同分钟可对东财/同花顺双倍打击(风控面)。

### T3|LLM 情绪判读分裂(High)
4 个独立 judge_sentiment 触发点(rescore / ww_news_search / research tier1 / screen news),共用 prompt 但**零共享缓存**:仅 rescore 有当日缓存(`var/rescore_news_cache.jsonl`);ww_news_search 每调必真 LLM;同日同票重复花钱且口径可能分裂。**已证缺陷**:rescore 当日缓存全命中时 `market_read/market_tilt` 恒 None(rescore.py:163-173)→ rerank 大盘情绪上下文空转;自然日缓存 key 使早晨判"无新闻"的票晚间出利空也不复判。

### T4|情绪产物数据文件各自为政(用户痛点确认属实)
`var/macro_pulse/snapshots.jsonl`(~15KB/行 append-only,ww 工具默认 refresh=True 线性膨胀)/`var/rescore_news_cache.jsonl`/`rescore_runs.jsonl`(rerank 块嵌内)三家三 schema 零互认,无轮转。

### T5|新鲜度治理缺失(High:regen 停摆)
- **regen 产物停 07-02 而 stocks bin 07-06 已新鲜**——定时器现成(`GUANLAN_REGEN_DAILY=1` opt-in)但未启用;选股 L1/行业聚合/席位旁路每天消费 2 交易日旧排名,regen 后还需手动重启 9999。
- DL 三源(fincast/lstm 07-02、gat 06-30 已断供)生产端零排程,07-07 起全断=DL 混合实质长期关闭,无人被通知。
- `tushare_stock_basic` 正本 06-02 起未刷,三读点并存全无年龄闸(新股/行业变更静默缺席)。
- mainline 月度面板双源分叉:market_status 读 stocks 侧 06-08 陈尸版,选股 L2 读 artifacts 07-02 版——同月两个答案。

### T6|私有路径依赖 + 硬编码散装(Medium)
news_marks.py:17 富层读 `_news_staging`(stocks 管线中间态,不在其声明的两层内,迁移即静默 rich_available:false);全仓 **12+ 处 G:/stocks 硬编码**各自私设 env 覆盖,engine 明有 `get_data_paths` 分层解析器(env>loaders.yaml>user>dev)但观澜自有模块基本绕开。

### T7|cn_data 双写者异源(High,结构性)
观澜 `ww_update_data`(pytdx 日线不复权+腾讯 daily_basic 缺 ps_ttm/dv_ttm)与 stocks 正典入口 `incremental_update_tushare.py`(tushare)写同一 qlib bin。06-12 close/pe_ttm 节奏错位事故(regen.py:122-129 补丁)即其症状;字段口径混排无人审计。

## 三、整合方案:情绪/文本中台「一客户端 + 一快照 + 一健康表」

### ①统一实时客户端 `guanlan_v2/datafeed/live_client.py`(收编 T1/T2 现拉面)
单一模块包子进程调 **`probe_live_sources.py`(21源新 CLI)**:目录从 catalog 动态派生(启动缓存,替代手抄白名单)、模块级跨调用最小间隔(补跨进程节流缺口)、统一信封透传 status/planned/error。挂点改造(全部现成注入缝):
- `console/tools.py` live_text_impl → 换 `_STOCKS_PROBE` 指向+枚举动态化(ww_live_text 免费升 21 源);
- `macro/astock.py:11 build_astock(live_fn=)` → 注入新客户端(顺带解决 limit=50 涨停饱和:内部调用放开 limit);
- `seats/news_marks.py` 三注入点(stock_news_fn/kuaixun_fn/parquet_path)→ 取数端整体可换,合并/PIT 逻辑不动;
- 引擎 news_pulse.fetch_* 保留(研报子进程导不到 guanlan_v2),但作为登记过的"第二实现"标注。

### ②统一情绪快照 `var/sentiment/`(收编 T3/T4 数据文件)
- `judgments-YYYYMM.jsonl`:**(date,code)→{tag,read,score,as_of,market_read,market_tilt,source}** 单表当日缓存,rescore/ww_news_search/rerank 三家共享"当日已判先读、miss 再 LLM 并回写"——一次判读全平台复用,顺带修 market_read 全命中丢失+口径分裂;月切轮转。
- `snapshots-YYYYMM.jsonl`:统一快照行 `{ts, macro:{temps,astock_temp,zt_count…}, market:{read,tilt,as_of}, top_stocks:[{code,tag,score}]}`——macro 温度计并入为一节(pulse.py 落盘段改写此文件),rescore/rerank 读最新行取大盘情绪。
- 读接口:`datafeed/sentiment.py::read_snapshot()/read_judgment(code,date)`,帷幄加 `ww_sentiment`(或并入 ww_macro_pulse)。

### ③数据健康总闸(收编 T5)
扩 `/screen/health` 成全仓新鲜度注册表:regen stale_days、DL 三源(读 v4_dl_provenance)、tushare_stock_basic 正本年龄、stocks live cache manifest available_ts、pit_store _meta、mainline 双源版本对比;帷幄加 `ww_data_health`。断供从"看 provenance 的人才知道"变一处可见。

### 不动的(明确非目标)
PIT 回测链(news_marks pit 态/PitReader)零改动;rescore/rerank"展示型绝不动信号"红线不变;cn_data 双写(T7)属跨仓治理,单列决策:建议观澜 ww_update_data 降级为"仅腾讯当日 daily_basic 兜底"或干脆指引用户跑 stocks 正典入口,不在中台里顺手改。

## 四、快赢清单(不等大整合,单独可交付)
1. probe 切新 CLI + 枚举动态派生(T1 根治,~半天);
2. `GUANLAN_REGEN_DAILY=1` 启用 + regen 后自动踢 9999 重启(T5 最痛一条,现成开关);
3. rescore market_read 持久化进当日缓存(全命中不再空转);
4. v4 列名兼容抽单一 `read_v4_ranking()`(rescore/aggregate 双份手写已咬过一次);
5. news_marks 富层路径进 loaders.yaml(等 stocks staging 有数后切官方层);
6. artifacts 死重清理(spot_2026-04-03/fincast_daily_pred 两键)+ vendored 拷贝加正本年龄告警;
7. mainline 双源收敛:market_status 改读 regen 自产面板。

## 五、待用户拍板
A. 中台三件(①②③)是否立项、次序(建议 ①→②→③,各自独立可交付);
B. 快赢清单哪些先做(建议 1+2 立即);
C. T7 双写者治理方向(观澜降级 vs 保留双源);
D. 行情读切 stocks live cache 的前提=stocks 侧把 cache 排上盘中节奏(跨仓协调,观澜侧先不动)。
