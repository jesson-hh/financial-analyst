# 盘口实时快照中台(datafeed 中台第④块)设计

> 状态:已与用户拍板(2026-07-08),待落实现计划。
> 上游:数据端体检 `2026-07-06-data-seams-audit.md` 的「没用到」缺口 —— 30 个实时源里
> 只有 3 个(em_zt_pool/ths_hot_reason/ths_hot_list,喂打板温度)有常设消费方,~24 个仅
> agent 手动可达。本设计把其中 9 个全市场无-code 源补成一份统一只读快照。

**目标(一句话):** 把没有常设消费方的高价值全市场实时源(打板生态/龙虎榜/北向/热榜/行业)
聚合进一份统一只读盘口快照,SWR 保鲜、data_health 纳管、帷幄可读、宏观页展示,并收敛打板温度的重复拉取。

**架构(2-3 句):** 新增 `guanlan_v2/datafeed/market_tape.py` 作为与 `live_client`/`sentiment`/`health`
平级的第④块中台砖。零重造:所有拉取仍走已建统一客户端 `live_client.probe`,本模块只做「9(+1)源
聚合 + stale-while-revalidate 磁盘缓存 + 只读读取 API」。落地面 = HTTP 端点 + 帷幄工具 + 健康闸项 + 宏观页面板。

**技术栈:** Python / FastAPI(datafeed 路由)/ 既有 `live_client` 子进程正典 probe / 前端宏观页(Vite+React)。

## 全局约束(Global Constraints)

- **展示/上下文型,绝不回写信号**:快照与 `derived` 只供展示与 agent 阅读,绝不回写 `v4`/`blend`/`picks`/`seats` 任何信号或排序。
- **诚实降级绝不伪造新鲜**:每源独立 `pulled_at`,局部/整体陈旧一律经龄期显形;首拉无缓存返回 `warming` 而非编造;绝不宣称拉到未拉到的数据。
- **UI 只填现有页不新建**:宏观页(全球情绪温度计所在页)填一个只读盘口面板,不新建页面/不重构布局。
- **零重造**:不新增任何直连外源的 HTTP 拉取;一切经 `live_client.probe`(它才是观澜唯一现拉门户)。
- **PIT 不受影响**:这是 live 最新缓存,不是回测料,绝不进任何 PIT/vintage/回测通道。
- **不自 HTTP**:帷幄工具/端点在服务进程内直调 `read_tape()`(必要时 `asyncio.to_thread`),严禁协程内同步自 HTTP(会堵 loop → 看门狗杀 9999)。
- **交付层全量 content**:数据型帷幄工具必须自带全量 `content`,并有信封级测试穿透真 `_wrap`(避免 console 工具无 content 键 → `json[:400]` 断裂 JSON 的历史缺陷)。
- **守护计数四面同步**:新增帷幄工具须同步 `test_console_tools`(5 断言)+ `test_guanlan_mcp`(3 断言)+ glmcp README + `_WW_REACHABLE_ENDPOINTS`。

---

## 1. 源集(9 展示源 + 1 收敛源)

全市场无-code 口径(能进大盘快照);个股口径源(fund_flow/lhb_stock/margin/unlock/dividend/…)不在本设计,留 agent 按票拉。

| 组 | source_id | 客户端调用 | 展示 |
|---|---|---|---|
| 打板生态 | `em_zt_pool`(涨停) | `probe("em_zt_pool", date=today)` | ✓ |
| 打板生态 | `em_zb_pool`(炸板) | `probe("em_zb_pool", date=today)` | ✓ |
| 打板生态 | `em_dt_pool`(跌停) | `probe("em_dt_pool", date=today)` | ✓ |
| 打板生态 | `em_yzt_pool`(一字板) | `probe("em_yzt_pool", date=today)` | ✓ |
| 龙虎榜 | `eastmoney_lhb`(全市场当日) | `probe("eastmoney_lhb", date=today)` | ✓ |
| 北向 | `ths_hsgt_realtime` | `probe("northbound")` | ✓ |
| 热榜 | `eastmoney_hot_rank`(人气榜) | `probe("em_hot_rank", limit=N)` | ✓ |
| 热榜 | `ths_hot_list`(热股) | `probe("ths_hot_list", limit=N)` | ✓ |
| 行业 | `eastmoney_industry_comparison`(涨幅榜) | `probe("industry_rank", limit=N)` | ✓ |
| **收敛** | `ths_hot_reason`(热点原因) | `probe("ths_hot_reason", date=today)` | 打板温度用,面板不单列 |

`ths_hot_reason` 并入拉取集只为让打板温度**完全**读快照(见 §4);展示面板仍只呈现 9 组。共 10 次探针/刷新。

## 2. 数据文件

`var/live/market_tape.json` —— 单文件覆盖写、原子落盘(写临时文件 + `os.replace`)、跨进程共享、重启存活。

```json
{
  "pulled_at": "2026-07-08T10:15:03",
  "ttl_s": 180,
  "sources": {
    "em_zt_pool": {"status": "ok", "n": 64, "pulled_at": "2026-07-08T10:15:01", "note": "", "rows": [ {"源原生行": "..."} ]},
    "ths_hsgt_realtime": {"status": "ok", "n": 3, "pulled_at": "2026-07-08T10:15:04", "note": "", "rows": [ "..." ]},
    "...": "每源独立 status/n/pulled_at/note/rows"
  },
  "derived": {
    "zt_count": 64, "max_streak": 7, "break_ratio": 0.08,
    "dt_count": 3, "zb_count": 12, "north_net": 12.3
  }
}
```

- 每源独立 `pulled_at`:某源本轮刷新失败则保留其上一轮 `pulled_at` + 旧 rows(局部陈旧诚实显形),不整份作废。
- `rows` = `live_client.native_rows` 的源原生行形(保真,与 ww_live_text 同信封)。
- `derived` = 确定性纯算术(见附录),refresh 时算一次;无 LLM、无信号。

## 3. stale-while-revalidate 读取语义

### `read_tape(fresh_within_s: int = 180) -> dict`
返回 `{"ok", "pulled_at", "sources", "derived", "freshness", "warming", "note"}`:
- **读永远秒回**磁盘缓存(不阻塞在网络上)。
- `freshness` = `{"overall_age_s": int, "stale": bool, "per_source": {sid: age_s}}`。
- 缓存缺失或 `overall_age_s > fresh_within_s` → 触发 `_trigger_refresh()`(后台线程),本次仍立即返回现有值 + note「已触发后台刷新」。
- **首拉无缓存** → `warming=True`,`sources={}`,note「预热中,后台首拉已触发」。绝不阻塞、绝不编造。

### `_trigger_refresh()`
- `threading.Lock` + 模块级 `_REFRESH_INFLIGHT` 旗:已有刷新在跑则直接返回(防并发踩踏 → 防外源被打)。
- 起 daemon 线程跑 `_refresh()`。

### `_refresh() -> dict`
- 逐源 `live_client.probe(...)`(沿用其 1s 跨启动节流 → 一轮 ~10s;180s 才一轮,后台跑,对外源礼貌)。
- 每源结果 → `{status,n,pulled_at,note,rows}`;失败源保留旧缓存条目(读旧文件合并)。
- 算 `derived`,原子写 `var/live/market_tape.json`,更新 `_MEM_CACHE`(进程内镜像,省磁盘读)。

### TTL
- 默认 180s;env `GUANLAN_MARKET_TAPE_TTL_S` 覆盖。
- 非盘中数据不变:靠 `pulled_at` 龄期显形,不伪装新鲜(可能整天 stale=True,正确)。

## 4. 打板温度收敛(macro/astock.py)

现状:`build_astock` 经 `_client_live` 自拉 `em_zt_pool`+`ths_hot_reason`+`ths_hot_list` 三源。

改为:
- 优先 `market_tape.read_tape()`,从快照 `sources` 取这三源的 `rows`(温度公式 + `themes.yaml` astock 常数**仍留 astock**,它拥有温度语义;tape 只供原始行)。
- 快照缺席/warming/该源缺失 → **回落原直拉逻辑**(不破温度计,不硬依赖快照)。
- 结果:一个拉取点,消除重复拉取;温度计对外输出逐字段不变(测试守护)。

## 5. 落地面

### 5a. HTTP 端点(datafeed/api.py)
`GET /data/market_tape` → `asyncio.to_thread(read_tape)` → 返回 read_tape dict。供宏观页面板 fetch。

### 5b. 帷幄工具 `ww_market_tape`(console/tools.py)
- `market_tape_impl(fresh_within_s: int = 180)`:进程内**直调 `read_tape()`**(不自 HTTP),零 LLM。
- **自带全量 content**:北向净额 / 涨停家数·连板高度 / 跌停数·炸板率 / 龙虎榜 top / 人气榜 top / 行业涨跌前后 + 整体 pulled_at·龄期。warming/空/局部陈旧一律诚实标注。
- 注册进 `WW_TOOL_TABLE`(置于 `ww_data_health` 附近);`_WW_REACHABLE_ENDPOINTS` 加 `/data/market_tape`。
- 守护计数四面同步 +1。

### 5c. 宏观页面板(前端)
- 定位全球情绪温度计所在宏观页,**填一个只读盘口面板**(fetch `/data/market_tape`)。
- 展示 9 组 + 整体 pulled_at·龄期徽章(fresh/stale 显形);warming 显"预热中"。
- `?v` bump;只填现有页,不新建、不重构。

## 6. data_health 纳管(datafeed/health.py)

`collect_data_health()` 加 `market_tape` 数据项:
- 读 `var/live/market_tape.json` 的 `pulled_at`。
- `status`:缺文件=`missing`;龄期 ≤ 阈值(默认 `_TAPE_STALE_MIN=30` 分钟盘中口径)=`fresh`;超=`stale`。
- 属 data 项(非 ops),参与 overall。

## 7. 组件边界 & 测试矩阵

| 单元 | 职责(一句话) | 依赖 | 测试要点 |
|---|---|---|---|
| `market_tape.py` | 拉 10 源 + SWR 缓存 + read API | `live_client` | 离线桩 probe:刷新落盘/SWR 秒回+过期触发异步/首拉 warming/局部失败保留旧源龄期/原子写/并发单刷 |
| `astock.py`(改) | 读快照算温度,缺席回落 | `market_tape` | 快照在→读之;缺席/warming→直拉;温度输出逐字段不变 |
| `health.py`(改) | +market_tape 项 | 缓存文件 | pulled_at 龄期→fresh/stale;缺文件→missing;进 overall |
| `datafeed/api.py`(改) | GET /data/market_tape | `read_tape` | to_thread 返 read_tape dict |
| `tools.py`(改) | market_tape_impl 全量 content | `read_tape` | **信封级 _wrap 穿透**(content 无截断)+ 零 LLM + warming/空诚实 |
| glmcp/tests | 守护计数四面 +1 | — | 5+3 断言 + README + reachable |
| 宏观页前端 | 只读盘口面板 | `/data/market_tape` | 真机 fetch 渲染 + 龄期徽章 |

## 附录:derived 纯算术定义(复用 astock 既有口径,无信号)

- `zt_count` = `em_zt_pool` 行数(≥300 截断则标注)。
- `max_streak` = `em_zt_pool` 各行 `zt_stat` 解 `(\d+)板` 或 `limit_days` 的最大值。
- `break_ratio` = `em_zt_pool` 中 `break_times>0` 占比(round 4)。
- `zb_count`/`dt_count` = `em_zb_pool`/`em_dt_pool` 行数。
- `north_net` = `ths_hsgt_realtime` 北向净额(源字段,单位保真;缺则 None)。

## 非目标(YAGNI)

- 个股口径源(fund_flow/lhb_stock/margin/unlock/dividend/holder_change/cninfo_*/research/hot_concept/eps_forecast)的常设接入 —— 留 agent 按票拉,另议。
- T2 东财快讯三路收敛 —— 独立议题,风险在引擎 fork + 选股页 C 节契约,不在本设计。
- 盘中定时 tick 调度器 —— SWR 已够;不引入新常驻调度。
- 任何把快照喂进信号/排序的通道 —— 红线禁止。
