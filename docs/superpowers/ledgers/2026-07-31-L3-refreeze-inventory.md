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
| P3-data | `13210944b6afe738a34c4767453a6514d1147dd5c4a51ae71580425a4c5a72a4` | MATCH | MOVES |
| P3-full | `0601eef771be221dbc203c56849f357b277d213a6b00274ee94aeaf70725b231` | MATCH | MOVES |
| P4 | `8aea7bc0cb543a8bed8ea4505caa2f4a7f2cce9636762ad690b624d14de1e0f9` | MATCH | MOVES |
| P5 | `af064cbaaa1e9b0cdea7b23ced1cffb103f6605fbcda476598b5762651df0b8d` | MATCH | MOVES |
| P6 | `af064cbaaa1e9b0cdea7b23ced1cffb103f6605fbcda476598b5762651df0b8d` | MATCH(恒等节点,== P5) | MOVES |
| P7 | `b78b63e476bfd5bf0c303524244dbc9a23a585ccc924c8230dcfe906f1b07750` | MATCH | MOVES |
| P8 | `78f79d330ae99e7efcadb7b367f6f84badb0cca2541473824a6fb7618eb4f951` | MATCH | MOVES |
| P9 | `e3a293615a21db3d88c10969395850369d71be9349c0d3462abc4e4015401e9d` | MATCH | MOVES |
| P10 | `f1b730529dab1d0d1e3243cde86127b5ee960dc9ac42c9fd639023159c3a535a` | MATCH | MOVES |

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
| 「11 个 digest」 | 见下 | 11 | **对账**:9 个链 catalog 摘要 + 2 个随绑定字节移动的数据桥材料摘要(`bridge.data_runtime.prefetch@1` `cab80122…`、`bridge.data_runtime.descriptor@1` `817d2882…`)= **11 个进入 Task-5 再生成范围的摘要身份**。这是我能实测出的唯一落在 11 上的划分,记录为对账假设而非对 07-29 原意的断言;因为**每个值都与 spec 逐字节相同**,该差异是计数口径,不是漂移。 |

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
| `bridge.data_runtime.prefetch@1` | `cab80122821607f8d6de0f72be4062174615c6789a039f67f5fe338f9250469a` | **移动** | 构造法:行增广绑定重建后摘要 != 密封摘要(且它是独立性探针的阳性对照) |
| `bridge.data_runtime.descriptor@1` | `817d2882e20760ebe06cfd41e5a1656b7554961952644fc04ee077a75ab89a7b` | **移动** | 构造法:内嵌 `config_ref`,随 prefetch 一起重建后摘要 != 密封摘要 |

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
| 4 | `tests/orchestration/golden/data_catalog_manifest_v1.json` | 6 | P3-data | 13210944 | MOVES | golden |
| 5 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 4 | P9 | e3a29361 | MOVES | golden |
| 6 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 6 | P10 | f1b73052 | MOVES | golden |
| 7 | `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` | 4 | P3-data | 13210944 | MOVES | golden |
| 8 | `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` | 6 | P3-full | 0601eef7 | MOVES | golden |
| 9 | `tests/orchestration/golden/phase4_catalog_manifest_v1.json` | 4 | P3-full | 0601eef7 | MOVES | golden |
| 10 | `tests/orchestration/golden/phase4_catalog_manifest_v1.json` | 6 | P4 | 8aea7bc0 | MOVES | golden |
| 11 | `tests/orchestration/golden/phase5_catalog_manifest_v1.json` | 4 | P4 | 8aea7bc0 | MOVES | golden |
| 12 | `tests/orchestration/golden/phase5_catalog_manifest_v1.json` | 6 | P5-P6 | af064cba | MOVES | golden |
| 13 | `tests/orchestration/golden/phase6_catalog_manifest_v1.json` | 4 | P5-P6 | af064cba | MOVES | golden |
| 14 | `tests/orchestration/golden/phase6_catalog_manifest_v1.json` | 5 | P5-P6 | af064cba | MOVES | golden |
| 15 | `tests/orchestration/golden/phase7_catalog_manifest_v1.json` | 4 | P5-P6 | af064cba | MOVES | golden |
| 16 | `tests/orchestration/golden/phase7_catalog_manifest_v1.json` | 6 | P7 | b78b63e4 | MOVES | golden |
| 17 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 4 | P7 | b78b63e4 | MOVES | golden |
| 18 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 6 | P8 | 78f79d33 | MOVES | golden |
| 19 | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | 4 | P8 | 78f79d33 | MOVES | golden |
| 20 | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | 6 | P9 | e3a29361 | MOVES | golden |
| 21 | `tests/orchestration/test_phase10_handoff.py` | 123 | P8 | 78f79d33 | MOVES | handoff |
| 22 | `tests/orchestration/test_phase10_handoff.py` | 127 | P9 | e3a29361 | MOVES | handoff |
| 23 | `tests/orchestration/test_phase4_handoff.py` | 160 | P3-full | 0601eef7 | MOVES | handoff |
| 24 | `tests/orchestration/test_phase5_handoff.py` | 217 | P3-full | 0601eef7 | MOVES | handoff |
| 25 | `tests/orchestration/test_phase5_handoff.py` | 223 | P4 | 8aea7bc0 | MOVES | handoff |
| 26 | `tests/orchestration/test_phase6_handoff.py` | 246 | P2-static | b41bf223 | FROZEN | handoff |
| 27 | `tests/orchestration/test_phase6_handoff.py` | 249 | P3-data | 13210944 | MOVES | handoff |
| 28 | `tests/orchestration/test_phase6_handoff.py` | 252 | P3-full | 0601eef7 | MOVES | handoff |
| 29 | `tests/orchestration/test_phase6_handoff.py` | 255 | P4 | 8aea7bc0 | MOVES | handoff |
| 30 | `tests/orchestration/test_phase6_handoff.py` | 258 | P5-P6 | af064cba | MOVES | handoff |
| 31 | `tests/orchestration/test_phase7_handoff.py` | 207 | P4 | 8aea7bc0 | MOVES | handoff |
| 32 | `tests/orchestration/test_phase7_handoff.py` | 210 | P5-P6 | af064cba | MOVES | handoff |
| 33 | `tests/orchestration/test_phase8_handoff.py` | 228 | P3-full | 0601eef7 | MOVES | handoff |
| 34 | `tests/orchestration/test_phase8_handoff.py` | 247 | P4 | 8aea7bc0 | MOVES | handoff |
| 35 | `tests/orchestration/test_phase8_handoff.py` | 250 | P5-P6 | af064cba | MOVES | handoff |
| 36 | `tests/orchestration/test_phase8_handoff.py` | 254 | P7 | b78b63e4 | MOVES | handoff |
| 37 | `tests/orchestration/test_phase9_handoff.py` | 299 | P2-static | b41bf223 | FROZEN | handoff |
| 38 | `tests/orchestration/test_phase9_handoff.py` | 302 | P3-data | 13210944 | MOVES | handoff |
| 39 | `tests/orchestration/test_phase9_handoff.py` | 305 | P3-full | 0601eef7 | MOVES | handoff |
| 40 | `tests/orchestration/test_phase9_handoff.py` | 308 | P4 | 8aea7bc0 | MOVES | handoff |
| 41 | `tests/orchestration/test_phase9_handoff.py` | 311 | P5-P6 | af064cba | MOVES | handoff |
| 42 | `tests/orchestration/test_phase9_handoff.py` | 315 | P7 | b78b63e4 | MOVES | handoff |
| 43 | `tests/orchestration/test_phase9_handoff.py` | 318 | P8 | 78f79d33 | MOVES | handoff |

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

### 3.1 Task 4 的新落点 —— **不是链摘要,所以不进上表**(2026-08-11 实测,修订轮重校)

上表(以及解析它的 gate)只收 **链 catalog 摘要**的字面量:`_LITERAL_ROW` 要求 `P<node>` + 8 位
digest8 + `FROZEN|MOVES` 三列同时成立,`_tree_literals()` 也只扫链摘要。Task 4 移动的密封材料
摘要**不是链节点**,把它们塞进上表会同时打断「42 处 / 16 文件」的对账和「记录 == 实测」的漂移断言。
因此按**实测集治**的原则记录在此,不改上表结构 —— 但 Task 5 的再生成清单必须包含它们。

> **交叉引用格式(修订轮新增,可执行):** 本文件里指向 §4 的引用一律写成
> **§4 #NN(`token`)**,其中 `token` 必须出现在 §4 第 NN 行的 `file` 或 `anchor` 单元格里。
> `test_l3_refreeze_gate.py::TestGate8…::test_every_section4_cross_reference_resolves`
> 逐条重解析并断言 —— **本节第一版的编号整体差 +5,靠人眼没看出来**,所以这条钉不是装饰。

- `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` —— `bridge.memory_runtime.analyzer`
  的材料摘要 `e794e012…`(随 `memory/catalog.py` 的 `_ANALYZER_BYTES` 改写移动)。§4 #62(`e794e012`)。
- `tests/orchestration/golden/phase8_catalog_manifest_v1.json` —— `skill.text.news@1` 的
  skill-manifest 摘要 `b1775ea2…`(随 SKILL.md 去掉 `ww_f10` 移动)。§4 #61(`b1775ea2`)。
- `tests/orchestration/golden/phase8_catalog_manifest_v1.json` —— `pv.technical` / `text.news` 的两个
  WorkerSpec 语义摘要(D-1 收窄 + Task 4 的 SKILL 字节共同推动)。
  §4 #59(`3b2ae164`) / §4 #60(`079de032`)。
- `tests/orchestration/test_phase8_handoff.py` —— `FINAL_WORKER_DIGESTS` 里 **`market.regime` 一项**
  `4e1a747870f3…` → `813fe36ee5e4…`(随 regime prompt 材料字节移动;其余 6 个 final worker 逐字节不变)。
  §4 #56(`test_point2_final_worker_semantic_digests_are_the_frozen_baseline`)。
- 十节点深度预设 record digest `696daf80…` 的三处落点(golden 1 + 测试常量 2):
  §4 #63(`phase10_catalog_manifest_v1.json`) / §4 #64(`test_pipeline_deep_preset.py`) /
  §4 #65(`test_pipeline_reduced_deep_preset.py`)。
- **减证据**预设 record digest `2c4b4e0d…`(修订轮新增,控制器裁决:同一句过陈述两个预设一起订正)。
  实测**只有一处落点**:`test_pipeline_reduced_deep_preset.py:125`,**无任何 golden 落点**。
  §4 #55(`test_the_reduced_record_digest_is_golden_frozen`)。

**三个「移动了但没有字面量落点」的材料摘要 —— 它们只经链摘要间接显形:**

| 材料 | 旧 → 新 | 字面量落点 | 机械镜像 |
|:--|:--|:--|:--|
| `lane0.experience.analyzer` | `1a613689…` → `88cb5048…` | 无(P5 golden 只列 **id**,不列摘要) | 描述符 1 处 |
| `lane0.experience.provider` | `5c319cd1…` → `7917cb2b…` | 无(同上) | 描述符 1 处 |
| `lane0.regime.prompt` | `903e49ac…` → 新值 | 无(同上) | 无;但经 `market.regime` 的 `system_prompt_ref` 推动 §4 #56(`test_point2_final_worker_semantic_digests_are_the_frozen_baseline`) |

**强耦合(两次都由实测发现,不是读代码猜的):** 密封材料
`config/orchestration/materials/lane0/experience_bridge_descriptor.md` **内嵌**
`support_analyzer_ref` 与 `provider_handler_ref` 两个 `content_digest`,
`bootstrap.assemble_lane0_catalog` 逐个比对并在漂移时抛 `CatalogError`。
Task 4 两次都同步重生成了该描述符。**今后任何改动 experience 分析器/提供者材料的人都必须一起改它,
否则 Lane-0 目录加载当场炸(响亮,不静默)。**

**工具链危险(Task 4 实测,记录给 Task 5):** 本仓 `core.autocrlf=true` 且**没有 `.gitattributes`**。
`git checkout -- <material>` 会把 LF 的密封材料重写成 CRLF —— **每一个材料摘要当场改变**,
而 `git status` / `git diff` 仍报告该文件「未修改」(因为 git 在入索引时又把 CRLF 归一回 LF)。
Task 4 有两轮全套测量因此作废。**Task 5 再生成 goldens 时,不要用 `git checkout` 还原
`config/orchestration/materials` 下的任何文件**;要还原就按字节写回(`git show HEAD:<path>` 的原始输出)。

## 4. 红窗 allowed-red 账本 —— **Task 4 从度量整表重推** **[gate-parsed]**

> **本表已于 2026-08-11 由 L3 Task 4 从实测整表重写**,替换了 Task 1 起草、Task 2/3 逐行打补丁的旧 29 行表。
> 重推的理由是控制器裁决(§11.1):旧表被实测推翻过三次(`text.policy` 的 tool-call 归类、
> 四条 lane-batch 行为钉、§11 的 D 组)—— **逐行打补丁的做法到此为止**。
> 自本节起,红窗的**操作性**允许集 = **本表**(不再是「§4 ∪ §11 处置」);
> §11 / §11.1 原样保留为**历史记录**,其每一条处置都已折进本表成行。
>
> **重推方法(可复现):** 在同一棵脏工作树上跑两次全套
> `python -m pytest tests/orchestration -q -p no:randomly` —— 一次在 Task 4 改动**之前**
> (改动集按 sha256 备份后还原 HEAD 字节、新测试文件移出树),一次在改动**之后**(按 sha256 还原改动集)。
> 表里每一行 = 两次红集**并集**中的一个测试,外加 Task 5 必须改写的非测试落点与红窗越界探针。
> 实测:改前 **50 红**(45 failed + 5 errors),与 §11 记录的 Task 3 红集**逐条相同** ——
> 「Task 3 修订轮未改变红集」由此验证,不是假设;改后 **55 红**(50 failed + 5 errors):
> **2 治愈、7 新增,7 条全部可归属**,逐条见 §11.3。
>
> `verdict` 是**封闭词表**,只有三个取值(gate 断言:任何第四种拼法 = 红):
> `FLIP` = 已经红(或本期必然变红),必须由 owner 有意识翻转并留双向证据;
> `FLIP-IF` = 只在某个具体裁决/条件下变红(条件写在备注,并注明今天是否成立);
> `GREEN-VERIFY` = 不该红,每次红窗都要显式复核(它变红说明改动越界)。
>
> **表的契约是 `anchor` 列,不是 `line` 列。** gate 断言每行的 `file` 存在、且 `anchor` 字符串
> 出现在该文件中(全文件匹配)。上游代码上下移动不会让本表假红,而钉被**删掉或改名**会立刻红。
> `line` 列一律写 `—`:行号在九期里漂移过太多次,写进表里只会制造假精确。
>
> **每一行都带 owner。** `Task 5` = 关窗时的再生成 / 有意识翻转;`Task 4` = 本期已交付。
> **归不到任何一行的红 = STOP,上报。**

| # | file | line(s) | anchor | verdict | 成因归属 / owner |
|---|---|---|---|---|---|
| 1 | `tests/orchestration/data/test_data_catalog.py` | — | `test_matches_the_frozen_golden_manifest` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 2 | `tests/orchestration/memory/test_catalog.py` | — | `test_data_catalog_golden_is_byte_untouched` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 3 | `tests/orchestration/memory/test_catalog.py` | — | `test_full_catalog_golden_matches` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 4 | `tests/orchestration/test_phase4_registry.py` | — | `test_phase4_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 5 | `tests/orchestration/test_phase5_registry.py` | — | `test_phase5_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 6 | `tests/orchestration/test_phase6_registry.py` | — | `test_phase6_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 7 | `tests/orchestration/test_phase7_registry.py` | — | `test_phase7_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 8 | `tests/orchestration/test_phase8_registry_catalog.py` | — | `test_phase8_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 9 | `tests/orchestration/test_phase9_registry_chain.py` | — | `test_phase9_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 10 | `tests/orchestration/test_phase10_chain.py` | — | `test_phase10_catalog_matches_the_frozen_golden` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 11 | `tests/orchestration/test_phase4_handoff.py` | — | `test_point5_phase3_full_catalog_export_reproduces_the_frozen_digest` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 12 | `tests/orchestration/test_phase4_handoff.py` | — | `test_point8_owning_modules_of_frozen_symbols_are_upstream` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 13 | `tests/orchestration/test_phase4_handoff.py` | — | `test_step2_records_the_exact_phase3_full_registry_and_catalog_digests` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 14 | `tests/orchestration/test_phase5_handoff.py` | — | `test_point4_phase3_full_registry_and_catalog_reproduce` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 15 | `tests/orchestration/test_phase5_handoff.py` | — | `test_point5_phase4_registry_and_catalog_chain_reproduce` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 16 | `tests/orchestration/test_phase5_handoff.py` | — | `test_step2_records_the_exact_phase3_full_registry_and_catalog_digests` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 17 | `tests/orchestration/test_phase5_handoff.py` | — | `test_step2_records_the_exact_phase4_registry_and_catalog_digests` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 18 | `tests/orchestration/test_phase6_handoff.py` | — | `test_point3_catalog_builders_verify_their_predecessor_snapshot` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 19 | `tests/orchestration/test_phase6_handoff.py` | — | `test_point3_catalog_chain_digests_reproduce_and_link` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 20 | `tests/orchestration/test_phase6_handoff.py` | — | `test_step2_records_the_exact_catalog_chain_digests` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 21 | `tests/orchestration/test_phase7_handoff.py` | — | `test_point6_catalog_chain_nodes_reproduce_and_link` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 22 | `tests/orchestration/test_phase7_handoff.py` | — | `test_step2_records_the_exact_phase4_5_6_chain_digests` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 23 | `tests/orchestration/test_phase8_handoff.py` | — | `test_clause_a_catalog_builders_are_heterogeneous_but_accessors_are_uniform` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 24 | `tests/orchestration/test_phase8_handoff.py` | — | `test_point1_chain_verifies_back_through_p6_p5_p4_to_p3_full` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 25 | `tests/orchestration/test_phase8_handoff.py` | — | `test_point1_phase7_chain_symbols_resolve_and_reproduce` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 26 | `tests/orchestration/test_phase9_handoff.py` | — | `test_point2_catalog_chain_resolves_and_base_pins_verify` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 27 | `tests/orchestration/test_phase10_handoff.py` | — | `test_catalog_chain_resolves_by_exact_frozen_digest` | FLIP | 链摘要/golden 位移。**成因**:Task 3 的三条新行(prefetch/descriptor 材料摘要)+ Task 4 的四处密封材料字节(experience 分析器、memory `_ANALYZER_BYTES`、regime prompt、深度预设描述)沿 P3→P10 同一条链累加。**owner**:Task 5 一次性再生成。 |
| 28 | `tests/orchestration/test_pipeline_deep_preset.py` | — | `test_the_report_is_supported_and_the_unsupported_set_is_EMPTY` | FLIP | **成因**:D-1/D-2 授权(Task 3)。断言 `report.supported is False` 且 unmet 恰为 `{pv.technical, text.news}`。**owner**:Task 5 有意识翻转,留双向证据。 |
| 29 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `test_the_ten_node_baseline_is_now_supported_with_an_empty_unmet_set` | FLIP | 同上;它同时是减证据预设「存在理由」的回归半边。**owner**:Task 5。 |
| 30 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `test_the_two_dropped_nodes_are_exactly_the_two_healed_readers` | FLIP | **成因**:授权后 unmet 集空,`full_ids - reduced_ids == unmet_nodes` 两边同时塌。**owner**:Task 5 换成新的因果断言,不是删钉。 |
| 31 | `tests/orchestration/test_data_world_recipe.py` | — | `test_every_granted_prefetch_row_is_within_the_supported_set` | FLIP | **成因**:任何新增行都会动它的 `granted == {verified_snapshot}` 那一行。**owner**:Task 5 改为 `{verified_snapshot, news}`。 |
| 32 | `tests/orchestration/test_lane_batch_pv.py` | — | `test_worker_spec_matrix` | FLIP | **成因**:D-1 Option A 收窄 `pv.technical.capability_methods`(行为侧的相等断言)。**owner**:Task 5 有意识改期望值。 |
| 33 | `tests/orchestration/test_lane_batch_text.py` | — | `test_worker_spec_matrix` | FLIP | 同上的 `text.news` 半边。**owner**:Task 5。 |
| 34 | `tests/orchestration/test_lane_batch_text.py` | — | `test_allowlist_broadening_applied` | FLIP | **成因**:D-1 有意识反转了上一次评审过的扩权。**owner**:Task 5 **改写**本钉使其记录新裁决,**绝不删除** —— 删掉等于抹掉「曾经授过、后来按逐能力论证收回」这段评审史。 |
| 35 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_measured_chain_equals_the_refreeze_baseline` | FLIP | **成因**:门把 §2/§2.3 的表逐行重新度量并断言相等,本期正是要移动那些值。**owner**:Task 5 取重冻后的新值重钉。 |
| 36 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_ledger_records_exactly_the_measured_chain` | FLIP | **成因**:门把 §2/§2.3 的表逐行重新度量并断言相等,本期正是要移动那些值。**owner**:Task 5 取重冻后的新值重钉。 |
| 37 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_bridge_material_movement_is_measured_not_declared` | FLIP | **成因**:门把 §2/§2.3 的表逐行重新度量并断言相等,本期正是要移动那些值。**owner**:Task 5 取重冻后的新值重钉。 |
| 38 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_ledger_records_the_measured_bridge_material_verdicts` | FLIP | **成因**:门把 §2/§2.3 的表逐行重新度量并断言相等,本期正是要移动那些值。**owner**:Task 5 取重冻后的新值重钉。 |
| 39 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_provider_and_analyzer_materials_are_binding_INDEPENDENT` | FLIP | **成因**:门把 §2/§2.3 的表逐行重新度量并断言相等,本期正是要移动那些值。**owner**:Task 5 取重冻后的新值重钉。 |
| 40 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_reviewed_rows_are_the_pm_row_plus_the_three_l3_grants` | FLIP | **成因**:唯一评审授权从 1 组变 4 组。**owner**:Task 5 重钉。 |
| 41 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_the_fifteen_p8_grants_stand_against_four_sealed_rows` | FLIP | **成因**:17 组授权对 1 条行,实测已是 15 组对 4 条行。**owner**:Task 5 重钉为 15 对 4。 |
| 42 | `tests/orchestration/test_l3_refreeze_gate.py` | — | `test_a_candidate_row_alone_trips_the_invariant_in_the_rows_direction` | FLIP | **成因**:候选行探针用的 `pv.technical/verified_snapshot` 现在是真行,`operations must be duplicate-free per (worker, capability)` 当场拒绝,探针自炸。**owner**:Task 5 **必须把探针重新指向一个仍未授权的 (worker, method) 组合** —— 只改到能跑通、不重新指向 = 探针从此不再证明任何东西。 |
| 43 | `tests/orchestration/data/test_subject_projection.py` | — | `test_the_real_sealed_row_resolves_and_validates_from_the_subject` | FLIP | **成因**:`real_row` fixture 断言「绑定里只有一条行」—— 与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是 L1 期的同一句话。**owner**:Task 5 翻转(同一改法,换个文件);控制器已裁,见 §11.1 D1。 |
| 44 | `tests/orchestration/data/test_subject_projection.py` | — | `test_without_the_subject_the_same_real_row_still_refuses` | FLIP | **成因**:`real_row` fixture 断言「绑定里只有一条行」—— 与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是 L1 期的同一句话。**owner**:Task 5 翻转(同一改法,换个文件);控制器已裁,见 §11.1 D1。 |
| 45 | `tests/orchestration/data/test_subject_projection.py` | — | `test_a_node_may_never_contradict_the_run_subject` | FLIP | **成因**:`real_row` fixture 断言「绑定里只有一条行」—— 与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是 L1 期的同一句话。**owner**:Task 5 翻转(同一改法,换个文件);控制器已裁,见 §11.1 D1。 |
| 46 | `tests/orchestration/data/test_subject_projection.py` | — | `test_a_node_carrying_the_same_value_resolves` | FLIP | **成因**:`real_row` fixture 断言「绑定里只有一条行」—— 与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是 L1 期的同一句话。**owner**:Task 5 翻转(同一改法,换个文件);控制器已裁,见 §11.1 D1。 |
| 47 | `tests/orchestration/data/test_subject_projection.py` | — | `test_a_pointer_outside_the_closed_set_is_still_refused` | FLIP | **成因**:`real_row` fixture 断言「绑定里只有一条行」—— 与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是 L1 期的同一句话。**owner**:Task 5 翻转(同一改法,换个文件);控制器已裁,见 §11.1 D1。 |
| 48 | `tests/orchestration/test_pipeline_screening.py` | — | `test_the_screening_stamp_now_serves_a_real_row` | FLIP | **成因**:`targeted` 实测已是 `{dec.pm, pv.technical, text.news, text.policy}`;钉名里的 today 说明它记录的本就是当期事实。**owner**:Task 5 翻转(§11.1 D2)。**其源码注释半边已由 Task 4 交付**(`pipeline/screening.py` 的 `subject_params` 字段注释,原文「zero screening workers carry data prefetch rows」已为假)。 |
| 49 | `tests/orchestration/test_lane_batch_text.py` | — | `test_new_seats_data_source_priority_names_real_tools` | FLIP | **Task 4 新造红。成因**:控制器裁决(旧 §4 #29)要求去掉 `text.news` SKILL.md 的 `ww_f10` 承诺,本钉的 `ww_f10 in news` 随之为假。**owner**:Task 5 改写期望 —— D-1 拿走了唯一供给它的 `cap.data.fundamentals`,这条承诺不该再存在。 |
| 50 | `tests/orchestration/test_pipeline_deep_preset.py` | — | `test_the_record_digest_is_golden_frozen` | FLIP | **Task 4 新造红。成因**:`config/orchestration/presets/v2/luozi_deep_decide_v1.json` 的 `pv.microstructure` 过陈述订正 → 深度预设 record digest `696daf80…` 位移。**owner**:Task 5 重冻该 record digest。 |
| 51 | `tests/orchestration/test_pipeline_deep_preset.py` | — | `test_the_phase10_registry_holds_the_base_plus_the_deep_record` | FLIP | **Task 4 新造红。成因**:`config/orchestration/presets/v2/luozi_deep_decide_v1.json` 的 `pv.microstructure` 过陈述订正 → 深度预设 record digest `696daf80…` 位移。**owner**:Task 5 重冻该 record digest。 |
| 52 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `test_the_sealed_ten_node_record_digest_did_not_move` | FLIP | **Task 4 新造红。成因**:`config/orchestration/presets/v2/luozi_deep_decide_v1.json` 的 `pv.microstructure` 过陈述订正 → 深度预设 record digest `696daf80…` 位移。**owner**:Task 5 重冻该 record digest。 减证据预设**自己的** record digest 不动(它的预设文件未被触碰);本钉红的是它对**十节点**预设的引用。 |
| 53 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `test_the_phase10_catalog_reference_names_only_the_sealed_ten_node_preset` | FLIP | **Task 4 新造红。成因**:`config/orchestration/presets/v2/luozi_deep_decide_v1.json` 的 `pv.microstructure` 过陈述订正 → 深度预设 record digest `696daf80…` 位移。**owner**:Task 5 重冻该 record digest。 |
| 54 | `tests/orchestration/test_phase10_chain.py` | — | `test_deep_decide_preset_reference_binds_the_committed_sealed_record` | FLIP | **Task 4 新造红。成因**:`config/orchestration/presets/v2/luozi_deep_decide_v1.json` 的 `pv.microstructure` 过陈述订正 → 深度预设 record digest `696daf80…` 位移。**owner**:Task 5 重冻该 record digest。 本钉比对 golden 的 `deep_decide_preset_record_ref` 与**已提交的预设文件**,正是「改预设而不重冻目录必须响亮」的守卫 —— 响亮如设计地成立。 |
| 55 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `test_the_reduced_record_digest_is_golden_frozen` | FLIP | **Task 4 修订轮新造红(控制器裁决:减证据预设一并订正)。成因**:`luozi_deep_decide_reduced_v1.json` 逐字重复了同一句 `pv.microstructure` 过陈述;订正后它**自己的** record digest `2c4b4e0d…` 位移。爆炸半径实测**恰好这一条**:该字面量只出现在 `test_pipeline_reduced_deep_preset.py:125`,无任何 golden 落点;同文件的 `test_the_golden_is_load_bearing` 是不等式,保持绿。**owner**:Task 5 重冻该 digest。 |
| 56 | `tests/orchestration/test_phase8_handoff.py` | — | `test_point2_final_worker_semantic_digests_are_the_frozen_baseline` | FLIP | **Task 4 新造红。成因**:regime prompt 材料字节改动 → `lane0.regime.prompt` 内容摘要位移 → `market.regime` 的 WorkerSpec `system_prompt_ref` 变 → 其 semantic digest `4e1a747870f3…` → `813fe36ee5e4…`。实测**只有 `market.regime` 一项**变动,其余 6 个 final worker 逐字节不变。**owner**:Task 5 重冻 `FINAL_WORKER_DIGESTS`。 |
| 57 | `tests/orchestration/test_lane_batch_text.py` | — | `test_promise_supply_lint_clean` | GREEN-VERIFY | **Task 4 已治愈**(旧 §4 #29)。去掉 `ww_f10` 承诺后 `lint_skill_supply(text.news)` 由一条 issue 回到 `()`,**未改任何测试**。**owner**:Task 4 交付 / Task 5 出口必须仍绿。 |
| 58 | `tests/orchestration/test_phase8_registry_catalog.py` | — | `test_capability_promise_supply_lint_clean_across_every_seat` | GREEN-VERIFY | **Task 4 已治愈**(§11 D3,同一根因在全 27 席作用域上的第二处落点)。**owner**:同上。 |
| 59 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | — | `3b2ae16497fbf36327467b6be61b9ca9d77696ce0030a1f90f79697afc2d7172` | FLIP | `pv.technical` 的 WorkerSpec 语义摘要,随 D-1 收窄移动。**owner**:Task 5 重写。 |
| 60 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | — | `079de03217501ee09e306bd4ee265845553cd19729b8eb6247a81a51fcbdf332` | FLIP | `text.news` 的 WorkerSpec 语义摘要,随 D-1 收窄**与** Task 4 的 SKILL 字节共同移动。**owner**:Task 5 重写。 |
| 61 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | — | `b1775ea2a5579c9679204cfb73e8780d8b9c52a2ec51e19251c471486bfd8242` | FLIP | **Task 4 新增落点**:`skill.text.news@1` 的 skill-manifest 摘要,随 SKILL.md 去掉 `ww_f10` 移动。**owner**:Task 5 重写。 |
| 62 | `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` | — | `e794e0128f5fcc1d74fad4a977d9b597d07fb83af12772a633c0a01aa10d50c9` | FLIP | **Task 4 新增落点**:`bridge.memory_runtime.analyzer` 材料摘要,随 `_ANALYZER_BYTES` 改写移动。**owner**:Task 5 重写。 |
| 63 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | — | `696daf8044e55039a6c5d82ccb4e815d678d4952462ba0d1f971dc5f6a5cf6ca` | FLIP | 深度预设 record digest(golden 侧)。**owner**:Task 5 重写。 |
| 64 | `tests/orchestration/test_pipeline_deep_preset.py` | — | `696daf8044e55039a6c5d82ccb4e815d678d4952462ba0d1f971dc5f6a5cf6ca` | FLIP | 同上(测试常量第 1 处)。**owner**:Task 5 重写。 |
| 65 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `696daf8044e55039a6c5d82ccb4e815d678d4952462ba0d1f971dc5f6a5cf6ca` | FLIP | 同上(测试常量第 2 处)。**owner**:Task 5 重写。 |
| 66 | `guanlan_v2/orchestration/pipeline/api.py` | — | `permanent-honest grant-gap ruling` | FLIP | 文档钉:「sealed Phase-3 data-prefetch grants still cover `dec.pm` only … support-refuses with `tool_calls_required_unmet`」。授权后这段话是假的。**owner**:Task 5 改字(本行不产生测试红,但属再冻结清单,必须逐行走过)。 |
| 67 | `guanlan_v2/orchestration/pipeline/deep_decide.py` | — | `is a real product, not a fallback` | FLIP | `PresetSelectionRefused` docstring —— 减证据预设「存在理由」的散文半边。**owner**:Task 5 改字。 |
| 68 | `guanlan_v2/orchestration/pipeline/live_decide.py` | — | `SUPPORT-REFUSED at admission` | FLIP | 「the deep run is still SUPPORT-REFUSED at admission」(实测 2 处)。**owner**:Task 5 改字。 |
| 69 | `guanlan_v2/orchestration/pipeline/pv_aux.py` | — | `sealed prefetch binding grants it no row (L3)` | FLIP-IF | `AuxDataUngranted` 的消息字面量。**条件**:只在 D-1 给 `pv.price_action`/`pv.microstructure` 授行时;裁决未授它们 → 今天条件不成立。  **owner**：Task 5（仅当条件成立时）；今天条件不成立，它应保持绿。 |
| 70 | `tests/orchestration/test_pv_aux_nodes.py` | — | `AuxDataUngranted` | FLIP-IF | **条件**:只在 D-1 给两个 aux 座席授行时翻。本期授权对象是 `pv.technical`/`text.news`/`text.policy`,与它们不是同一批 worker → 两轮全套实测**全程绿**。  **owner**：Task 5（仅当条件成立时）；今天条件不成立，它应保持绿。 |
| 71 | `tests/orchestration/test_pv_aux_nodes.py` | — | `aux_data_ungranted` | FLIP-IF | 同上(类型化拒绝码的那一半)。两轮实测绿。  **owner**：Task 5（仅当条件成立时）；今天条件不成立，它应保持绿。 |
| 72 | `tests/orchestration/test_data_world_recipe.py` | — | `test_unsupported_method_refuses_through_production_staging` | FLIP-IF | **条件**:只有 D-1 选 B(建 `indicators` 后端)才翻;裁决取 A → 两轮实测绿。  **owner**：Task 5（仅当条件成立时）；今天条件不成立，它应保持绿。 |
| 73 | `tests/orchestration/test_pipeline_reduced_deep_preset.py` | — | `test_the_reduced_draft_is_runtime_supported` | GREEN-VERIFY | 减证据预设本来就 supported;它变红 = 授权改到了八节点通路上。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 74 | `tests/orchestration/test_runtime_support.py` | — | `tool_calls_required_unmet` | GREEN-VERIFY | 通用算术钉(合成 worker,非两位读者)。变红 = 动了 `check_runtime_support` 的 `tc is REQUIRED and min_sum < 1` 判据。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 75 | `tests/orchestration/data/test_phase_handoff.py` | — | `tool_calls_required_unmet` | GREEN-VERIFY | Phase-3 handoff 的同一判据存在性钉。两轮实测绿。**同一文件另钉 P2 静态摘要(§3 #1)—— 本期唯一不许移动的链节点 —— 亦两轮实测绿,这是「P2 不动」的正向证据,不是没去看。**  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 76 | `tests/orchestration/test_data_world_recipe.py` | — | `test_every_sealed_prefetch_row_route_equality` | GREEN-VERIFY | 每条新行都会进入这个迭代;照同一配方冻结路由就应保持绿。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 77 | `tests/orchestration/test_data_world_recipe.py` | — | `test_all_seven_methods_have_the_single_surface_route` | GREEN-VERIFY | 与新行无关(全七方法),作为红窗越界探针留表。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 78 | `tests/orchestration/test_data_world_recipe.py` | — | `test_the_real_session_cross_resolves_the_production_registry` | GREEN-VERIFY | 用真 `_frozen_route_for` 交叉解析生产注册表。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 79 | `tests/orchestration/data/test_data_catalog.py` | — | `test_grant_without_prefetch_row_fails` | GREEN-VERIFY | one-to-one 不变量的「有授权无行」方向。判据字节全程不动(计划红线)。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 80 | `tests/orchestration/data/test_data_catalog.py` | — | `test_prefetch_row_without_grant_fails` | GREEN-VERIFY | 同上的「有行无授权」方向。两轮实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 81 | `tests/orchestration/test_experience_rowless_admission.py` | — | `test_no_sealed_worker_can_trip_the_new_refusal` | GREEN-VERIFY | **最灵敏的越界探针**:一旦收窄/授权动到任何 worker 的 `read_categories` 或 `capability_allowlist` 就立刻红。Task 4 只动材料文本与散文,两轮实测绿。  **耦合提示（Task 2 记录、修订轮恢复）：**同文件的 `TestRunnableRequiredWorkerNotRefused`（min_sum≥1 不早拒的那条裁决钉）**复用 `test_runtime_support.py` 的私有合成桥手法**（`_Analyzer` / `_BridgeSpec` / `build_scenario`）——有意「复用而非重新推导」（密封目录表达不出「一个节点两条桥、边界不同」），代价是**改动该手法的签名会同时打断两个套件**，动它时两边一起看。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 82 | `tests/orchestration/test_lane_batch_text.py` | — | `test_skill_grammar_and_mirror_identity` | GREEN-VERIFY | **Task 4 的自证行**:`guanlan_v2/orchestration/skills/text.news/SKILL.md` 与 `config/orchestration/materials/skills/text.news.md` 镜像必须逐字节相等。Task 4 同步改了两处 → 实测绿;只改一处会立刻红。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 83 | `tests/orchestration/test_l3_material_texts.py` | — | `TestTheExperienceAnalyzerMaterialStatesTheRuledBehaviour` | GREEN-VERIFY | Task 4 新增钉(RED-first 证据:改前 19 failed / 13 passed)。实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 84 | `tests/orchestration/test_l3_material_texts.py` | — | `TestTheMemoryAnalyzerMaterialStatesTheRuledBehaviour` | GREEN-VERIFY | 同上。实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 85 | `tests/orchestration/test_l3_material_texts.py` | — | `TestTheDriftRecordingCommentsAreGone` | GREEN-VERIFY | 同上 —— 它钉的是「P10 期的 material-vs-code 漂移注释已被删除」;注释回归即红。实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 86 | `tests/orchestration/test_l3_material_texts.py` | — | `TestTheExperienceProviderMaterialStatesTheRuledBehaviour` | GREEN-VERIFY | **修订轮新增**：第三处漂移注释对应的 `lane0.experience.provider` 材料钉（含它与描述符的唯一镜像耦合）。实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 87 | `tests/orchestration/test_l3_material_texts.py` | — | `TestThePresetDescriptionMatchesWhatTheWorkerDoes` | GREEN-VERIFY | **修订轮扩充**：两个预设（十节点 + 减证据）的 `pv.microstructure` 描述钉 + 它们的实测半边（本预案零节点读 MicrostructureReport@1）。实测绿。  **owner**：standing——每轮红窗显式复核；它变红 = 改动越界，STOP 上报。 |
| 88 | `tests/orchestration/test_phase4_registry.py` | — | `test_upstream_goldens_are_byte_unchanged` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 89 | `tests/orchestration/test_phase6_registry.py` | — | `test_upstream_goldens_are_byte_unchanged` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 90 | `tests/orchestration/test_phase7_registry.py` | — | `test_upstream_goldens_are_byte_unchanged` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 91 | `tests/orchestration/test_phase8_registry_catalog.py` | — | `test_upstream_goldens_are_byte_unchanged` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 92 | `tests/orchestration/test_phase9_registry_chain.py` | — | `test_upstream_goldens_are_byte_unchanged` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 93 | `tests/orchestration/test_phase10_chain.py` | — | `test_upstream_goldens_are_byte_unchanged` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 94 | `tests/orchestration/test_phase10_handoff.py` | — | `test_chain_sealing_goldens_are_byte_identical` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |
| 95 | `tests/orchestration/test_phase10_handoff.py` | — | `phase9_catalog_golden_sha256` | FLIP | **L3 Task 5 新增(再生成期实测发现,非事后补票)。成因**:goldens 的**二阶落点** —— 六个期各自钉住其上游 golden **文件的字节 sha256**(`UPSTREAM_GOLDEN_SHA256`),这类跨物件守卫**用摘要值 grep 根本扫不到**(§11.3 结尾已把这一类漏算记录在案),所以 Task 1 的字面量清单结构上不可能收到它们。本期章程授权移动的正是这些 golden 文件,因此它们必然随之移动,归因完整。**8 处全部是 catalog manifest,零处是 schema manifest**(§12 的不动证明逐条复核)。**owner**:Task 5 一并重冻。 |

> **红窗纪律(计划 Global Constraints):** Task 4-5 期间,`tests/orchestration` 全套只允许在
> 本表 `FLIP` / `FLIP-IF`(且其条件成立)的条目上红。**本表之外的任何一处红 = STOP,上报。**
> Task 5 关窗后本表每一行都必须回到绿;此后任何 golden 位移都是回归。


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
| 移动的摘要 | `phase8_catalog_manifest_v1.json` 里两个 WorkerSpec 语义摘要(`pv.technical` `3b2ae164…`、`text.news` `079de032…`)。**P8 worker 摘要已在本期重冻范围内**,零额外爆炸半径。 |
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
且已被变异证明承重(§4 #79(`test_grant_without_prefetch_row_fails`) / §4 #80(`test_prefetch_row_without_grant_fails`) 两向钉)。放宽它会在两个方向上重新打开静默授权洞。

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
- 于是是**两行,不是四行**。P8 两个 WorkerSpec 语义摘要(`pv.technical` `3b2ae164…`、`text.news` `079de032…`)移动——已在本期重冻范围,零额外爆炸半径。L2-b supported-methods 守卫的 `granted == {"verified_snapshot"}` 字面量 → `{"verified_snapshot", "news"}`,有意识翻转(§4 #31(`test_every_granted_prefetch_row_is_within_the_supported_set`))。
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
- **爆炸半径(实现者实测)**:零额外——`capability_methods` 不动 → 其 P8 WorkerSpec 摘要不动;prefetch/descriptor 摘要本就移动(§2.3);supported-methods 守卫集合仍 `{verified_snapshot, news}`(§4 #31(`test_every_granted_prefetch_row_is_within_the_supported_set`) 已 FLIP)。若 grep 发现引用 text.policy 支持态的既有钉,按 §4 行格式入表(经解析器)。

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

## 10. 附:Task 4 已交付的密封材料对齐 + Task 5 仍需带走的东西

> **本节于 2026-08-11 修订轮按 Task 4 的实际交付重写。** 原文写的是「Task 4 未做、Task 5 会用到」,
> 在 Task 4 落地(`12bc075a` + 本修订轮)之后整节已经过时,连同它指向 §4 的那个编号一起。

**已交付(item 5,全部在本次重冻窗口内动的密封字节):**

| 落点 | 旧文本陈述 | 现状 |
|:--|:--|:--|
| `config/orchestration/materials/lane0/experience_support_analyzer.md` | 「a worker without a reviewed row fails analysis」(C3 的反面) | 已改写为按 allowlist 区分的裁决行为;摘要 `1a613689…` → `88cb5048…` |
| `guanlan_v2/orchestration/memory/catalog.py` `_ANALYZER_BYTES` | 「every activated reader needs exactly one closed query projection」 | 已改写:`rows == 0` = 从来不是评审读者 ⇒ 诚实零贡献;`rows > 1` 与 activation drift 仍然响亮 |
| `config/orchestration/materials/lane0/experience_provider.md` | 「an empty selection … still finalizes one successful call」(只描述 GRANTED 路径) | 已改写:该句显式限定为 GRANTED 路径,并在旁边写出 non-granted 那一臂;摘要 `5c319cd1…` → `7917cb2b…` |
| `config/orchestration/materials/lane0/regime_prompt.md` | 未提 `drivers`;anchor 规则无条件陈述 | 已要求 drivers 排序+去重,anchor 规则拆成两臂并注明 `unknown_reason` 归运行时 |
| 十节点深度预设描述的 `pv.microstructure` 过陈述 | 「…+ pv.microstructure + … 喂给辩论」 | 已订正为实测事实(本预案无节点读该 schema) |
| **减证据**预设描述的同一句 | 同上,逐字重复 | 已按同一方式订正(修订轮,控制器裁决) |

**三处 P10 期的 material-vs-code 漂移注释已全部删除**(`bootstrap.py` 的分析器与提供者两处、
`memory/catalog.py` 一处)。它们当初存在的唯一理由是「字节不能动」;字节动了,注释就必须走,
否则同一条不变量被陈述两遍、其中一遍是错的。
`tests/orchestration/test_l3_material_texts.py::TestTheDriftRecordingCommentsAreGone::test_no_drift_note_pattern_survives_in_either_module`
按**两种措辞**(`pre-Task-11 invariant` / `pre-ruling invariant`)+ 注释抬头 + `stay BYTE-FROZEN` 结论
一起钉死 —— 第一版只钉了一种措辞,于是从第三处漂移注释旁边直接走了过去。

**Task 5 仍需带走的:**

- 本文件 §3.1 列出的全部非链落点(逐条带 §4 行号,并由 gate 交叉验证)。
- **`CHECKER_VERSION` 要不要动 —— 重冻评审必须显式过一遍。** L3 Task 2 改变了
  `check_runtime_support` 会拒什么(新增 `rowless_read_category_unrunnable`),但
  `CHECKER_VERSION = "static-runtime-checker-v1"` 未动:它是 Literal 钉死的版本标记,动它会让
  **P5 schema manifest 位移** —— 在 L3 围栏外,所以本期**有意不动**。Task 5 要么把它一并挪、
  要么把「有意不动」作为一条书面决定记进重冻对照表;**不允许默认跳过**。
- §4 里 owner 写着 `Task 5` 的每一行。**计数按实测更正两次:**原文写「69 行(65 `FLIP` + 4 `FLIP-IF`)」,
  Task 4 重推后的实测是 **70 行(66 `FLIP` + 4 `FLIP-IF`)**;Task 5 在再生成期又实测发现 8 处
  二阶落点(golden **文件字节 sha** 钉,§4 #88(`test_upstream_goldens_are_byte_unchanged`) 起连续 8 行),入表后 Task-5 归属行 = **78 行
  (74 `FLIP` + 4 `FLIP-IF`)**,§4 总行数 95(78 + 17 `GREEN-VERIFY`)。
  三个数都由 gate 从表现算并断言,不是手抄。

**已不再属于本节的:** 「无行 experience worker(items 2+6)」由 L3 Task 2 交付,裁决见 §9 的
“RULING — dec.research_mgr experience row”,早拒绝见 `tests/orchestration/test_experience_rowless_admission.py`。

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
`test_phase8_registry_catalog.py::test_phase8_catalog_matches_the_frozen_golden`(§3 #17/#18,**并含 旧 §4 #19/旧 §4 #20 的两个 WorkerSpec 语义摘要**)、
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
   探针自己炸掉。`test_the_fifteen_p8_grants_stand_against_four_sealed_rows` 同理:17 组授权对 1 条行
   的实测事实,本期已变成 15 组对 4 条行。

**§4 没有任何一行指向门文件。** Task 3 按简报要求**未编辑门**。
**Task 5 必须给门补 §4 行并有意识翻转它**(候选行探针要换一个仍未授权的 (worker, method) 组合,
否则它证明的东西就没了)。

### D 组 —— 归不到任何既有行的红(7 项)—— **已上报控制器,待裁**

| # | 测试 id | 实测根因 | 类别 |
|---|---|---|---|
| D1 | `data/test_subject_projection.py::TestRealRowRoundTrip`(5 条,fixture `real_row` setup ERROR) | 该 fixture 断言 `[(r.worker_id, r.method_ref.id) for r in rows] == [("dec.pm","verified_snapshot")]` —— 即「绑定里**只有一条**行」。与 `data/test_data_catalog.py` 的 `verified_snapshot_row` 是**同一句 L1 期的话**,Task 3 只被授权改后者(在本任务 charter 内),前者不在 charter 内,**按红窗法未动**。 | 有意识翻转,机械 |
| D2 | `test_pipeline_screening.py::TestSubjectParamsStamp::test_zero_screening_workers_carry_data_prefetch_rows_today` | `assert targeted == {"dec.pm"}`,实测现为 `{dec.pm, pv.technical, text.news, text.policy}`。钉名里的 “today” 说明它本就是一条**记录当期事实**的钉;L1 Task 4 的注释也写着「vacuous behaviorally: zero screening workers carry data prefetch rows」——**这句话现在假了**:`text.news` 就是选股 lane 的 worker。 | 有意识翻转,**且连带源码注释需改** |
| D3 | `test_phase8_registry_catalog.py::test_capability_promise_supply_lint_clean_across_every_seat` | 与 旧 §4 #29 **同一个根因**(`text.news` 的 `ww_f10` 承诺无供给),只是在全 27 席的作用域上再钉一次。随 Task-4 去掉 `ww_f10` 承诺后**无需改测试即自愈**。 | Task-4 义务的第二处落点 |

三组都是「钉记录了 L3 之前的世界」,**没有一条指示实现缺陷**;但它们不在 §3/§4 任何一行里,
所以按红窗法(「归不到的红 = STOP,上报」)Task 3 **不自行修复、不自行入表**,列在此处等控制器裁,
由控制器决定是补 §4 行(#30-32)还是另行处置。

### 结论

- red ⊆ (§3 ∪ §4):**否** —— A 组 27 + B 组 8 = 35 项在表内;C 组 8 项归属清楚但表内无行;D 组 7 项待裁。
- **零红出现在 GREEN-VERIFY 行上。** 没有任何一处红指示越界或实现缺陷。
- goldens **未再生成**;`CHECKER_VERSION` **未动**;密封 schema 的三个 class docstring **未动**。

### 11.1 控制器裁决:C 组 / D 组处置 + 红窗操作账本(2026-08-11,Task 3 复审轮,逐字记录)

**红窗操作账本:** 自本次提交起、至 Task 4 重推为止,红窗的**操作性**允许集 =
**§4 ∪ §11(含本节处置)**。

**§4 本身在 Task 4 开工时从度量重推**(它已被实测推翻三次:`text.policy` 的 tool-call 归类、
四条 lane-batch 行为钉、本节 D 组)—— **逐行打补丁的做法到此为止**。重推经 gate 解析器,
每一处红都必须有归属,**归不到的 = STOP**。此项由控制器写进 Task 4 派工。

**C 组处置(`test_l3_refreeze_gate.py`,8 项):**

- 5 条**链/材料重度量**测试(`test_the_measured_chain_equals_the_refreeze_baseline`、
  `test_the_ledger_records_exactly_the_measured_chain`、
  `test_the_bridge_material_movement_is_measured_not_declared`、
  `test_the_ledger_records_the_measured_bridge_material_verdicts`、
  `test_the_provider_and_analyzer_materials_are_binding_INDEPENDENT`)——
  **在 Task 5 重钉**,取 §2 / §2.3 重冻后的新值。
- 3 条**候选行探针**测试(含 `_row_augmented_materials` 的使用者、
  `test_a_candidate_row_alone_trips_the_invariant_in_the_rows_direction`)——
  **Task 5 必须把探针重新指向一个仍未授权的 (worker, method) 组合**。
  **只把它改到能跑通、不重新指向 = 探针从此不再证明任何东西**(Task 3 的提示,现为记录在案的义务)。
- `test_the_fifteen_p8_grants_stand_against_four_sealed_rows` —— 重钉为 **15 组对 4 条行**。

**D 组处置:**

| # | 处置 |
|---|---|
| D1 `data/test_subject_projection.py::TestRealRowRoundTrip`(5) | **Task 5 FLIP** —— 与 Task 3 在 `data/test_data_catalog.py` 里被授权做的**同一行 fixture 改法**,只是换个文件。 |
| D2 `test_pipeline_screening.py::test_zero_screening_workers_carry_data_prefetch_rows_today` | **Task 5 FLIP**;**外加 `guanlan_v2/orchestration/pipeline/screening.py:~577` 的注释「zero screening workers carry data prefetch rows」为 Task 4 义务**(item-5 类散文,随 `ww_f10` 一起改)。 |
| D3 `test_phase8_registry_catalog.py::test_capability_promise_supply_lint_clean_across_every_seat` | **FLIP-IF,交叉引用 旧 §4 #29** —— 随 Task 4 改 SKILL.md 后**无需改测试即自愈**。 |

### 11.2 Task 7 必须计入的行为变更:每一次选股跑都会强制一次真新闻读取

`text.news` 的新行是 `always_invoke` + `success_requires_finalized_call=True`,而 `text.news`
**是选股 lane 的 worker**(`config/orchestration/presets/v2/screening_lane_v1.json` 九个 worker 之一)。
于是 **每条选股 lane、每一次跑,都会强制一次真实的 news 读取**,`cache_or_invoke` 的缓存旁路对它不适用
—— 这是 D-2 Option A 有意选择的语义(读不到就不许宣称成功),但它同时是一条**真实的成本 / 时延
行为变更**:选股是**批量**通路(实测 fixture 为 3 条 lane,生产按候选数量放大),
所以放大系数是「每批候选数」而不是 1。

**Task 7 义务:**(a) 把这项计入真跑的成本与时延预算;(b) **实测**并记录每批的实际读取次数与耗时,
不要按「和深度研判一样」估;(c) 顺带验证 §8.2 的第二种失败语汇 ——
news 断供 → `tool_discipline_unmet` / INCOMPLETE —— 在**选股**通路上也成立,而不只在深度通路上。

### 11.3 红窗实测红集 (Task 4) —— 收官测量与逐条归属

两轮全套 `python -m pytest tests/orchestration -q -p no:randomly`,同一棵脏工作树、同一环境,
差别**只有** Task 4 的改动集(按 sha256 备份/还原,逐字节可复现):

| 轮次 | 结果 | 红集大小 |
|:--|:--|:--|
| 改动前(HEAD `65510271` 字节,新测试文件移出树) | 45 failed, 5975 passed, 1 xfailed, 5 errors in 18:14 | **50** |
| 改动后(Task 4 首轮交付物在树) | 50 failed, 6002 passed, 1 xfailed, 5 errors in 18:16 | **55** |
| 修订轮(+ 减证据预设订正 + provider 材料 + 三处散文修) | 定向复跑,见下 | **56** |

**改前 50 红与 §11 记录的 Task 3 红集逐条相同** —— 简报要求的「Task 3 修订轮未改变红集」由此
**验证**(Task 3 修订轮本身没有重跑全套,所以这是该断言的第一次实测)。

**首轮转移(2 治愈 / 7 新增,全部命中重推后的 §4):**

| 方向 | 测试 | §4 行 | 归属 |
|:--|:--|:--|:--|
| 治愈 | `test_lane_batch_text.py::test_promise_supply_lint_clean[text.news]` | §4 #57(`test_promise_supply_lint_clean`) | 去掉 `ww_f10` 承诺;**未改测试** |
| 治愈 | `test_phase8_registry_catalog.py::test_capability_promise_supply_lint_clean_across_every_seat` | §4 #58(`test_capability_promise_supply_lint_clean_across_every_seat`) | 同上,27 席作用域的第二处落点 |
| 新增 | `test_lane_batch_text.py::test_new_seats_data_source_priority_names_real_tools` | §4 #49(`test_new_seats_data_source_priority_names_real_tools`) | 同一次 SKILL.md 编辑的**另一面**:该钉断言 `ww_f10 in news` |
| 新增 | `test_pipeline_deep_preset.py::TestPresetLoadAndSeal::test_the_record_digest_is_golden_frozen` | §4 #50(`test_the_record_digest_is_golden_frozen`) | 预设描述订正 → record digest 位移 |
| 新增 | `test_pipeline_deep_preset.py::TestRegistryExtensionChain::test_the_phase10_registry_holds_the_base_plus_the_deep_record` | §4 #51(`test_the_phase10_registry_holds_the_base_plus_the_deep_record`) | 同上 |
| 新增 | `test_pipeline_reduced_deep_preset.py::TestReducedPresetSeal::test_the_sealed_ten_node_record_digest_did_not_move` | §4 #52(`test_the_sealed_ten_node_record_digest_did_not_move`) | 同上(它钉的是**十节点**预设的引用) |
| 新增 | `test_pipeline_reduced_deep_preset.py::TestNoUpstreamMovement::test_the_phase10_catalog_reference_names_only_the_sealed_ten_node_preset` | §4 #53(`test_the_phase10_catalog_reference_names_only_the_sealed_ten_node_preset`) | 同上 |
| 新增 | `test_phase10_chain.py::test_deep_decide_preset_reference_binds_the_committed_sealed_record` | §4 #54(`test_deep_decide_preset_reference_binds_the_committed_sealed_record`) | 同上;这正是「改预设不重冻目录必须响亮」的守卫 |
| 新增 | `test_phase8_handoff.py::test_point2_final_worker_semantic_digests_are_the_frozen_baseline` | §4 #56(`test_point2_final_worker_semantic_digests_are_the_frozen_baseline`) | regime prompt 字节 → `market.regime` 的 WorkerSpec 语义摘要位移(**只此一项**) |

**修订轮转移(+1 新增,0 治愈,0 消失):**

| 方向 | 测试 | §4 行 | 归属 |
|:--|:--|:--|:--|
| 新增 | `test_pipeline_reduced_deep_preset.py::TestReducedPresetSeal::test_the_reduced_record_digest_is_golden_frozen` | §4 #55(`test_the_reduced_record_digest_is_golden_frozen`) | 减证据预设描述订正 → 它**自己的** record digest `2c4b4e0d…` 位移 |

修订轮的另外两处密封字节 **零新红**,与预测一致:

- `lane0.experience.provider` 材料改写(第三处漂移注释的那一处):它的摘要**没有任何字面量落点**,
  只经链摘要间接显形,而链摘要的每一个落点**本来就在红**。机械镜像(描述符)已同步。
- 三处散文修(`bootstrap.py` 的两句、`lane_catalog.py:18-21` 的模块 docstring)均**零摘要**。

**修订轮的验证方式(未重跑全套,按简报授权):** 定向复跑了改动波及的 **15 个测试文件 + 664 项**
(reduced/deep preset、phase5/6/7/8/9/10 registry+handoff+chain、lane_batch pv/text、
memory/data catalog、experience rowless admission、bootstrap e2e/plan/profile、lane0 reports/driver、
experience contracts)。结果与已记录的 55 条**逐条比对**:作用域内 26 → 27,
**唯一差异就是上表那一条新增**,零治愈、零消失。因此 **其它任何红集成员资格都没有变化**。

**预测对账(诚实记录):** Task 4 开工前预判 5 条新红,首轮实测 7 条。多出的两条是
`test_deep_decide_preset_reference_binds_the_committed_sealed_record` 与
`test_point2_final_worker_semantic_digests_are_the_frozen_baseline`。
两条**都在事后完整归因到 Task 4 自己的材料字节**,**没有一条指示实现缺陷,也没有一条落在
GREEN-VERIFY 行上**;但「预测 5、实测 7」本身说明两类传播在开工估算里被漏掉了 —— 记录下来,别粉饰:
①「已提交材料 vs live 目录」这类**跨物件守卫**(不是 golden 字面量,grep 摘要值根本扫不到它);
②「材料摘要 → WorkerSpec 语义摘要 → 另一个文件里的冻结基线钉」这条二级传播。
修订轮的 +1(减证据预设)是**事前预测并逐项验证过的**,爆炸半径预判与实测都恰好一条。

**结论:**

- `red ⊆ 重推后的 §4`:**是**。56 条收官红逐条落在 §4 的 `FLIP` 行上;
  零红落在 `GREEN-VERIFY` 行;四条 `FLIP-IF` 的条件今天都不成立,四条实测全绿。
- goldens **未再生成**(Task 5 的活);`CHECKER_VERSION` **未动**;
  三个注册模型的 class docstring(`ParamBinding` / `DataPrefetchOperation` /
  `DataBridgePrefetchBinding`)**未动** —— Task 4 改的两个分析器与一个提供者都是**普通类**,
  不是注册模型,所以 10 份 schema manifest 零位移。
- **P2 锚点未动的正向证据**:`data/test_phase_handoff.py`(§3 #1)与
  `fixtures/stock_deep_dive_equivalence_v1.json`(§3 #2)两轮全绿。

---

## 12. 对照表 —— 一次性重冻 (Task 5) 的逐摘要账 **[本节是关窗凭据]**

> **窗口自本节所在提交起 CLOSED。** 此后 `tests/orchestration` 全绿;
> **任何位置的任何 golden 位移都是回归**,不再有「本期在重冻」这个解释。
>
> 度量点:HEAD `a0fff3aa`(Task 4 收官)之后、Task 5 三提交序列
> (`dce0fdef` 再生成 → `8b817687` 有意识翻转 → 本提交 台账)。
> 链路按顺序再生成:P3-data → P3-full → P4 → P5 → **P6(恒等,重新推导而非假定)**
> → P7 → P8 → P9 → P10,**每一期的 base 在生成下一期之前断言 == 上一期的新鲜结果**。

### 12.1 链路顺序再生成的连接证明(实测输出)

```
=== chain linkage (each base asserted == the prior fresh result) ===
  OK  P3-data base == P2:      b41bf223f0dd vs b41bf223f0dd
  OK  P3-full base == P3-data: 13210944b6af vs 13210944b6af
  OK  P4  base == P3-full:     0601eef771be vs 0601eef771be
  OK  P5  base == P4:          8aea7bc0cb54 vs 8aea7bc0cb54
  OK  P6  base == P5:          af064cbaaa1e vs af064cbaaa1e
  OK  P7  base == P6:          af064cbaaa1e vs af064cbaaa1e
  OK  P8  base == P7:          b78b63e476bf vs b78b63e476bf
  OK  P9  base == P8:          78f79d330ae9 vs 78f79d330ae9
  OK  P10 base == P9:          e3a293615a21 vs e3a293615a21
  P6 == P5 identity: True
```

**P6 是重新推导出来的,不是抄的 P5。** 上表的 `P6 base == P5` 与 `P6 == P5 identity`
是两条独立断言:前者说它的基座正确,后者说恒等关系仍然成立。

### 12.2 移动的 20 个摘要身份 —— 逐条旧值 → 新值 → 成因

`成因` 列写的是**具体哪一次改动推动了它**,不是分类。
**任何一个「动了但归不到成因」的摘要 = STOP** —— 本次没有触发。

| # | 摘要身份 | 旧值(8) | 新值(8) | 成因(Task 3 / Task 4 的具体改动) |
|---|---|---|---|---|
| 1 | P3-data catalog | `ba708692` | `13210944` | **Task 3**:三条新预取行进入密封绑定 → prefetch 材料 + descriptor 字节 |
| 2 | P3-full catalog | `c13294e5` | `0601eef7` | 继承 P3-data + **Task 4**:`memory/catalog.py` `_ANALYZER_BYTES` 改写 |
| 3 | P4 catalog | `aefe0cf3` | `8aea7bc0` | 纯继承:base(P3-full)移动 |
| 4 | P5 catalog | `42af2460` | `af064cba` | 继承 P4 + **Task 4**:lane0 experience 分析器/提供者 + regime prompt 材料字节 |
| 5 | P6 catalog(恒等节点) | `42af2460` | `af064cba` | 同 P5(恒等);**已重新推导** |
| 6 | P7 catalog | `c760df02` | `b78b63e4` | 纯继承:base(P6)移动 |
| 7 | P8 catalog | `7f00dde4` | `78f79d33` | 继承 P7 + **D-1 收窄**两条 allowlist + **Task 4** SKILL/regime 字节 |
| 8 | P9 catalog | `0c48db78` | `e3a29361` | 纯继承:base(P8)移动 |
| 9 | P10 catalog | `ff4cdc61` | `f1b73052` | 继承 P9 + **Task 4**:`pv.microstructure` 过陈述订正推动深度预设 record ref |
| 10 | data `prefetch_binding_digest` | `96d24137` | `734d5c20` | **Task 3**:绑定里从 1 条行变 4 条 |
| 11 | `bridge.data_runtime.prefetch@1` | `0c9d38a7` | `cab80122` | **Task 3**:`serialize_prefetch_binding` 覆盖三条新行 |
| 12 | `bridge.data_runtime.descriptor@1` | `79706448` | `817d2882` | **Task 3**:内嵌 `config_ref`,随 #11 一起 |
| 13 | `bridge.memory_runtime.analyzer@1` | `b233d619` | `e794e012` | **Task 4**:`_ANALYZER_BYTES` 改写为陈述 C3 后的不变量 |
| 14 | `bridge.memory_runtime.descriptor@1` | `6e47c9cf` | `e04f2ccb` | **Task 4**:内嵌 memory 分析器 ref,随 #13 一起 |
| 15 | WorkerSpec `market.regime` | `4e1a7478` | `813fe36e` | **Task 4**:regime prompt 材料字节 → `system_prompt_ref` |
| 16 | WorkerSpec `pv.technical` | `3aacbf8b` | `3b2ae164` | **D-1 Option A**:`capability_methods` 收窄到 `("verified_snapshot",)` |
| 17 | WorkerSpec `text.news` | `2794a571` | `079de032` | **D-1 Option A** 收窄到 `("news",)` **+ Task 4** SKILL.md 去掉 `ww_f10` |
| 18 | skill manifest `skill.text.news@1` | `34bf82c2` | `b1775ea2` | **Task 4**:SKILL.md 撤回无供给的 `ww_f10` 承诺 |
| 19 | 十节点预设 record digest | `d7c5092f` | `696daf80` | **Task 4**:`pv.microstructure` 过陈述订正 |
| 20 | 减证据预设 record digest | `2c4b4e0d` | `f44646dc` | **Task 4 修订轮**:同一句过陈述在减证据预设里的订正 |

**衍生落点(不是独立身份,随上表移动):**
`pipeline.deep_decide.preset_record.ref@1` 材料 `1bcae024` → `99a8dd57`(内嵌 #19)。

**三个「移动了但无字面量落点」的 lane0 材料**(§3.1 已预告,此处补新值):
`lane0.experience.analyzer` `1a613689` → `88cb5048`、
`lane0.experience.provider` `5c319cd1` → `7917cb2b`、
`lane0.regime.prompt` `903e49ac` → `087c4b8a`;
其机械镜像 `lane0.experience.bridge.descriptor` 现为 `caac2c23`。
它们只经 P5 链摘要间接显形(P5 golden 只列 id,不列摘要),故不在上表编号内。

### 12.3 字面量落点计数(实测)

| 口径 | 实测 | 对照 |
|---|---|---|
| 链摘要 MOVES 字面量 | **38** | 与 §2.2 记录的 38 一致 |
| 非链摘要字面量(材料/worker/skill/预设 record) | **15** | — |
| 合计文本替换 | **53** | 逐处带身份打印,见任务报告 |
| golden **文件字节 sha** 钉(二阶落点) | **8** | §4 #88(`test_upstream_goldens_are_byte_unchanged`) 起 8 行 |
| P2 字面量(**必须不动**) | **5 处**(家族内 4 + 家族外 1) | 与 §2.2 的「42 − 4 处 P2」口径一致:那里数的是家族内的 4 处,家族外的 `fixtures/stock_deep_dive_equivalence_v1.json:11` 是第 5 处。替换脚本对每个文件断言 P2 出现次数前后相等,两个只钉 P2 的文件根本不在改动清单里 |

### 12.4 不动集的证明(脚本输出,逐条实测)

```
=== NOT-MOVING SET (live re-derivation vs the pre-window record) ===
  P2-static catalog_digest                 UNMOVED    b41bf223f0dd0b05
  bridge.data_runtime.analyzer@1           UNMOVED    3f93fdf29d28f266
  bridge.data_runtime.provider@1           UNMOVED    3a1e727b2e3f761b
  cap.data.fundamentals                    UNMOVED    ea363fbeddac909c
  cap.data.indicators                      UNMOVED    e6f78ef3870b86fb
  cap.data.instrument_names                UNMOVED    4ee0e3cb63b7b3f4
  cap.data.news                            UNMOVED    46557a55ada3737f
  cap.data.ohlcv                           UNMOVED    39b51cd0f630caff
  cap.data.signals                         UNMOVED    7dad246868beadbb
  cap.data.verified_snapshot               UNMOVED    ecea7d3bac5ff524
  data.render.deterministic                UNMOVED    482d2b4675e16bcc
  data.source.guanlan_datafeed             UNMOVED    9395a4d2c748dc25
  memory policy_digest                     UNMOVED    b9ec67390cbd9bb6
  memory prefetch_binding_digest           UNMOVED    9c4d97ba4f99c1a4
  planner_spec_digest                      UNMOVED    336b1e55cd49abc1
  source descriptor guanlan.online_live    UNMOVED    47112724f309e3f9
  source descriptor guanlan.pit_replay     UNMOVED    140d9e291c3b50bb
  mismatches: 0

=== THE SCHEMA MANIFESTS + THE P2 ANCHOR CARRIERS ===
  data_schema_manifest_v1.json                   IDENTICAL
  phase10_schema_manifest_v1.json                IDENTICAL
  phase3_full_schema_manifest_v1.json            IDENTICAL
  phase4_schema_manifest_v1.json                 IDENTICAL
  phase5_schema_manifest_v1.json                 IDENTICAL
  phase6_schema_manifest_v1.json                 IDENTICAL
  phase7_schema_manifest_v1.json                 IDENTICAL
  phase8_schema_manifest_v1.json                 IDENTICAL
  phase9_schema_manifest_v1.json                 IDENTICAL
  runtime_schema_manifest_v1.json                IDENTICAL
  schema_manifest_v1.json                        IDENTICAL
  stock_deep_dive_equivalence_v1.json            IDENTICAL
  test_phase_handoff.py                          IDENTICAL
  files compared: 13; moved: 0
```

**schema manifest 实测是 11 份,计划正文写的是 10。** 按本文件既有规则「实测集治,
spec 计数是交叉核对」,记录实测值;**11 份全部未动**,所以这个差异是口径不是漂移。
一份 schema manifest 移动 = 动了注册模型 = STOP —— 本次零触发,
三个被点名不可动的 class docstring(`ParamBinding` / `DataPrefetchOperation` /
`DataBridgePrefetchBinding`)本期未被触碰。

#### 12.4.1 度量方法本身的一次修正(必须记录,否则下一个人会重犯)

不动证明的第一版用 `sha256(worktree bytes)` 对比 `git show <pre>:path` 的 blob。
它报告 **8 份 schema manifest + P2 fixture「MOVED — STOP」**。**那是错的。**
本仓 `core.autocrlf=true`:blob 是 LF,工作树是 CRLF,于是逐字节比较把**行尾当成内容**。
改用 git 自己的对象身份(`git rev-parse <pre>:path` vs `git hash-object --path path path`)后
13 个文件全部 IDENTICAL。

**这条 CRLF 陷阱不只咬 `git checkout`,它同样咬度量工具本身。** Task 4 曾因此作废两轮测量;
Task 5 又在不动证明上撞了一次。凡是要断言「字节没动」的地方,**用 git 的对象身份,
不要用工作树原始字节**;`git status` 也不可信(见 §12.6 的 stat-cache 假阴性)。

### 12.5 §4 的 8 行新增 —— 再生成期发现的二阶落点

Task 1 的字面量清单靠 grep 摘要值构建,它**结构上不可能**发现「钉 golden **文件**字节 sha」
的那一类守卫 —— 那些常量不是任何摘要的值。它们在 goldens 被改写的那一刻才显形:

`test_upstream_goldens_are_byte_unchanged`(phase4/6/7/8/9/10 六处)、
`test_phase10_handoff.py::test_chain_sealing_goldens_are_byte_identical` 及其
`phase9_catalog_golden_sha256` 常量 —— 共 **8 处**,已入 §4 #88(`test_upstream_goldens_are_byte_unchanged`) 起连续 8 行,verdict `FLIP`,
owner Task 5,并已一并重冻。

**归因完整,不是无主漂移:**每一处都是「本期章程授权移动的那些 golden 文件」的字节 sha。
**8 处全部是 catalog manifest,零处是 schema manifest** —— 后者若出现一处即 STOP。

这是 §11.3 结尾那条诚实记录(「预测 5、实测 7」,漏算的两类传播之一是**跨物件守卫**)
在 Task 5 上的第二次兑现:**同一类漏算又发生了一次**,只是这次在再生成期就被抓住并入表。

### 12.6 `.gitattributes` —— 控制器裁决 C 的执行与验证

**背景(Task 4 记录、Task 5 复现):**`core.autocrlf=true` 且无 `.gitattributes`,
`git checkout` 会把 LF 的密封材料重写成 CRLF,**每个材料摘要当场改变**,而 `git status` 报「未修改」。

**Task 5 的实测(先上报、未擅自强推):**计划给定的单行
`config/orchestration/materials/** -text` 会让**一个**文件被 git 视为已修改 ——
`config/orchestration/materials/data/cn-a-share-sessions-2026.json`(工作树 CRLF / blob LF)。

```
index blob                                   = cb933627fdd868c381602ba3cc4e0e4a26de8fbc  (LF)
git hash-object --path 加了 `-text` 之后      = 17057b3d085a5158c05e9462c02fa1031ddc96e0  (CRLF 原始字节)
git hash-object --path 不加                   = cb933627…  (== index)
```

**`git status` 在这里给的是假阴性** —— 加了属性之后它依然安静,因为 stat cache 没有失效,
根本没去读内容。**权威方法是 `git hash-object --path`。**

**控制器裁决:变体 C** —— 单行属性 + 对该离群文件做一次性工作树规范化,
而不是给它开永久例外(「例外会把一个 checkout 产物固化成政策」)。执行与验证:

1. 用 `git show HEAD:<path>` 的原始输出以二进制写回工作树:5149 B / CRLF → 4897 B / LF;
   `git hash-object --no-filters` == index blob `cb933627…`。
2. 落 `.gitattributes` 单行 `config/orchestration/materials/** -text`。
3. **对全部 93 个受版本控制的材料文件**做 `git hash-object --path` vs index blob 比对:
   **0 处不一致**;`git status` / `git diff` 对 `config/orchestration/materials` 全空。
4. 日历经**真实生产装载器** `data_world._load_calendar_material()` 正常构造出
   `ImmutableTradingCalendar`;定向 `-k calendar` 全套 **40 passed**。
   (该文件的摘要是**解析式**的,不是字节式的,所以行尾规范化零摘要风险 —— 这一点是先测后做。)

**因此密封 `.md` 材料从此对 checkout 免疫**,Task 4 那种「两轮测量作废」不会再发生。

### 12.7 D-2 断供后果:两种语汇 × 两个预设

新增 `tests/orchestration/test_l3_d2_outage.py`,跑在**真**密封 P9 目录 / **真**生产装配 /
**真**每 run 主体投影 / **真** `worker.execute_node` 上,断供从**唯一的生产门**
(`adapters/data_world._BACKEND`)注入。

| 单元 | 预设 | 方法 | `optional` | 实测节点终态 |
|---|---|---|---|---|
| 1 | 十节点深度 | `pv.technical` / `verified_snapshot` | False(core) | `FAILED` / `bridge_execution_error` / `error_type=NotConfiguredError` |
| 2 | 十节点深度 | `text.news` / `news` | True | `INCOMPLETE` / `tool_discipline_unmet`(`finalized 0 < minimum 1`) |
| 3 | 选股 lane | `text.news` / `news` | True | 同上,A 线上逐字相同 |
| 4 | 选股 lane | core 语汇 | — | **结构上不可达,并按此钉住**:该 lane 九个 worker 只带 1 条评审行且其方法 optional |

**§8.2 的实测修正在此坐实:**计划正文说 `bridge_execution_error` 已在这条路上退役 —— 不成立。
L2-b 退役的是**无行 worker** 的拒绝路径;**有行 core 方法**断供确实落在 `bridge_execution_error`,
那就是诚实语汇。裁决 §9 以 §8.2 为准,本节是它的可执行证据。

**图层后果**(`TestTheTrunkSurvivesTheOutage`,带自己的变异对照):两位读者的每一条出边都是
`DEGRADE`,且主干 `sentiment→research-mgr→pm→trader` 的任何节点都不依赖它们 ——
这正是「aux 硬失败 → 主干仍走完 → run COMPLETED」成立的原因,也正是把 `DEGRADE` 翻成阻塞语义
会打红的那条钉。**真跑观测到的 COMPLETED 属于 Task 7,本文件不宣称。**

**一条值得留下的 harness 教训:**`refusal_sink=None` 会让 gateway 在桥阶段抛 `AttributeError`,
而 executor 忠实地把**它**报成 `bridge_execution_error` —— 一个穿着断供外衣的 harness bug。
换成真 sink 之后 `error_type` 才是关于数据源的证据,而不是关于测试的证据。

### 12.8 `CHECKER_VERSION` —— 有意识不动(书面决定,不是默认跳过)

`CHECKER_VERSION = "static-runtime-checker-v1"` **本期不动**,理由逐条:

- L3 Task 2 确实改变了 `check_runtime_support` 会拒什么(新增 `rowless_read_category_unrunnable`),
  所以「该不该 bump」是一个真问题,§10 要求本次重冻必须显式过一遍 —— 现过。
- 它是 **Literal 钉死的版本标记**,动它会让 **P5 schema manifest 位移**。
  而 schema manifest 位移 = 动了注册模型 = 本任务的 STOP 类,**在每一条裁决的授权之外**。
- 控制器默认即「记录不动」;本次实测未提供任何推翻该默认的理由(§12.4 的不动证明正是它的反面证据:
  11 份 schema manifest 全部未动,本期没有任何一处越过那条线)。

**结论:有意不动,已记录。** 若将来要 bump,它必须走自己的 charter,并自带一次 schema
manifest 位移的评审 —— 不允许再搭任何重冻的便车(本期是唯一被授权的重冻窗口,且已关闭)。

### 12.9 交叉引用格式加固(Gate 8 扩展)

`test_every_section4_cross_reference_resolves` 只检查**已经**写成 `§4 #NN(`token`)` 的引用;
写成裸 `§4 #NN` 的引用**整个绕过它** —— §3.1 第一版整体差 +5 就是这么活下来的。
本轮把格式本身变成强制:

- 新增 `test_no_bare_section4_citation_survives_outside_the_enforced_format`:
  ledger 里每一处 `§4 #NN` 要么带见证 token,要么显式标 `旧`。
- 新增 `test_the_historical_escape_hatch_is_bounded`:`旧` 不是免检通道 ——
  其总数被钉死(6),且 `旧` 与 token **互斥**(历史引用不得同时声称能解析到今天的行)。
- 逐条处置:`:221` 转成强制格式;**live 散文**里的三处按今天的编号**改对**
  (旧编号 `#14/#15` → 今 #79/#80;两处旧 `#9` → 今 #31;此处刻意不写 `§4 ` 前缀,以免这段说明本身被解析成一条待解析的引用);**§11/§11.1 的历史记录**里的三处标 `旧`
  (它们引用的是 Task 4 重推前那张 29 行表,按定义不该解析到今天的行)。

### 12.10 关窗测量 —— 红窗从 56 红回到全绿

同一棵脏工作树、同一环境、同一命令 `python -m pytest tests/orchestration -q -p no:randomly`:

| 轮次 | 结果 | 红集 |
|:--|:--|:--|
| **翻转前**(Task 5 开工基线,goldens 未再生成) | `51 failed, 6009 passed, 1 xfailed, 5 errors in 1085.19s` | **56** |
| **关窗后**(三提交序列全部落地) | `6079 passed, 1 xfailed in 1081.76s` | **0** |

**开工基线的 56 红与 §11.3 记录的 Task 4 收官红集逐条相同。** 这两次测量由不同任务、
相隔数日、各自独立完成,数字对上 —— 所以「Task 5 接手时红集未变」不是假设,是实测。
该清单存档于任务报告,每一条翻转都指名它在其中的成员身份(这就是双向证据的「红」那一半)。

**唯一的 xfail 已具名并有章程:**
`test_pipeline_candidates.py::test_lane0_happy_path_spec_for_a_code_bearing_upstream`
—— `xfail(strict=True)`,理由写在装饰器里:「ratified D4:上游今天不携带龙头股票代码
(`test_phase10_handoff.test_d4_rotation_report_carries_no_leader_codes` 钉住这项缺席)。
这是未来 happy-path 的规格 —— 当那道门变红、抽取落地时,本条 XPASS 并**强制 D4 重新评审**。」
它是**有意的未来规格**,不是被容忍的失败;`strict=True` 保证它一旦通过就必须有人来处理。
(简报提到的 BJ-920 那条 xfail 已在 L2-b 期翻面,本树上不存在。)

**此后任何一处 golden 位移都是回归。**

### 12.11 独立复审条件记录(2026-08-11,复审判定 READY TO PROCEED: YES)

独立复审对 P3-data / P10 两端做了独立再推导、逐行审 §12.2 对照表、逐条审翻转的双向证据,
并用穷举 hex 集差确认**零无主位移**。判定通过,附一条 actionable 条件 + 三条挂账 + 一条 Task 7 交底。

#### 已闭环(本轮微修)

- **A-1(actionable):** `test_l3_refreeze_gate.py` 的 gate item 1 取
  `surface.prefetch_binding.operations[0]` 并当作 `dec.pm` 的行 —— 绑定从 1 条长到 4 条之后,
  它**只是按排序恰好还是** `dec.pm`,不是按身份。已改为按 `worker_id == "dec.pm"` 取,
  并加断言 `method_ref.id == "verified_snapshot"`。与
  `data/test_data_catalog.py::verified_snapshot_row`、
  `data/test_subject_projection.py::real_row` 是同一个改法、同一个理由。

#### Task 7 必须先做的事(I-2)—— **不是运行期才发现,是开工前就要排进计划**

`var/orchestration` 里有 **72 个 payload + 47 行 `plan_approvals.jsonl`**,全部绑定在
**重冻前**的 P9 目录摘要 `0c48db78` 上。`approval.py:871` 的 lease 匹配键是
**(preset_id, preset_record_digest, catalog_digest, registry_digest)** 四元组,
而**不匹配时它 fall through 到「问人」**—— 这是**静默**的,不报错。

后果说白:**每一条重冻前的 `ApprovalLease` 都已经停止自动准入。** 这不是缺陷
(设计上就该如此:目录变了,旧批准不该继续生效),但它**看起来像什么都没发生**,
于是极易在真跑中途被误读成「审批坏了」。

**Task 7 义务:把「重新铸 lease」写进开工步骤,而不是留给运行期去撞。**
本期不动它 —— 重铸属于 Task 7 的真跑准备,不属于重冻窗口。

#### post-L3 backlog(复审提出,本期不动)

- **I-1 · 第三类清单逃逸:对「本次改动会让其变长的集合」做位置/下标访问。**
  它和已知两类并列,且**最阴**:摘要值 grep 扫不到它,文件 sha grep 也扫不到它,
  而失效方式是**静默**的(指到了另一行却依然全绿)。下一次爆炸半径清单**必须加第三条 grep**。
  复审已逐点分类了剩余位置:
  **良性**(取到的行随即被 `model_copy` 整体改写,或本就只当形状模板用) ——
  `test_l2b_handoff.py:203/426/441`、`test_data_world_recipe.py:185`、
  `test_production_data_provider.py:819/1144`;
  **自我保护**(取到行后立刻断言只有 `dec.pm` 行才满足的形状 ——
  `cache_or_invoke` + `success_requires_finalized_call=False` + `row_min_finalized=0`,
  取错行当场红) —— `test_l3_refreeze_gate.py:1420`、`test_pm_two_bridges.py:738`。
  两类都**不改**:良性的改了没有收益,自我保护的本来就带着自己的证明。
  记录在案的是**判据**,不是「以后有空再说」。

- **M-1 · golden 的字节 sha 钉读的是工作树字节,而工作树是混合 EOL 的。**
  于是这些 sha 是**机器相关**的。这是**既有**状况(不是本期引入):本次再生成
  **逐文件保持了原有 EOL 分类**,所以没有把它变坏,但也没有把它治好。
  §12.4.1 立的规矩(要断言「字节没动」就用 git 对象身份,别用工作树原始字节)
  **尚未推广到 `tests/orchestration/golden`**。推广它 = 一次单独的 charter。

- **M-2 · `test_l3_d2_outage.py:679` 的阳性对照把断言在自己体内重写了一遍。**
  对照因此验证的是「这段重写的逻辑会红」,而不是「上面那条真断言会红」——
  两者今天等价,但会各自漂移。改法:抽一个共享 helper,让对照绑定**真**断言。

#### 计划正文的历史值标注(M-3)

`docs/superpowers/plans/2026-07-31-orchestration-L3-grants-refreeze.md` 的 Ground-truth
段落里那条链路值(`ba708692 … ff4cdc61`)现在是**重冻前的历史值**。已在该处加一行标注,
指向本文件 §12.2 的当前链路。不改数值本身:那段文字记录的是 07-29 侦察时的实测,
把它改成今天的值等于伪造一次历史测量。

