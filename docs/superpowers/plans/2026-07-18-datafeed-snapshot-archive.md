# 小 phase 甲 · 盘口/资金快照归档(datafeed snapshot archive)Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the archiver, the fundflow closure, the scheduler hook and the reader contract. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.
>
> **时间闸(R13,优先级最高):数据不可回填,晚开工一天=永失一天历史。** 本 phase 与 orchestration Phase 4 并行排期,优先于一切非时间闸任务;与小 phase 乙(事件库)互相独立、可并行。

**Goal:** 给 `market_tape` 与 `fundflow` 两个 SWR 快照中台补上**每日收盘后 append-only 归档**(现状:两者都只落单份 `var/live/*.json`、每次原子覆写、零历史),并交付冻结签名的读取器 `read_archive(kind, start=None, end=None, asof=None)` 作为**三方契约**(§9-4):小 phase 甲(落盘)→ Phase 5 `market.factor` 电池 rot 族 6 因子 + 炸板率/北向/主力分位历史序列的**唯一供给口** → Phase 9 replay manifest 的 coverage floor(`first_date`)。归档 payload 按 R16 拍板取**全量行级**(zt/zb/yzt 池 + fundflow boards + 大盘五档,~10MB/月 接受),月轮转 jsonl。归档起点后短序列诚实(`first_date` 显形),**不回填、不伪造**(schema spec §5)。

**Architecture:** 纯加法新模块 `guanlan_v2/datafeed/snapshot_archive.py`,只读现有缓存文件(`var/live/market_tape.json`,由 `market_tape._write_cache_atomic` L55-59 落盘,含 `sources` 行级池 + `derived` + `board_date/board_backfilled` L201-202;`var/live/fundflow_live_{concept,industry}.json`,由 `fundflow.pulse._write_live_cache` L152-158 落盘)。归档器**绝不调 `_refresh`/`_trigger_*`、绝不触网**——SWR 热路径(`read_tape`/`read_live` 及其缓存契约)零改。月轮转语义照 macro 先例(72573b8):写入恒当月主文件 `snapshots.jsonl`,发现非本月行搬 `snapshots-YYYYMM.jsonl`;**尾读红线**:最近数据永远在主文件尾,读近期不扫归档文件。

**Tech Stack:** Python ≥3.11,纯 stdlib(json/pathlib/os),`pytest`。零新第三方依赖。新模块 `from __future__ import annotations`。

## Global Constraints

- **时间闸**:立即开工(R13);与 Phase 4 并行;两个小 phase 互不依赖。
- **纯加法**:零改 orchestration 已交付代码(63 commits,D10/接线总原则:"无一需要回改 Phase 1/2/3/3b");零改 SWR 热路径与前端契约;唯一例外=Task 2 的 `fundflow/pulse.py` 模块 docstring L6 死话更正(**纯注释,行为零改**)。
- **绝不触网**:归档路径只读缓存文件;`probe`/`sector_fn`/`_refresh`/`_trigger_*` 全程不出现;守护测试以桩被调即 fail 结构性保证。
- **available_at 语义统一**(与小 phase 乙一致):`archived_at` = 落盘时刻 = 本系统真正可知时刻,绝不早报。
- **诚实红线全继承**:缺源/`ok:false`/`warming` 当日该 kind 诚实跳过并记 note,**绝不落半假行**;不补零、不回填、不伪造新鲜;`first_date` 显形;降级必标注。
- **月轮转 correction clause**:实现时若 72573b8(macro 月轮转)已合 main 则直接复用其函数;仍未合则按同款语义独立实现,**勿 cherry-pick**(并发分支交错,"勿擅自搅 git" 红线)。
- **测试隔离**:一切路径 env/参数注入 tmp,绝不碰真 `var/`(as_of 冻结=测试污染真 store 的旧教训);运维输出 ASCII(GBK 坑)。
- **Explicit pathspec commits only**(共享分支有并发 session);never `git add -A`。TDD:每任务先红后绿;collection error 不算红。Run tests from repo root `G:\guanlan-v2` with `pytest`。

---

## Task 1: 归档器 `archive_eod`(甲-1)

**Files:**
- Create: `guanlan_v2/datafeed/snapshot_archive.py`
- Test: `tests/test_datafeed_snapshot_archive.py`

**Consumes:** `var/live/market_tape.json`(sources 行级:zt/zb/dt/yzt 池 + derived 标量 + `board_date/board_backfilled`)、`var/live/fundflow_live_concept.json`、`var/live/fundflow_live_industry.json`(boards 排行行级 + 大盘五档分解)。

**Produces:** `var/archive/<kind>/snapshots.jsonl`,每行 `{trade_date, archived_at, kind, payload}`;kind ∈ `{market_tape, fundflow_concept, fundflow_industry}`。

**Invariants:** 绝不触网;幂等——同 `(trade_date, kind)` 已有则跳过;append-only——重跑不改已有行;写入恒当月主文件,非本月行搬 `snapshots-YYYYMM.jsonl`(尾读红线);payload 全量行级(R16:`rot.ladder_theme/rot.diffusion/rot.leader_persist` 电池因子需要行级明细,裁减=对应因子永久 UNAVAILABLE,不值)。

- [ ] **Step 1: Write failing tests**

覆盖:①`archive_eod(now=None, cache_dir=None, archive_dir=None)` 读 tmp 注入缓存、三 kind 各 append 一行且 payload 含 zt 池行级/boards 行级/大盘五档;②同日重跑幂等(文件字节不变);③跨月:主文件里塞上月行 → 归档搬移至 `snapshots-YYYYMM.jsonl`、主文件只剩本月;④`archived_at` 为调用时落盘时刻;⑤网络接缝桩(monkeypatch `market_tape._refresh`/`_trigger_refresh`、`fundflow.pulse._refresh_live`)被调即 fail。

Run: `pytest tests/test_datafeed_snapshot_archive.py -v` — expected red: module missing。

- [ ] **Step 2: Implement `snapshot_archive.py`**

`archive_eod` 纯读缓存文件;market_tape 的 `trade_date` 取缓存 `board_date`(YYYYMMDD,`_backfill_board_pools` 已定日),fundflow 取 payload 自带日期字段、缺则取 `now` 之交易日;返回 report dict `{archived: [...], skipped: [{kind, reason}]}`(诚实跳过显形,不落半假行)。月轮转按 Global Constraints 的 correction clause 落。

- [ ] **Step 3: PASS + 回归**

Run: `pytest tests/test_datafeed_snapshot_archive.py tests/test_datafeed_client.py tests/test_datafeed_health.py -v`,加 `python -m compileall -q guanlan_v2/datafeed`。Expected: PASS。

```bash
git add guanlan_v2/datafeed/snapshot_archive.py tests/test_datafeed_snapshot_archive.py
git commit -m "feat(datafeed): eod snapshot archiver for market_tape/fundflow with monthly rotation (mini-phase jia task 1)"
```

---

## Task 2: fundflow 侧收口 + 死 docstring 更正(甲-2)

**Files:**
- Modify: `guanlan_v2/fundflow/pulse.py`(仅模块 docstring L6)
- Test: `tests/test_datafeed_snapshot_archive.py`(扩展)

`fundflow/pulse.py` L6 声称"每次真拉…向 var/fundflow/<当日>.jsonl 追加快照",但全文件无此实现(L18 `_SNAP_DEFAULT` 定义后从未使用)——母版 macro 抄来的死话。

- [ ] **Step 1: Write failing tests**:①缓存缺失/`ok:false`/`warming` 三态 → 该 kind 出现在 `skipped` 且 reason 具体、归档文件零新行;②`_SNAP_DEFAULT` 死路径不被归档器引用。
- [ ] **Step 2: 实现三态诚实跳过**(Task 1 骨架内补齐分支);docstring L6 改为指向 `datafeed/snapshot_archive.py`(**只改注释,行为零改**;`_SNAP_DEFAULT` 留置不动,避免连带)。
- [ ] **Step 3: PASS**:`pytest tests/test_datafeed_snapshot_archive.py -v` + 现有 fundflow 套件回归。

```bash
git add guanlan_v2/fundflow/pulse.py guanlan_v2/datafeed/snapshot_archive.py tests/test_datafeed_snapshot_archive.py
git commit -m "fix(fundflow): honest skip states in archiver and correct dead snapshot docstring (mini-phase jia task 2)"
```

---

## Task 3: 调度挂载 `maybe_archive_eod`(甲-3)

**Files:**
- Modify: `guanlan_v2/datafeed/snapshot_archive.py`(新函数)+ 宿主 tick 单行调用(实现时 reviewed 定唯一挂点)
- Test: `tests/test_datafeed_snapshot_archive.py`(扩展)

照 `autonomy/runtime.py::maybe_enqueue_daily_review`(L118-140)的三门钩子模式:①env 总闸 `GUANLAN_SNAPSHOT_ARCHIVE == "1"`;②收盘后 `now >= 15:05`;③当日 dedup(当月主文件已有当日 `(trade_date, kind)` 则不重复落)。**自吞异常绝不拖垮宿主**;非交易日:board 池空由 `_backfill_board_pools` 徽章语义兜,归档器按 `board_date` 判重不重复落。挂载宿主=服务器现有日跑调度 tick 的单行加法调用(绝不挂 `read_tape`/`read_live` SWR 读路径)。

- [ ] **Step 1: Write failing tests**:三门矩阵(闸关/未到 15:05/当日已归档 各返 False 零写)+ 异常自吞返 False。
- [ ] **Step 2: Implement `maybe_archive_eod(now=None, ...)`** 并在宿主 tick 加单行调用。
- [ ] **Step 3: PASS** + 宿主模块既有套件回归。

```bash
git add guanlan_v2/datafeed/snapshot_archive.py tests/test_datafeed_snapshot_archive.py
git commit -m "feat(datafeed): env-gated post-close archive hook with daily dedup (mini-phase jia task 3)"
```

(宿主 tick 文件按实际挂点加入 pathspec。)

---

## Task 4: 读取器 `read_archive` —— 三方契约(甲-4)

**Files:**
- Modify: `guanlan_v2/datafeed/snapshot_archive.py`
- Test: `tests/test_datafeed_snapshot_archive.py`(扩展)

**冻结签名:`read_archive(kind, start=None, end=None, asof=None) -> {kind, rows, first_date}`**,rows 按 `trade_date` 升序;`asof` 给定则过滤 `archived_at <= asof`(PIT);`first_date` = 归档真实起点显形。**这是三方契约(§9-4)**:小 phase 甲(落盘)→ Phase 5 `market.factor` 电池 rot 族 6 因子 + 炸板率/北向/主力分位历史序列的唯一供给口 → Phase 9 replay manifest coverage floor。签名/返回键任何改动须三 plan(甲/Phase 5/Phase 9)同批 reconcile,不得单方漂移。

- [ ] **Step 1: Write failing tests**:①升序 + `first_date` 正确;②`asof` 过滤(晚于 asof 落盘的行不可见);③脏行跳过(照 macro `_read_snapshots` L72-91 惯例);④跨月区间=按月枚举 `snapshots-YYYYMM.jsonl` + 主文件合并;近期读(区间落在当月)只碰主文件(尾读红线);⑤空归档 → `rows=[], first_date=None` 诚实返回,不 raise 不造数。
- [ ] **Step 2: Implement。**
- [ ] **Step 3: PASS**:`pytest tests/test_datafeed_snapshot_archive.py -v`。

```bash
git add guanlan_v2/datafeed/snapshot_archive.py tests/test_datafeed_snapshot_archive.py
git commit -m "feat(datafeed): read_archive PIT reader as three-way contract for phase5 battery and phase9 replay (mini-phase jia task 4)"
```

---

## Task 5: 守护测试(甲-5)

**Files:**
- Test: `tests/test_datafeed_snapshot_archive.py`(收尾守护段)

- [ ] **Step 1: 契约不变守护**:`read_tape`/`read_live` 签名与 payload 契约逐键不变(快照对比现有键集);
- [ ] **Step 2: 无网络接缝守护**:归档全路径 probe/sector_fn/`_refresh` 桩上被调即 fail;
- [ ] **Step 3: append-only 守护**:重跑 `archive_eod` 不改已有行(前后字节对比);
- [ ] **Step 4: tmp 隔离守护**:测试全程无任何真 `var/` 路径被创建/写入(扫描断言);
- [ ] **Step 5: 全绿收尾**:`pytest tests/test_datafeed_snapshot_archive.py tests/test_datafeed_client.py tests/test_datafeed_health.py -v` PASS。

```bash
git add tests/test_datafeed_snapshot_archive.py
git commit -m "test(datafeed): guard suite for archive contract stability, no-network and append-only (mini-phase jia task 5)"
```

---

## Exit Gates

- [ ] `archive_eod` 三 kind 落盘、幂等、append-only、月轮转(尾读红线)全部有测试证据;payload 为全量行级(zt/zb/yzt 池 + fundflow boards + 大盘五档);
- [ ] 归档路径零网络接缝(结构性桩测试);SWR 热路径与 `read_tape`/`read_live` 契约逐键不变;
- [ ] 三态(缺源/`ok:false`/`warming`)诚实跳过并显形,零半假行;不回填、不伪造新鲜;
- [ ] `maybe_archive_eod` 三门 + 自吞异常;env 闸默认关;
- [ ] `read_archive` 冻结签名交付,`asof` PIT 过滤 + `first_date` 显形 + 跨月合并读通过;三方契约(→Phase 5 电池 / →Phase 9 manifest)在本 plan 与 Execution Handoff 明文;
- [ ] 零改 orchestration 已交付代码;`fundflow/pulse.py` 唯一 diff 为 docstring;全部测试 tmp 注入不碰真 `var/`;
- [ ] 明确不做(scope guard):不动 SWR 现拉/读路径与前端契约;不做归档起点前历史回填;不实现电池因子本身(Phase 5 AMEND-1/2);health 总闸计数不动(挂账)。

## Execution Handoff

按任务序实现;评审检查点:Task 1 后(归档行 schema + 月轮转语义)、Task 3 后(挂点唯一性 + 门矩阵)、Task 4 后(三方契约签名冻结)、Task 5 后(全 Exit Gates)。

下游绑定:Phase 5 Task 1/3(电池 loader)以 `read_archive` 为 rot 族 6 因子 + 炸板率/北向/主力分位历史序列唯一供给口,归档起点后短序列 DEGRADED + `first_date` 显形;Phase 9 Task 2b 以各 kind `first_date` 为 replay manifest 的 per-feed coverage floor,floor 之前的决策点该 feed 因子 UNAVAILABLE + badge(不回填不冒充)。本 phase 交付即开始积累历史——**上线日即归档起点**,与 Phase 4 并行、不等任何 orchestration phase。
