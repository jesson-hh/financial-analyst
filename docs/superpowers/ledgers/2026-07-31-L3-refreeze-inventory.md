# L3 · 重冻爆炸半径清单 + D-1 / D-2 裁决包 (Task 1)

> 计划:`docs/superpowers/plans/2026-07-31-orchestration-L3-grants-refreeze.md` Task 1。
> 本文件是 **Task 3-4 的 allowed-red 集**、**Task 5 的再生成清单**,以及控制器裁 D-1 / D-2 的证据包。
> 可执行门:`tests/orchestration/test_l3_refreeze_gate.py` —— 本文件下面每一张被标注
> **[gate-parsed]** 的表都被那份测试逐行重新度量并断言相等。文档漂移 = 测试红。
>
> **本文件不做裁决。** §7 / §8 只摆选项与实测代价;控制器的裁决写进 §9,然后 Task 2 才能派工。

---

## 0. 度量身份 (measured at)

| 项 | 值 |
|---|---|
| HEAD | `d6182056a446330a86a5723f6feb258912afa3bc` |
| branch | `report-evidence-pack` (== main) |
| 度量日期 | 2026-08-10 |
| 计划 ground truth 的度量点 | `0c601b5` (2026-07-31) |
| 其间落地 | L2-b 全期 (`a02c8936..ff3dc1d5`) + seats/research 若干(编排外) |
| 工作树 | **脏**(~118 个并发会话未提交文件)。测试跑在脏树上,这是被接受的;本任务只新增两个文件,提交用精确 pathspec。 |

工作树里与本任务相关的脏 diff(**只读记录,一律不碰**):
`guanlan_v2/orchestration/adapters/data_world.py`(11 行,仅 `ThreadConfinedDataBackend` docstring 的 probe 传输描述更新)、
`guanlan_v2/server.py`、`tests/conftest.py`、`tests/orchestration/test_startup_binding.py`。
其中 data_world.py 的脏 diff **不含任何行为字节**(纯 docstring),不影响本文件任何度量值。

---

## 1. 前置门结果 (Step 1,逐项)

| # | 门项 | 结果 | 证据 |
|---|---|---|---|
| 1 | **L1 已落地** —— `dec.pm`/`verified_snapshot` 行 RUNNABLE | **PASS** | 真 `RunSubject@1` → `SubjectParams.project` → 真 `_assemble_params` → `InstrumentUniverseParams.model_validate` 全通,零 `param source pointer … does not resolve`;`ff26c23` 的 DECLARED-but-NOT-RUNNABLE 钉已被 L1 有意识翻转(类名 `TestVerifiedSnapshotRow…IsDeclaredNotRunnable` → `…RunnableOnlyUnderSubjectProjection`) |
| 2 | **L2-b 已落地** —— 生产 `DataRuntimeWorld` + 真 provider | **PASS** | `guanlan_v2/orchestration/adapters/data_world.py::ProductionDataWorldResolver.world_for` 是 `guanlan_v2/` 下**唯一**的 `DataRuntimeWorld(...)` 构造点;`data_runtime_provider_factory` 的零生产调用者 AST 钉已翻成「唯一调用者 = adapters/data_world.py」;两个退役 provider(dead-row / worldless)的名字在 `guanlan_v2/` 与 `tests/` 的任何 `.py` 中都不存在;`bridge.data_runtime.provider@1` 上注册出来的 incumbent 是 `ProductionDataProvider`;每 run 视图 `pipeline/assembly.py::_SubjectScopedFactories` 的 override 目标是 `production_data_provider_factory(subject_params, …)` |
| 3 | **适配器清单** | **完成** | §5。真后端 = `{verified_snapshot, news, ohlcv}`;`{instrument_names, indicators, fundamentals, signals}` **无后端**。07-29 spec 的「indicators/fundamentals 无后端适配器」仍然成立,且范围更大。 |
| 4 | **基线摘要** | **PASS · 零漂移** | §2:10 个链节点全部与 spec §1 逐字节相同。无 STOP。 |
| 5 | **指针集可满足性** | **完成 · 出现 L1-module movement** | §6:`verified_snapshot` / `news` 可满足;`indicators` / `fundamentals` **不可满足**(`InstrumentSeriesParams.symbol` 要单个 `Symbol`,闭合集只供整条 `(Symbol,)` 元组;元素指针 `/code/0` 在闭包外被显式拒绝)。 |
| 5b | **授权落点 (grant site) —— Task 1 新发现,超出简报五项** | **阻断性结构事实** | §7.0。one-to-one 不变量的 `granted` 集是在 **end-of-Phase-2 的 5 个 worker** 上求的;`pv.technical`/`text.news` 是 Phase-8 worker,不在其中。因此 D-1 三个选项**都无法直接执行**,先要裁「授权写在哪儿」。 |

**没有任何 STOP 条件触发。**

---

## 2. 链路摘要基线 (chain digests) **[gate-parsed]**

live 度量方式:直接调用九个 catalog snapshot 构造器(`load_compat_catalog().snapshot`、
`phase3_data_catalog_snapshot`、`phase3_full_catalog_snapshot`、`phase4/5/6/7_catalog_snapshot`、
`phase8_catalog_snapshot`、`phase9_catalog_snapshot`、`phase10_catalog_snapshot`)并读 `.catalog_digest`。

| node | catalog digest (measured @ HEAD) | vs spec §1 | 本期是否移动 |
|---|---|---|---|
| P2-static | `b41bf223f0dd0b05c5a4f4f7bbd32960eef90d7176a17300dbe576f6b61bf0d3` | MATCH | FROZEN |
| P3-data | `ba7086929a79de08a8d4002f36549a4e325dc4c344f615d77506adaf74454e0c` | MATCH | MOVES |
| P3-full | `c13294e5f020de542cc553d92e509d3dfea45673700373ac0c013d9115c3a773` | MATCH | MOVES |
| P4 | `aefe0cf3b12f875d4d5062db7b6d00e8ae65cf3f5081eb527d8750671b7e651b` | MATCH | MOVES |
| P5 | `42af246047adb25956df507b56aabb215baec692859772e4b4f98d776c149229` | MATCH | MOVES |
| P6 | `42af246047adb25956df507b56aabb215baec692859772e4b4f98d776c149229` | MATCH(恒等节点,== P5) | MOVES |
| P7 | `c760df02763b4342096d07df4663e8880c91cf49715a243bcf22d5dbe1fd9a96` | MATCH | MOVES |
| P8 | `7f00dde4f78761cc4a5c991b7717b9002ea82d7d86d31ab007d4af80f0112c91` | MATCH | MOVES |
| P9 | `0c48db784ddcde2f094c7089fcf6a87c251b3ea8e321b1f231dd4177a092a21a` | MATCH | MOVES |
| P10 | `ff4cdc6105ab17e53da8b4d7e28a66695f1c9943db899290a354fa05a635ff16` | MATCH | MOVES |

**零漂移。** 计划里那条「任一 digest 与 spec §1 不同即整份 07-29 侦察作废、全部计数必须重推」的最高风险假设,
在 HEAD 上实测成立:L2-b 的 35 个提交没有动过密封链的任何一位。

### 2.1 L2-b 有意识重冻过的两个摘要 —— **不属于本链**

| 摘要 | 旧值 → 新值 | 归属提交 | 是不是链摘要 |
|---|---|---|---|
| 生产数据世界 `source_registry_digest` | `0db2421b` → `701be202` → `bb81979a` | L2-b Task 1b `c6bb0a8`;Task 9 addendum `8d7210f1` | **否** —— 它是 `ProductionDataWorldRecipe` 的配方摘要,唯一 golden 落点是 `tests/orchestration/golden/production_data_registry_manifest_v1.json:4` |
| 生产数据世界 `routing_snapshot_digest` | `7b49aa16` → `b2897dd2` → `1f47a283` | 同上 | **否** —— 只出现在账本/报告,不进 golden |

HEAD 实测:`source_registry = bb81979af8824a99574c67b9ef6176720568745a8e58772afce9ea755d915aca`、
`routing = 1f47a2830b21281e078539243cd40c6b5aeb336dd06d407224c9b0a1c7d00c77`、
`source_config = 76c96f67e39018a86f3c7c59fd6f6040670c3698a0239ed322249593f904248a`(未动)。
**归属清楚,不是无主漂移,因此不是 STOP。** 本期重冻不涉及这两个值(除非 D-1 裁决改动源注册表本身)。

### 2.2 计数对账 (measured vs spec 16/42/11)

| 口径 | 实测 | spec §1 | 结论 |
|---|---|---|---|
| 文件数(至少携带一个 MOVES 字面量) | **16** = 9 golden manifest + 7 handoff 测试 | 16(9+7) | **完全一致** |
| 字面量数(golden + handoff 家族内全部链摘要出现次数,含不动的 P2) | **42** | 42 | **完全一致** |
| 链摘要出现次数(全 `tests/` 范围) | 43 | — | 多出的 1 处 = `tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json:11`,只携带**不动**的 P2,不在 golden/handoff 家族内 |
| MOVES 字面量数 | 38 | — | 42 − 4 处 P2(#1 / #3 / #26 / #37) |
| 链摘要 **值** 的种数 | **9**(P6 == P5) | — | — |
| 「11 个 digest」 | 见下 | 11 | **对账**:9 个链 catalog 摘要 + 2 个随绑定字节移动的数据桥材料摘要(`bridge.data_runtime.prefetch@1` `0c9d38a7…`、`bridge.data_runtime.descriptor@1` `79706448…`)= **11 个进入 Task-5 再生成范围的摘要身份**。这是我能实测出的唯一落在 11 上的划分,记录为对账假设而非对 07-29 原意的断言;因为**每个值都与 spec 逐字节相同**,该差异是计数口径,不是漂移。 |

### 2.3 四个数据桥材料摘要 —— 计划里说「四个都会动」,实测是**两个**

`理由` 列写的是**实际度量方式**,不是解释:

* **移动 半边 = 构造法。** gate 真的用「多一条候选行」的绑定重建 prefetch 材料与 descriptor
  (`_row_augmented_materials`,与 §7.0 那条 one-to-one 证明共用同一个构造点),再与密封 ref 比 —— 摘要确实不同。
* **不动 半边 = 独立性断言 + 源头钉。** 断言密封绑定的序列化**和**行增广绑定的序列化
  都**不出现在** provider/analyzer 的材料字节里(以 prefetch 材料作**阳性对照** —— 它确实内嵌了密封序列化,
  证明这个 `in` 探针不是恒真);再在源码上钉 provider/analyzer 走 `raw=_PROVIDER_BYTES` / `raw=_ANALYZER_BYTES`
  两个模块常量,而 prefetch 走 `raw=config_bytes`(即 `serialize_prefetch_binding`)。
  **明确不用的做法:**拿材料自己的字节重建再比摘要 —— 那是恒等式(`build_text_material` 是纯函数,
  绑定根本不进入计算),任何伪造都翻不红它。那是本清单第一版的写法,已被复审判定「证明不了任何东西」并改掉。

| 材料 | 摘要 @ HEAD | D-1/D-2 改绑定后是否移动 | 理由(度量方式) |
|---|---|---|---|
| `bridge.data_runtime.provider@1` | `3a1e727b2e3f761bfdecadf919dc678bc8dfd19b993495ca5dd223ecf3d8e065` | **不动** | 独立性:两份绑定序列化均不在其字节内 + 源头钉 `raw=_PROVIDER_BYTES` |
| `bridge.data_runtime.analyzer@1` | `3f93fdf29d28f26676e7291dbeeb1d6e3b899265862940bf5c16ae477aeb1968` | **不动** | 同上,源头钉 `raw=_ANALYZER_BYTES` |
| `bridge.data_runtime.prefetch@1` | `0c9d38a76099dced0d954066539f72112a1cff7989a81c5f39b3793ab5d61172` | **移动** | 构造法:行增广绑定重建后摘要 != 密封摘要(且它是独立性探针的阳性对照) |
| `bridge.data_runtime.descriptor@1` | `797064488e810f2ae86e8f2ed49e1b5e528dfddcf6ae9958192d3df63a46c058` | **移动** | 构造法:内嵌 `config_ref`,随 prefetch 一起重建后摘要 != 密封摘要 |

(provider/analyzer 前 8 位与计划正文引用的 `3a1e727b…` / `3f93fdf2…` 一致,交叉验证通过。)

---

## 3. 字面量清单 —— 表内 **43 行** = 42 处家族内链字面量 + 1 处家族外(#2);**必改文件 16 个** **[gate-parsed]**

> 表按 1..43 连续编号,**行号即行号**,Task 5 操作员按编号逐行走即可,不需要跨表换算。
> spec §1 的「42 处」= 本表 43 行**减去**家族外的 #2(`fixtures/…json:11`,且它是 FROZEN);
> spec §1 的「16 文件」= `class=MOVES` 行去重后的文件数(9 golden + 7 handoff)。
> 两个数都由 gate 从本表现算并断言,不是手抄。

`class` 列:`FROZEN` = P2 静态目录摘要,本期**必须验证其不动**;`MOVES` = Task 5 必须逐处重写。
`family` 列:`golden` / `handoff` = spec §1 的 16 文件家族;`other` = 家族外(只 1 处,且是 FROZEN)。

| # | file | line | node | digest8 | class | family |
|---|---|---|---|---|---|---|
| 1 | `tests/orchestration/data/test_phase_handoff.py` | 181 | P2-static | b41bf223 | FROZEN | handoff |
| 2 | `tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json` | 11 | P2-static | b41bf223 | FROZEN | other |
| 3 | `tests/orchestration/golden/data_catalog_manifest_v1.json` | 4 | P2-static | b41bf223 | FROZEN | golden |
| 4 | `tests/orchestration/golden/data_catalog_manifest_v1.json` | 6 | P3-data | ba708692 | MOVES | golden |
| 5 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 4 | P9 | 0c48db78 | MOVES | golden |
| 6 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 6 | P10 | ff4cdc61 | MOVES | golden |
| 7 | `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` | 4 | P3-data | ba708692 | MOVES | golden |
| 8 | `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` | 6 | P3-full | c13294e5 | MOVES | golden |
| 9 | `tests/orchestration/golden/phase4_catalog_manifest_v1.json` | 4 | P3-full | c13294e5 | MOVES | golden |
| 10 | `tests/orchestration/golden/phase4_catalog_manifest_v1.json` | 6 | P4 | aefe0cf3 | MOVES | golden |
| 11 | `tests/orchestration/golden/phase5_catalog_manifest_v1.json` | 4 | P4 | aefe0cf3 | MOVES | golden |
| 12 | `tests/orchestration/golden/phase5_catalog_manifest_v1.json` | 6 | P5-P6 | 42af2460 | MOVES | golden |
| 13 | `tests/orchestration/golden/phase6_catalog_manifest_v1.json` | 4 | P5-P6 | 42af2460 | MOVES | golden |
| 14 | `tests/orchestration/golden/phase6_catalog_manifest_v1.json` | 5 | P5-P6 | 42af2460 | MOVES | golden |
| 15 | `tests/orchestration/golden/phase7_catalog_manifest_v1.json` | 4 | P5-P6 | 42af2460 | MOVES | golden |
| 16 | `tests/orchestration/golden/phase7_catalog_manifest_v1.json` | 6 | P7 | c760df02 | MOVES | golden |
| 17 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 4 | P7 | c760df02 | MOVES | golden |
| 18 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 6 | P8 | 7f00dde4 | MOVES | golden |
| 19 | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | 4 | P8 | 7f00dde4 | MOVES | golden |
| 20 | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | 6 | P9 | 0c48db78 | MOVES | golden |
| 21 | `tests/orchestration/test_phase10_handoff.py` | 123 | P8 | 7f00dde4 | MOVES | handoff |
| 22 | `tests/orchestration/test_phase10_handoff.py` | 127 | P9 | 0c48db78 | MOVES | handoff |
| 23 | `tests/orchestration/test_phase4_handoff.py` | 160 | P3-full | c13294e5 | MOVES | handoff |
| 24 | `tests/orchestration/test_phase5_handoff.py` | 217 | P3-full | c13294e5 | MOVES | handoff |
| 25 | `tests/orchestration/test_phase5_handoff.py` | 223 | P4 | aefe0cf3 | MOVES | handoff |
| 26 | `tests/orchestration/test_phase6_handoff.py` | 246 | P2-static | b41bf223 | FROZEN | handoff |
| 27 | `tests/orchestration/test_phase6_handoff.py` | 249 | P3-data | ba708692 | MOVES | handoff |
| 28 | `tests/orchestration/test_phase6_handoff.py` | 252 | P3-full | c13294e5 | MOVES | handoff |
| 29 | `tests/orchestration/test_phase6_handoff.py` | 255 | P4 | aefe0cf3 | MOVES | handoff |
| 30 | `tests/orchestration/test_phase6_handoff.py` | 258 | P5-P6 | 42af2460 | MOVES | handoff |
| 31 | `tests/orchestration/test_phase7_handoff.py` | 207 | P4 | aefe0cf3 | MOVES | handoff |
| 32 | `tests/orchestration/test_phase7_handoff.py` | 210 | P5-P6 | 42af2460 | MOVES | handoff |
| 33 | `tests/orchestration/test_phase8_handoff.py` | 228 | P3-full | c13294e5 | MOVES | handoff |
| 34 | `tests/orchestration/test_phase8_handoff.py` | 247 | P4 | aefe0cf3 | MOVES | handoff |
| 35 | `tests/orchestration/test_phase8_handoff.py` | 250 | P5-P6 | 42af2460 | MOVES | handoff |
| 36 | `tests/orchestration/test_phase8_handoff.py` | 254 | P7 | c760df02 | MOVES | handoff |
| 37 | `tests/orchestration/test_phase9_handoff.py` | 299 | P2-static | b41bf223 | FROZEN | handoff |
| 38 | `tests/orchestration/test_phase9_handoff.py` | 302 | P3-data | ba708692 | MOVES | handoff |
| 39 | `tests/orchestration/test_phase9_handoff.py` | 305 | P3-full | c13294e5 | MOVES | handoff |
| 40 | `tests/orchestration/test_phase9_handoff.py` | 308 | P4 | aefe0cf3 | MOVES | handoff |
| 41 | `tests/orchestration/test_phase9_handoff.py` | 311 | P5-P6 | 42af2460 | MOVES | handoff |
| 42 | `tests/orchestration/test_phase9_handoff.py` | 315 | P7 | c760df02 | MOVES | handoff |
| 43 | `tests/orchestration/test_phase9_handoff.py` | 318 | P8 | 7f00dde4 | MOVES | handoff |

**16 个必改文件**(去重后的 MOVES 承载文件):
`golden/data_catalog_manifest_v1.json`、`golden/phase3_full_catalog_manifest_v1.json`、
`golden/phase4_catalog_manifest_v1.json`、`golden/phase5_catalog_manifest_v1.json`、
`golden/phase6_catalog_manifest_v1.json`、`golden/phase7_catalog_manifest_v1.json`、
`golden/phase8_catalog_manifest_v1.json`、`golden/phase9_catalog_manifest_v1.json`、
`golden/phase10_catalog_manifest_v1.json`、
`test_phase4_handoff.py`、`test_phase5_handoff.py`、`test_phase6_handoff.py`、
`test_phase7_handoff.py`、`test_phase8_handoff.py`、`test_phase9_handoff.py`、
`test_phase10_handoff.py`。

> **注意 `tests/orchestration/data/test_phase_handoff.py:181` 与 `fixtures/stock_deep_dive_equivalence_v1.json:11`:**
> 两处只钉 P2,**必须保持不动**。Task 5 若顺手把它们也重写了,就是把「P2 不动」这条唯一的
> 上游锚点也移了 —— 那是本期唯一不许碰的链节点。

---

## 4. 兄弟计划钉 + 支持拒绝钉 —— Task 3-4 的 allowed-red 集 **[gate-parsed]**

`verdict` 是**封闭词表**,只有三个取值(gate 断言:任何第四种拼法 = 红):
`FLIP` = D-1/D-2 一旦授权就必然变红,Task 3-5 必须有意识翻转并留双向证据;
`FLIP-IF` = 只在某个具体裁决下变红(条件写在备注);
`GREEN-VERIFY` = 不该变红,但每次红窗都要显式复核(它变红说明改动越界)。

**表的契约是 `anchor` 列,不是 `line` 列。** gate 断言:每行的 `file` 存在,且 `anchor` 字符串
**出现在该文件中**(全文件匹配,不按行号)—— 所以上游代码上下移动不会让这张表假红,而钉被
**删掉或改名**会立刻红。`line` 列只是给人用的导航提示,允许漂移,不参与断言。
gate 另外断言解析出的行数 == §4 表里编号行的行数,所以没有哪一行能被正则悄悄跳过。

| # | file | line(s) | anchor | verdict | 钉 / 备注 |
|---|---|---|---|---|---|
| 1 | `tests/orchestration/test_pipeline_deep_preset.py` | 1042 | `test_the_report_is_honestly_unsupported_naming_the_data_grant_gap` | FLIP | 断言 `report.supported is False` 且 unmet worker 恰为 `{pv.technical, text.news}`。授权后 supported 转 True(或 unmet 集缩小)—— 本钉正是「诚实拒绝」的化身,必须双向翻。 |
| 2 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | 327 | `test_the_untouched_ten_node_baseline_is_still_honestly_unsupported` | FLIP | 同 #1;它同时是减证据预设「存在理由」的回归半边。 |
| 3 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | 344 | `test_the_two_dropped_nodes_are_exactly_the_unmet_ones` | FLIP | 断言 `full_ids - reduced_ids == unmet_nodes`。授权后 unmet 集空,等式两边同时塌;翻转时必须换成新的因果断言,不是删掉。 |
| 4 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | 321 | `test_the_reduced_draft_is_runtime_supported` | GREEN-VERIFY | 减证据预设本来就绿;授权不该动它。它变红 = 授权改到了八节点通路上。 |
| 5 | `tests/orchestration/test_runtime_support.py` | 595 | `tool_calls_required_unmet` | GREEN-VERIFY | 通用算术钉(合成 worker,非两位读者)。算术本身不变;变红说明动了 `check_runtime_support` 里 `tool_calls_required_unmet` 那条判据(`tc is ToolCallRequirement.REQUIRED and min_sum < 1`)。**按符号写,不写行号** —— L3 Task 2 的插入已经让原来记的 `:673` 失效,而 gate 的锚点检查本来就是「符号在文件里」而非按行,备注也照此对齐。 |
| 6 | `tests/orchestration/data/test_phase_handoff.py` | 828 | `tool_calls_required_unmet` | GREEN-VERIFY | Phase-3 handoff 的存在性钉。同 #5。 |
| 7 | `tests/orchestration/test_pv_aux_nodes.py` | 26, 479, 546, 566 | `AuxDataUngranted` | FLIP-IF | **只在 D-1 给 `pv.price_action` / `pv.microstructure` 授行时翻。** 今天的 D-1 候选是 `pv.technical` / `text.news`,与这两个 aux 座席**不是同一批 worker**(见 §7.0 的 17 条 grant 全表)。若 D-1 只授两位读者,本组保持绿。 |
| 8 | `tests/orchestration/test_pv_aux_nodes.py` | 27, 472 | `aux_data_ungranted` | FLIP-IF | 同 #7(类型化拒绝码的那一半)。 |
| 9 | `tests/orchestration/test_data_world_recipe.py` | 755 | `test_every_granted_prefetch_row_is_within_the_supported_set` | FLIP | **必然翻。** 任何新增行都会动它的 `granted == {verified_snapshot}` 那一行,**即使新方法在支持集内**。若授的方法在支持集**外**(`indicators`/`fundamentals`),更早的 `granted ⊆ {verified_snapshot, news, ohlcv}` 那一行会**先**红 —— 这是 L2-b 设计的第一道拦截,不许绕。 |
| 10 | `tests/orchestration/test_data_world_recipe.py` | 155 | `test_every_sealed_prefetch_row_route_equality` | GREEN-VERIFY | 每条新行都会进入这个迭代。七方法默认路由都已注册为单条 `(surface.source_ref, spec.capability_ref)`,新行照同一配方冻结路由就应保持绿;变红 = 新行 `frozen_route` 写错了。 |
| 11 | `tests/orchestration/test_data_world_recipe.py` | 165 | `test_all_seven_methods_have_the_single_surface_route` | GREEN-VERIFY | 与新行无关(全七方法),列在这里作为红窗越界探针。 |
| 12 | `tests/orchestration/test_data_world_recipe.py` | 179 | `test_the_real_session_cross_resolves_the_production_registry` | GREEN-VERIFY | 用真 `_frozen_route_for` 交叉解析首行;新行进来后应仍绿。 |
| 13 | `tests/orchestration/test_data_world_recipe.py` | 738 | `test_unsupported_method_refuses_through_production_staging` | FLIP-IF | 只有 D-1 选 **B**(建 `indicators` 后端)才翻;A/C 下保持绿。 |
| 14 | `tests/orchestration/data/test_data_catalog.py` | 179 | `test_grant_without_prefetch_row_fails` | GREEN-VERIFY | one-to-one 不变量的「有授权无行」方向。不变量**字节不动**(计划红线);变红 = 有人改了 `catalog.py:803` 的判据。 |
| 15 | `tests/orchestration/data/test_data_catalog.py` | 185 | `test_prefetch_row_without_grant_fails` | GREEN-VERIFY | 同 #14 的「有行无授权」方向。注意:它用的是 `dec.research_mgr`(Phase-3 内、未授权),与 §7.0 用 `pv.technical`(Phase-3 外)证的不是同一件事。 |
| 16 | `tests/orchestration/test_pipeline_deep_preset.py` | 135 | `d7c5092fda61ca242de3fb91f48897e06fcc2e8a5836bfae80aae23e6e08a918` | FLIP-IF | 深度预设 record digest。只在 Task 5 改预设描述里的 `pv.microstructure` 过陈述时移动;不是链摘要,但和链一起在本期唯一的重冻窗口里动。 |
| 17 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | 130 | `d7c5092fda61ca242de3fb91f48897e06fcc2e8a5836bfae80aae23e6e08a918` | FLIP-IF | 同 #16(第 2 处)。 |
| 18 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 27 | `d7c5092fda61ca242de3fb91f48897e06fcc2e8a5836bfae80aae23e6e08a918` | FLIP-IF | 同 #16(第 3 处,golden 侧)。 |
| 19 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 153 | `3aacbf8be000b9e5e5b061ebb38eb86d9299cdfee4b7d190aa08e3def04357ab` | FLIP-IF | `pv.technical` 的 WorkerSpec 语义摘要。只在 D-1 选 A(收窄 allowlist)或 D-2 选 B(改 `tool_calls`)时移动。**P8 worker 摘要本来就在本期重冻范围内**,零额外爆炸半径。 |
| 20 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 161 | `2794a57155bdf312a5f48ee4bdd3554c6d2e32920706fbb5d79b5bfdc78fcbea` | FLIP-IF | `text.news` 的 WorkerSpec 语义摘要。同 #19。 |
| 21 | `guanlan_v2/orchestration/pipeline/api.py` | 1188 | `permanent-honest grant-gap ruling` | FLIP | 文档钉:「sealed Phase-3 data-prefetch grants still cover `dec.pm` only … support-refuses with `tool_calls_required_unmet`」。授权后这段话变成假的,Task 5 必须改字。 |
| 22 | `guanlan_v2/orchestration/pipeline/deep_decide.py` | 333 | `is a real product, not a fallback` | FLIP | `PresetSelectionRefused` docstring —— 减证据预设「存在理由」的散文半边。同 #21。 |
| 23 | `guanlan_v2/orchestration/pipeline/live_decide.py` | 1157, 1168 | `SUPPORT-REFUSED at admission` | FLIP | 「the deep run is still SUPPORT-REFUSED at admission」。同 #21。 |
| 24 | `guanlan_v2/orchestration/pipeline/pv_aux.py` | 197 | `sealed prefetch binding grants it no row (L3)` | FLIP-IF | `AuxDataUngranted` 的消息字面量。同 #7:只在授 aux 座席时。 |
| 25 | `tests/orchestration/test_experience_rowless_admission.py` | 246 | `test_no_sealed_worker_can_trip_the_new_refusal` | GREEN-VERIFY | L3 Task 2 早拒绝的**越界探针**(本行由 Task 2 补入,裁决见 §9 的 “RULING — dec.research_mgr experience row”)。它断言「靠读类目 activate 到某个声明了能力的桥、且完全无授权」的密封 worker 集恰为 `{dec.research_mgr: FORBIDDEN}`。D-1 只收窄 `pv.technical`/`text.news` 的 data 侧,两位都不声明 `experience_cases`,所以本行**不该红**。**Task 3-4 期间它变红 = 收窄/授权动到了某个 worker 的 `read_categories` 或 `capability_allowlist`,即越界信号按设计生效 —— STOP,上报。** **耦合提示:**同文件的 `TestRunnableRequiredWorkerNotRefused`(min_sum≥1 不早拒的那条裁决钉)**复用 `test_runtime_support.py` 的私有合成桥手法**(`_Analyzer` / `_BridgeSpec` / `build_scenario`)—— 有意「复用而非重新推导」(密封目录表达不出「一个节点两条桥、边界不同」),代价是**改动该手法的签名会同时打断两个套件**,动它时两边一起看。 |

| 26 | `tests/orchestration/test_lane_batch_pv.py` | 128, 187 | `test_worker_spec_matrix` | FLIP | **D-1 Option A 收窄的行为半边(Task 3 实测补入,控制器已裁)。** `:187` 逐字断言 `tuple(c.id for c in w.capability_allowlist) == exp["caps"]`,而 `:128` 的期望是 `("cap.data.indicators", "cap.data.verified_snapshot")`。收窄后必红。§4 #19 只钉了 golden 侧的语义摘要,**行为侧的这条钉 Task 1 未枚举** —— 红窗清单在此之前是不完整的。Task 5 有意识改期望值,不是删钉。 |
| 27 | `tests/orchestration/test_lane_batch_text.py` | 65, 127 | `test_worker_spec_matrix` | FLIP | 同 #26 的 `text.news` 半边:`:127` 同一条相等断言,`:65` 期望 `("cap.data.fundamentals", "cap.data.news")`。对应 §4 #20 的行为侧。 |
| 28 | `tests/orchestration/test_lane_batch_text.py` | 152-154 | `test_allowlist_broadening_applied` | FLIP | `:154` `assert "cap.data.fundamentals" in {…text.news allowlist}`。**注意它是「上一次评审过的扩权」的档案钉**,D-1 现在有意识地把那次扩权反转 —— Task 5 **改写**这条钉使其记录新的裁决,**绝不删除**:删掉就等于抹掉「曾经授过、后来按逐能力论证收回」这段评审史。 |
| 29 | `tests/orchestration/test_lane_batch_text.py` | 160-166 | `test_promise_supply_lint_clean` | FLIP-IF | **不只是钉,是真缺口。** Task 3 实测:收窄后 `lint_skill_supply` 对 `text.news` 返回 `skill promises tool 'ww_f10' but no allowlisted capability supplies it (allowlist capabilities: ['cap.data.news'])`(收窄前为 `()`)。**该缺口是控制器已裁的 Task-4 义务**:Task 4 从 `guanlan_v2/orchestration/skills/text.news/SKILL.md` 去掉 `ww_f10` 承诺(item 5 密封材料文本对齐),本钉届时**无需改测试即自愈**,并且**必须在 Task 5 出口重新为绿**。实测不对称:`pv.technical` 的同一 lint 收窄前后**都是空元组**,其 SKILL.md 不承诺任何只由 `indicators` 供给的工具 —— D-1 要求的逐能力论证在两处收窄上都已闭合。Task 4 的 SKILL.md 编辑会带动 skill 材料摘要 + P8 skill manifest 位移,§3 字面量表可能需要一次 Task-4 实测扩表(Task 4 量,Task 5 再生成)。 |

> **红窗纪律(计划 Global Constraints):** Task 3-4 期间,`tests/orchestration` 全套只允许在
> 本表 `FLIP` / `FLIP-IF`(且其条件成立)的条目上红。**本表之外的任何一处红 = STOP,上报。**

---

## 5. 适配器清单(按 source id) **[gate-parsed]**

L2-b 世界在 gate 时刻注册的适配器:**恰好一个** ——
`production_data_adapters() == {"guanlan.datafeed": LiveClientSource(calendar=<committed>)}`,
键就是密封面 `phase3_data_surface().source_ref.id`。
配方的 `method_selections` 把**七个方法全部**指向 `guanlan.datafeed`,但适配器只服务其中三个。

度量方式(行为式,不读私有常量):对每个方法,用配方冻结的默认路由在**真** `ThreadConfinedDataBackend`
上 stage,适配器是**真** `LiveClientSource`,其 `probe_fn` 换成一个哨兵异常。
到达传输 = 有真后端;在到达传输前抛 `RoutingConfigurationError("… is not bound to this source")` = 无后端。

| method | route source id | real backend | method optional | params_cls | pointer-satisfiable |
|---|---|---|---|---|---|
| fundamentals | guanlan.datafeed | NO | YES | InstrumentSeriesParams | NO |
| indicators | guanlan.datafeed | NO | YES | InstrumentSeriesParams | NO |
| instrument_names | guanlan.datafeed | NO | NO | InstrumentUniverseParams | YES |
| news | guanlan.datafeed | YES | YES | InstrumentUniverseParams | YES |
| ohlcv | guanlan.datafeed | YES | NO | InstrumentSeriesParams | NO |
| signals | guanlan.datafeed | NO | YES | InstrumentSeriesParams | NO |
| verified_snapshot | guanlan.datafeed | YES | NO | InstrumentUniverseParams | YES |

- **真后端集 = `{news, ohlcv, verified_snapshot}`** —— 与 `LiveClientSource` 的封闭支持集完全一致,
  也与 L2-b 的 `granted ⊆ {verified_snapshot, news, ohlcv}` 守卫一致。
- **07-29 spec 的「indicators/fundamentals 无后端适配器」在 HEAD 上仍然成立**,且实际无后端的是四个方法。
- `method optional` 列取自真方法 spec 的 `optional` 位,它决定路由耗尽时的行为(见 §8)。

---

## 6. 指针集可满足性(D-1 输入,seam-review finding 4) **[gate-parsed:与 §5 同表]**

L1 的闭合指针集:`SUBJECT_PARAM_POINTERS == frozenset({"/asof_date", "/code"})`,
主体文档 = `{"asof_date": <aware UTC ISO str>, "code": (Symbol,)}`(单例宇宙语义)。

用 L1 的真 round-trip 手法(真 `_assemble_params` + 真 `params_cls.model_validate`,候选形状的行 +
从真 `RunSubject@1` 投影出来的主体)逐方法验:

| 候选方法 | 归属候选 worker | params_cls | 结论 |
|---|---|---|---|
| `verified_snapshot` | `pv.technical` | `InstrumentUniverseParams` | **可满足**(`/as_of ← /asof_date`、`/symbols ← /code`,与已密封的 `dec.pm` 行同形)|
| `news` | `text.news` | `InstrumentUniverseParams` | **可满足**(同一 params 类,同一对绑定)|
| `indicators` | `pv.technical` | `InstrumentSeriesParams` | **不可满足 → L1-module movement** |
| `fundamentals` | `text.news` | `InstrumentSeriesParams` | **不可满足 → L1-module movement** |

### 6.1 不可满足的确切原因(实测,两条独立)

1. **元素 vs 集合。** `InstrumentSeriesParams.symbol` 是**单个** `Symbol`;闭合集只供整条
   `/code = (Symbol,)`。把 `/symbol ← /code` 绑上去,`_assemble_params` 会通过,但
   `model_validate` 抛
   `Input should be a valid dictionary or instance of Symbol [type=model_type, input_value=(Symbol(code=…),), input_type=tuple]`。
   想取元素就要写 `/code/0`,而闭合集是**精确成员判定**(`pointer in SUBJECT_PARAM_POINTERS`),
   于是 `_assemble_params` 用它的 cause-named 拒绝把这条路堵死:
   > `param source pointer '/code/0' does not resolve … A subject projection IS bound, but it serves only the closed pointer set '/asof_date', '/code' -- this pointer is outside that closure and is refused, never guessed.`
2. **一个时点 vs 一个窗口。** `InstrumentSeriesParams` 要 `start` **和** `end` 两个 aware ISO;
   闭合集只有 `/asof_date` 一个时间指针。两个都绑到 `/asof_date` 在结构上能过窗口校验
   (`end >= start`),但那是一个**零长度窗口**,对时间序列读是无意义的 —— 这不是可满足性,
   是把无意义伪装成可满足。诚实结论:需要第二个时间指针。

### 6.2 这项代价的定价

给 `indicators` / `fundamentals` 授行,**必须先做 L1 模块改动**:
把 `SUBJECT_PARAM_POINTERS` 从两指针扩到(至少)`{"/asof_date", "/code", "/symbol", "/window_start"}` 之类,
并同步扩 `SubjectParams.project` 的投影配方 + `as_document()` 的闭合文档 + 它们的钉。
这是 **L1 计划的范围,不是 L3 的**,更不许在 Task 3 现场即兴。
在 D-1 的定价里它是一条**独立的、先决的**成本项。

---

## 7. RULING D-1 —— one-to-one grant granularity

### 7.0 先决问题(Task 1 实测发现):**授权写在哪儿?**

计划正文说「literal one-to-one therefore forces FOUR rows」。**实测这一步推不出来。**

`catalog.py:803` 的 `granted != rows` 判据里,`granted` 是这样求的
(`catalog.py:769-774`):

```
workers = tuple(updates_by_id.get(w.id, w) for w in phase2_snapshot.workers)
granted = {(w.id, cap.id, cap.version) for w in workers
           for cap in w.capability_allowlist if <cap is a data capability>}
```

`phase2_snapshot` 是 **end-of-Phase-2 的静态目录**,实测只有 **5 个 worker**,
里面**没有** `pv.technical`,也**没有** `text.news`(它们是 Phase-8 才加的)。
这个不变量在**整个仓库里只有这一处**,只在 `build_phase3_catalog` 里跑一次。

实测后果(两向,都在树上跑过):

| 尝试 | 结果 |
|---|---|
| 把 `"pv.technical": ("verified_snapshot",)` 加进 `_REVIEWED_INTEGRATION_GRANTS` | `phase3_data_catalog_snapshot()` 在 `catalog.py:882` 的 `next(w for w in base.workers if w.id == worker_id)` 上抛 **`StopIteration`** —— 授权机制**结构上无法命名**一个 Phase-2 之后才出现的 worker |
| 只在密封 prefetch 绑定里加一条 `pv.technical` 行(材料一并重建) | `build_phase3_catalog` 抛 **`CatalogError: worker data-capability grants and prefetch rows must be one-to-one; grants without a row: []; rows without a grant: [('pv.technical', 'cap.data.verified_snapshot', '1')]`** |

同时实测:**P8 / P10 目录上一共有 17 组 (worker, data-capability) 授权,只有 1 条行。**

```
dec.pm/verified_snapshot                pv.microstructure/{indicators,signals,verified_snapshot}
pv.price_action/ohlcv                   pv.technical/{indicators,verified_snapshot}
quant.factor/signals                    quant.fundamentals/fundamentals
quant.model/signals                      text.macro/{indicators,signals,verified_snapshot}
text.news/{fundamentals,news}            text.policy/news
text.research_report/news
```

也就是说:**one-to-one 不变量今天保护的是那 5 个 Phase-2 worker,不是这 17 组授权。**
「rows==0 对 ruling C3 无歧义」这句话,其成立范围只在 Phase-3 的 worker 作用域内。

**因此控制器必须先裁 D-1(0):L3 的授权落在哪个 catalog 节点上?**

| 落点 | 做法 | 实测代价 |
|---|---|---|
| **(0-a) 把同一条判据搬/复制到「授权 worker 真正存在」的那期** | 注意:**不能**只改 `catalog.py:769-774` 的 `workers` 推导 —— Phase-3 构建器在链上跑到时,P8 的 worker 还不存在,它**看不到**它们。唯一可行形状是在 `build_phase8_catalog_snapshot` / `build_phase10_catalog_snapshot` 处**再跑一次同一条判据**,输入换成该期的累计 worker 集 + 继承下来的数据桥绑定。 | 判据字节**可以**一字不动(复用同一函数/同一消息),动的是**求值点**。需要控制器明确区分「加一个求值点」与「放宽判据」(后者才是 Option C)。代价:一旦在 P10 求值,今天那 **17 组**授权会**一次性全部**要求配行(或在 P8 里收窄 allowlist)—— 爆炸半径远超计划设想的四行,且其中多数方法既无后端又指针不可满足(§5/§6)。 |
| **(0-b) 授权搬到 Phase-8/10 节点** | 把 prefetch 行(以及对应的 one-to-one 校验)加在 `pv.technical`/`text.news` 真正存在的那本目录上 | 但 `bridge.data_runtime.prefetch@1` 是 **Phase-3 密封材料**,descriptor 在 Phase-3 就把 `config_ref` 冻进去了;在 P8 追加行意味着**再造一份数据桥绑定材料**,是新材料、新 descriptor、新的支持分析器输入 —— 结构性新工程,不是补授权。 |
| **(0-c) 把两位 worker 的 data-capability allowlist 从 P8 拿掉,改由 Phase-3 授权** | 结构上不可能:Phase-3 的 base 目录里没有这两个 worker |
| **(0-d) 只授 `dec.pm` 之外的**已在 Phase-3 worker 集里**的 worker** | 实测:Phase-3 的 5 个 worker 里,除 `dec.pm` 外没有任何一个持有 data capability;所以这条等价于「什么都不授」 |

> **建议(仅供参考,不构成裁决):** 先裁 D-1(0)。在 (0-a) 与 (0-b) 之间,(0-a) 的代价是**一次性、可枚举、可测**的
> (17 组授权要么配行、要么在 P8 里收窄 allowlist);(0-b) 的代价是**新造一份密封材料**,
> 会在这条链上第二次引入「一个 bridge 两份 config」的形状 —— 那是本程序一直在收口的东西。
> 但两者都超出计划正文对 D-1 的描述,所以必须由控制器明确裁,而不是由 Task 3 现场选。

### 7.1 计划正文的三个选项,附实测适配器清单

以下沿用计划的 A/B/C,**前提是 D-1(0) 已裁**。

#### Option A —— 把两条 allowlist 收窄到「真有适配器」的能力

把 `pv.technical` 的 `capability_methods` 从 `("verified_snapshot", "indicators")` 收窄到 `("verified_snapshot",)`;
把 `text.news` 的从 `("news", "fundamentals")` 收窄到 `("news",)`。于是 literal one-to-one 只强制**两行**,不是四行。

| 项 | 实测 |
|---|---|
| 移动的摘要 | `phase8_catalog_manifest_v1.json` 里两个 WorkerSpec 语义摘要(`pv.technical` `3aacbf8b…`、`text.news` `2794a571…`)。**P8 worker 摘要已在本期重冻范围内**,零额外爆炸半径。 |
| 对 L2-b `granted ⊆ {verified_snapshot, news, ohlcv}` 守卫 | **不触发** —— 收窄后授的两个方法 `{verified_snapshot, news}` 都在支持集内。守卫的第二行 `granted == {"verified_snapshot"}` 会红(必然,任何新行都会),必须有意识改成 `{"verified_snapshot", "news"}`。 |
| 对指针集 | **零 L1 movement** —— 两个方法都是 `InstrumentUniverseParams`,与已密封的 `dec.pm` 行同形(§6)。 |
| 风险(计划已点名) | 收窄的是**已评审过的 P8 授权**。`text.news` 丢掉 `fundamentals` 意味着一个承诺 `ww_f10` 的新闻读者不再持有基本面能力 —— 必须**逐能力论证**,不许一刀切。实测支持这个论证:`fundamentals` 既**无后端**又**指针不可满足**,所以今天持有它等于持有一个结构上无法行使的权限。`indicators` 同理。 |

#### Option B —— 补建缺失的适配器(`indicators` 和/或 `fundamentals` 后端)

| 项 | 实测 |
|---|---|
| 真实工作量 | 在 `LiveClientSource` 里为这两个方法各绑一个 `live_client` facade 源(今天 `_METHOD_LIVE_SOURCE` 只映了三条),并扩 `_SUPPORTED_METHOD_IDS`。**在 L3 的围栏之外。** |
| 对 L2-b 守卫 | **守卫本身就是这个选项的可执行代价**:`granted ⊆ {verified_snapshot, news, ohlcv}` 必须先**有意识翻转**(适配器先长,守卫后翻),授权才能上线。顺序反了就是让一条读不到数的行进生产。 |
| **额外的、计划没算的代价** | 即使后端建好了,`indicators`/`fundamentals` 仍然**指针不可满足**(§6):`InstrumentSeriesParams` 要单个 `Symbol` + 两个时间点,闭合集给不出。所以 B **必然附带一次 L1 模块改动**(扩 `SUBJECT_PARAM_POINTERS` + 投影配方 + 其钉)。这是**两个计划的开工**,不是一个。 |

#### Option C —— 放宽 one-to-one 不变量

计划**建议反对**,实测支持这个反对:该不变量是 `catalog.py:803` 唯一一处,是 rows==0 对 ruling C3 无歧义的**唯一**依据,
且已被变异证明承重(§4 #14/#15 两向钉)。放宽它会在两个方向上重新打开静默授权洞。

**对 L2-b supported-methods 守卫的可执行代价(计划 Step 3 要求逐选项说明):**
C **让守卫继续站着,但不再保护** —— 被容忍的「有授权无行」grant 根本不产生 prefetch 行,而
`test_data_world_recipe.py:755` 的守卫是**在行上**求 `granted`(`{row.method_ref.id for row in
prefetch_binding.operations}`),所以这类 grant **从守卫下面绕过去**:守卫保持绿,而未经适配器
检查的能力已经授出。对照:A 只要收窄后的方法落在支持集内就不触发第一行断言(但必然要改
`granted == {verified_snapshot}` 那一行);B 的可执行代价**就是**那道守卫(适配器先长、守卫后有意识翻)。

**另外要注意:§7.0 的 (0-a) 不是 C** —— (0-a) 加的是判据的**求值点**,判据字节不动;C 改的是判据本身。

### 7.2 建议(不构成裁决)

- 先裁 **D-1(0) 授权落点**。
- 在 A/B/C 之间,实测证据指向 **A**:两个「缺适配器」的方法恰好也是两个「指针不可满足」的方法,
  于是 A 的收窄不是削权,而是**把一个结构上无法行使的权限从纸面上拿掉**;
  而 B 的真实代价是「补后端 + 扩 L1 闭合指针集」两件事,都在 L3 围栏外。
- 无论怎么裁,**不变量的判据字节保持不动**。

---

## 8. RULING D-2 —— `always_invoke` 与断供语义

### 8.1 算术事实(实测)

- 两位 worker 的 `evidence_policy.tool_calls` 都是 **`REQUIRED`**(`lane_catalog.py`)。
- `DataPrefetchOperation.row_min_finalized` 只有在 `invocation_mode == "always_invoke"` **且**
  `success_requires_finalized_call is True` 时才是 1,否则 0(`catalog.py:191-195`)。
  从不由 `tool_calls=REQUIRED` 推断(`catalog.py:152-155` docstring)。
- 已密封的 `dec.pm` 行实测是 **`cache_or_invoke` + `success_requires_finalized_call=False` → `row_min_finalized=0`**。
  它之所以不产生 `tool_calls_required_unmet`,是因为 **`dec.pm` 的 `tool_calls` 是 `OPTIONAL`**,不是因为行的形状。
  —— 这一点修正了 D-2 里「同族先例」的读法(见 8.3)。

结论不变:要让 `pv.technical` / `text.news` 清掉 `tool_calls_required_unmet`,
新行**必须**是 `always_invoke` + `success_requires_finalized_call=True`。

### 8.2 断供的生产后果 —— 实测是**两种**失败语汇,不是一种

计划正文写「a data outage becomes a hard node failure」,并说后 L2-b 的语汇
「不是 `bridge_execution_error`,后者 L2-b 在这条路上退役了」。**实测:这句话在 HEAD 上不准确。**
L2-b 退役的是**无行 worker** 的那条拒绝路径(两个 pv aux 座席现在冻结 catalog-licensed EMPTY);
**有行 worker** 的异常映射一字未动。断供实际会走两条中的一条,取决于方法的 `optional` 位:

| 方法 | `optional` | 路由耗尽时 (`data/registry.py::_exhaustion`) | 节点终态 |
|---|---|---|---|
| `verified_snapshot`(pv.technical) | **False(core)** | **抛** first retryable error | 异常逃出 `freeze_for_execution` → `worker.py:1974-1977` → `NodeStatus.FAILED`,`reason_code="bridge_execution_error"`,`error_type=<异常类名>` |
| `news`(text.news) | **True(optional)** | 建一个**诚实 UNAVAILABLE** 的 DataResult,`tool_call_record=None`,**不抛** | 桥正常 completed → `worker.py:2008-2014` 的 `_check_tool_discipline` 发现 `finalized 0 < min 1` → `NodeStatus.INCOMPLETE`,`reason_code="tool_discipline_unmet"`,reason 文本 `bridge 'data.runtime' finalized 0 tool calls (< minimum 1)` |

两者都是**硬失败**(节点不会假装成功),D-2 Option A 的实质主张成立。
但 **Task 5 必须把两条路径都钉住**,只钉一条就等于让另一半的降级路径无证据。
这是本节相对计划正文的实测修正,必须写进 Task 5 的简报。

### 8.3 同族先例的准确读法

计划引用「`verified_snapshot` 断供 ⇒ pm 节点硬失败 ⇒ deep run 诚实拒绝,L2-b 已裁接受」。
实测:pm 之所以硬失败,是因为 **`verified_snapshot` 是 core 方法**(`optional=False`),
路由耗尽必抛 —— **不是**因为 `always_invoke`(pm 的行根本不是 `always_invoke`)。
所以先例支持的是「core 方法断供 ⇒ 硬失败可接受」,把它延伸到两位 aux 读者时:
- `pv.technical`/`verified_snapshot` 是**同一条 core 路径**,先例直接适用;
- `text.news`/`news` 是 **optional 方法**,先例不直接适用 —— 它的硬失败来自
  `always_invoke + success_requires_finalized_call` 这个**新选择**本身。
  换句话说,**D-2 对 `text.news` 是一个真的新决定,不是既有裁决的延伸。**

### 8.4 选项

#### Option A —— 接受新行上的 `always_invoke` + `success_requires_finalized_call=True`

- 诚实:一个全部价值就是数据的读者,读不到东西时不许宣称成功。
- 后果有界:满十节点预设里两位都是 AUX 证据节点,经 degrade 依赖流入 bull/bear,
  主干 `sentiment→research-mgr→pm→trader` 仍然走完 —— 断供会**大声**降级(节点硬失败 + degrade 徽章),不会静默。
- **Task 5 必须为两个预设都钉住这条降级路径**,而且按 §8.2 钉**两种**失败语汇。

#### Option B —— 把两位 worker 的 `tool_calls` 从 REQUIRED 翻成 OPTIONAL,并授 `cache_or_invoke` 行

- P8 worker 摘要移动(已在本期范围内)。
- 后果:断供 = 节点「成功」但证据陈旧或为空 —— 本程序在原则上拒绝的静默降级形状。

#### 建议(不构成裁决)

**A。** 它是唯一与 spec §2.4「把拒绝变成支持,不是把拒绝变成沉默」一致的形状。
附带要求:控制器在裁 A 时请一并确认 §8.3 —— `text.news` 那一半是**新决定**,不是先例延伸。

---

## 9. 控制器裁决(2026-08-11,控制器 = 主 session;基于 0c3275a0+2840dda3+c0dcefea 的实测证据,复审 Approved 后落笔)

### D-1(0) 授权落点

**裁决:取 (0-a) 的形,附三条钉死的边界。**

1. one-to-one 判据获得**第二求值点**,位于全目录折叠处(`build_phase10_catalog_snapshot`,或 Task 3 实测发现更自然的 P8 折叠点——以真实接缝绑定,机制逐字记录进任务报告),输入 = 该期累计 worker 集 + 继承的全部预取行。
2. **P3 求值点对它的 5 个 worker 的判据字节与语义逐字保持不动**;但 P3 构建器的行侧检查需要一个作用域拆分:属于 P3-known worker 的行照旧在 P3 双向检查;属于 P3-unknown worker 的行**不许静默接受**——结转,由第二求值点强制认领(在最终折叠点仍无授权的行 = `CatalogError`,消息复用既有措辞)。这是计划 Task 3 Step 2 预授权的 correction-clause 级修订("widening a P3 builder signature"),按修订记录,不是即兴。
3. 第二求值点的判据是**方向不对称的**,编码的是本程序已有语义,不是新语义:
   - **行→授权:全集严格**——任何行必须有对应授权;特权升级洞在任何作用域都关死;
   - **授权→行:仅对 `evidence_policy.tool_calls == REQUIRED` 的 worker 强制**——一个 REQUIRED 读者的无行授权 = 结构性死座位(永远 unsupported),构建期即拒;OPTIONAL worker 的无行授权 = C3 已裁的 licensed-EMPTY 评审态,**不在本期清扫**。
4. 后果账(以 §7.0 的 17 组为底)——**本条计数经 Task 3 实测更正,见下方「修正裁决」**:~~13 组~~ **11 组** OPTIONAL paper 授权保持 licensed-EMPTY,**列入 post-L3 backlog**(附 §5/§6 测量:各自后端缺席/指针不可满足状况);2 组 REQUIRED 无行授权(`pv.technical/indicators`、`text.news/fundamentals`)按 D-1 Option A 收窄;2 组候选(`pv.technical/verified_snapshot`、`text.news/news`)配行;**1 组 REQUIRED paper(`text.policy/news`,原稿误归 OPTIONAL)按修正裁决配第三条行**;`dec.pm/verified_snapshot` 既有行不动。11+2+2+1+1 = 17。
5. **否决 (0-b)**(为一个 bridge 再造一份材料/descriptor 的形状——fact C 红线,本程序一直在收口的东西);**(0-c)/(0-d) 按实测不可行/空集否决**。新行落在**既有** P3 密封预取材料里,其 config_ref + descriptor 摘要移动——与 §2.3 实测「恰好两个材料摘要移动」一致,已在重冻清单内。

不对称的理由(记录在案):全双向强制会迫使 13 组 OPTIONAL 授权当场配行(多数后端缺席且指针不可满足)或当场收窄(13 处超出 charter 的 P8 语义修改)——把一次补授权行动膨胀成全目录清账,且每处收窄各自应得的逐能力评审会在同一个窗口里被稀释。判据不对称不是 Option C 式放宽:P3 一字不动,新求值点在从未有过强制的地方**加**强制。

### D-1 授权粒度

**裁决:Option A。**

- `pv.technical.capability_methods`: `("verified_snapshot", "indicators")` → `("verified_snapshot",)`
- `text.news.capability_methods`: `("news", "fundamentals")` → `("news",)`
- 逐能力论证(计划要求,不许一刀切):`indicators` 与 `fundamentals` 同时是「无后端」(§5)且「指针不可满足」(§6:`InstrumentSeriesParams` 需单个 `Symbol` + 两个时点,闭合集给不出)——收窄不是削权,是把一个结构上无法行使的纸面权限从纸面上拿掉。恢复它们 = 「补 LiveClientSource 后端 + 扩 L1 闭合指针集」两件围栏外工程,归未来 charter(post-L3 backlog,与 13 组 OPTIONAL paper 授权同列)。
- 于是是**两行,不是四行**。P8 两个 WorkerSpec 语义摘要(`pv.technical` `3aacbf8b…`、`text.news` `2794a571…`)移动——已在本期重冻范围,零额外爆炸半径。L2-b supported-methods 守卫的 `granted == {"verified_snapshot"}` 字面量 → `{"verified_snapshot", "news"}`,有意识翻转(§4 #9)。
- **Option B 否决**(实测定价 = 两个计划的开工:后端 + L1 模块移动,均在 L3 围栏外);**Option C 否决**(不变量判据字节全程不动——两处求值点都是;§7.0 (0-a) 改的是作用域,不是判据)。

### D-2 `always_invoke` 与断供语义

**裁决:Option A。** 两条新行 = `always_invoke` + `success_requires_finalized_call=True`(`row_min_finalized=1`)。

- 确认 §8.3:`text.news`/`news` 的硬失败语义是**新决定,不是先例延伸**——有意裁之:一个全部价值就是读数据的 REQUIRED 读者,读不到时不许宣称成功;optional 方法的 honest-UNAVAILABLE 经 tool discipline 成为 INCOMPLETE(`tool_discipline_unmet`),响亮且有界(AUX 证据节点,主干 sentiment→research-mgr→pm→trader 走完)。
- 确认 §8.2:断供落在**两种失败语汇**——core `verified_snapshot` → `bridge_execution_error`/FAILED;optional `news` → `tool_discipline_unmet`/INCOMPLETE。**Task 5 必须把两条路径、两个预设都钉住**,只钉一条 = 让另一半降级路径无证据。此实测修正随裁决进入 Task 3/5 简报;计划正文「`bridge_execution_error` 已在此路径退役」按 §8.2 勘误(该退役只覆盖无行 worker 的拒绝路径)。
- **Option B 否决**(断供 = 节点假成功 + 陈旧/空证据 = 程序原则性拒绝的静默降级形状)。

### D-1(0)/D-1 修正裁决:`text.policy/news` 配第三条行(2026-08-11,Task 3 实测触发)

Task 3 实现者以真 P8/P9/P10 快照 + 真 `data_capability_refs()` 枚举 17 组授权的 `tool_calls` 位,实测分解为:
**11 OPTIONAL paper + 1 REQUIRED paper(`text.policy/news`,`lane_catalog.py` `tool_calls=REQUIRED`)
+ 2 REQUIRED 收窄 + 2 候选配行 + 1 `dec.pm` 既有行 = 17**。
本节 item 4 原写「13 组 OPTIONAL paper」计数错一,且把 `text.policy/news` 误归 OPTIONAL——
原句已按实测更正,不再是本程序的说法。若不修正,新求值点会在每次目录构建上因
`text.policy/news`(REQUIRED、无行)抛 `CatalogError`,红溢出 §4——实现者按红窗法 STOP,正确。

**裁决:`text.policy/news` 配第三条行(实现者选项 B)。**

- **理由**:该方法有真后端(§5)且指针可满足(§6),Option A 的「拿掉无法行使的纸面权限」论证对它**不成立**——收窄 = 真削权,且不治死座位(REQUIRED + 零数据能力 = 一旦被调度即永远 `tool_calls_required_unmet` 的静默不可支持态,本程序拒绝的形状)。豁免表(把它例外在新判据之外)= 判据首日带例外 + 纸面 REQUIRED 授权继续挂账,同样拒绝。**把拒绝变成支持**是唯一同时满足「判据无例外」与「不削可行使权限」的形(spec §2.4)。
- **行形状**:与两条已裁行同形(`node_param` `/as_of ← /asof_date`、`/symbols ← /code` ⊆ 闭合集;`always_invoke` + `success_requires_finalized_call=True`)。D-2 语汇:`news` 为 optional 方法,断供 → `tool_discipline_unmet`/INCOMPLETE(§8.2 第二种)。
- **边界**:`text.policy` 不在任何 preset;本裁决**不**把它排进任何 preset,不扩 Task 7 真跑范围;其「supported」新真值仅经 DYNAMIC 计划 + 人批通道可达——与既有审批纪律一致。
- **爆炸半径(实现者实测)**:零额外——`capability_methods` 不动 → 其 P8 WorkerSpec 摘要不动;prefetch/descriptor 摘要本就移动(§2.3);supported-methods 守卫集合仍 `{verified_snapshot, news}`(§4 #9 已 FLIP)。若 grep 发现引用 text.policy 支持态的既有钉,按 §4 行格式入表(经解析器)。

**求值点确认:P8 折叠(`build_phase8_catalog_snapshot`)**,按实现者四条实测理由
(P8 = 行首次可认领的折叠;授权集 P8/P9/P10 实测恒等 17;既有 import 边零新增;单一真调用方)。
**附加一条覆盖假设钉**:post-P8 折叠(P9/P10)不新增持有 data capability 的 worker——
用测试钉死该假设;未来任何期在 P8 之后引入数据授权 worker 时此钉变红,
强制该期把求值点扩到自己的折叠处,而不是静默漏检。

### 执行注记(随裁决进 Task 3/5 简报)

- 求值点机制 = Task 3 实测绑定 + 报告记录;若唯一诚实接缝要求加宽 P3 构建器签名,按 correction-clause 修订记录(已预授权)。
- 行的形状:与已密封 `dec.pm` 行同形的 `node_param` 指针对(`/as_of ← /asof_date`、`/symbols ← /code`),⊆ `SUBJECT_PARAM_POINTERS`;逐行 `_assemble_params` + `params_cls.model_validate` round-trip(§6 手法)。
- 变异证明(Task 3 Step 3)在**新求值点**做两向 + D-2 算术;P3 既有两向钉(`test_data_catalog.py` 的 `test_grant_without_prefetch_row_fails`/`test_prefetch_row_without_grant_fails`)保持原样继续跑。

---

### RULING — dec.research_mgr experience row

(§2.1 item 2;**「控制器已裁」= 计划冻结时预裁**,执行期无需回程;本节由 L3 Task 2 落笔,
证据为 HEAD `54176b2e` 上的实测 —— 真 Phase-9 目录 + 真生产桥视图 + 真 `ExperienceBridgeSupportAnalyzer`。)

#### 裁决:Option A

**rowless-EMPTY 就是本期 `dec.research_mgr` 经验位的评审终态。** 计划正文逐字:

> **Option A (RULED — 控制器已裁): rowless-EMPTY is the reviewed end state for this phase.**
> Consistent with C3 and `4ee43ca`; the live reduced run proved research-mgr completes honestly
> with an empty contribution (4 LLM invocations settled). Deriving a real row remains blocked on
> facts the worker does not have (`ExperiencePrefetchBinding@1` feature-vector pointer) and a
> Literal conflict (`tool_calls=FORBIDDEN` vs min=1) — resolving those is a contract change beyond
> L3's fence.

#### 冲突裁决:`tool_calls=FORBIDDEN` ↔ 行的 Literal `min=1`(item 2 的「冲突裁决落文档」)

实测三条(逐条可复现):

1. `dec.research_mgr.evidence_policy.tool_calls == FORBIDDEN`;`read_categories` 含
   `experience_cases`;`capability_allowlist` **不含** `experience.retrieve@1`。
   —— 它是靠**读类目**activate 到 `experience.bridge` 的、从未被授权的读者。
2. `ExperiencePrefetchBinding@1` 的 `invocation_mode` / `success_requires_finalized_call`
   是 **Literal 钉死**的 `"always_invoke"` / `True`:实测把它们分别改成 `"cache_or_invoke"` /
   `False` 去构造,`ValidationError` 当场拒绝。于是**任何**一条经验预取行都必然让真分析器
   产出 `min_finalized_tool_calls_on_success=1` / `max_capability_invocations=1` 的摘要
   —— 用真 `ExperiencePrefetchBinding.build` 造一条 `dec.research_mgr` 候选行 + 真序列化器 +
   真分析器实测:`min=1 max=1 caps=['experience.retrieve']`。
3. 因此「给 `dec.research_mgr` 配经验行」与「它 FORBIDDEN 工具调用」在**准入期**正面对撞:
   `check_runtime_support` 的 `tool_calls_forbidden_violated`(判据 `max_sum != 0`)会当场拒掉
   这个座位(该判据本身由 `test_runtime_support.py:618` 与 `data/test_phase_handoff.py:842` 钉住)。
   这不是口味问题,是两条 Literal 之间的**结构性矛盾**。

**冲突往哪边让:本期哪边都不让 —— 不配行。** 让任一边都要动契约:

- **让 worker 一边** = 把 `dec.research_mgr` 的 `tool_calls` 从 FORBIDDEN 翻成 REQUIRED/OPTIONAL。
  代价:P8 WorkerSpec 语义摘要移动 + 一次逐能力评审;而且把一个「只吃上游产物、不外呼」的研究经理
  改成允许外呼是**产品语义变更**,不是重冻顺手捎带。
- **让绑定一边** = 改 `ExperiencePrefetchBinding@1` 的 Literal。代价:它是**注册模型**,
  pydantic class docstring 即 JSON-Schema `description`,属本计划红线点名的「不可动」那一类;
  连带 P5 schema manifest 位移。

两条都在 L3 围栏外。**Option A 因此不是「拖着」,是本期唯一不越界的诚实终态。**

**另有一条独立的先决缺口(与上面的冲突互不依赖,任一条都足以否掉配行):**
`ExperiencePrefetchBinding@1` 要求 `feature_vector_pointer` / `feature_schema_version_pointer`
两个闭合 JSON 指针,投影进**绑定输入 `market_factor_report`** 的载荷。实测:
已配行的 `market.regime` 的**唯一** input 就是 `market_factor_report`;而 `dec.research_mgr` 的
inputs 是 `sentiment(SentimentReport)` / `bullbear_transcript(DebateTranscript)` /
`upstream_ratings(UpstreamRatingsExtract)` —— **没有特征向量的源**,两个指针无处可指。
这与 §6 里 `indicators`/`fundamentals` 的「指针不可满足」是同一类结构性缺口。

#### 与 Task 2 的早拒绝(item 6)的关系:不冲突,互为另一半

Task 2 新增的准入拒绝 `rowless_read_category_unrunnable` 的**完整裁定判据**是两条,不是一条:

> `evidence_policy.tool_calls == REQUIRED` **且** `min_sum == 0`
> —— 即该节点上**没有任何**活跃桥保证它一次 finalized 调用
> (`sum(min_finalized_tool_calls_on_success) == 0`)。

`dec.research_mgr` 是 FORBIDDEN,第一条就不满足,所以本裁决的 rowless-EMPTY 终态**原样保留**,并被
`tests/orchestration/test_experience_rowless_admission.py::TestC3RowlessReaderPreserved` 正向钉住
(真 `PlanAdmissionService` 准入通过 + 真 provider/真 gateway 上零调用、零 ToolCallRecord、
零证据写、零 untrusted block)。

**判据的理由(2026-08-11 控制器裁决,复审第一轮把先前写法推翻,此处按裁决重写):**
item 6 的授权是**晚失败 → 早拒绝的「转换」**,不是新增一类拒绝 —— **一个不会在 gateway 上失败的
worker,就不许在准入期被拒**。`tool_calls == REQUIRED` 是 **worker 级**纪律,由该节点的**求和**
边界满足:一个 REQUIRED 座位只要有 granted + 配行的**兄弟桥**把 `min_sum` 抬到 ≥ 1,它就**跑得完**,
而它那条空的 category 桥贡献恰恰**就是 C3 已裁的 licensed-EMPTY**,不构成缺陷,更不许早拒。
因此发拒的判据是 **worker 级的 `min_sum == 0`**,不是贡献级的「空贡献满足不了 REQUIRED」。
(先前本节写的「自己声明 REQUIRED 的座位靠空跑永远完不成,是结构性死座位」是**贡献 vs worker 的
混淆**,已被控制器推翻,不再是本程序的说法。)与 §9 D-1(0) 第 3 条同源的,是**判据不对称**这件事
本身——只对 REQUIRED 加强制,其余留给 licensed-EMPTY——而不是那句被推翻的理由。

**变异证明(三次,方向各不相同):**

1. **M1(静默)** —— 让发拒条件永不成立:红的恰是三条新准入钉,
   兄弟套件的控制钉 `test_phase10_e2e.py::TestLateFailureLoudness` **保持绿**
   (证明「已准入的计划到不了 gateway」这半边仍然成立)。
2. **M2(过打)** —— 去掉 REQUIRED 判据让它见谁打谁:红的恰是上述 C3 保留钉,
   **含**兄弟套件 `TestLateFailureLoudness::test_admission_support_passes_the_rowless_dynamic_plan`。
3. **M3(复审修正轮)** —— 去掉 `min_sum == 0` 这一条:红的**恰好一条**,
   `TestRunnableRequiredWorkerNotRefused::test_the_runnable_required_worker_is_admitted`
   (`1 failed, 13 passed`)。这一条就是本次裁决新加的**另一侧钉**:
   REQUIRED worker + granted/配行兄弟桥(min=1)+ 靠读类目 activate 的 0/0 空桥 ⇒ **准入通过**,
   既不出 `rowless_read_category_unrunnable` 也不出 `tool_calls_required_unmet`。
   三次变异后源码均按 sha256 逐字还原。

**红窗分类(§4 已补行,不是留给 Task 5 的建议):** `test_experience_rowless_admission.py` 全套按
§4 的词表属 **GREEN-VERIFY** —— 本期两位候选读者(`pv.technical`/`text.news`)都不声明
`experience_cases`,授权也只动 data 侧,所以它不该变红;其中
`TestThePremise::test_no_sealed_worker_can_trip_the_new_refusal` 是最灵敏的越界探针
(它断言「靠读类目 activate 且完全无授权」的密封 worker 集恰为 `{dec.research_mgr: FORBIDDEN}`),
一旦 Task 3 的收窄/授权动到了任何 worker 的 `read_categories` 或 `capability_allowlist`,它会立刻红。
**该钉已按控制器指示于复审修正轮补进 §4,编号 25,verdict = GREEN-VERIFY,并已过 gate 解析器**
(连号 + 全行可解析 + 词表封闭 + file/anchor 实解析)。

#### Option B —— 本期不取的路(road-not-taken,原文留档)

> **Option B: derive the row this phase** — requires ruling where the feature-vector pointer comes
> from (widen worker capability vs change the binding contract), moves additional digests, grows the
> window. Only take if the controller wants experience-grounded research-mgr output before the next
> phase.

否决理由(实测定价):三件围栏外工程叠加 —— (a) 解 FORBIDDEN↔min=1(改 P8 worker 语义 **或** 改注册
schema 的 Literal);(b) 给 `dec.research_mgr` 一个 `market_factor_report` 量级的特征向量来源
(改 worker inputs = P8 语义摘要 + 上游节点拓扑);(c) 相应的 P5/P8 摘要位移进本期重冻窗口。
**归 post-L3 backlog**,与 §9 D-1(0) 第 4 条的 13 组 OPTIONAL paper 授权同列。

#### post-L3 backlog(Task 2 实测顺带发现,本期不动)

- **allowlisted-but-rowless 的「响亮」目前是 *无类型* 的响亮。** 一个 allowlist 里**持有**
  `experience.retrieve@1` 却没有评审行的 worker,会让分析器抛 `CatalogError` 一路穿出
  `check_runtime_support` —— 准入期就炸(所以 charter item 6 的「提前到准入」在这条路上已满足),
  但**不产出任何 support report**,运维拿到的是异常而不是具名的 typed issue,也没有落库的拒绝回执。
  改成类型化拒绝是 post-L3 的事,且**必须不放松 Task-11 的 LOUD 裁决**:
  「丢了行的既有授权」与「从未授权」是两件事,前者永远不许静默降级成 licensed-EMPTY。

---

## 10. 附:本任务未做、但 Task 5 会用到的东西

- **密封材料文本对齐(计划 item 5)**:`guanlan_v2/orchestration/bootstrap.py` 的 experience 分析器
  docstring(`:455-470` 一带,叙述 Task-11 rowless 裁决)、`guanlan_v2/orchestration/memory/catalog.py:270-274`
  的 material-vs-code drift note(明说 `_ANALYZER_BYTES` 仍陈述 pre-Task-11 不变量、按裁决**字节冻结**)。
  两处在 HEAD 上都存在,行号相对 spec 有小幅漂移(已按实测记录)。
  regime prompt material 与深度预设描述的 `pv.microstructure` 过陈述见 §4 #14。
- **无行 experience worker(计划 items 2+6)**:本任务未度量。
  ~~Task 6 的范围。~~ **勘误(Task 2 落笔):是 Task 2 的范围**,已于 HEAD `54176b2e` 实测并交付
  —— 裁决见 §9 的 “RULING — dec.research_mgr experience row”,早拒绝见
  `tests/orchestration/test_experience_rowless_admission.py`。
- **`CHECKER_VERSION` 要不要动 —— Task 5 重冻评审必须显式过一遍。** L3 Task 2 **改变了
  `check_runtime_support` 会拒什么**(新增 `rowless_read_category_unrunnable`),但
  `CHECKER_VERSION = "static-runtime-checker-v1"` 未动:它是 Literal 钉死的版本标记,动它会让
  **P5 schema manifest 位移** —— 在 L3 围栏外,所以本期**有意不动**。Task 5 在唯一的重冻窗口里
  要么把这个版本标记一并挪、要么把「有意不动」作为一条书面决定记进重冻对照表;
  **不允许默认跳过**(判据变了而版本标记没变,本身就是一处需要被看见的漂移)。

---

## 11. 红窗实测红集 (Task 3) —— 这就是 Task 5 的工作清单

Task 3 提交点的 **实测** 全套结果(`python -m pytest tests/orchestration -q -p no:randomly`,
脏工作树,与开工基线同一环境):

| 轮次 | 结果 |
|---|---|
| 开工基线(HEAD `61f7e579`,改动前) | **5976 passed, 1 xfailed** in 19:20 —— 全绿,无 STOP |
| 收官(Task 3 改动后) | **45 failed, 5973 passed, 1 xfailed, 5 errors** in 18:58 |

红集共 **50 项**。逐项归属如下 —— 归属是**逐条写的,不是按文件一刀切**;
任何归不到既有行的红都单列在 D 组并已上报控制器。

### A 组 —— §3 链摘要 / golden 位移(27 项,全部落在 §3 的 43 行字面量表内)

`data/test_data_catalog.py::test_matches_the_frozen_golden_manifest`(§3 #4)、
`memory/test_catalog.py::test_data_catalog_golden_is_byte_untouched`(§3 #4)、
`memory/test_catalog.py::test_full_catalog_golden_matches`(§3 #7/#8)、
`test_phase4_handoff.py::{test_point5_phase3_full_catalog_export_reproduces_the_frozen_digest, test_point8_owning_modules_of_frozen_symbols_are_upstream, test_step2_records_the_exact_phase3_full_registry_and_catalog_digests}`(§3 #23)、
`test_phase4_registry.py::test_phase4_catalog_matches_the_frozen_golden`(§3 #9/#10)、
`test_phase5_handoff.py::{test_point4_phase3_full_registry_and_catalog_reproduce, test_point5_phase4_registry_and_catalog_chain_reproduce, test_step2_records_the_exact_phase3_full_registry_and_catalog_digests, test_step2_records_the_exact_phase4_registry_and_catalog_digests}`(§3 #24/#25)、
`test_phase5_registry.py::test_phase5_catalog_matches_the_frozen_golden`(§3 #11/#12)、
`test_phase6_handoff.py::{test_point3_catalog_builders_verify_their_predecessor_snapshot, test_point3_catalog_chain_digests_reproduce_and_link, test_step2_records_the_exact_catalog_chain_digests}`(§3 #26-30)、
`test_phase6_registry.py::test_phase6_catalog_matches_the_frozen_golden`(§3 #13/#14)、
`test_phase7_handoff.py::{test_point6_catalog_chain_nodes_reproduce_and_link, test_step2_records_the_exact_phase4_5_6_chain_digests}`(§3 #31/#32)、
`test_phase7_registry.py::test_phase7_catalog_matches_the_frozen_golden`(§3 #15/#16)、
`test_phase8_handoff.py::{test_clause_a_catalog_builders_are_heterogeneous_but_accessors_are_uniform, test_point1_chain_verifies_back_through_p6_p5_p4_to_p3_full, test_point1_phase7_chain_symbols_resolve_and_reproduce}`(§3 #33-36)、
`test_phase8_registry_catalog.py::test_phase8_catalog_matches_the_frozen_golden`(§3 #17/#18,**并含 §4 #19/#20 的两个 WorkerSpec 语义摘要**)、
`test_phase9_handoff.py::test_point2_catalog_chain_resolves_and_base_pins_verify`(§3 #37-43)、
`test_phase9_registry_chain.py::test_phase9_catalog_matches_the_frozen_golden`(§3 #19/#20)、
`test_phase10_chain.py::test_phase10_catalog_matches_the_frozen_golden`(§3 #5/#6)、
`test_phase10_handoff.py::TestItem1ChainDigests::test_catalog_chain_resolves_by_exact_frozen_digest`(§3 #21/#22)。

**P2 锚点未动的正向证据:**`data/test_phase_handoff.py`(§3 #1,只钉 P2)与
`fixtures/stock_deep_dive_equivalence_v1.json`(§3 #2)**全程绿** —— 「P2 不动」这条唯一的上游锚点
在实测上成立,不是靠不去看它。

### B 组 —— §4 有意识翻转(8 项,全部命中已列行)

| 测试 id | §4 行 |
|---|---|
| `test_pipeline_deep_preset.py::TestFullPhase9BridgeView::test_the_report_is_honestly_unsupported_naming_the_data_grant_gap` | #1 FLIP |
| `test_pipeline_reduced_deep_preset.py::TestTheSupportFinding::test_the_untouched_ten_node_baseline_is_still_honestly_unsupported` | #2 FLIP |
| `test_pipeline_reduced_deep_preset.py::TestTheSupportFinding::test_the_two_dropped_nodes_are_exactly_the_unmet_ones` | #3 FLIP |
| `test_data_world_recipe.py::TestSupportedSetGroupC::test_every_granted_prefetch_row_is_within_the_supported_set` | #9 FLIP |
| `test_lane_batch_pv.py::test_worker_spec_matrix[pv.technical]` | #26 FLIP |
| `test_lane_batch_text.py::test_worker_spec_matrix[text.news]` | #27 FLIP |
| `test_lane_batch_text.py::test_allowlist_broadening_applied` | #28 FLIP |
| `test_lane_batch_text.py::test_promise_supply_lint_clean[text.news]` | #29 FLIP-IF(Task-4 义务) |

**GREEN-VERIFY 全部实测为绿**,逐条复核:#4 减证据预设仍 supported;#5/#6 `tool_calls_required_unmet`
算术钉未动;#10/#11/#12 L2-b 路由钉(三条新行照同一配方冻结路由,进迭代后仍绿);
#14/#15 P3 两向不变量钉(判据字节未动);**#25 越界探针绿** —— 收窄/授权没有碰任何 worker 的
`read_categories` 或 `capability_allowlist` 之外的东西。#7/#8/#13/#24 的 FLIP-IF 条件不成立,保持绿。

### C 组 —— Task 1 自己的门(8 项)

`test_l3_refreeze_gate.py::TestGate1L1Landed::test_the_one_reviewed_grant_is_still_the_single_pm_row`、
`TestGate4BaselineDigests::{test_the_measured_chain_equals_the_spec_section_1_values, test_the_ledger_records_exactly_the_measured_chain, test_the_bridge_material_movement_is_measured_not_declared, test_the_ledger_records_the_measured_bridge_material_verdicts, test_the_provider_and_analyzer_materials_are_binding_INDEPENDENT}`、
`TestGate6GrantSiteReality::{test_the_seventeen_p8_grants_stand_against_one_sealed_row, test_a_candidate_row_alone_trips_the_invariant_in_the_rows_direction}`。

两类根因,都是**设计上必然**:

1. **链/材料摘要类**(前 5 条):门把 §2 / §2.3 的表逐行重新度量并断言相等。本期正是要移动那些值,
   所以门必红,直到 Task 5 把 §2 / §2.3 一并重冻。
2. **候选行探针类**(后 3 条,含 `_row_augmented_materials` 的使用者):门用「给密封绑定**再加一条**
   `pv.technical/verified_snapshot` 候选行」来做构造法证明。**那条候选行现在是真行了**,于是
   `DataBridgePrefetchBinding` 的 `operations must be duplicate-free per (worker, capability)` 当场拒绝,
   探针自己炸掉。`test_the_seventeen_p8_grants_stand_against_one_sealed_row` 同理:17 组授权对 1 条行
   的实测事实,本期已变成 15 组对 4 条行。

**§4 没有任何一行指向门文件。** Task 3 按简报要求**未编辑门**。
**Task 5 必须给门补 §4 行并有意识翻转它**(候选行探针要换一个仍未授权的 (worker, method) 组合,
否则它证明的东西就没了)。

### D 组 —— 归不到任何既有行的红(7 项)—— **已上报控制器,待裁**

| # | 测试 id | 实测根因 | 类别 |
|---|---|---|---|
| D1 | `data/test_subject_projection.py::TestRealRowRoundTrip`(5 条,fixture `real_row` setup ERROR) | 该 fixture 断言 `[(r.worker_id, r.method_ref.id) for r in rows] == [("dec.pm","verified_snapshot")]` —— 即「绑定里**只有一条**行」。与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是**同一句 L1 期的话**,Task 3 只被授权改后者(在本任务 charter 内),前者不在 charter 内,**按红窗法未动**。 | 有意识翻转,机械 |
| D2 | `test_pipeline_screening.py::TestSubjectParamsStamp::test_zero_screening_workers_carry_data_prefetch_rows_today` | `assert targeted == {"dec.pm"}`,实测现为 `{dec.pm, pv.technical, text.news, text.policy}`。钉名里的 “today” 说明它本就是一条**记录当期事实**的钉;L1 Task 4 的注释也写着「vacuous behaviorally: zero screening workers carry data prefetch rows」——**这句话现在假了**:`text.news` 就是选股 lane 的 worker。 | 有意识翻转,**且连带源码注释需改** |
| D3 | `test_phase8_registry_catalog.py::test_capability_promise_supply_lint_clean_across_every_seat` | 与 §4 #29 **同一个根因**(`text.news` 的 `ww_f10` 承诺无供给),只是在全 27 席的作用域上再钉一次。随 Task-4 去掉 `ww_f10` 承诺后**无需改测试即自愈**。 | Task-4 义务的第二处落点 |

三组都是「钉记录了 L3 之前的世界」,**没有一条指示实现缺陷**;但它们不在 §3/§4 任何一行里,
所以按红窗法(「归不到的红 = STOP,上报」)Task 3 **不自行修复、不自行入表**,列在此处等控制器裁,
由控制器决定是补 §4 行(#30-32)还是另行处置。

### 结论

- red ⊆ (§3 ∪ §4):**否** —— A 组 27 + B 组 8 = 35 项在表内;C 组 8 项归属清楚但表内无行;D 组 7 项待裁。
- **零红出现在 GREEN-VERIFY 行上。** 没有任何一处红指示越界或实现缺陷。
- goldens **未再生成**;`CHECKER_VERSION` **未动**;密封 schema 的三个 class docstring **未动**。
