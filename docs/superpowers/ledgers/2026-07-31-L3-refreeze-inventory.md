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

| 材料 | 摘要 @ HEAD | D-1/D-2 改绑定后是否移动 | 理由 |
|---|---|---|---|
| `bridge.data_runtime.provider@1` | `3a1e727b2e3f761bfdecadf919dc678bc8dfd19b993495ca5dd223ecf3d8e065` | **不动** | 材料字节是 provider handler 文本,与 prefetch 绑定无关 |
| `bridge.data_runtime.analyzer@1` | `3f93fdf29d28f26676e7291dbeeb1d6e3b899265862940bf5c16ae477aeb1968` | **不动** | 同上(analyzer 文本) |
| `bridge.data_runtime.prefetch@1` | `0c9d38a76099dced0d954066539f72112a1cff7989a81c5f39b3793ab5d61172` | **移动** | 材料字节 == `serialize_prefetch_binding(binding)`,加行即改字节 |
| `bridge.data_runtime.descriptor@1` | `797064488e810f2ae86e8f2ed49e1b5e528dfddcf6ae9958192d3df63a46c058` | **移动** | descriptor 内嵌 `config_ref`,随 prefetch 材料一起动 |

(两者的 provider/analyzer 前 8 位与计划正文引用的 `3a1e727b…` / `3f93fdf2…` 一致,交叉验证通过。)

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
| 5 | `tests/orchestration/test_runtime_support.py` | 595 | `tool_calls_required_unmet` | GREEN-VERIFY | 通用算术钉(合成 worker,非两位读者)。算术本身不变;变红说明动了 `runtime_support.py:673` 的判据。 |
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

## 9. 控制器裁决(Task 2 派工前由控制器逐字写入)

### D-1(0) 授权落点

> _(待裁)_

### D-1 授权粒度

> _(待裁)_

### D-2 `always_invoke` 与断供语义

> _(待裁)_

---

## 10. 附:本任务未做、但 Task 5 会用到的东西

- **密封材料文本对齐(计划 item 5)**:`guanlan_v2/orchestration/bootstrap.py` 的 experience 分析器
  docstring(`:455-470` 一带,叙述 Task-11 rowless 裁决)、`guanlan_v2/orchestration/memory/catalog.py:270-274`
  的 material-vs-code drift note(明说 `_ANALYZER_BYTES` 仍陈述 pre-Task-11 不变量、按裁决**字节冻结**)。
  两处在 HEAD 上都存在,行号相对 spec 有小幅漂移(已按实测记录)。
  regime prompt material 与深度预设描述的 `pv.microstructure` 过陈述见 §4 #14。
- **无行 experience worker(计划 items 2+6)**:本任务未度量,Task 6 的范围。
