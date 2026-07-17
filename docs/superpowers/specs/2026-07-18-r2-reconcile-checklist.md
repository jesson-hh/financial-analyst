# R2 → Phase 4–9 六 plan · Reconcile 总清单

日期:2026-07-18 · 状态:**已施改完毕**——用户裁决(2026-07-18):R1–R16 全按默认;六 plan 已按本清单改完(P4/P5/P6/P8/P9 计 +526/−169 行,P7 零改动),小 phase 甲乙已立项为独立 plan(`2026-07-18-datafeed-snapshot-archive.md` / `2026-07-18-events-store-v1.md`)。本文档转为施改审计底稿。
来源:7 个并行分析员逐字通读六份 plan(4/5/6/7/8/9)+ R2 spec + 四件交付物;每节格式统一为 A 新增 / B 修改(带 plan 行号锚)/ C 明确不改 / D 疑点。
用法:先裁 §1 拍板表 → 对照 §2–§7 各节改对应 plan → §8 两个小 phase 独立立项(时间闸,建议立刻排)。

## §0 冲击总览

| plan | 新增 | 修改 | 一句话 |
|---|---|---|---|
| Phase 4 evaluator-governor | 1(D6 节流原语) | 4 处注记/门 | 治理原语收口:节流规则+修订族恒等约定+席位权重未来落点声明 |
| Phase 5 bootstrap-lane0 | 1(渲染契约 Task 3b) | 5 组(电池 15→17、三态、字段对齐①、公式修、RegimeReport 对齐④) | **冲击最大之一**:交付物①④ 就是本 plan 的规格 |
| Phase 6 shadow-consumer | 1(仓位带词表+分批触发契约) | 6 处 | 趁 @1 未冻结吸收 trader 仓位带,免 Phase 8 被迫 @2 |
| Phase 7 dynamic-planner | **0** | **0** | 全部机制已天然吸收(选择域目录动态派生),零改动 |
| Phase 8 lane-workers | 4(D11 生成器/#26/#27+词典/六注入面) | 8 组 | **冲击最大**:plan 零处引用交付物②③,必须显式接入 |
| Phase 9 adapters | 2(归档 coverage 进 manifest/handoff 增补) | 5 处 | replay 历史因子数据来源必须落 driver 正文 |
| 小 phase 甲/乙 | 立项 | — | 快照归档 5 任务 + 事件库 5 任务,时间闸,与 Phase 4 并行 |

## §1 拍板表(R1–R16,汇总各节 D 段;带我的默认建议)

| # | 争议 | 默认建议 |
|---|---|---|
| R1 | #27 形态回放装进 Phase 4 v1 契约还是等 @2(candidate_kind 无 pattern_definition、指标无盈亏比;golden 冻结前是最后窗口) | **现在加**(Task 9 冻结前):`candidate_kind="pattern_definition"`+`source="pattern_replay"`+`profit_loss_ratio/n_occurrences`;形态回放指标 R2 已明文,非投机 |
| R2 | D6 频率词表:Phase 4 常量表 vs frequency Literal 化进契约 | 常量表按 monthly/daily 键+未知拒绝;**不 Literal 化**(保 family 恒等与 golden 稳定) |
| R3 | 因子 id 粒度:①表复合行(limit_strength 双序列/ladder 双指标)vs FactorSeries 单序列 | ①表行=因子组;**落地 id 每序列一个**(17 行→约 20 id),Phase 5 实现定 id 清单,渲染按族分组 |
| R4 | 晋级率口径:plan 冻结 limit_days>=2 分子 vs ①写"首板晋级率" | 归档前 v1 用已实现口径+DEGRADED 标注;归档数据够后切首板口径=新 definition_version |
| R5 | val.pct 源真伪:baidu_valuation_percentile 实为个股分位,指数分位无上游 | 真机核验;无则 v1 **UNAVAILABLE 诚实**,补源挂 stocks 层 |
| R6 | 三轴枚举值中文(④)vs 英文(plan RealizedRegime) | **中文值**(已冻结 RotationStage 即中文先例);grader 词表同步中文,判读与 realized 同词表才能做校准 |
| R7 | trader 仓位带:入 Phase 6 schema@1(结构强制)vs 仅 Phase 8 prompt 纪律 | **入 schema@1**+确定性车道豁免带校验(校验钉 proposal/intent 层,不在 TargetPosition 本体);P6/P8/P9 同批改 |
| R8 | TrancheTrigger 分批触发字段现在注册 vs Phase 8 定形 @2 | **现在注册**最小形状(price_low/high/fraction 全 Optional),省 bump |
| R9 | #25 WorkerSpec 装配归属(27 计数跨 P5/P8/P9) | **Phase 5 装 #25**(Lane 0 家族,BOOTSTRAP 目录链);Phase 8 计数=27 含之;P9 去硬编码 |
| R10 | AMEND-6a 形态词典边界 | 注册表契约+种子词典材料进 Phase 8;识别器 handler+历史回放统计=**独立小任务**(只依赖日线,可与甲乙并行早做) |
| R11 | allowed_actions 宿主形态 | **pre-input adapter**(memory-bridge 同构,不动 27 席 roster,不当 worker) |
| R12 | skill 命名 canonical 规则 | 目录=worker_id(带点)为准;frontmatter name=人读名放开(catalog 按 ContentRef 绑定,name 仅展示) |
| R13 | replay 可行窗口塌陷(归档起点≈2026-07,更早区间 Lane 0 全 UNAVAILABLE) | 接受"**可行窗口从归档起点起**"(P9-A1 即够)+小 phase 甲**立刻开跑**;不把归档升格为 P9 硬前置 |
| R14 | R2 新红线测试宿主 | 维持分工:结构性测试归 Phase 5,P9 只加边界探针(B3/B4),不集中 |
| R15 | 事件库 vs datafeed P3 新闻归档 | **P3 升格为事件库**(单一存储,免双事实源);jsonl 归档不再另做 |
| R16 | 事件库 available_at 口径 + 分词器 + 归档粒度 | PIT 裁决列=**入库落盘时刻**(保守但真;首发时刻存 event_time);分词优先 jieba 预分词(纯 pip,免外部 dll),simple 扩展作升级项;归档**全量行级**(~10MB/月可接受;裁减=ladder_theme/diffusion/leader_persist 永久 UNAVAILABLE,不值) |

## §2–§8 · 逐 plan 清单(分析员原文,行号锚以各 plan 现文件为准)

## §2 · Phase 4 evaluator-governor

## Phase 4(Evaluator-Governor)× R2 对账清单

依据:`docs/superpowers/specs/2026-07-16-orchestration-round2-agents.md`(下称 spec)与 `docs/superpowers/plans/2026-07-16-orchestration-phase4-evaluator-governor.md`(下称 plan,共 901 行,已通读)。§9 归属表(spec:298):AMEND-5/6/6a/8 主体归"目录装配 phase",但其中 **TrialLedger/Governor 级治理原语属 Phase 4 冻结面**,须现在收口。

## A. 新增任务

**A1 · 「Task 4b:D6 修订节流治理原语(governor.py 扩展)」**
- 模块常量 `REVISION_THROTTLE_MIN_MATURED = {"monthly": 3, "daily": 20}`(D6 拍板值,spec:289;单位=族本频成熟观察期:月频=期、日频=交易日),与 `GOVERNOR_VERSION` 同批冻结(plan:357)。
- 纯函数 `revision_throttle_check(*, frequency, matured_observation_count) -> (allowed, reason)`:前一 `definition_version` 成熟观察 < N ⇒ blocked;**未知 frequency ⇒ 拒绝而非默认放行**(与 L2 `unavailable` 诚实同构,plan:370)。成熟计数由调用方按 PIT matured 语义供给(数据源=Phase 5 matured-case grader),Phase 4 只交付规则不接数据(时机诚实,同 spec:77 模式)。
- 定位依据:AMEND-5 过拟合红线③(spec:124)与①②同族——①(TrialLedger 同 family 计账)②(sealed 只揭一次)已是 Task 5/6 本体,③的规则必须同点收口,否则三管家各 phase 自抄 N 值必漂移。
- 测试:2/3、19/20/21 边界矩阵 + 未知频率拒绝 + 无 I/O(并入 Task 4 invariant 7 的 import 面检查,plan:381)。
- 插入:并入 Task 4 或紧随其后;依赖 Task 1(`StudySpec.frequency`)。**不接 `should_stop`/`run_optimize`**——节流是修订提案 admission 前检查(修订=新 study),不在 optimize 轮内。

## B. 修改现有任务

- **B1 · Task 1/Task 4 加"修订族恒等约定"注记**(plan:143 `StudySpec` 语义段;plan:363 `derive_study_family` docstring)。原文:"display fields deliberately excluded so free text can never perturb family identity"。改法:补一句约定——**同一被治理资产(factor_id/pattern_id lineage)的历次修订须构造同 family**(`objective_digest` 锚资产 lineage,修订表达式只进 candidate 不进 study 身份);身份句柄变更走 `parent_family_id + change_reason`。为什么:AMEND-5 ①要求"每次修订=**同 family_id** 的 TrialLedger 一笔 trial"(spec:124),机制已在(五句柄投影天然排除表达式),但不写约定,curator phase 可能把每次修订造新 family ⇒ 试验预算清零 ⇒ 多重检验计账穿透。
- **B2 · Task 5 加 AMEND-8 未来落点声明**(plan:449 `TrialLedger` 构造段/docstring;连带 plan:456 注明 closed key set 不动)。原文:"the ledger is cross-run and cross-experiment by construction"。改法:docstring 增注"Lane D 席位权重计账(AMEND-8,spec:261:先均权、成熟后按席位历史 accuracy)由目录装配 phase 经链式注册表扩展 + Task 3 同款 guard 翻转落入本 ledger;本 phase 不实现、`effective_trial_stats` 键集不预留"。为什么:R2 明文"席位权重进 TrialLedger",Phase 4 若不预声明允许的未来加法触点(如 Phase 1 events.py:48-50 预留注释之于本 plan Task 3),后续 phase 改 `trial_ledger.py` 会撞冻结面表述;但席位事件名 R2 未定,只声明机制、不发明名字。
- **B3 · Exit Gates scope 行扩展**(plan:841)。原文:"no dynamic Planner, Bootstrap Lane 0, shadow intent, debate, retry/repair or real trading authority was added"。改法:not-added 清单追加"no curator workers #25/#26/#27, no pattern dictionary/registry(AMEND-6a), no seat-weight accounting(AMEND-8), no judge swap-double-run audit(D12)"。为什么:R2 新增物均有明确归属 phase(spec:298),Phase 4 须结构性挡蔓延,与现有 scope gate 同款。
- **B4 · Execution Handoff 补跨 phase 接缝**(plan:898)。原文只述 Phase 5/6/9 绑定。改法:补"目录装配 phase 的 #26/#27 修订提案须经 A1 节流原语 + TrialLedger 同 family 计账;throttle 的成熟观察计数数据源=Phase 5 matured-case grader"。为什么:D6 是跨 phase 接缝(规则在 4、数据在 5、执法在装配 phase),不写进交接则实现时三头互相以为对方管。

## C. 明确不改/不加

- **AMEND-5 红线①②/AMEND-3 红线①②⑤(计账/sealed 只揭一次/draft-only/离线 lane)**:已是本 plan 主体——Global Constraints(plan:19-23 one-shot holdout/draft-only)、Task 5 triple-key 计账、Task 6 一次性 lease。零新工作。
- **三管家 trial family 推导覆盖(核对结论:覆盖)**:Task 1 五句柄 domain-neutral 投影(plan:143)+ Task 4 `derive_study_family` + lineage 字段已覆盖 #25 mining families(objective=挖掘目标、label=RegimeCase 成熟标注 digest)、#26 修订 families、#27 形态 families;仅需 B1 约定注记,不需新推导机制。#26 因子修订候选=formula 节点 workflow graph(research loop 现行路),`candidate_kind="workflow_graph"` 直接覆盖。
- **D12 swap 双跑抽查(核对结论:不属本 plan)**:AMEND-8 整体归目录装配 phase(spec:298);Phase 4 evaluator 是 optimize 候选四层(Task 7),评的是 `OptimizeCandidate` 非 live LLM 席位,且 Exit Gate 明文禁加 debate(plan:841,B3 再显式化);审计记录走 Phase 2 audit partition 即可,无 Phase 4 前置件。
- **#25/#26/#27 worker 本体、AMEND-6a K线形态词典、形态几何注册表**:归 Phase 5/目录装配/独立小任务(spec:298),Phase 4 不建 worker、不建注册表。
- **D11 capability manifest 生成器**:目录装配 phase 第一任务(spec:294);Phase 4 catalog 仅加一条 gate_metric material、workers/capabilities unchanged(plan:729),无需动。
- **D6 数据侧**:Task 8 `MaturityPending` 已绑 Phase 5 matured-case grader(plan:631),D6"成熟观察"复用同一 maturity 语义;`last_revealed_at` 已在 `effective_trial_stats` closed key set(plan:456)支持修订节奏查询——ledger 无需改。
- **AMEND-8 其余(allowed_actions 硬约束前置/辩论机械 prompt/逐席规范/D13 V/F 词典)**:纯 prompt/装配层内容,与 Phase 4 契约面零接口。
- **接线总原则(spec:298"纯加法、不回改 1/2/3/3b")**:plan 已合规——唯一上游触点是 Task 3 加法式 events.py 扩展,R2 无任何条目要求更多上游触碰。

## D. 疑点

- **D-1 · #27 形态回放能否装进 v1 契约(真冲突,需拍板)**:`OptimizeCandidate.candidate_kind` 闭 Literal 仅 `"workflow_graph"`(plan:146),`ValidationMetrics.source` 闭 Literal 仅 `run_graph/shadow_backend/case_grader` 且无盈亏比字段(plan:147)。R2 称形态与因子"共享同一 research/loop 求值机器"(spec:157),但形态回放求值(命中率/胜率/盈亏比,spec:144)的候选载体与指标面未必可表达为 workflow graph。选项 a:golden 冻结前(Task 9)现在加 `candidate_kind="pattern_definition"`、`source="pattern_replay"`、`profit_loss_ratio/n_occurrences` 字段(免费,但对未定设计投机);选项 b:留给 curator phase 出 `OptimizeCandidate@2`/`ValidationMetrics@2`(干净,但多一次 schema bump)。冻结后改=只能走版本升级,故须在 Task 9 golden 冻结前裁决。
- **D-2 · D6 频率词表归属**:`StudySpec.frequency` 是自由串(plan:143)且参与 family 恒等。A1 建议 Phase 4 常量表按 `"monthly"/"daily"` 键 + 未知拒绝、由 curator phase 负责映射自家 freq 串;若用户要求词表冻结进契约(frequency Literal 化),则是 Task 1 契约改动并影响 family 恒等与 golden,需拍板。

---

## §3 · Phase 5 bootstrap-lane0

## Phase 5 plan(bootstrap-lane0)× R2 对账清单

对象:`docs/superpowers/plans/2026-07-16-orchestration-phase5-bootstrap-lane0.md`(下称 plan)
权威输入:R2 spec(AMEND-1/2/3/4 归 Phase 5,§9)+ 交付物①(`2026-07-17-market-factor-report-schema.md`,R2 §1 明言"以交付物①为准")+ 交付物④(`2026-07-17-lane0-regime-rotation-skills.md`)。

## A. 新增任务

**A1 · Task 3b「factor report 渲染契约 render_for_prompt」**(插在 Task 3 之后;被 Task 8 材料清单与 Task 10 e2e 依赖)
- 按①§4 实现纯渲染函数:不可信定界块,头部 `as_of/clock_mode/universe_registry_version/battery_digest 前8位`;每因子一行 summary(`latest|Δ5d|Δ20d|pct250`)+ 60 日紧凑序列(3 位有效数字、按周分行,D1)。
- UNAVAILABLE 因子必须显式一行 `<factor_id>: UNAVAILABLE(<reason>)`(缺席即信息,绝不静默省略);DEGRADED 标 coverage+reason;有界长度、超限拒绝(镜像 plan L642 experience renderer 纪律)。
- 新 handler 材料 `lane0.factor_report.renderer` 进 Task 8 材料表(L603-617);Task 10 e2e 断言 LLM 节点吃的是渲染块而非裸 typed payload,且块头 digest == 输出 `factor_report_digest`。
- 理由:①§0/§4 红线"LLM 不看原始标量"、渲染块是"喂 regime/rotation 的唯一出口";plan 现状只有 experience renderer,factor report 走 InputBinding 裸注入,直接违反交付物①。

## B. 修改现有任务

1. **Task 1 L151/L153-169 + 出口门 L841**:原文「exactly 15 factor ids mapping the spec §5 table's 12 rows」→ 电池改为①§3 的 **17 因子**。核验结论:**AMEND-2 四轮动新因子(`rot.ladder_theme`/`rot.leader_persist`/`rot.flow_streak`/`rot.theme_burst`)确不在 plan 清单**(plan 仅 rotation.hhi/mainline_diffusion/industry_dispersion)。id/前缀/参数以①为准(`breadth.nhnl`、`rot.*`、`flow.northbound`、`flow.main_pct`、`vol.rv`、`val.pct`);L841"15 factor ids"同步 17。理由:AMEND-2 拍板"第一批实现,不等挖掘"。
2. **Task 1 L172**:原文「status: Literal["ok","unavailable"]…never by a third status value」→ 改①§2 三态 `OK/DEGRADED/UNAVAILABLE`(DEGRADED=短覆盖/归档年轻,reason 必填)。plan 的"无第三态"引自 R2 §1.1 早期草稿,已被①取代。
3. **Task 1 L172-173 字段对齐①**:`points`→`series`(≤60 点,D1);加 `summary{latest,chg_5d,chg_20d,pct_250d}|None`(覆盖不足 pct_250d=None 不硬算)、`family`、`n_days`、`first_date`(归档起点诚实显形)、`provenance(sources+snapshot_refs)`、`reason`;`missing_policy` 由 Literal 改一句话语义。报告级加 `clock_mode(eod/intraday)`、`universe_registry_version`(AMEND-1 universe 版本化进 provenance)、`coverage_summary{n_ok,n_degraded,n_unavailable}`;`factor_set_digest`→`battery_digest`。`feature_vector/feature_coverage/missing_features` 系经验层(Task 4/5)消费,作 plan 增补字段保留。
4. **Task 3 公式/源修**:L291 ad_ratio slope_window=5→**20 日斜率**;L296 divergence z 窗 expanding-z20→**250 日窗**(`alert=+1.5 ⏳` 进 params 元数据);L302 `rv_ratio=rv20/rv60`→**RV5/RV20 短长比**;L297 north 记 sgt 护栏语义(点密度<半数当日置空,上游已实现,loader 透传);L307-316 loader 表补四新因子原料行(涨停原因归因/概念归属 ww_live_text/板块领涨股/universe 版本 diff——v1 无归档=None ⇒ 诚实 UNAVAILABLE)。
5. **Task 2 L226-227 RegimeReport**:增 `factor_report_digest`(输入可审计)、`evidence: tuple[EvidenceAnchor,...]`(≥1,factor_id+value+reading)、`conflicts`、`analog_case_ids`;label 元组改④§1 三新枚举 `TrendState/RiskState/HeatState`(Phase 5 自有模块定义,Phase 1 enums.py 不动;TrendState 值为中文 牛/熊/震荡);`confidence_score` 0-1 浮点删除、仅留 `Confidence` 离散档(④无此字段;R2 AMEND-7-3 校准理由:LLM 自报 0-1 浮点饱和)。plan 的 modal/unknown 门控验证器作增补保留(④"字段供实现期定稿")。
6. **Task 2 L233-234 RotationReport**:报告级 `stage` 删除,阶段落到**每主线**(`MainlineRead.stage: RotationStage`,AMEND-4 §4.2"每主线阶段/强度/持续性");mainline 对齐④:加 `universe_key`(绑 universe_registry_version)、strength [0,1]→**[0,10]**、`persistence_sessions`→`persistence` 证据句、每主线 `evidence` 锚、`chain_nodes=()`(when-supplied);增 `factor_report_digest/conflicts/analog_case_ids`;**空 mainlines=诚实合法**(不再要求 stage==UNKNOWN 才准空)。
7. **Task 8 L619-620**:prompt/skill"content requirements"草稿→直接采用④§2/§3 **逐字安装件**为 `regime_skill.md`/`rotation_skill.md`(已含 skill-v1 frontmatter+⚠️ 开局+词表红线"读得懂但绝不输出"+冷启动条款);L598 rotation 的 Not-ideal-for 不再写"产业链未绑定"——④按 when-supplied 处理(无块则 chain_nodes 空,不即兴)。
8. **Task 8 L597-598 + Task 10 L771**:LLM 输入改吃 A1 渲染块;e2e 增断言:每个 EvidenceAnchor.factor_id 存在于所读 report、`factor_report_digest`==注入报告 digest(④设计点②机器可复核面;number_critic 消费归 Lane D 后续)。
9. **Task 7 L550 seed_judgment_proxy**:规则引用随新电池改 id/取值位(vol.rv、ad20 改从 summary/序列侧取);proxy 输出随 Task 2 新形状(`factor_report_digest`=所算 report、evidence 锚、`analog_case_ids=()`、conflicts 空)。Task 10 L764/Task 4 L376 内嵌 judgment 形状与验证器随动。
10. **Task 1 L171 + Out of Scope L817**:前置声明改为显式引用**①§5 归档依赖**:rot 族 6 因子+炸板率/北向/主力分位历史序列依赖 market_tape/fundflow 快照落盘归档"新小交付"(建议与事件库小 phase 同批;macro 月轮转 72573b8 先例);归档起点后为短序列 DEGRADED+`first_date` 显形,不回填不伪造。named owner 从"a later reviewed data job"改为该小交付。
11. **Task 9 L697**:`PHASE5_INTERNAL_MODELS` 补 `EvidenceAnchor/MainlineRead/FactorSummary` 与三新枚举;goldens 随 17 因子重冻(仍手冻、评审签字)。

## C. 明确不改/不加(scope guard)

- **#25 `market.factor_miner` 不占位**:不建 WorkerSpec、不占目录席位(catalog 仍 +3 finals)。AMEND-3 明言"spec 先行、真跑依赖经验库成熟标注→Phase 5 之后";**电池注册表化(AMEND-1)与 plan factor 任务已一致**——Task 1 `MarketFactorSetSpec`(factor_id@definition_version+冻结 params+golden+digest)+Task 8 handler 材料钉电池 digest(L607)即注册表形态,仅需 B3 命名/字段对齐;演化通道(①§6:miner draft→人审→bump、D6 节流 N=3)在 Out of Scope 点名 owner 即可,不写代码。
- **#26 quant.curator / #27 pv.curator / AMEND-6a K线形态词典**:§9 归属目录装配 phase+独立形态小任务。
- **AMEND-7 Lane C 五席接线/事件库**:目录装配 phase+独立小 phase(D10"不回改已完成 phase");①§5 建议归档小交付与事件库同批,亦不进本 plan(仅 B10 改声明措辞)。
- **AMEND-8 Lane D 六席**:目录装配 phase;温度护盾本 plan 仅投影+parity(D5),重接线归 Lane D——plan L816 已列,不动;温度系数/冰点阈值 25 用户拍板冻结(plan D5/L21 已守)。
- **RotationStage/Confidence 枚举**:已冻结直接用,plan Task 2(L222)已正确消费;`migrate_rotation_stage` 保持 UNMAPPABLE(plan D11),与④词表红线一致。
- **逐主线概率分布**:④设计说明①明言"过度工程,留 @2 观察",不加。
- **⏳ 阈值调参**(divergence.alert 等):Phase 4 `run_optimize`+TrialLedger+sealed holdout,plan L814 已 defer,与①§6/§7.3 一致。
- **AMEND-4 共享 guardrails**(FSI 隔离/skill-v1 信封机器强制/反捏造):plan Task 8 `honesty_guardrail`+`parse_skill_v1`(L612/L637)已覆盖,仅正文换④逐字件(B7)。
- **D1/D2/D3 拍板**:plan 已按 reasoner 档(L597)+三轴概率(L227)落,方向一致,仅字段形对齐(B5)。

## D. 疑点(需用户裁决)

1. **因子 id 粒度**:①把"涨停 EMA+炸板率"并入 `breadth.limit_strength@1` 一行、"最高连板+首板晋级率"并入 `breadth.ladder@1`,但 `FactorSeries` 是单序列单 summary——一 id 双序列怎么落(拆回 plan 式独立 id,还是 summary/aux 承载)?连带:①§3 rot 族标"(6"实为 7 行、总数 17 才成立,疑标题笔误,请确认。
2. **晋级率口径冲突**:plan D8(L40)冻结"复用 implemented `promotion_rate`(今日 limit_days>=2 分子)直到 pool history 具备";①§3 breadth.ladder 写"**首板**晋级率"。归档开始后严格首板可算——v1 归档前用哪个口径(或直接 DEGRADED)需裁决。
3. **val.pct 源真伪**:①称源=`baidu_valuation_percentile`(B⑤已接),但该源实测为**个股**分位(600519 PE1.1);指数 PE/PB 五年分位是否真有上游,plan L303 判 UNAVAILABLE——需真机核验后定 v1 状态,否则①的"上游口径"是虚承诺。
4. **枚举序列化值**:④ TrendState 用中文值(牛/熊/震荡),plan `RealizedRegime`(L377)用英文 literal——④为准则 grader 词表须同步中文(判读 vs realized 同词表才可做④§4 校准分析),影响 golden/digest,若偏好英文需明示。

---

## §4 · Phase 6 shadow-consumer

# Phase 6 影子消费端 plan × R2 对账清单

**总判**:R2 §9 归属表(R2 L298)没有任何 AMEND 直接落 Phase 6;唯一交集是 AMEND-8 8.3 `dec.trader` 行(R2 L274)——trader 产出物 = `PortfolioTargetProposal`,而该 schema 由本 plan Task 1 冻结注册(plan L110-151)。Phase 6 未执行,现在吸收可免 Phase 8 被迫 bump `@2`。改动面窄:一个新任务 + 五处修改;其余 R2 内容全部挡在 scope guard 外。

## A. 新增任务

**A1 · Task 1b「目标仓位带词表 + 分批触发契约」**(插入 Task 1 之后、Task 2 之前;依赖 Task 1)
- `TARGET_WEIGHT_BANDS: tuple = (0.0, 0.25, 0.5, 0.75, 1.0)` 模块常量并 **export**(供 Phase 8 `allowed_actions` 的"最大目标仓位带"复用同一封闭词表,避免平行定义);
- `PortfolioTargetProposal` 模型校验器新增:每个 `positions[].target_weight ∈ TARGET_WEIGHT_BANDS`(精确比较、零容差、绝不吸附最近带),违者拒收,`ProposalRejected` 封闭 reason 集(plan L121)追加 `"non_band_weight"`;
- 新 frozen 子模型 `TrancheTrigger`(分批触发价区间,R2 L274"可选分批触发价区间"):`price_low/price_high/fraction` 等**数字字段全 Optional、default=None、无任何计算默认值**;挂 `TargetPosition.entry_tranches: tuple[TrancheTrigger, ...] = ()`,进 `TargetPosition@1` 的 JSON schema 与 digest;
- 测试矩阵:带值域边界(0.25 过 / 0.3 拒)、tranche 全 None 合法、digest 敏感性;
- 校验器位置钉死在 proposal/intent 层,**不在 `TargetPosition` 本体**(理由见 B3)。

## B. 修改现有任务

- **B1 · Task 1(plan L123-124)**:原文 `target_weight: FiniteFloat = Field(ge=0, le=1)` 是连续值域 → 经 A1 校验器收窄为五档离散带。为什么:R2 L274 裁定 trader 输出形态="5档评级联动**离散目标仓位带**(0/25/50/75/100%)……不出连续精确仓位(无锚漂移)";结构性约束优于 prompt 纪律,与 plan 自身 "no silent normalization" 哲学同构。注:`target_weight` 本身保持 required(它就是决策本体);R2"数字字段全 Optional"按反面教材(买入默认现价×1.15)读作价格/数量类字段——`stop_loss_pct/take_profit_pct/max_hold_bars` 已是 Optional-None(plan L123),无需改。
- **B2 · Task 3(plan L212, L214)**:原文 "the same portfolio matrix as `PortfolioTargetProposal`" 与 "positions/cash_weight/rationale/confidence copied **verbatim**" → 显式写明矩阵含 A1 的带值域与 tranche 校验(intent 永不比 proposal 更松),verbatim 复制含 `entry_tranches`。为什么:防 intent 层成为绕过带约束的旁路。
- **B3 · Task 6(plan L343)**:原文 `DeterministicTargetSet` "validated by the exact Task 1 duplicate/weight-sum/leverage matrix" → 改为"…matrix,**豁免带值域校验**(显式列出豁免项及理由)"。为什么:无锚漂移是 LLM 病理,确定性规则腿必须能表达等权三只=33.3% 之类连续权重;带校验若落在 `TargetPosition` 本体会被双车道同时继承,故 A1 把它钉在 proposal/intent 层。同时 Task 6 runner(plan L341, L347-355)加一条:`shadow-match-v1` 对携带非空 `entry_tranches` 的 intent **诚实拒绝**(仿 `UnsupportedBarFrequencyError` 模式),绝不静默忽略——分批触发的撮合执行留待后续版本,schema 先行注册免 bump。
- **B4 · Task 8(plan L453)**:`map_intents_to_compat_signals` 的 profile 校验矩阵追加"intent 携带非空 tranche → `CompatibilityProfileError`"(前端 `runBacktest` 无分批语义,按既有"不静默扩宽"原则拒绝)。
- **B5 · Task 11(plan L578 四接缝防御 + L572-580)**:红线套件新增"**禁提取层默认值**"结构性测试:全 None 可选字段的 proposal → intent 仍全 None → runner 不产生任何 stop/take/max_hold 单、无任何路径把 None 补成计算价(反面教材:CN 版买入默认现价×1.15,R2 L274);None 保真断言穿 proposal→intent→runner 三层。
- **B6 · Task 10(plan L533, L537)**:`PHASE6_PUBLIC_MODELS` 与 golden manifest 按上游机制处理 `TrancheTrigger`(嵌套子模型随 `TargetPosition@1` 入 schema digest 即可;若上游分类机制要求独立注册则单列)——Task 0 correction clause 现有措辞已覆盖此类裁定,只需在 Task 10 文字里点名。

## C. 明确不改/不加(scope guard)

1. **`allowed_actions` 确定性计算器(R2 L247 结构性裁定1)不落本 plan**:它是 `dec.pm` 之前的 Lane D runtime 前置层,R2 §9 归 目录装配 phase;Phase 6 无任何 LLM 席位可供约束。执行时刻的 A股制度约束(T+1/涨跌停/一字/手数/停牌)本 plan 已由 engine Broker 原样穿透(Task 6 invariant 2,plan L360)——这是"约束在成交处强制",与"约束在决策前预算"是两个阶段;Phase 8 可直接复用 Task 0 钉住的 engine limit helpers(plan L50)+ A1 导出的带词表,无重复建设风险。
2. **nullish 消毒("N/A"→None,R2 L274)属 Phase 8 提取层**:本 plan strict 类型(`FiniteFloat`)已结构性拒收 "N/A"(plan L121-124),恰好把消毒义务压给 Phase 8 的 trader 输出提取器——Phase 6 只需保持 strict,不加消毒代码。
3. **typed payload 外的确定性哨兵行(R2 L274)**:定义即"payload 之外",是 Phase 8 worker 输出信封/日志层之物,schema 无处安放,不加。
4. **8.2 辩论机械 / `DebateMessage`**:Phase 8;plan 已双重守卫其缺席(Task 10 invariant 5 plan L546、Task 11 item 7 plan L580),已覆盖,不动。
5. **3档 action 与 5档评级分层、"只吃 pm 决策不吃原始报告"(R2 L274)**:trader 的 prompt/接线纪律,Phase 8 装配层;Phase 6 的 intent 已带 `source_decision_artifact_id` 因果锚(plan L212),链路凭证已够。
6. **D11 capability manifest 生成器**:归目录装配 phase 第一任务;Phase 6 catalog 是零 worker 零 capability 的 identity 链节点(plan L536),这份"空"本身是红线事实,不改。
7. **AMEND-1/2/3/4(Lane 0 因子电池)→ Phase 5;AMEND-5/6/6a(#26/#27/K线形态)→ 目录装配+形态小任务;AMEND-7 与 D10 事件库 → 目录装配/datafeed 小 phase**:均与影子消费端无接口交集,不加。
8. **时间模型 `cutoff_at <= decision_as_of < eligible_execution_at`**:plan 已内置并标注"ruled 2026-07-16"(plan L22、L214、L621),与 R2 无冲突,不动。
9. **LLM 零买卖结构红线**:R2 与 v1.1 一致,plan 已全覆盖(closed Literals + 无 live capability + 无晋升面,plan L5/L20/L574-580),不动。

## D. 疑点

1. **带值域入 schema `@1` 还是仅留 trader prompt 纪律?** 我建议入 schema(B1,结构性强制),但这收窄了 `PortfolioTargetProposal@1` 合法值域——若用户预期该 schema 未来还服务连续仓位的非 LLM 生产者(目前该角色已由 `DeterministicTargetSet` 承担),则应改为仅 prompt 层约束 + evaluator 检查。需拍板。附带:B3 的"确定性车道豁免带校验"是我的推导(R2 只说 trader 输出形态),同点一并裁决。
2. **分批触发价区间的字段形状 R2 未给细节**:`TrancheTrigger`(price_low/price_high/fraction)是我的最小化设计;备选是等 Phase 8 起草 trader skill 时定形并 bump `@2`(代价:schema 变更 + registry 链新增版本)。现在注册省 bump 但有猜错字段形状的风险。需拍板。

---

## §5 · Phase 7 dynamic-planner(零改动,核验过程存档)

## Phase 7(动态 Planner + Plan 人审承载面)× R2 对账清单

**总判定:R2 对本 plan 影响确为最小——A 节为空,B 节为空。** R2 §9 AMEND→phase 归属表未把任何 AMEND 归给 Phase 7(AMEND-1/2/3/4→Phase 5;AMEND-5/6/6a/7/8→目录装配 phase;事件库→独立小 phase)。逐条核验如下。

## A. 新增任务

**无。** R2 的全部增量(#25/26/27 三管家、因子电池注册表、Lane 0/C/D prompt、capability manifest 生成器、事件库)均归属 Phase 5 / 目录装配 phase / 独立小 phase(R2 spec 298 行归属表),无一落在 Phase 7。Phase 7 plan 现有 10 任务已闭合。

## B. 修改现有任务

**无必须修改项。** 三个疑似点逐一核验后均不成立:

1. **worker 数 24→27 不需要改**——全文 grep 无任何 "24 worker" 硬编码。Planner 选择域完全是目录动态派生:Task 2(plan L174)shape 检查 `planner_worker_not_dynamic`(`selection_scope != "dynamic_allowed"` 即拒);Task 4 Step 2(L276)prompt roster = "a deterministic serialization of `dynamic_allowed` `final` workers only",按快照现算非枚举;Exit Gate(L624)"cannot select ... any non-`dynamic_allowed` worker"。R2 的 #25/26/27 全是 static/offline lane(#25 红线⑤"不进每日主 DAG",R2 L75;#26/#27 同待遇 proposal-only),未来注册时只要 `selection_scope` 非 dynamic_allowed,Planner 结构性选不到——现有机制零改动即吸收。
2. **Phase 7 目录链与 D11 生成器兼容,不需要改**——Task 9(L530)`build_phase7_catalog_snapshot` 只加 planner 材料、零新 worker、钉死 base=`PHASE6_CATALOG_DIGEST`;D11(R2 L294)裁定生成器+27 worker 落"目录装配 phase 第一个任务",且"新目录=新 digest,旧 Plan 照旧可回放"——与 plan 的 chain discipline(L26"no latest alias"、L540 invariant 5"older digests remain resolvable ... never rebound")同一模型,后续 phase 在 Phase 7 digest 之上续链即可。Task 9 invariant 3"`count_final_workers` unchanged from Phase 6"是相对 base 快照的断言而非绝对数,27 worker 装入时也不破。
3. **Task 0 上游门无需改**——AMEND-1/2/3/4 会扩大 Phase 5 交付面,但 Task 0 Step 2(L64-66)只在实现时冻结"exact upstream registry/catalog digests",correction clause (e)(L60)已覆盖 Phase 5/6 导出名漂移;plan 未预写任何 Phase 5 digest 值,R2 对 Phase 5 的扩容被自然吸收。

## C. 明确不改/不加(scope guard)

- **AMEND-3 #25 `market.factor_miner`**:归 Phase 5 后真跑(R2 L77),Phase 7 不注册、不预留其 WorkerSpec。Planner 侧防线已存在(见 B.1),不加针对性代码。
- **AMEND-5/6 #26/#27 curator**:归目录装配 phase(R2 L298)。其 proposal-only/TrialLedger 红线与 Phase 7 无接口。Task 10 red line(L583-585)已含"Planner cannot schedule compat.*/itself"+import sweep"no memory/skill/code write",覆盖面足够,不为未来 worker 加占位测试。
- **AMEND-6a K线形态词典(P0)**:纯 Lane B 数据面交付,与 Planner/审批面零交集,不加。
- **AMEND-4 §4.3 共享 guardrail(skill-v1 信封硬格式)**:plan 已覆盖——Task 9(L530、L539)planner SKILL.md 走 `parse_skill_v1`,显式要求 frontmatter+canonical-JSON triggers+`## ⚠️ CRITICAL: Data Source Priority` 开局,与 R2 L103 逐字一致,无需改。
- **AMEND-7 capability manifest 生成器(D11)**:归目录装配 phase 第一任务,Phase 7 的 planner 材料登记走现成 `build_catalog_snapshot` 手工路径即可(仅 3 份材料,非 27 worker 清单,无手抄腐烂面),不提前引入生成器。
- **AMEND-8 Lane D prompt(含交付物③ research_mgr/pm skills)**:归目录装配 phase。Phase 7 fallback preset `main.research_baseline`(Task 3,L225)按 worker id 引用 pilot 三席,不冻结 skill 内容 digest,Lane D skill 升级换 digest 不破 preset golden,不改。
- **D7 外部技术分析投递入口(console 指令+固定投递目录)**:虽同骑 console,但属 #27 摄入回路,Phase 7 的 console 改动(Task 8)只加 plan-approval 三端点+一卡,**不得顺手加投递入口**。
- **D2 模型档位**:regime/rotation=reasoner 归 Phase 5;Phase 7 planner 席 `reasoner_deep` 是 plan 自己的 reviewed deviation(L531),两者不冲突,不动。
- **D9 情绪供给改判 / D10 事件库 / D13 V/F 词典**:分别属 text.sentiment skill(目录装配)、独立小 phase、Lane D 词典资产,与 Planner/审批面无接口,均不加。
- **R2 接线总原则(L298"纯加法、不回改已完成 phase")**:与 plan Global Constraints(L13-28)及 Scope Protection(L656-658)已同构,无需增写。

## D. 疑点

**无真冲突。** 一条低风险提示(不需裁决即可执行):plan 文内 "Phase 8" 指"dynamic-selection batches / debates-gates-reducers 解禁"(L19、L674),R2 的"目录装配 phase"指 spec v1.1 §12 第 8 期(四车道目录/skills/Lane D)。两者若非同一 phase(即 27 worker 装配与 dynamic-selection 分属两个后续 phase),Phase 7 均不受影响(其依赖仅 Phase 2/5/6);该命名对齐属目录装配 phase 自己的 reconcile 范围。

---

## §6 · Phase 8 lane-workers(冲击最大)

## Phase 8 plan(四车道目录/skills/Lane D)× R2 对账清单

对账基线:plan 全文 949 行已读;R2 spec(AMEND-1..8、D1-D13、§9 归属表 L298)已读;交付物②③已读。plan 全文**零处引用**交付物②③与 R2 spec——两份"逐字安装件"正是本 plan Task 4/10 的直接材料,必须显式接入。

## A. 新增任务

**A1. capability manifest 生成器 + 漂移守护(D11,本 phase 第一个实现任务)**
- 插入位置:Task 0 之后、现 Task 1 之前(D11 原文"生成器+漂移守护先行,再装 27 worker";依赖 Task 0)。
- 从 `WW_TOOL_TABLE` 同源派生 capability manifest(与已有 `scripts/gen_agent_interface_doc.py`/`tests/test_agent_interface_doc.py` 同模式复用),产 27 席 allowlist↔真实接口映射材料;Phase 3 `CapabilityRef` id 绑定沿用 clause (c)(L58)。
- 漂移守护测试 = 手抄 allowlist 腐烂疫苗(R2 §7.1 点名 glmcp 58≠64 病);Phase 2 pilot catalog 不回改(D11)。
- 加"承诺-供给 lint":各 SKILL `Data Source Priority` 点名的真实工具 ⊆ manifest(§7.2 第 1 条机器化);Task 4-7/10 各批次第 4 项"capability manifest"改为消费本生成器输出。

**A2. worker #26 `quant.curator` 装配(AMEND-5)**
- 插入位置:并入 Task 6 Lane A 批次(roster 5→6,L438-444)。
- LLM/reasoner、FORBIDDEN·()、`can_emit_decision=False`、离线 research lane 不进每日主 DAG(与 quant.factor_miner 同待遇,R2 §3 红线⑤同构);primary=`FactorLifecycleProposal@1`(`draft_only: Literal[True]`,四职能:衰减警报/修订 draft/退役/组合触发,全 proposal-only,R2 §5 职责表)。
- guardrail:draft-only + 过拟合红线五条(TrialLedger 同 family_id 计账/sealed holdout 只揭一次/**修订节流 N=3 月频(D6)**/人审必经/修订须引用触发证据工件);SKILL 写明边界:不 spawn、不指挥 Lane A、不直写 factorlib(R2 §5)。

**A3. worker #27 `pv.curator` 装配 + K线形态词典 P0 契约(AMEND-6/6a)**
- 插入位置:pv.curator 并入 Task 5 Lane B 批次(roster 3→4,L377-381);形态词典契约作 Task 5 前置子任务。
- pv.curator:LLM、FORBIDDEN、draft-only;primary=`PatternLifecycleProposal@1`;两路分流入 SKILL((a) 可计算形态→`pattern_id@definition_version` 识别器提案;(b) 方法论→skill diff proposal 走 A/B 影子对照);外部投喂=不可信数据 FSI guardrail("满仓干"指令绝不提升);节流 N=20 交易日(D6)。
- 形态词典契约:几何注册表(15键→N键,AMEND-1 同款可扩)+ **首批自建种子词典材料**(单/双/三 bar/多 bar 结构/缺口族/A股特色六类,AMEND-6a 表)——版本化、回放统计随条目冻结、样本不足显 UNAVAILABLE、多 bar 允许宽松几何近似+置信分;`pv.price_action`/`pv.technical` SKILL 数据面引用 `pattern_id` 不重新口述(交付纪律④)。

**A4. Lane D 注入面 bridge/adapter 任务(AMEND-8 + 交付物③装配说明 L213)**
- 插入位置:Task 9 与 Task 10 之间(Task 10 接线消费);全部确定性、typed payload、when-supplied 可缺席(pilot 链照常)。
- 六面逐一:①辩论历史块=已有 `DebateTranscript` 输入(免建);②**上游评级确定性抽取块**→`UpstreamRatingsExtract@1`(research_mgr opt input,背离须点名);③**allowed_actions 块**→`AllowedActions@1`(CRO 硬规则+A股制度前置确定性化,每票可否买卖/手数/目标仓位带;dec.pm 与 dec.risk_debate opt input,prompt 标 "already validated";R2 §8.1 第 1 条);④**veto+公告烈度旗标**→`AnnouncementRiskFlags@1`(确定性词表+排除词+三层烈度 立案>问询>关注函);⑤**教训块 PIT 配方**参数化绑 Phase 3 memory bridge(同票近 5 全文+跨票近 3 只反思/matured-only/pending 不注入/payload 须列 lesson_id);⑥风险三席输出=已有 riskdebate transcript+`opponent_stances`(免建)。

## B. 修改现有任务

- **B1. 全局计数 24→27**:Goal L5 "final-24"、Architecture L7 "18 new…reconciling the final-24"、File Structure L94("16 payload schemas")/L95("18 new + 5 updated")、Task 11 L763(21 schemas)/L770(`count_final_workers==24`、lane counts market3/quant5/pv3)、Exit Gates L894/L916("every one of the 24 ids")、Reviewer quick-ref L944。改为:27 = 3 Lane 0 + #25(Phase 5 装)+ 3 pilots + 20 本 phase 新作(18+#26+#27);lane counts market4/quant6/pv4/text5/decision6/xcut2;schema/材料数随 A2-A4 扩。为什么:D5 拍板 24→27,归属表钉 #26/#27 归目录装配 phase。
- **B2. Task 0 Step 1 第 2 项(L45)**:baseline 除 3 pilots+3 Lane 0 外须记录 #25 `market.factor_miner` 的语义 digest(若 Phase 5 已装,见 D1 疑点);第 6 项 L49 fixture "24 planned workers" 措辞连带核对。
- **B3. Task 4(L296-312)**:①pilot update(L312)从"仅 rebind skill ref+加 guardrail.anti_fabrication"扩为**安装交付物②逐字 SKILL**(新材料版本=新 digest;clause (d) 只护 WorkerSpec 字段,不应冻结 skill 内容);②四新席 SKILL 按 §7.2 十条起草(承诺-供给一致/`<unavailable>` 占位/confidence 规则上限 LLM 只准下调/source_span 强制/去重键=类目×主体×日期/insufficient_evidence 合法/Limitations 专章);③`Data Source Priority` 逐席点名**真实工具名**(§7.1 表:ww_news_live/ww_newsradar/ww_live_text/ww_f10/ww_macro_pulse/ww_overseas…,A1 lint 核对);④text.policy 加逐年措辞对比+政策黑话词典版本化附录+公告整篇喂不切 chunk;⑤schema@2 富化(evidence_refs/insufficient_evidence/events,交付物②文末)在本批拍板——交付物②明言"Phase 8 批次迁移时决定"。
- **B4. Task 1(L112、骨架 L136-156)**:骨架示例的 Data Source Priority 应按 AMEND-7 层 2 点名真实工具而非仅抽象 capability 名;pilot 三件 relocate 保持 byte-identical,但明确后续 Task 4/10 reviewed edit = 交付物②③整体替换(非小修)。
- **B5. Task 5(L393)**:`PriceActionFeatureReport.feature_set_version: Literal["pa-15key-v1"]` 冻结字面量与 AMEND-6 "15键→N键可扩注册表"直接冲突——改为注册表版本串(15键 v1 仍是首版逐位一致红线),并留 patterns 扩展位挂 `pattern_id@definition_version` 命中。
- **B6. Task 10(L681-727)**:①dec.bear 输入表(L711)加不对称风险弹药 opt input(`AnnouncementRiskFlags@1`/解禁/质押载荷)——AMEND-8 §8.2 "bull/bear 吃完全相同上游=塌缩结构诱因";②`BullCase`/`BearCase`(L694-695)加每轮立场声明字段(维持/更新+新证据,justified belief 条款);③research_mgr/pm pilot update(L690)扩为安装交付物③逐字 SKILL;④`guardrail.debate_rounds`(L704)内容扩:250-400 词+Thesis→Evidence→Counter+硬性 must-oppose+**对方未回应不许虚构对方观点**+越权边界("不给 BUY/SELL")+静态前动态后排版;⑤dec.pm/dec.risk_debate/dec.research_mgr 接 A4 注入面 opt inputs(L706-717 表);⑥**D13**:V1–V9/F1–F14 锚词典升为版本化 catalog 材料(dec.bull/bear 绑其 digest),修订走 proposal+人审、不设新管家——现 plan 只在 legacy source 提 `playbook_V1_V10.md`(L683-684),无版本化资产。
- **B7. Task 12(L793-841)**:e2e 增断言:fake gateway 观察装配请求静态材料在前、动态数据在后(TA #750);bear 节点收到 bull 未收到的风险载荷;allowed_actions/veto 块注入 dec.pm 且 veto 在场 rating 封顶 Hold;六注入面全缺席时 pilot 链照常(优雅缺席回归)。
- **B8. Appendix 材料清单(L846-861)**:prompt/handler/guardrail/schema 行随 A1-A4 扩(新 guardrail:外部投喂 FSI/修订节流/vf_anchor_dict/allowed_actions 等);"registers no new capability"(L860)与 A1 生成器的关系写明:生成器产 manifest 材料与 lint,不注册新 capability。

## C. 明确不改/不加

- AMEND-1/2/3(电池注册表/扩容/#25 spec)与 AMEND-4(Lane 0 prompt 六段)→ Phase 5;plan 已钉 "no Lane 0 re-authoring"(L925)与 Lane 0 继承(L7、clause (e))。
- AMEND-7 §7.3 事件库(SQLite 三件套/FTS5/Graphiti)→ D10 拍板独立小 phase/datafeed 归档 P3,本 plan 不建库。
- D7 console 投递入口+固定投递目录 → 运行时/console 接线非目录装配(plan 纪律:生产数据绑定归 Phase 9,L290);本 plan 只装 #27 WorkerSpec+FSI guardrail。
- D12 swap 双跑 → Phase 4 evaluator 抽查项;声明条款随交付物③安装(B6)即足。
- 席位权重进 TrialLedger 先均权后按 accuracy 加权 → Phase 4(交付物③ L214 自己也这么归)。
- §8.1 第 2 条模型风格实测选型/换手杠杆 runtime 硬约束 → 选型实验非目录装配;结构化部分由 A4 allowed_actions 承接。
- 已覆盖无需改:≤2 轮硬上限+逐席逐轮预算+judge 非席位(L21、Task 9 L610-668);bear 晚一波结构化(`opponent_case` required=True L711+invariant 4 L726);trader 只吃 pm 决策(L715);typed speaker(`DebateMessage.role` L622);risk r2 各见其余席 r1(`opponent_stances` many L713);dec.pm 唯一 reasoner_deep(L20/L687,与交付物③ L210 一致);text.sentiment FORBIDDEN 吃预取块+D9 供给不含社媒(WorkerSpec 层 L307 已对,供给面措辞随交付物②安装)。

## D. 疑点

1. **27 计数的跨 plan 依赖**:归属表 AMEND-3→Phase 5,但 D11 说目录装配 phase "装 27 worker"。若 Phase 5 reconcile 不装 #25 的 WorkerSpec,本 plan 须自装(计数=3+3+21)。需与 Phase 5 plan 对账统一。
2. **AMEND-6a "独立形态词典小任务"边界**:归属表措辞未定其在本 phase 内还是独立小 phase。A3 方案=注册表契约+种子词典材料进本 plan、~30 个识别器 handler+历史回放统计另立;需用户拍板。
3. **allowed_actions 宿主形态**:R2 只说"上移为确定性 guardrail、在 dec.pm 前算出"。A4 按 memory-bridge 式 pre-input adapter(不动 27 席 roster);若做成确定性 worker 则 roster/计数再变。需裁决。
4. **dec.trader 目标仓位带落点**:0/25/50/75/100% 带+数字 Optional 禁默认值落在 Phase 6 冻结的 `PortfolioTargetProposal@1`(plan L688/L724 钉"imported, never redefined")。若该 schema 无仓位带字段,需 Phase 6 reconcile 或 @2;本 plan 只能写 skill/prompt 纪律。
5. **交付物②③与 plan 的 skill 命名冲突**:交付物 frontmatter `name` 用人读名("A-share sentiment read")、目录用连字符(`text-sentiment`);plan 规范骨架 name=worker_id、目录带点(`text.sentiment`,L112/L138)。安装前需定一条 canonical 规则(建议:目录=worker_id 带点为准,name 字段是否放开需拍板)。

---

## §7 · Phase 9 adapters

## Phase 9 plan(2026-07-16-orchestration-phase9-adapters.md)× R2 spec 对账清单

**归属基线**:R2 §9 归属表把 AMEND-1/2/3/4 判给 Phase 5、AMEND-5/6/6a/7/8 与 D11 判给目录装配 phase(Phase 8)、事件库判独立小 phase(D10)。Phase 9 是纯消费端,R2 直接命中本 plan 的只有三处:replay 的历史因子数据前置、§11 红线套件扩容、与 D9/D5 的一致性钉子。

## A. 新增任务

**A1 · Task 2b「因子电池快照归档 coverage 进 replay manifest + 可行窗口约束」**(插在 Task 2 之后;Task 4/12 依赖)
- 依据:R2 §1 交付物①《market-factor-report-schema》的"快照归档依赖"+ AMEND-1 红线"缺历史覆盖→UNAVAILABLE,绝不拿当前快照冒充历史"(spec :46)。plan 现状:Task 2 `build_replay_manifest`(plan :210)只绑 pit_store `_meta.json` 的 `news_coverage_floor/cal_start/cal_end`,对 market_tape/fundflow/macro 快照归档起点**零提及**——而每个决策点重跑 Bootstrap(Task 4 步③,plan :321)必经 market.factor,其历史序列只能来自这些归档。
- 要点:① manifest 扩展 per-feed 归档 coverage floor 条目(digest-bearing,物理路径 audit-only);② 派生"replay 可行窗口"事实:决策点早于某 feed 归档起点 ⇒ 该 feed 因子 UNAVAILABLE + badge,Bootstrap 降级诚实继续,driver 不得视为失败;③ 结构性保证 PIT_REPLAY 模式路由永不选中 ONLINE live 源(复用 Task 2 描述子 supported modes `(PIT_REPLAY,)` 既有机制,加显式测试);④ Global Constraints 增一条前置声明:replay 对 Lane 0 的可行窗口由归档起点决定,plan 不隐含"任意历史区间可跑"。
- 测试:归档 floor 之前的点出 UNAVAILABLE 不补零、digest 稳定;floor 之后的点正常出数;两者混跑一区间不炸。

**A2 · Task 0 handoff 增补项(并入 Task 0 Step 1,非独立任务)**
- 在 Task 0 清单(plan :43-51)第 4 项后增:Phase 5 交付的 BootstrapPlan/`market_factor_report` 须暴露 coverage/UNAVAILABLE 语义与 `factor_report_digest`(交付物①/④ 的可消费面),否则 A1/B3 的断言无锚;按既有 correction-clause 风格记为 C3 的细化,不发明平行语义。

## B. 修改现有任务

**B1 · Task 9(plan :627)**:原文"**No new worker id is added** — the 24-final-worker table is Phase 8's and stays closed"。R2 D5 已拍板 24→27(#25/#26/#27),且 AMEND-5/6 归目录装配 phase(=Phase 8)装配。改法:删硬编码"24",改为"worker 表继承 Phase 8 评审后的表,逐字节 pin,Phase 9 不加任何 worker id;禁止对 worker 数写整数断言"(同 plan :267 禁源计数硬编码的同款疫苗)。为什么:不改则 Phase 8 装 27 席后此句与 invariant 3(plan :634)的 pin 直接红。

**B2 · Task 0 第 4 项(plan :46)**:原文"Phase 8 exports the lane catalog whose `dec.trader` emits only `PortfolioTargetProposal`"。改法:补一句——handoff 接受 R2 修订后的 Phase 8 目录(含 #26/#27 与 D11 的 capability manifest 生成器产物,若在),不断言席位数;`dec.trader` 断言保留不动。为什么:D11 把生成器判给目录装配 phase 第一个任务,Phase 9 按 digest 消费其产物,handoff 措辞须容纳。

**B3 · Task 12 e2e `test_luozi_interval_e2e`(plan :767)**:原文"per-point Bootstrap+MainPlan with per-point PIT snapshots"。改法:增两断言——① 每点 Bootstrap 产出的 regime/rotation 报告绑定**该点**`market_factor_report` 的 `factor_report_digest`(跨点引用拒收);② 报告内 EvidenceAnchor 经 Phase 5 的机器复核器可解析(消费不重实现)。为什么:交付物④ 把 digest 绑定与 EvidenceAnchor 机器可复核定为 Lane 0 硬性质,而"逐点各有一份因子报告、绝不许串点"恰是 interval replay 独有的失效面,只有 Phase 9 能测。

**B4 · Task 12 红线套件(plan :772-780)**:原文八条"spec §11 verbatim, one test per line"。改法:第 7 条(degradation badged,plan :779)扩展或增第 9 条——replay 边界的 UNAVAILABLE 诚实渲染:归档 floor 之前的点在 trace/curves API 中显 UNAVAILABLE+badge,绝不补零、绝不以当前快照冒充历史(AMEND-1 红线)。为什么:plan :785 钉死 `pytest tests/orchestration -v` 是旧入口下线的"单一绿灯",R2 新红线若不进聚合面,下线前置条件对 R2 是盲的。

**B5 · Task 4 步③(plan :321)**:原文"admit + run the versioned `BootstrapPlan` (Phase 5) → frozen per-point ContextSnapshot"。改法:句后加注——per-point Bootstrap 的 market.factor 历史读数经 A1 归档源在 PIT 下解析;UNAVAILABLE 因子 ⇒ 降级诚实快照(badge 进 ShadowReplayRunState.badges 面),非 run 失败。为什么:回答"历史因子数据从哪来"必须落在 driver 正文,否则实现时默认走 live 源即前视/冒充。

## C. 明确不改/不加

- **AMEND-1/2/3(因子电池/扩容/#25)**:归 Phase 5;Phase 9 经 clause C3(plan :58)消费 BootstrapPlan,电池实现与 miner 一概不进本 plan。
- **AMEND-4(Lane 0 六段 prompt/guardrails)**:Phase 5 装配;Phase 9 不写任何 prompt。冷启动条款(检索为空显式声明)由 Phase 3 memory facade `prepare_pit_replay`(plan :315)+ Phase 5 prompt 承担,plan 已 PIT 化逐点记忆,无需加。
- **AMEND-5/6/6a(#26/#27/K线形态词典)**:目录装配 phase + 独立形态小任务。#27 (b) 路依赖的"落子影子决策积累+matured 门"本 plan Task 5/6(evaluator handoff + WAITING_FOR_MATURITY)**已是其基质**,无需新代码。
- **AMEND-7(Lane C 接线/skill/事件库)**:目录装配 phase;事件库=D10 独立小 phase。Task 2 读 news/events/policy 走 pit_store(plan :209),不接 SQLite 事件库,不提前接。
- **D9(text.sentiment 供给改判)**:与本 plan 已一致——Task 3 LiveClientSource 绑"market-wide tape snapshot"(plan :267)与 D9 的打板温度供给同向,且全 plan 无一处引用已废弃的雪球 `ww_news_collect`;macro_pulse/sentiment store 的 capability 接线归目录装配 phase,**不在 Task 3 加新绑定**(帷幄 research 适配器不为 text lane 供数)。
- **AMEND-8(Lane D 六席/allowed_actions 前置/辩论机械)**:目录装配 phase;Phase 9 Task 4 步④(plan :321)把"Phase 8 decision chain ending in dec.trader"当整体 MainPlan 消费,内部升级对 Phase 9 透明。
- **D7(外部技术分析投递入口)**:console+固定目录归 #27 侧;"落子页面入口后置"已被 Global Constraints「UI 只填充不重建」(plan :23)结构性保证,无需加文。
- **D11(capability manifest 生成器)**:目录装配 phase 第一任务;Task 9 invariant 4(plan :635)已禁 regeneration、按 digest 消费,无需改。
- **词表红线(RotationStage vs 旧 market_cycle 两套枚举)**:Phase 1 enums 已锁 + Phase 5 prompt 纪律;经 plan :785 聚合命令自然纳入绿灯,Phase 9 不重测。
- **D6/D13(修订节流 N / V-F 锚词典治理)**:#26/#27 与 Lane D 治理项,与 Phase 9 无接触面。

## D. 疑点

1. **replay 可行窗口可能整段塌陷,需用户裁决**:market_tape 归档"挂芯片"未做、fundflow 无归档、macro 快照月轮转(72573b8)在 worktree 未合 main——若归档起点≈2026-07,则任何更早区间的 replay 其 Lane 0 半边全程 UNAVAILABLE(合法但可能无研究价值)。选项:(a) 接受"可行窗口从归档起点起"(A1 即够);(b) 把 datafeed P3 归档小 phase(口径已定暂不开工)升级为 Phase 9 硬前置。plan 与 R2 均未裁此题。
2. **24→27 的连锁归属**:B1/B2 只让 Phase 9 去硬编码;把 27 席真装进目录是 Phase 8 plan 的 reconcile 义务。若 Phase 8 plan 未同向改(仍按 24 席写),Phase 9 的 byte-identical pin 语义悬空——两 plan 须同批裁决。
3. **R2 新红线的宿主之争**:EvidenceAnchor 复核/digest 绑定的**结构性**测试属 Phase 5,本清单只在 Phase 9 加边界侧探针(B3/B4)。若用户想把四条 R2 红线全部集中进 Phase 9 §11 套件(作为唯一绿灯的完备性),范围会外溢到 Lane 0 内部性质,需明示。

---

## §8 · 小 phase 甲/乙 任务骨架(时间闸,立即立项)

## 小phase甲「盘口/资金快照归档」(纯加法,时间闸)

**现状锚定(已读实码)**:`market_tape.py` SWR 只落单份 `var/live/market_tape.json`,每次 `_refresh` 原子覆写(L55-59/L203),**零历史**;`derived` 已含 rot/breadth 族全部原料(zt/zb/dt/yzt 计数、max_streak、break_rate、promotion_rate、ladder 直方图、north_net/north_scope,L93-100)+ `board_date/board_backfilled`。`fundflow/pulse.py` 同款:`read_live` 只覆写 `var/live/fundflow_live_{kind}.json`(L152-158),`read_history` 分钟线仅当日(L285 注:"跨日回看需另接存档")。**注意**:pulse.py 模块 docstring L6 声称"每次真拉…向 var/fundflow/<当日>.jsonl 追加快照",但全文件无此实现(L18 `_SNAP_DEFAULT` 定义后从未使用)——陈述是母版 macro 抄来的死话。macro 月轮转先例(72573b8)**不在本分支**(worktree awesome-agnesi 未合),本分支 `macro/pulse.py` 仅有 append-only `snapshots.jsonl` + 脏行跳过读(L72-91),轮转语义照 R2/memory 描述实现,勿猜代码。

**任务拆分(TDD,每条先测后码)**:

- **甲-1 归档器** `guanlan_v2/datafeed/snapshot_archive.py`(新模块):`archive_eod(now=None, cache_dir注入)` 纯读现有缓存文件(`market_tape.json` + `fundflow_live_concept/industry.json`),**绝不调 `_refresh`/`_trigger_*`、绝不触网**;每 kind append 一行 `{trade_date, archived_at(=available_at=落盘时刻), kind, payload}` 到 `var/archive/<kind>/snapshots.jsonl`;写入恒当月主文件,发现非本月行→搬 `snapshots-YYYYMM.jsonl`(macro 先例同款,**尾读红线**:最近数据永远在主文件尾,读近期不扫归档文件)。幂等:同 (trade_date,kind) 已有则跳过。payload 建议全量含 sources rows(zt/zb/yzt 池 + fundflow boards+market 五档——`rot.ladder_theme/diffusion` 电池因子需要行级明细,见疑点4)。
- **甲-2 fundflow 侧收口**:同归档器覆盖两 kind;顺带把 pulse.py L6 死 docstring 改为指向本归档器(只改注释,行为零改);缓存缺失/`ok:false`/`warming` → 该 kind 当日诚实跳过并记 note,**绝不落半假行**。
- **甲-3 调度挂载**:env 总闸 `GUANLAN_SNAPSHOT_ARCHIVE=1` + 收盘后(≥15:05)触发 + 当日 dedup 三门,照 `autonomy/runtime.py::maybe_enqueue_daily_review`(L118-140)的钩子模式;自吞异常绝不拖垮宿主;非交易日:board 池空由 `_backfill_board_pools` 徽章语义兜,归档器按 `board_date` 判重不重复落。
- **甲-4 读取器** `read_archive(kind, start=None, end=None, asof=None)`:逐行读+脏行跳过(照 macro `_read_snapshots` 惯例);`asof` 给定则过滤 `archived_at<=asof`;返回按 trade_date 升序序列 + `first_date`(真实起点显形)——这是未来 Phase 5 `market.factor` 电池 rot 族 6 因子+炸板率/北向/主力分位历史序列的唯一供给口(schema spec §5「归档开始前短序列诚实,不回填不伪造」)。
- **甲-5 守护测试**:① `read_tape`/`read_live` 签名与 payload 契约逐键不变(快照对比);② 归档路径全程无网络接缝(probe/sector_fn 桩上被调即 fail);③ 归档文件 append-only(重跑不改已有行);④ 全测试 tmp 目录注入,不碰真 var/。

**明确不改(scope guard)**:不动任何 SWR 现拉/读路径与前端契约;不做归档起点前的历史回填;不实现电池因子本身(那是 Phase 5 AMEND-1/2);health 总闸计数不在本 phase 动(挂账)。

## 小phase乙「事件库第一步」(SQLite 三件套,零 LLM,时间闸)

**依据**:R2 §7.3 分级路线第一步(events 表 + FTS5/simple + SimHash/72h 桶,原文 L231)+ D10 裁定(就地追加独立小 phase,先吃现有 feed,text worker 到位后 payload 入同一张表,不回改已完成 phase)。feed 现状:`kuaixun.fetch_kuaixun` 规范行 `{time(16位), title, summary, codes}` 抛错上传/空返 `[]`;`newsradar` 缓存行 `{title, url, summary, source, sector, ts(epoch)}`,TTL 30min,RSS=不可信外部输入(模块红线 L12-13 已声明)。

**任务拆分(TDD)**:

- **乙-1 库 + schema** `guanlan_v2/events/store.py` → `var/events/events.db`(WAL):`events` 表列 `event_id, event_type, tickers(json), title, summary, source, source_ref, event_time, available_at NOT NULL, invalidated_at NULL, simhash, line_id, ingested_from`;PIT 检索谓词冻结为一行 `WHERE available_at<=:asof AND (invalidated_at IS NULL OR invalidated_at>:asof)`;v1 只留 `invalidated_at` 列+读取语义,失效流(Graphiti 式)明确属第二步不做。测:建表幂等、PIT 列非空约束、路径注入。
- **乙-2 确定性入库 adapter**(零 LLM):kuaixun 行→`event_time=time, tickers=codes, event_type="kuaixun"`;newsradar 行→`event_time=ts, event_type="newsradar:<sector>", tickers=[]`。v1 **不做语义事件分类**(taxonomy/trigger-type-roles 属 text worker,AMEND-7 §7.2 条5);`available_at`=入库落盘时刻(保守,见疑点2);入库单事务,源抛错向上传播不半写。
- **乙-3 SimHash 转载去重 + 桶事件线**:title+summary 64 位 simhash,海明距≤3 判转载(重复行仍入库但共享 `line_id`,不丢证据);(ticker×event_type×72h) 桶内聚合 `line_id`;测:构造近重复文本/跨 72h 边界。
- **乙-4 FTS5 中文检索**:外部表 + wangfenjin/simple 分词扩展 `load_extension`;`search_events(query, asof, limit)` **asof 必填,缺则 raise**(不是默认 now——静默 now=前视温床);扩展加载失败→诚实降级 LIKE + payload 标 `fts:"unavailable"`,绝不静默装好。
- **乙-5 调度挂载 + 守护测试**:入库 tick 挂 env 总闸(kuaixun 高频源可 30min 对齐 newsradar TTL,EOD 兜底一次);守护:① 入库路径零 LLM 接缝(llm 桩被调即 fail);② 本模块不 import `guanlan_v2/orchestration/*`(已交付代码零回改,D10);③ 无 asof 检索必炸;④ 全测 tmp db。

**明确不改(scope guard)**:不接 Graphiti/GraphRAG/embedding(R2 "别碰"清单 L233);不训抽取模型;不做 LLM 事件抽取/importance/sentiment(那是 Lane C text worker,目录装配 phase);不动 kuaixun/newsradar 现有出口契约。

## 共同约束

- **两者都是时间闸**:数据不可回填,晚开工一天=永失一天历史(schema spec §5 显式点名归档为 rot 族历史序列前置)——排期上与 Phase 4 并行、优先于非时间闸任务;两 phase 互相独立可并行。
- **纯加法**:零改 orchestration 已交付 63 commits(D10/接线总原则原文:"无一需要回改 Phase 1/2/3/3b");也零改 SWR 热路径。
- **available_at 语义统一**=本系统真正可知时刻(落盘/入库),绝不早报;首发/披露时刻另存 `event_time`。
- **诚实红线全继承**:缺源当日跳过并显形,不补零、不伪造新鲜、`first_date` 显形、降级必标注。
- 测试隔离:一切路径 env/参数注入 tmp,绝不碰真 var/(as_of 冻结=测试污染真 store 的旧教训);运维脚本输出 ASCII(GBK 坑)。

## 疑点(需用户裁决)

1. **乙 与 datafeed P3 新闻归档的关系**:D10 留了"或并 datafeed 已立项新闻归档 P3"未拍死;memory 记 P3 口径=RSS+东财快讯双源月轮转 jsonl(暂不开工)。乙 的 events.db 同吃同两源——是 P3 直接升格为事件库(单一存储,建议)还是 jsonl 归档照做+事件库双写(双事实源风险)?
2. **available_at 口径**:AMEND-7 接线原则写"快讯=首发时刻",本稿取保守"入库落盘时刻"(首发时刻存 `event_time`)。回测 PitGuard 裁决用哪列需拍板——用首发时刻会把"我们尚未抓到"的窗口算成可知(乐观),用落盘时刻则受抓取频率制约(保守但真)。
3. **wangfenjin/simple 分词器**=外部编译 SQLite 扩展(Windows 需 dll):获取/vendor 方式属外部下载需批准;不可得时的降级档(LIKE vs jieba 预分词入 FTS)需确认。
4. **甲归档 payload 粒度**:全量 sources rows(zt 池 300 行×日,月文件 ~10MB 级)vs 只存 derived 标量。`rot.ladder_theme/rot.diffusion/rot.leader_persist`(schema spec §3)需要行级明细,本稿建议全量存 zt/zb/yzt 池+fundflow boards;若嫌大需用户裁减并接受对应因子 UNAVAILABLE。
5. 甲实现时若 72573b8(macro 月轮转)已合 main 则直接复用其函数;仍未合则按同款语义独立实现,**勿 cherry-pick**(并发分支交错,memory 有"勿擅自搅 git"红线)。

---

## §9 跨 plan 接缝(改 plan 时同批处理,防三头互踢)

1. **D6 节流**:规则在 P4(A1 常量+纯函数)、成熟计数数据在 P5(matured-case grader)、执法在 P8(#26/#27 guardrail 引用 P4 原语)——三份 plan 的 Execution Handoff 都要写这条接缝。
2. **27 计数**:R9 裁决后 P5(装 #25)/P8(计数 27)/P9(去硬编码 24)同批改,否则 P9 的 byte-identical pin 悬空。
3. **仓位带**:R7 裁决后 P6(schema@1+词表 export)/P8(trader skill 引用同一词表)/P9(红线套件 None 保真)同批。
4. **归档链**:小 phase 甲(落盘)→ P5 电池 rot 族(读取器唯一供给口)→ P9 replay 可行窗口(manifest coverage floor)——甲的 read_archive 接口是三方契约。
5. **事件库**:小 phase 乙 v1 零 LLM 入库 → P8 Lane C text worker payload 入同一张表(乙-1 的表 schema 预留 text-worker 列语义即可,不预建)。
