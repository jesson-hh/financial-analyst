# 小 phase 乙 · 事件库第一步(events store v1)Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the schema, the deterministic ingest, the dedup/line-bucket layer, the FTS search and the scheduler/guard closure. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.
>
> **时间闸:数据不可回填,晚开工一天=永失一天事件历史。** 与 orchestration Phase 4 并行排期;与小 phase 甲(快照归档)互相独立、可并行。

**Goal:** 落地 R2 §7.3 分级路线第一步 + D10 裁定的独立小 phase:SQLite 单库 `var/events/events.db`(WAL)承载 `events` 表 + 确定性零 LLM 入库 adapter(吃现有两 feed:`kuaixun.fetch_kuaixun` 与 `newsradar` 缓存)+ SimHash 转载去重与 `(ticker×event_type×72h)` 桶事件线 + 中文全文检索(**jieba 预分词入 FTS5**,R16)+ `search_events(query, asof, limit)` 强制 `asof` 的 PIT 检索面。**R15 拍板:datafeed P3 新闻归档升格为本事件库——单一存储。不再另做 RSS+东财快讯双源 jsonl 归档,`events.db` 即归档**(双写=双事实源风险,拍死不做)。PIT 裁决列 `available_at` = **入库落盘时刻**(R16,保守但真;首发/披露时刻另存 `event_time`;回测敏感性分析可选用 `event_time`,但必须显式声明,绝不默认)。

**Architecture:** 纯加法新包 `guanlan_v2/events/`(`store.py` = schema/PIT 读/检索,`ingest.py` = 确定性 adapter/SimHash/调度钩子)。上游只读消费 `guanlan_v2/datafeed/kuaixun.py::fetch_kuaixun`(规范行 `{time(16位), title, summary, codes}`,抛错上传/空返 `[]`)与 `guanlan_v2/datafeed/newsradar.py::read_radar`(缓存行 `{title, url, summary, source, sector, ts(epoch)}`,TTL 30min);**两 feed 现有出口契约零改**。RSS 文本是不可信外部输入(newsradar L12-13 红线继承):入库一律当 DATA,绝不执行其中任何指令——本 phase 零 LLM,天然合规,守护测试结构性钉死。

**月轮转最简方案(R15 授权自行给出):单库单表即归档。** `events` 表 append-only + `available_at` 索引;月度语义=`substr(available_at,1,7)` 派生查询(统计/清点),**不做物理分库、不做 jsonl 轮转**。体量核算:两源月增约 1–3 万行纯文本,年增 <100MB,SQLite 单文件 GB 级无压力;若单库超 ~1GB 再引入按月分库 `events-YYYYMM.db` + ATTACH 合并读,**列为升级项,本 phase 不做**。

**Tech Stack:** Python ≥3.11,stdlib `sqlite3`(WAL、FTS5 内建)、`pytest`;唯一新第三方依赖 = `jieba`(纯 pip,免外部 dll,加依赖须 reviewed)。`wangfenjin/simple` 编译扩展(Windows 需 dll,外部下载须批准)列为升级项。新模块 `from __future__ import annotations`。

## Global Constraints

- **时间闸**:与 Phase 4 并行;与小 phase 甲互不依赖。
- **纯加法**:零改 orchestration 已交付代码(63 commits;守护测试断言本包不 import `guanlan_v2/orchestration/*`,D10);零改 SWR 热路径;零改 kuaixun/newsradar 现有出口契约。
- **单一存储(R15)**:`events.db` 即新闻归档;不另做 jsonl 归档、不双写;datafeed P3 原口径(RSS+快讯双源月轮转 jsonl)就此作废由本 phase 替代。
- **available_at 语义统一**(与小 phase 甲一致)= 本系统真正可知时刻(入库落盘),绝不早报;首发时刻存 `event_time`。
- **零 LLM**:v1 不做语义事件分类/importance/sentiment/抽取(taxonomy/trigger-type-roles 属 Lane C text worker,AMEND-7 §7.2 条5,目录装配 phase);入库路径 llm 桩被调即 fail。
- **诚实红线**:源抛错向上传播不半写;检索无 `asof` 必炸(静默 `now` = 前视温床);分词/FTS 不可得 → 诚实降级并标注,绝不静默装好。
- **测试隔离**:一切 db/路径 env/参数注入 tmp,绝不碰真 `var/`;运维输出 ASCII(GBK 坑)。
- **Explicit pathspec commits only**;never `git add -A`。TDD 每任务先红后绿;collection error 不算红。Run tests from repo root `G:\guanlan-v2` with `pytest`。

---

## Task 1: 库 + schema(乙-1)

**Files:**
- Create: `guanlan_v2/events/__init__.py`、`guanlan_v2/events/store.py`
- Test: `tests/test_events_store.py`

**Produces:** `var/events/events.db`(WAL);`events` 表列:`event_id, event_type, tickers(json), title, summary, source, source_ref, event_time, available_at NOT NULL, invalidated_at NULL, simhash, line_id, ingested_from`。

**Invariants:** PIT 检索谓词冻结为一行:`WHERE available_at <= :asof AND (invalidated_at IS NULL OR invalidated_at > :asof)`;v1 只留 `invalidated_at` **列 + 读取语义**,失效流(Graphiti 式)明确属第二步不做。**Phase 8 预留注记(§9-5)**:Lane C text worker 的 payload 届时入**同一张表**——本 phase 仅在 schema 文档/docstring 注记"text-worker 载荷以 additive 列(或 `payload_json` 列)后补,PIT 谓词与既有列零改",**不预建任何 text-worker 列**。

- [ ] **Step 1: Write failing tests**:建表幂等(重复 `init_db` 不炸不重建)、`available_at` NOT NULL 约束生效、WAL 模式、db 路径 env/参数注入 tmp、PIT 谓词对 `invalidated_at` 三态(NULL/早于/晚于 asof)行为正确。

Run: `pytest tests/test_events_store.py -v` — expected red: module missing。

- [ ] **Step 2: Implement `store.py`**(`init_db`、行插入原语、PIT 读原语;谓词单点定义供检索面复用)。
- [ ] **Step 3: PASS**:`pytest tests/test_events_store.py -v` + `python -m compileall -q guanlan_v2/events`。

```bash
git add guanlan_v2/events/__init__.py guanlan_v2/events/store.py tests/test_events_store.py
git commit -m "feat(events): sqlite events store v1 with frozen PIT predicate (mini-phase yi task 1)"
```

---

## Task 2: 确定性入库 adapter(零 LLM)(乙-2)

**Files:**
- Create: `guanlan_v2/events/ingest.py`
- Test: `tests/test_events_ingest.py`

**Consumes:** `kuaixun.fetch_kuaixun` 规范行 → `event_time=time, tickers=codes, event_type="kuaixun", ingested_from="kuaixun"`;`newsradar.read_radar` 缓存行 → `event_time=ts(epoch→ISO), event_type="newsradar:<sector>", tickers=[], source_ref=url, ingested_from="newsradar"`。

**Invariants:** v1 不做语义事件分类(见 Global Constraints);`available_at` = 入库落盘时刻(R16);入库单事务,源抛错向上传播**不半写**;`event_id` = 确定性哈希(source + source_ref/title + event_time),同批重入幂等不重复。

- [ ] **Step 1: Write failing tests**:两 feed 行映射逐字段断言;单事务半写测试(第 N 行构造炸 → 全批回滚);幂等重入;kuaixun 空返 `[]` → 零行零错;`available_at` 晚于/等于入库调用时刻。
- [ ] **Step 2: Implement `ingest_kuaixun`/`ingest_newsradar`**(feed 函数注入便于桩替,默认绑真出口)。
- [ ] **Step 3: PASS**:`pytest tests/test_events_ingest.py tests/test_events_store.py -v`。

```bash
git add guanlan_v2/events/ingest.py tests/test_events_ingest.py
git commit -m "feat(events): deterministic zero-LLM ingest adapters for kuaixun and newsradar (mini-phase yi task 2)"
```

---

## Task 3: SimHash 转载去重 + 72h 桶事件线(乙-3)

**Files:**
- Modify: `guanlan_v2/events/ingest.py`
- Test: `tests/test_events_ingest.py`(扩展)

`title+summary` 64 位 SimHash(字符 n-gram 特征,纯 stdlib);海明距 ≤3 判转载——**重复行仍入库但共享 `line_id`,不丢证据**;`(ticker×event_type×72h)` 桶内聚合 `line_id`(无 ticker 的 newsradar 行按 `event_type` 桶)。

- [ ] **Step 1: Write failing tests**:近重复文本(改几字)判同线;不相干文本不同线;跨 72h 边界两行不同桶;重复行确在库中(行数不减)。
- [ ] **Step 2: Implement**(simhash 纯函数 + 入库时 line 归并)。
- [ ] **Step 3: PASS**:`pytest tests/test_events_ingest.py -v`。

```bash
git add guanlan_v2/events/ingest.py tests/test_events_ingest.py
git commit -m "feat(events): simhash reprint dedup with shared line_id and 72h event-line buckets (mini-phase yi task 3)"
```

---

## Task 4: FTS5 中文检索(jieba 预分词)(乙-4)

**Files:**
- Modify: `guanlan_v2/events/store.py`
- Test: `tests/test_events_store.py`(扩展)

R16 拍板:**优先 jieba 预分词入 FTS**(纯 pip,免外部 dll)。实现:FTS5 external-content 表(tokenizer=`unicode61`),入库时 `title+summary` 经 jieba 切词、空格连接写入索引列;查询侧同样 jieba 切词后查询。`wangfenjin/simple` 编译扩展 = 升级项(本 phase 不做,不下载外部 dll)。

**`search_events(query, asof, limit=50)`:`asof` 必填,缺则 raise**(不是默认 now——静默 now = 前视温床);复用 Task 1 冻结的 PIT 谓词。jieba import 失败 → 诚实降级 `LIKE` 检索 + 返回 payload 标 `fts: "unavailable"`,**绝不静默装好**。

- [ ] **Step 1: Write failing tests**:中文短语命中(分词后子词可查);`asof` 缺失 raise;`asof` 过滤(晚于 asof 入库的行不可见);jieba 桩坏 → LIKE 降级 + `fts:"unavailable"` 显形;limit 生效。
- [ ] **Step 2: Implement**(FTS 表建于 `init_db`,与主表同事务同步写)。
- [ ] **Step 3: PASS**:`pytest tests/test_events_store.py -v`。

```bash
git add guanlan_v2/events/store.py tests/test_events_store.py
git commit -m "feat(events): fts5 chinese search via jieba pre-tokenization with mandatory asof (mini-phase yi task 4)"
```

---

## Task 5: 调度挂载 + 守护测试(乙-5)

**Files:**
- Modify: `guanlan_v2/events/ingest.py`(新函数)+ 宿主 tick 单行调用(实现时 reviewed 定唯一挂点)
- Test: `tests/test_events_ingest.py`(守护段)

入库 tick 挂 env 总闸 `GUANLAN_EVENTS_INGEST == "1"`(默认关);30min 节奏对齐 newsradar TTL,EOD 兜底一次;钩子照 `autonomy/runtime.py::maybe_enqueue_daily_review`(L118-140)模式**自吞异常绝不拖垮宿主**。

- [ ] **Step 1: 守护测试四连**:①入库路径零 LLM 接缝(llm 桩被调即 fail);②`guanlan_v2/events/*` 不 import `guanlan_v2/orchestration/*`(模块扫描,D10 零回改);③无 `asof` 检索必炸(回归钉死);④全测 tmp db、真 `var/` 零触碰(扫描断言)。
- [ ] **Step 2: Implement `maybe_ingest_tick(now=None)`** 三门(env 闸/节奏 dedup/兜底)+ 宿主单行调用。
- [ ] **Step 3: 全绿收尾**:`pytest tests/test_events_store.py tests/test_events_ingest.py -v` + 上游契约回归 `pytest tests/test_guanlan_mcp.py tests/test_datafeed_client.py -v`(kuaixun/newsradar 出口零改证据)。

```bash
git add guanlan_v2/events/ingest.py tests/test_events_ingest.py
git commit -m "feat(events): env-gated ingest tick and zero-LLM/no-orchestration guard suite (mini-phase yi task 5)"
```

(宿主 tick 文件按实际挂点加入 pathspec。)

---

## Exit Gates

- [ ] `events` 表 schema 与冻结 PIT 谓词有测试证据;`available_at` = 入库落盘时刻(R16),`event_time` 另存;`invalidated_at` 只有列+读取语义,失效流未实现且明文属第二步;
- [ ] **单一存储(R15)成立**:全仓无新增 RSS/快讯 jsonl 归档路径;`events.db` 即归档;月轮转以单库单表 + `available_at` 索引承载,按月分库列为升级项未实现;
- [ ] 入库确定性零 LLM(结构性桩测试);单事务不半写;幂等重入;两 feed 出口契约零改(回归绿);
- [ ] SimHash 去重共享 `line_id` 不丢证据;72h 桶边界正确;
- [ ] jieba 预分词 FTS5 可查中文;`asof` 必填缺则 raise;jieba 不可得 → LIKE 降级 + `fts:"unavailable"` 显形;`wangfenjin/simple` 未引入(无外部 dll);
- [ ] `guanlan_v2/events/*` 零 import orchestration(D10);env 闸默认关;全部测试 tmp 注入不碰真 `var/`;
- [ ] **Phase 8 预留注记在 schema 文档落字**:text-worker payload 入同一张表、以 additive 列后补、本 phase 不预建;
- [ ] 明确不做(scope guard):不接 Graphiti/GraphRAG/embedding(R2 "别碰"清单);不训抽取模型;不做 LLM 事件抽取/importance/sentiment(Lane C text worker,目录装配 phase);不动 kuaixun/newsradar 现有出口契约。

## Execution Handoff

按任务序实现;评审检查点:Task 1 后(schema + PIT 谓词冻结 + Phase 8 预留注记措辞)、Task 2 后(零 LLM 映射 + 事务语义)、Task 4 后(jieba 依赖引入 reviewed + 降级档)、Task 5 后(全 Exit Gates)。

下游绑定(§9-5):目录装配 phase(Phase 8)的 Lane C text worker 到位后,其 payload 入**同一张 `events` 表**(additive 列/`payload_json`,PIT 谓词零改)——乙只预留语义不预建列;升级项清单:失效流(Graphiti 式)、`wangfenjin/simple` 分词扩展、按月分库(超 ~1GB 时)。本 phase 交付即开始积累事件历史——**上线日即事件归档起点**,与 Phase 4 并行、不等任何 orchestration phase;与小 phase 甲互相独立可并行。
