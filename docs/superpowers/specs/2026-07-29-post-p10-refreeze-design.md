# Post-P10 Re-freeze Phase — 设计(立项)

- 日期:2026-07-29
- 状态:**已立项,未执行**(Phase 10 交付时由控制器裁决 A 归入本期)
- 前置:Phase 1–10 全部完成并合入 main(`4baf2f3`,套件 5392 绿)
- 上游文档:`docs/superpowers/specs/2026-07-18-orchestration-integration-design.md`(接入设计)、`docs/superpowers/plans/2026-07-18-orchestration-phase10-integration.md`(P10 计划)、`.superpowers/sdd/progress-orchestration.md`(全九期+P10 台账,含本期清单来源)

---

## 0 · 这一期为什么必须存在

Phase 10 把编排内核接到了帷幄选股与落子买卖点两条产品线上,**时序被证明了,决策那一半还没通电**。

具体说:今天在生产上开 `GUANLAN_SEATS_DEEP=1`,深链每一次升级都会走到 `deep_outcome="refused"` —— 这不是 bug,是 **裁决 B** 的诚实产物:P3 的审阅授权表结构上装不下深链需要的两行数据授权,而强行改它会挪动 P3→P9 的密封摘要链。P10 选择了"诚实拒绝"而不是"假装支持",并把缺口钉成了不可满足的 strict-xfail。

**本期就是把那两行授权真正审下来、把因此位移的密封链重新冻结的那一期。** 在它完成之前:

> 🔴 **红线:两条产品线都不得跑真 LLM 生产。** 框架跑得起来 ≠ 决策跑得起来。

---

## 1 · 阻断真 LLM 生产的五个结构事实(全部经源码核实)

| # | 事实 | 位置 | 为什么绕不过 |
|---|---|---|---|
| A | 审阅授权表只有一行 `dec.pm → verified_snapshot` | `data/catalog.py:95-97` | `pv.technical` / `text.news` 不是 Phase-2 worker,`phase3_data_catalog_snapshot` 在 `:845` 直接 StopIteration |
| B | 授权与预取行必须**一对一** | `data/catalog.py:766-773` (`granted != rows` 即 raise) | 只加授权不加行、或只加行不加授权,两个方向都被拒 |
| C | 一个 bridge_id 只允许一个描述符 | `catalog_runtime.py:640-644` (`BridgeCatalogView.build`) | 想在 Phase-10 层"叠加"一份补充授权来绕开 P3,结构上被拒 |
| D | `always_invoke` 不得由 `tool_calls=REQUIRED` 反推 | `data/catalog.py:150-153`(显式禁止) | REQUIRED 的算术需要 `always_invoke` 行,只能审下来,不能推出来 |
| E | `dec.research_mgr` 的经验行无法诚实派生 | `bootstrap.py:278-316` / `:477-481` / `runtime_support.py:676-679` | `ExperiencePrefetchBinding@1` 要 worker 没有的特征向量指针;空 allowlist 分析器拒绝;`tool_calls=FORBIDDEN` 与该行 Literal 钉死的 min=1 冲突 |

**连带代价(本期的主要工作量):**任何一处改动都会移动 P3→P9 的密封目录/注册表摘要链。2026-07-29 侦察实测:**11 个 digest、42 处字面量、散在 16 个文件**(9 个 golden manifest + 7 个 handoff 测试)。链路(每期的 base == 上期结果,全部实测):P2 `b41bf223` **不动** → P3-data `ba708692` → P3-full `c13294e5` → P4 `aefe0cf3` → P5 `42af2460` → P6 同值(恒等节点)→ P7 `c760df02` → P8 `7f00dde4` → P9 `0c48db78` → P10 `ff4cdc61`。**不动的**:7 条 data_capabilities、analyzer/provider 材料、渲染器与源句柄、planner_spec_digest、以及 10 个 schema manifest(前提是不碰 schema)。九期以来"上游 golden 零位移"的纪律到此为止;本期是唯一被授权移动它们的一期,因此必须**一次性、成建制、带完整再审**地做。

---

## 1.5 · 2026-07-29 侦察:补授权**不等于**真跑(必读,改变了本期的定义)

多 agent 侦察 + 控制器逐条源码复核后确认:**把两行授权补上,只会让 `check_runtime_support` 变绿,不会让任何数据被真正读到。** 三个新事实:

| # | 事实 | 证据 |
|---|---|---|
| F | **数据桥从未在生产执行过** | `DataRuntimeWorld(...)` 全仓仅在 `tests/orchestration/data/test_runtime_integration.py` 构造过;`data_runtime_provider_factory`(`data/runtime.py:653`)无任何生产调用者 |
| G | **标的代码到不了数据桥** | `ParamBinding.source_kind` 封闭为 `node_param\|input_value\|const`(`data/catalog.py:112-141`)。封存 v2 preset **结构上禁止节点参数**(`pipeline/assembly.py:945-948`「a sealed v2 preset is structurally code-free」,十节点全 `params=None`);`input_value` 未实现(`data/runtime.py:392-411` 直接抛);`const` 语义错。标的今天**只经 `SubjectPromptAssembler` 的 prompt 注入**到达 worker,不经数据桥 |
| H | **今天唯一存在的 `dec.pm` 授权本身就是死的** | 其绑定取 `/asof_date`、`/code` 两个 node param,而 `dec.pm` 的 `params_schema_ref=None`,`spec.py:951-954` 规定此类 worker 带 params 即 `params_not_allowed` → **任何计划(封存或动态)都不可能满足它**。从未炸过只因 F |

**因此本期的真实分层**(补授权只是第三层):

- **L1 · 标的→数据桥的通路(设计裁决 D-0,阻塞一切)**。三个候选,均有代价:(i) 物化时从 `RunSubject@1` 盖参数(推荐:preset 记录仍无 params 故 preset digest 不动,且顺带治好 H;代价=plan 身份变成按票,失去"每只股票草案逐字节相同/一次租约"的性质);(ii) 实现 `input_value` 并给两个 worker 声明 `subject` 输入(移动两个 P8 worker 语义摘要);(iii) 新增 `ParamBinding.source_kind`(爆炸半径最大,动桥句柄材料字节与 `worker.py` ABI)。**未裁决前不得开工。**
- **L2 · 生产数据运行时接线**:构造 `DataRuntimeWorld`、按 source id 注册真数据源、把 provider factory 绑进执行器。**今天完全不存在,工作量大于 L1+L3 之和。**
- **L3 · 补授权 + 重冻**(原§2.1 第 1–3 条)。另有两个待裁决点:**D-1** 一对一不变量按 worker 还是按能力(字面一对一会逼出四行,而 `indicators`/`fundamentals` 无后端适配器);**D-2** `invocation_mode="always_invoke"` + `success_requires_finalized_call=True` 是清除 `tool_calls_required_unmet` 的必要条件(`data/catalog.py:186-190`),意味着**数据中断即节点硬失败**——对新闻读取器是一个真实的生产行为选择。

**唯一能在 L2 之前拿到真 LLM 深链判断的路**:去掉 `pv.technical`/`text.news` 两个要求 tool-call 的旁证节点(它们只以 `degrade` 策略流进 bull/bear,决策主干 `sentiment→research-mgr→pm→trader` 全不依赖),八节点 preset 的支持检查即通过。已向用户呈报,用户选择**先修 H 这个潜伏缺陷**再定路线。

---

## 1.6 · 2026-07-29 真机首跑取证:最后一环不是 preset,是 Lane 0 从没跑过

减证据版 preset 交付后(`90aa7e6`,八节点,支持检查实证为绿),控制器在**生产装配**上真跑了一次落子深链——不是测试夹具:真存储绑定(14 命名空间)、真行情(宁德时代 396.84)、真 `build_production_decide_fn`、真 `config/llm.yaml` 座位、经策略 `deep_research` 开关正当 opt-in。

**结果:**

- **快线真调 LLM 并出了真判断**:观望 / 置信 70,理由引真几何(「突破前5高但量比仅1.3,未达1.5~4区间;且近3K含趋势阴,突破后跟随确认不足」)。
- **升级真触发**(`deep_attempted=true`),租约通道已备(有界租约:`max_admissions=1`、额度 6 次 LLM、绑死记录摘要)。
- **深链诚实拒绝**:`no committed ContextSnapshot`。

去看生产存储:**`var/orchestration/` 里除了手工签的那份租约空无一物**——零事件、零载荷、零快照。

> **事实 I:Lane 0 从未在生产上跑过一次;而它是 `ContextSnapshot` 的唯一产出者,深链、选股链、任何编排运行都要读它。启动器 `GUANLAN_ORCH_LAUNCHER` 默认关闭,且它只绑 replay/shadow 路由——生产里没有任何东西能驱动一次 Lane-0 运行。**

这条取证把优先级重排了(**本节覆盖 §1.5 的分层顺序**):

| 层 | 内容 | 顺序理由 |
|---|---|---|
| **L2-a · Lane-0 生产驱动器** | 建出那个从不存在的驱动器:生产 PIT 输入 → 引导计划 → 真准入 → 真网关执行 → 提交 `ContextSnapshot` | **升为第一件事**:所有编排运行的共同前置,没有它后面全是空谈 |
| **L1 · 标的→数据桥通路** | 裁决 D-0 | **降级**:只影响两个旁证 worker,不挡主干跑起来 |
| **L2-b · 数据运行时接线** | `DataRuntimeWorld` + 真数据源 + provider 绑执行器 | 仍是最大块,排 L2-a 之后 |
| **L3 · 补授权 + 重冻** | 两行授权 + D-1/D-2 + 一次性重冻 | 不变,最后 |

教训(值得单独记):**测试树 5452 条全绿,真机第一脚就踩到了测试永远踩不到的东西**——不是逻辑错,是"生产存储还是处女地"。这与九期以来反复出现的那一类抓(e2e 标绿 ≠ 生产跑过)同源。

---

## 2 · 交付边界

### 2.1 必做(解除生产红线的充分条件)

1. **审下两行数据授权**:`pv.technical` / `text.news` 的方法授权 + 与之一对一的预取行,走 P3 的既有审阅入口(不是新机制)。
2. **审下 `dec.research_mgr` 的经验行**:先裁决 `tool_calls` 的 FORBIDDEN↔min=1 冲突往哪边让;`ExperiencePrefetchBinding@1` 缺的特征向量指针从哪来(补 worker 能力,还是改绑定契约)。
3. **成建制重冻**:P3→P9 受影响的密封 golden/字面量**一次性重新生成 + 逐条再审**,附一张"哪些摘要为什么动了"的对照表。禁止逐个任务零敲碎打地挪。
4. **有意识地翻掉那颗 xfail**:`test_pipeline_deep_preset.py` 里承载缺口的 strict-xfail 必须**在同一提交里**从"钉住缺口"翻成"钉住支持",并保留翻转前后的双向证据。
5. **密封 material 文本与裁决后行为对齐**:`bootstrap.py:463` 与 `memory/catalog.py:270` 的字节冻结 handler 文本,今天陈述的是与裁决 C/C3 **相反**的不变量(P10 只能在注释里记录漂移,不能改字节)。goldens 本期本来就要动,顺带一起改正;同批修 `pv.microstructure` 描述的夸大。
6. **晚失败改成早拒绝**:rowless 经验 worker 出现在 DYNAMIC 计划里时,今天在准入通过、执行期 gateway 才失败(`max_capability_invocations=0`)。P10 已钉住"失败是响亮且类型化的";本期把它提前到准入拒绝。

### 2.2 必做(真跑之前的生产健壮性)

7. **深链失败的耐久回执**:持续失败无回执 → 每 tick 重升级,直到共享预算耗尽;**并且要覆盖每日 24 次的池子那一侧**——每 tick 重记会耗尽 watcher 预算,把快线在 tick 闸上饿死(终审加严项)。
8. **审批 journal 的压实**:replay-skip 的 WARNING 量级是 **O(耐久 journal) 每次构造**(不是每进程一次),与 `state_cells.jsonl` 的按 run 增长一并做压实。
9. **provider 接缝串行化 = LLM 吞吐天花板**(终审点名,面呈用户):真跑起来会先撞这个,不是撞模型。

### 2.3 应做(挂账,按需取用)

`_reviewed_material_universe` 记忆化(P9↔P10 preset 文件耦合的爆炸半径)· `/screening/latest` 的组装→渲染→落地触发器命名 · `plan_diff_ref` 悬空载荷 · 选股预算闸的账本身份断言 · `GET /state` 的"存储未绑"与"确实为空"分型徽章 · `ta_ingest` 读后追加的 TOCTOU 说明 · planner 花费与 RunBudget 两套独立上限的合并 · 链节点升格(`cand.*` 可被 planner 调度 + 选股 preset 进目录引用) · pools 缓存按 plan digest 无界。

### 2.4 不做

- 不动 `run_graph` / `engine` fork;
- 不借本期夹带新 worker、新 lane、新产品面;
- 不放宽任何"诚实拒绝优先于静默降级"的既有闸门——本期是**把拒绝变成支持**,不是**把拒绝变成沉默**。

---

## 3 · 纪律(本期特有)

1. **一次性重冻**:golden 位移集中在一个成建制的提交序列里,每一条位移都要有"为什么动"的书面理由;任何一期后续再动 golden 都视为回归。
2. **翻转必须有意识**:凡是 P10 里钉着"缺口存在"的测试,本期必须显式翻面并留下双向证据,不允许因为"顺带就绿了"而无声消失。
3. **变异证明**:每一条新的承重闸(授权一对一、经验行、早拒绝)都要 mutate→red→revert。
4. **提交卫生**:显式 pathspec;本仓长期存在并发 session 的未提交工作(见 §5)。
5. **真跑前的最后一步**:两条产品线各跑一次**真 LLM 的端到端**,并把"这次真跑证明了什么/没证明什么"写进台账——九期以来"e2e 标绿 ≠ 生产跑过"的教训。

---

## 4 · 完成的定义(Exit Gates 草案)

- [ ] `pv.technical` / `text.news` 的授权与预取行审下并一对一;`build_phase3_catalog` 通过;
- [ ] `dec.research_mgr` 的经验行诚实成立(冲突裁决落文档);
- [ ] 深链 preset 的 `support_report.supported is True`,承载缺口的 strict-xfail **已有意识翻面**;
- [ ] 生产深链一次真跑产出真决策产物(非测试夹具),且台账写明证明了什么;
- [ ] 位移的 golden 全部逐条再审并附对照表;此后套件全绿;
- [ ] 密封 material 文本与裁决后行为一致(不再靠注释记录漂移);
- [ ] rowless 经验 worker 在准入期即被拒(不再晚失败);
- [ ] 深链失败有耐久回执,且 24/日池子侧不会饿死快线;
- [ ] `GUANLAN_SEATS_DEEP` 未设时 watcher 行为仍逐位不变(P10 回归重跑)。

---

## 5 · 执行前须知(交给执行者)

- **合并雷已了结**:P10 已合入 main(`4baf2f3`)。BJ-920 那颗 xfail 与并发的北交所号段修复(`296bd02`,分支 great-meitner)仍会对撞——那条分支合并时须翻钉 + 改 `candidates.py:41-44` 的文档陈述。
- **工作树长期不干净**:`console/` `datafeed/` `glmcp/` `ui/screen/` `.data/wisdom/` 等携带 2026-07-16 起的未提交工作;任何提交只用显式 pathspec,绝不 `git add -A`。
- **生产以脚本形式跑 `server.py`**:模块层 `import guanlan_v2.*` 必炸而全套件仍绿(见 `watchdog-9999` 记忆的坑④);本期若碰启动路径,守护测试必须跟着跑。
- **改后端要重启 9999 才生效**;先在 9998 验证再动 9999(9999 被杀会触发看门狗代际轮换)。
