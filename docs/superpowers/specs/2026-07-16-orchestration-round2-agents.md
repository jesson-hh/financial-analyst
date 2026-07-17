# 编排框架 · 第二轮修改 spec(R2:agent/skill 细化)

日期:2026-07-16 · 状态:**R2 滚动草案**(随讨论持续追加,成熟一节冻结一节)
基线:`2026-07-15-orchestration-framework-design.md` v1.1(**本文档不改旧 spec**,只记录相对 v1.1 的增量修改;冲突处以本文档为准)
前置事实:Phase 1 契约层已实现(`guanlan_v2/orchestration/` 14 模块,778 tests);Phase 2/3 plan 用户 reconcile 中;Phase 2 Task 0 handoff gate 已提交(807 tests)。

---

## 0. 修改索引(相对 v1.1)

| # | 修改 | 对应 v1.1 位置 | 落地时机 |
|---|---|---|---|
| AMEND-1 | `market.factor` 因子电池 = 版本化可扩注册表 | §5 | 电池 schema 第一天就按此设计(Phase 5 Bootstrap 实现时) |
| AMEND-2 | 轮动观察因子族扩容(+4 因子) | §5 表 | 与 AMEND-1 同批,第一批实现,不等挖掘 |
| AMEND-3 | 新增 worker #25 `market.factor_miner` | §3.0 / §3.1 | spec 先行;**真跑依赖经验库成熟标注 → Phase 5 之后** |
| AMEND-4 | Lane 0 LLM prompt/skill 内容规范 | §3.6 细化 | prompt 起草即用;装配按 Phase 2 目录 ABI |
| AMEND-5 | 新增 worker #26 `quant.curator`(Lane A 管家) | §3.1 | **已拍板(D5=独立)**;真跑依赖回测卡在跑,先行于经验库 |
| AMEND-6 | 新增 worker #27 `pv.curator`(Lane B 管家 + 外部技术分析摄入) | §3.2 | 形态回放求值不依赖经验库,**可早于 #25 真跑**;方法论 A/B 半边依赖影子决策积累 |
| AMEND-6a | **P0 · K线经典形态覆盖缺口补全**(用户点名 2026-07-16"一定要完善") | §3.2 | Lane B 细化的第一优先交付;首批自建种子,不等外部投喂 |
| AMEND-7 | Lane C 文本五席:**数据接口 P0**(用户点名"一定要重视")+ skill 设计要领 + 文本→事件库定位 | §3.3 / §4 | 源接入按 Phase 3 数据 lane 节奏;skill 起草即用;事件库=数据资产(D10 定时机) |
| AMEND-8 | Lane D 决策/风控六席:硬约束前置确定性化 + 辩论机械 prompt 层 + 逐席规范 | §3.4 | prompt 起草即用;`dec.research_mgr`/`dec.pm` 兼 Phase 2 pilot 关键路径 |
| (追加区) | 后续讨论内容 | — | — |

---

## 1. AMEND-1 · `market.factor` 因子电池:版本化可扩注册表

**动机**:market.factor 是确定性 worker,**不能也不应自主更新因子**(自改 = PIT 回放碎、回测可比性碎、审计链碎;v1.1 红线:worker 只能提 proposal)。但市场在演化,电池必须能**受治理地生长**。结论:把"电池"从写死清单升级为注册表。

- 因子按 `factor_id@definition_version` 注册;**加因子 = 加配置 + handler,不动架构**。每次运行的 provenance 记录电池整体 digest(含全部 factor_id@version 集合)。
- 参数演化通道(继承 v1.1 §5):经验库成熟标注 → validation IC/命中率 → Evaluator-Optimizer 调参 → 新 `definition_version` → 人审上线;sealed holdout 永不参与调参。
- **定义演化通道(本轮新增)**:新因子定义只能经 AMEND-3 的 `market.factor_miner` draft → 人审 → 注册表 bump 进入。
- **universe 版本化**:题材/行业分类学(东财概念板块等)是**数据侧**输入,新主线出现(如"算力租赁")自动进入 HHI/扩散度等泛型因子的计算范围,**不需要新因子**;但 `universe_version` 必须进 per-factor provenance,否则回放对不上截面。

> **交付物① 已出**:`2026-07-17-market-factor-report-schema.md`(完整信封+逐因子 schema+17 因子电池 v1 参数表+渲染契约+快照归档依赖)。以下 §1.1 为早期草稿,以交付物①为准。

### 1.1 `market_factor_report` per-factor schema(草案)

```
factor_id / definition_version / params(冻结) / universe + universe_version /
frequency / series(走势向量) / latest / pct_250d(250日分位) /
coverage / missing_policy / available_at / content_digest
```

- 走势渲染(给 LLM 的不可信文本块,`data/render.py` 出):**60 交易日日频序列 + 最近 5/20 日变化摘要 + 250 日分位**(待拍板 D1)。
- 缺历史覆盖 → 该因子 `UNAVAILABLE`,绝不补零、绝不拿当前快照冒充历史(继承 v1.1)。
- 数据源映射(全部现成模块,组装非新建):market_tape(打板温度/炸板率/晋级率/连板梯队)、fundflow(主力净额/板块资金双端)、北向(**sgt 护栏**:点密度<半数置空)、乐咕全A涨跌(失效诚实显 "—")、估值五年分位(baidu_valuation_percentile)。

## 2. AMEND-2 · 轮动观察因子扩容(v1.1 §5 表新增 4 行)

v1.1 轮动仅 3 因子(HHI/主线扩散度/行业动量离散度),偏薄。新增(原料均在 market_tape/fundflow,第一批实现):

| 类 | 因子 | 计算 / 待设计参数 |
|---|---|---|
| 轮动 | 连板梯队题材归属 | 最高板与梯队人数按题材分布;"高度被哪条主线占据" |
| 轮动 | 龙头身份持续性 | 领涨股 identity 的 N 日换手(主线内领涨个股是否更替) |
| 轮动 | 主线资金连续性 | 主线板块连续净流入天数 |
| 轮动 | 新题材爆发强度 | 新入 universe 题材首日:题材内涨停数 / 成交占比 |

## 3. AMEND-3 · worker #25 `market.factor_miner`(feedback-driven 因子生成)

**定位**:市场级因子的挖掘 agent,`quant.factor_miner` 的市场级孪生;**复用现成 `research/loop.py`**(提案→求值→批判≤5轮→门→draft,已在选股因子上真实产出 `factorlib/mined/lib_rl_47074b_r0`)。不发明第二套回路。

```
反馈源:regime/rotation 判断错误的成熟案例(经验库延迟标注)
        + L3 归因指出"现有因子未捕捉 X"的 critique
  → LLM 提案:候选因子定义(公式+参数;只允许引用已注册原料 feed)
  → 确定性求值:按 available_at 回填历史序列;
        对成熟 RegimeCase 标签测 IC / 命中率 / 领先滞后
  → 门:稳健性 + coverage + 与现有因子族相关性上限(拒冗余)
  → DRAFT 因子定义(进 TrialLedger,记 family_id 防多重检验)
  → 人审 → 新 definition_version 注入 market.factor 电池(注册表 bump)
```

**红线**:① miner 永远只产 draft,**绝不热改** `market.factor`;② sealed holdout 不参与任何调参/选择;③ 候选因子历史回填必须 PIT 正确(前视=bug);④ `can_emit_decision=False`;⑤ 离线 research lane 运行,**不进每日主 DAG**(与 quant.factor_miner 同待遇)。

**时机诚实**:求值依赖经验库成熟标注(Phase 5 交付物),故本 worker Phase 5 后才能真跑;本轮只冻结 spec + 让 AMEND-1 的注册表先把"可插拔"焊进电池设计。

> **交付物④ 已出**:`2026-07-17-lane0-regime-rotation-skills.md`(两席逐字安装件 + `RegimeReport@1`/`RotationReport@1` schema 草案 + TrendState/RiskState/HeatState 新枚举提案;factor_report_digest 输入绑定 + EvidenceAnchor 机器可复核;词表红线显式;冷启动条款内置)。**四件交付物 ①②③④ 全部出齐。**

## 4. AMEND-4 · Lane 0 LLM prompt/skill 内容规范

现状:仓内最接近的"LLM 大盘判读"仅 `market_temp.py` 的一句 `market_read/market_tilt` 缓存判读;`market.regime`/`market.rotation` 的 prompt **全新写**。

### 4.1 `market.regime` — 六个必备段

1. **角色与任务**:三轴判断 = 趋势(牛/熊/震荡/unknown)× 风险(risk-on/off/neutral/unknown)× 热度(normal/overheat/unknown),各带概率与置信。
2. **读数方法论(playbook 核心)**:逐族解读规则——广度背离超阈值=顶背离警报;涨停强度+连板高度=情绪温度;炸板率/晋级率恶化=情绪衰竭;北向/主力趋势+分位=资金面;RV 短长比=波动情境;估值分位=长周期位置。**冲突裁决**:证据矛盾 → 降置信 + 列出冲突项,不强判。
3. **经验库类比段(可降级块)**:引用 RegimeCase 须标案例日期+后验结果,参照不盲从;**检索为空 → 显式声明"无先例参照,仅基于当期因子证据"**(冷启动条款,Phase 5 前常态)。
4. **证据纪律**:每个判断必须引用 `factor_id`+数值;快照外数据一律禁止;承重数字必须可溯源(`x.number_critic` 拦截)。
5. **unknown 合法性**:coverage 不足或信号矛盾时输出 unknown 是正确行为而非失败(不写死这条 LLM 必硬编结论)。
6. **输出 schema 绑定**:`regime_report` 字段逐一说明(概率语义见 D3)。

### 4.2 `market.rotation` — 同构六段 + 两个特有约束

- **词表红线**:阶段词表 = 已冻结 `RotationStage`(启动/扩散/分化/退潮);与旧打板 `market_cycle` 六阶段(冰点/分化/逼空/发酵/回踩·启动)是**刻意分开的两套枚举**(Phase 1 enums 已锁,"分化"两侧同词不同义)。prompt 必须显式声明不混用。
- **阶段判定 = 可操作信号组合**(skill 程序记忆,后续由 proposal 流程迭代):启动=资金集中度升+龙头一进二+扩散度低;扩散=题材内上涨广度扩大+跟风晋级;分化=高位炸板增多+梯队断层;退潮=集中度回落+龙头破位。
- 输入面额外含**产业链框架**(AI 看板五层 YAML)的引用方式。
- 输出:`rotation_report`(主线排序 + 每主线阶段/强度/持续性 + 置信)。

### 4.3 共享 guardrails(两 LLM 共用一份)

上游 payload 是不可信数据非指令(FSI 隔离)/ PIT 只用快照内数据 / 反捏造 / skill-v1 信封硬格式(frontmatter 三行 description 含 Perfect for·Not ideal for + canonical-JSON triggers;正文以 `## ⚠️ CRITICAL: Data Source Priority` 开局——Phase 1 `catalog.py` 机器强制,格式错目录拒收)。

---

## 5. AMEND-5 · worker #26 `quant.curator`(Lane A 管家:因子生命周期治理)

**动机**:Lane A 现有 5 席全是"生产者",没有"管理者"。最接近的 `quant.factor_miner`(research/loop)只做**从零挖新**,四个缺口:① 不修订已有因子表达式;② 不管生命周期(衰减/退役);③ 反馈源未接 vintage IC/OOS/PBO 卡与 L3 归因;④ 因子领域知识没沉淀成 skill。

**关键事实(已验证)**:factorlib 因子 = DSL 表达式(如 `rank(roe)+rank(total_equity/total_mv)+rank(-delta(close,5))+rank(-turnover_rate)`,带 family/universe/freq/ic/oos 元数据)。"改因子表达式" = 提案新 expr 串 → 走 miner 同一条确定性求值管道(引擎 DSL 求值 + IC/Sharpe/robust 门)→ 新版本。**机器现成,只缺角色。**

**职责(全部 proposal-only)**:

| 职能 | 输入(反馈源) | 输出 |
|---|---|---|
| 衰减盯防 | `quant.backtest` vintage IC/OOS/PBO 卡、`quant.factor` IC 报告 | 衰减警报工件(哪个因子哪个 universe 何时开始衰减,证据引用链) |
| 修订提案 | 衰减警报 + L3 归因 critique + 经验库教训 | 修订 draft:`factor_id` 不变、新 expr → 求值管道 → 新 `definition_version` |
| 退役提案 | 持续衰减 + 修订失败史 | 退役/降权 proposal(人审) |
| 组合触发 | 因子族截面变化 | v4 变体重训触发 proposal、族权重调整 proposal(接现有 regime weights / retrain 通道) |

**"管理"的边界(FSI 红线不破)**:curator **不能** spawn 子 agent、不能指挥 Lane A 其他 worker 运行(那是 Orchestrator 的活)、不能直接写 factorlib/模型/skill——它的"管理"全部体现为**产出生命周期 proposal 工件**,由人审与既有通道(train_promote/retrain/registry bump)落地。

**过拟合红线(本 AMEND 最重要的一条)**:反馈驱动改表达式是全系统最危险的数据窥探通道(改 N 次直到 IC 好看 = 过拟合机器)。强制:① 每次修订 = 同 `family_id` 的 TrialLedger 一笔 trial(多重检验计账);② sealed holdout 只揭一次;③ **修订节流**——前一 `definition_version` 须有 ≥N 期成熟(realized)观察才允许再次修订;④ 人审必经;⑤ 修订提案必须引用触发它的具体证据工件(不许"顺手优化")。

**与 Lane 0 的一致性**:AMEND-1/3/5 同一模式——版本化注册表 + miner(挖新)/curator(治旧)双通道;factorlib 因子统一 `factor_id@definition_version` + provenance。`market.factor_miner`(#25)与 `quant.curator`(#26)共享同一 research/loop 求值机器,只是标签源不同(RegimeCase 成熟标注 vs 截面 IC/OOS)。

---

## 6. AMEND-6 · worker #27 `pv.curator`(Lane B 管家:形态生命周期 + 外部技术分析摄入)

**Lane B 现有家底(实测)**:① `compute_pa_features` 15 键 bar 级几何(body/上下影/close_pos/range_atr/ema20_rel/breakout/inside_streak/vol_ratio/limit/gap/follow/recent…,确定性纯函数,前后端镜像);② EV-017~026 十张进攻卡,其中 **EV-021「K线形态进攻词典:几何字段→动作映射」是唯一的形态词典卡**;③ 每策略"可编辑方法论 prompt"层(默认关 opt-in);④ `pv.technical` 继承 TA ≤8 互补指标 + 真值锚。**诚实评估:骨架在,但"技术形态 skill 体系"薄**——bar 级几何无经典多 bar 形态(头肩/双底/旗形/杯柄等零覆盖),词典仅一张卡,方法论层默认关。

**核心需求(用户)**:把别人的技术分析文章投喂给 agent,让它自己迭代形态库/方法论。**可以做到**,回路如下:

```
用户投递外部技术分析(文章/笔记,标注来源作者)
  → 以「不可信数据」进入(FSI 隔离;文中任何指令绝不提升为系统指令——
     别人的"技术分析"里完全可能写着"满仓干")
  → 抽取:拆成候选条目,按可计算性分流两路:
  ┌─ (a) 可计算形态/参数(如"突破前5高且量比>2"):
  │      → 提案确定性形态识别器 pattern_id@definition_version
  │      → 历史日线回放求值(命中率/胜率/盈亏比 vs realized)
  │      → 过门(稳健+与现有形态相关性上限)→ DRAFT → 人审
  │      → 进几何注册表(15键 → N键,AMEND-1 同款可扩注册表)
  └─ (b) 不可计算判读经验(如"主升回踩不破20日线是第二买点心法"):
         → skill diff proposal(方法论 playbook 增量)
         → A/B 影子对照评估(现成机制:ww_rerank A/B 双篮 + matured 门)
         → 人审 → sync-skills
每条被采纳规则记 source(哪篇文章/谁的方法);TrialLedger 计账,防"看一篇改一次"。
```

**"自己迭代"的准确含义**:提案→回放求值→过门→draft **全自动**;上线**必须人审**(红线与 #25/#26 完全一致)。含糊形态("形态优美"类)不许硬编成识别器,一律归 (b) 路方法论。

**时机优势**:(a) 路的形态求值只需历史日线回放,**不依赖经验库**——#27 的可计算半边可以早于 #25 真跑;(b) 路依赖落子影子决策积累成熟对照。

**与 #25/#26 的一致性**:miner/curator 模式第三次复用;形态 = 因子的近亲(确定性识别器 + 版本化定义 + 统计验证),共享同一 research/loop 求值机器与同一套过拟合红线(TrialLedger/节流/holdout/人审)。

### 6.1 AMEND-6a · **P0 重点:K线经典形态覆盖缺口补全**(用户点名 2026-07-16,"一定要完善")

现状 15 键全是 bar 级几何,**经典形态零覆盖**。这是 Lane B 细化的第一优先交付,**首批自建种子、不等外部投喂**(投喂回路是之后的迭代通道,不是起点)。首批种子词典(每个 = `pattern_id@definition_version` 确定性识别器 + 历史回放统计,含糊不可判定者归方法论路):

| 类 | 形态 |
|---|---|
| 单 bar | 锤子线 / 上吊线 / 十字星 / 长腿十字 / 流星线 / 纺锤线 |
| 双 bar | 阳包阴吞没 / 阴包阳吞没 / 孕线 / 乌云盖顶 / 曙光初现 |
| 三 bar | 启明星 / 黄昏星 / 红三兵 / 三只乌鸦 |
| 多 bar 结构 | 头肩顶/底 / 双顶M·双底W / 三角形整理 / 旗形 / 箱体平台 / 杯柄 |
| **缺口族** | 普通缺口 / 突破缺口 / 持续(量度)缺口 / 衰竭缺口 + **缺口回补统计** |
| A股特色 | 一字板 / T字板 / 涨停突破 / 炸板长上影 |

交付纪律:① 每个识别器判定规则必须可复现(参数显式、版本化),LLM 可起草规则但求值/门/人审同款走完;② 回放统计(胜率/盈亏比/样本数)随词典条目一起冻结,**样本不足显 UNAVAILABLE,不硬给胜率**;③ 多 bar 结构类(头肩/杯柄等)判定主观性高,首版允许"宽松几何近似 + 置信分",不许假装精确;④ 词典条目进 `pv.price_action`/`pv.technical` 的 skill 数据面,判读方法论引用 `pattern_id` 而非重新口述形态定义。

---

## 7. AMEND-7 · Lane C 文本五席(数据接口 P0 + skill 要领 + 文本→事件库)

> 2026-07-16 三路 web 调研落定(skill 模式 / 知识库路线 / A股文本源盘点,全量简报见调研输出)。用户点名:**数据接口一定要重视**。

### 7.1 P0 · 真实接线:Lane C ↔ 既有接口面(**不外找源**)

**用户裁定(2026-07-16)**:数据接口全部在 stocks/datafeed 层已有,不在外找。真正的问题是两个:**agent 怎么知道接口在哪里** + **怎么真实接上**。

**单一事实源**:`docs/agent_data_interfaces.md`(生成式,派生自 `WW_TOOL_TABLE`,`tests/test_agent_interface_doc.py` 漂移守护)——64 个 MCP 工具 / 12 个数据面 / 48 条 HTTP 端点 / 接入通道与写门语义 / 全池红线。任何"接口在哪"的问题以它为准,手抄清单一律不信。

**"agent 知道接口"= 三层结构性接线**(不靠 agent 自己翻文档):

1. **catalog capability 层(唯一授权路径)**:每个真实接口(ww 工具/数据面端点)登记为 `CapabilityDescriptor`;WorkerSpec 白名单引用 `CapabilityRef`;CapabilityGateway 只从目录解析(Phase 1 已结构性强制:CapabilityRef 无 transport 字段,MCP 不授权)。**提案:capability manifest 与接口文档同源同模式——由生成器从 `WW_TOOL_TABLE` 派生 + 漂移守护测试**,杜绝 27 worker 允许清单手抄腐烂(正是 glmcp README 58≠64 那种病的疫苗)。
2. **SKILL.md Data Source Priority 层(agent 面知识)**:每席 skill 的机器强制开局段按优先级点名**真实工具名**(见下表),承诺-供给一致(#557 教训在此落地:skill 只许承诺白名单里真有的工具)。
3. **预取渲染层**:text worker 吃预取块——bridge 静态预取经 CapabilityGateway 调这些端点,DataReader/render 出不可信定界块。worker 零自主拉取。

**逐席映射(全部是今天真实存在的工具)**:

| worker | 主接口(真实工具名) | 备注 |
|---|---|---|
| `text.news` | `ww_news_live`(个股新闻+快讯+stocks 富层公告 event/政策 policy)· `ww_newsradar`(108 RSS·12 赛道)· `ww_live_text`(公告/互动易/涨停原因/热榜)· `ww_f10`(事件公告,**带 asof PIT 裁行**) | kuaixun=快讯唯一门户;newsradar 已带注入防御定界 |
| `text.sentiment` | 预取块注入(不调工具,v1.1 钉死)· 判读写回 `ww_sentiment` store(月轮转,键 (date,code) 后写胜)· **情绪供给=观澜现有资产**:`ww_macro_pulse`(全球情绪温度计 PM+Kalshi)+ `ww_sentiment`(A股个股/大盘判读 store)+ `ww_market_tape` 打板温度(A股行为情绪) | **用户裁定 2026-07-17:雪球 `ww_news_collect` 路已过时,废弃不用**;社媒不进第一批供给 |
| `text.research_report` | `ww_live_text` 个股·行业研报元数据(含 PDF URL,53 源在册)+ 现有 Kimi 抽取管线 | 元数据即结构化 payload;全文抽取走 Kimi 管线(六缺陷清单改前必读) |
| `text.policy` | `ww_news_live` stocks 富层 **policy 路**(已存在,修正调研"零源"误判)+ 快讯过滤 | 缺的是 skill 的措辞对比方法论,不是源 |
| `text.macro` | `ww_macro_pulse`(PM+Kalshi+打板温度)· `ww_market_tape` · `ww_overseas` / `overseas_index_quote` | 最完整,只欠 skill |

**Phase 3 接线原则**:`SourceRegistry`/`DataSource` adapter **包装既有端点**(live_client / 数据面 HTTP / probe CLI),不新建任何爬虫;`available_at` 语义逐源显式(快讯=首发时刻/公告=披露时刻/研报元数据=入库日另记 report_date/联播=播出日)。外网通道调研结论(反爬/哨兵/双通道)整体**降级为 stocks 层上游参考**,orchestration 不直连外网。

> **交付物② 已出**:`2026-07-17-text-sentiment-skill.md`(逐字安装件:信封合规 SKILL.md 全文 + 相对 pilot v1 八处升级 + 装配说明 + schema@2 提案)。

### 7.2 skill 设计要领(调研十条,全部有实战出处)

结论:**没有现成高质量新闻研判 skill 可直接拿**(anthropics/skills 官方库全是文档创作向;社区 agiprolabs/alphaear 两库可抄骨架)——我们的 SKILL.md 属自研空白区。十条:

1. **承诺-供给一致**(TA #557 捏造根因="prompt 承诺 > 数据供给",官方 docstring 承认 verified live):SKILL.md 承诺范围必须逐条等于 runtime 实喂 payload,skill 评审核对"承诺-供给差";
2. 数据一律 orchestrator 预取、定界块注入,worker 零自主拉取;缺源给显式 `<unavailable>` 占位 + confidence 联动(= 我们诚实徽章的 prompt 层同构);
3. 输出全 schema(TA #796 自由文本漂移教训):band 枚举 + 分数 + **confidence 离散档三件套**;confidence 上限由规则(源数/样本量/时效)派生,LLM 只准下调,不许自报 0-1 浮点(verbalized confidence 饱和 0.9 的校准文献);
4. **每条结论强制挂 source_span 证据引用,无 span evaluator 直接拒**;
5. 事件 = trigger/type/roles 三元组;taxonomy 种子 = TradingAgents-CN A股词表(强相关:停牌/复牌/解禁/定增/重组/借壳/退市/ST…;排除词专杀"成分股"式伪相关)+ DuEE-fin 13 类型 92 论元裁剪;
6. 去重键=(事件类目×主体×日期);importance 与 sentiment 正交,importance 含传播度分量(FinGPT dissemination-aware);
7. 事实/观点二分、跨源背离即信号、≥90/10 多空比标过热反指——进 text.sentiment skill;
8. **规则词表预过滤放 LLM 之前**(加减分制 0-100 阈值 30、标题权重>正文;便宜/可单测/PIT 安全),LLM 只精排;
9. **反着 TradingAgents-CN 写红线**:允许且要求 `insufficient_evidence`,禁止强迫表态("不许回答无法评估"条款是诱导捏造的反面教材);
10. SKILL.md 骨架:frontmatter 触发 / When-to-Use / 词表+阈值表 / 输出 schema / **Limitations 专章(反捏造红线宿主段)**。

`text.policy` 特有:锚定官方文本做**逐年/逐次措辞对比**(同一会议/报告的措辞增删是信号,中金"稳增长指数"路线),政策黑话词典("适度宽松↔稳健"、"逆周期↔跨周期")作 SKILL 附录并版本化。公告类**整篇喂不切 chunk**(论元跨句,Doc2EDAG 教训),payload=事件数组非单事件。信号演化三态(强化/弱化/证伪,alphaear)对接经验库延迟标注。

### 7.3 文本→事件库:定位与分级路线

**定位裁定**(答"是否与框架重复"):**不重复,但必须定位成数据资产(PIT 事件库),归 datafeed/DataReader 侧**——与记忆三层(agent 教训)、经验库(判断案例)、产业链 YAML(策展分类学)、pit_store(原始料)四者并立,是"第 5 样"。治理走数据管道,不走记忆 proposal;**不需要新 agent**——5 个 text worker 本身就是抽取器,payload 直接落库(相对一切开源方案的结构性成本优势:建库零额外 LLM)。

**调研核心判断**:聚合式图 RAG(GraphRAG/LightRAG 的实体合并描述、社区报告)**天然与 PIT 冲突**(跨期信息熔进同一节点、无 available_at,按 asof 切片不可能)——MS GraphRAG 全量 = **结构性前视泄漏,别碰**。唯一 PIT 原生范式 = edge 级 bi-temporal(Graphiti 式:valid_at/invalid_at + created_at,**created_at ≡ 我们的 available_at;"失效不删除"**——业绩预告修正/传闻辟谣/减持终止都是天然用例)。

分级路线:

- **第一步(2-4 周量级)**:SQLite 单库三件套——① `events` 表(worker payload 直落,列含 event_type/tickers/**available_at+event_time+invalidated_at**/source_ref;PIT=一行 `WHERE available_at<=:asof AND (invalidated_at IS NULL OR invalidated_at>:asof)`);② FTS5 全文索引(**中文必须挂 wangfenjin/simple 分词器**,默认 unicode61 不切中文);③ SimHash 转载去重 +(ticker×事件类型×72h)桶内事件线聚合。检索接口 **asof 必填**。
- **第二步(跑稳后)**:Graphiti bi-temporal 失效流;桶内残余重复率超阈再上本地小 embedding;研报 PDF 单独借 RAG-Anything 的 MinerU 解析链入同一 events 表。
- **别碰**:MS GraphRAG 全量 / nano-graphrag 托生产 / Graphiti 整框架直接接 108 源(写入 LLM 成本爆炸,只抄 schema)/ 自训抽取模型 / 任何"合并实体描述"当事实源的方案。

---

## 8. AMEND-8 · Lane D 决策/风控六席(prompt/skill 内容规范)

> 2026-07-16 三路 web 调研落定(TradingAgents 决策链考古 / ai-hedge-fund+辩论文献 / A股 fork 特化)。旧基线=引擎 tier3 四席(bull/bear/risk_officer/report_writer),成色好,升级不是重写。

### 8.0 旧基线八件传家宝(全部保留)

① V1–V9 / F1–F14 论点锚 taxonomy(每条论点 `[V#]`/`[F#]` 开头,空数组=无效);② 反和稀泥双向条款(看跌也须 2 条战术机会,看多也须 2 条风险);③ bear 晚一波逐条反驳(07-12 已实现);④ CRO 硬规则不可被观点推翻(游资票否决 mv<200亿∧pe>100∧60日涨幅>50%、恶性事件否决)+ risk_score 永不为正;⑤ 确定性块逐字照搬禁改数字(券商评级/股东主力);⑥ `covered=false` 与引用证据互斥;⑦ 时间线对账 + `timeline_lesson_ignored` 重复犯错侦查;⑧ blind_spots 盲区侦查。

### 8.1 结构性裁定(调研最重要的三条)

1. **硬约束层不是 LLM**(ai-hedge-fund risk_manager 零 prompt 纯代码:波动率/相关性分档算仓位上限)。落地:CRO 硬规则 + A股制度约束**上移为确定性 guardrail**,在 `dec.pm` 之前算出结构化 `allowed_actions`(每票:可否买卖/手数/最大目标仓位带),prompt 标注 "already validated",**LLM 只在合法集合里选,不做算术**。`dec.risk_debate` 三席辩的是 allowed set 内的激进度/时机/sizing 立场,不辩硬规则。
2. **决策风格是模型属性,prompt 矫正不了**(Alpha Arena 实盘:同 prompt 下 DeepSeek 分散低杠杆、GPT/Gemini 追涨杀跌 -60%)。换手/杠杆/仓位上限必须 runtime 硬约束;`dec.pm` 用 deep 档且选型须实测中性/激进倾向。
3. **persona 有害**(Wharton 2025:专家标签系统性降准确率)。bull/bear 不"演谁",只要**强制关注面不对称**(bull 只准找多头证据的纪律约束);国金"11 投资大师 agent"路线不抄。

### 8.2 辩论机械(prompt 层,契约层 v1.1 已冻结)

- **≤2 轮硬上限有据**:辩论收益几轮为顶、首轮几乎全部(2511.07784);TA 官方默认永远 1 轮、加轮开关是假的(#170)。
- **硬性对手指派,软性无效**:devil's advocate "you must oppose" 反驳率 99.2% vs "think critically" 48.3% 统计等于没说——bear prompt 必须硬性 "must rebut point-by-point",且"**对方未回应时不许虚构对方观点**"(冷启动条款,CN fork 原文)。
- **裁决防位置偏置**(judge 位置偏置 60–75%):`dec.research_mgr` prompt 显式声明"不以发言顺序/篇幅定胜负,只看证据强度",裁决**须引用辩手原话**(可审计);swap 双跑作 evaluator 抽查项(D12)。
- **每轮立场声明字段**:双方每轮显式输出"维持/更新立场 + 新证据"(justified belief——有理由的信念扭转需强证据,防随波翻转;文献:翻转多为 correct→incorrect)。
- **不对称弹药**:bull/bear 吃完全相同上游是塌缩的结构诱因(TA 源码可证)——bear 额外加权注入风险类 payload(公告风险载荷/解禁/质押)。
- **成本排版**(TA #750 前缀缓存 0 命中,单 run 30-40% 白烧):静态角色/skill 文本置前、动态数据置后;辩论历史**不重贴上游报告全文**(research_mgr 只喂辩论 history;trader 只吃 pm 决策)。
- **输出规格**(TA #828 官方修法照抄):每席限 250–400 词、结构 Thesis→Evidence→Counter;反遁词"指名具体因素并引来源";越权边界"不许给 BUY/SELL——那是下游的事"。
- 类型化 speaker 字段(TA 靠字符串前缀认发言人=脆);每 source node 专属 router(TA #1088/#1092 共享 router 病,我们静态 DAG 天然正字)。
- **聚合权重不来自 LLM 自报 confidence**(无校准,仅展示):席位权重进 TrialLedger,先均权,成熟后按席位历史 accuracy 加权。

> **交付物③ 已出**:`2026-07-17-dec-research-mgr-pm-skills.md`(两席逐字安装件:research_mgr 修 pilot fallback-Hold 反模式+辩论裁决段+强制升降级触发;pm A股六条+allowed_actions 前置+冲突裁决表+对称损失+PIT 教训纪律;全部 when-supplied 优雅缺席,pilot 链照常可用)。

### 8.3 逐席规范

| 席 | 保留 | 新增(出处见 8.1/8.2) |
|---|---|---|
| `dec.bull` | V1–V9 锚 + 反和稀泥 + 时间线对账 + disproof_signals | 250–400 词 + Thesis→Evidence→Counter 结构;"辩论而非罗列数据";越权边界;A-Share Bull Framework(政策顺风/北向确认/解禁出清) |
| `dec.bear` | F1–F14 锚 + 晚一波逐条反驳 | 硬性 must-oppose;"对方未回应不许虚构";风险载荷不对称加权;A-Share Bear Framework(窗口指导/解禁悬顶/质押爆仓——**术语不可英文化**) |
| `dec.research_mgr` | (从 report_writer 拆出裁决职责) | 5 档 `ResearchPlan`(P1 已冻结);"Reserve Hold for genuinely balanced"+"不以顺序/篇幅定胜负";**HOLD 也必须给升/降级触发条件**(把中性变成有信息量输出,防和稀泥最值一条);上游评级一致性检查(确定性抽取注入,背离须列分歧点+采纳理由);裁决引用辩手原话;只喂辩论 history |
| `dec.risk_debate` | CRO blind_spots 职责保留 | 三席=**同一制度的三种立场推演**(T+1:激进"防当日获利了结"/保守"锁死无退路=最大结构风险"/中性"仓位使隔夜缺口可承受"——中性≠不表态,是 sizing 主张);各见其余两席最新发言(定向反驳);硬规则不辩(已前置 guardrail) |
| `dec.pm` | 时间线对账升级 | deep 档;**A股约束六条唯一注入层**(T+1/分板涨跌停含 ST5%/手数/时段/ST退市→仓位/两融默认现金——astock 原文);结构化 allowed_actions 前置;**信号冲突裁决表**写死(低估值+资金流出=价值陷阱分批;业绩超预期+外资减持=利好出尽);**对称损失条款**(踏空与买错同为决策错误——正对我们落子"LLM 只会否决"观察);PIT past_context 配方=**同票近 5 全文+跨票近 3 只反思、pending 不注入、payload 须列引用 lesson_id**(TA 主线亲验的单点注入路线;反思强制 2–4 句);公告风险=确定性词表+排除词+**烈度分层**(立案>问询>关注函)作结构化载荷;产 `PortfolioDecision`(P1 已冻结) |
| `dec.trader` | — | 只吃 pm 决策不吃原始报告;3 档 action 与 5 档评级分层(粗细分工);**目标仓位带**(0/25/50/75/100%)+可选分批触发价区间,不出连续精确仓位(无锚漂移);**数字字段全 Optional 且禁提取层默认值**(CN 版"买入默认现价×1.15"=静默污染反面教材);nullish 消毒("N/A"→None,TA #1058);typed payload 外保留一行确定性哨兵便于日志 grep;产 `PortfolioTargetProposal` |

**反面清单(明确不抄)**:强制目标价("绝不允许说无法确定"=造假诱因,CN 版后来自己补锅 #734/#755);提取层默认值/智能推算;LLM 三连败静默产出"默认持有"假决策(诚实红线反面教材);全员各自向量记忆(TA 主线亲自收敛到单点注入);每轮重贴四报告全文。

---

## 9. 拍板记录(2026-07-17 用户全部裁定,无遗留)

| # | 决策 | 裁定 |
|---|---|---|
| D1 ✅ | 走势向量窗口 | 按默认:60 交易日 + 5/20 日摘要 + 250 日分位 |
| D2 ✅ | 模型档位 | 按默认:`market.regime`/`market.rotation` = reasoner(v1.1 仅钉 `dec.pm`=deep) |
| D3 ✅ | 概率语义 | 按默认:三轴各出概率分布 + 总置信 |
| D4 ✅ | 起草顺序 | 按修订版:① `market_factor_report` schema → ② `text.sentiment` SKILL → ③ `dec.research_mgr`+`dec.pm` → ④ regime/rotation(②③兼 Phase 2 pilot 关键路径) |
| D5 ✅ | Lane A 管家形态 | (2026-07-16)独立新 agent:#25 miner + #26 curator + #27 pv.curator,24 → 27 |
| D6 ✅ | 修订节流 N | 按默认:月频因子 N=3;日频形态 N=20 交易日 |
| D7 ✅ | 外部技术分析投递入口 | 按默认:console 指令 + 固定投递目录,必须标注来源作者;落子页面入口后置 |
| D8 — | tushare 付费 | 降级 stocks 层上游决策 |
| D9 ✅ | 情绪供给 | **改判(非降级)**:雪球 `ww_news_collect` 路**过时废弃**;`text.sentiment` 供给=观澜现有资产(`ww_macro_pulse` 全球 + `ww_sentiment` A股判读 store + `ww_market_tape` 打板温度);社媒不进第一批 |
| D10 ✅ | 事件库落地时机 | **就地追加,不等第二轮**:Phase 1/2/3/3b 已全落地(63 commits)→ 事件库作独立纯加法小 phase(或并 datafeed 已立项新闻归档 P3);先吃现有 feed(kuaixun/newsradar 确定性入库),text worker 到位后 payload 入同一张表。不回改已完成 phase |
| D11 ✅ | capability manifest 生成器 | **落"目录装配 phase"**(spec v1.1 §12 第 8 期·四车道目录/skills/Lane D)**第一个任务**:生成器+漂移守护先行,再装 27 worker。Phase 2 pilot catalog 不回改(版本化快照,新目录=新 digest,旧 Plan 照旧可回放) |
| D12 ✅ | research_mgr 位置偏置对策 | 按默认:声明条款+裁决引用原话;swap 双跑仅 evaluator 抽查 |
| D13 ✅ | V/F 锚词典治理 | 按默认:版本化词典资产,修订走 proposal+人审,不设新管家 |

**接线总原则(D10/D11 的一般化,用户问"等第二轮还是就地改"的答案)**:R2 全部增量都是**纯加法**,落到尚未执行的后续 phase(4–9)或独立小 phase;**无一需要回改 Phase 1/2/3/3b 已交付代码**——版本化注册表/目录快照本来就是为这种演化设计的(新目录新 digest、sealed 累积 registry、旧 Plan 永远可回放)。AMEND→phase 归属:AMEND-1/2/3(市场因子电池+扩容+#25)→ Phase 5(Bootstrap Lane 0+经验库);AMEND-4(Lane 0 prompt)→ Phase 5 装配、起草可前置;AMEND-5/6/6a(#26/#27+K线形态)→ 目录装配 phase + 独立形态词典小任务;AMEND-7(Lane C 接线+skill)→ 目录装配 phase,事件库独立小 phase;AMEND-8(Lane D)→ 目录装配 phase,prompt 起草可前置。

## 10. 追加区(后续讨论滚动写入)

(text lane / Lane D 细化,经验库标签阈值,skill 装配位置等——待讨论)
