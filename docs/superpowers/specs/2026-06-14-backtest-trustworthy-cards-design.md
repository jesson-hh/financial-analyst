# 回测可信卡体系 设计文档

> 日期 2026-06-14 · 模块:落子(回测/实盘 agent 决策上下文)· 状态:设计已与用户逐项对齐
> 本仓无 git —— "提交"= 跑 pytest;设计文档不 commit,落盘即存档。

## 目标(Goal)

让回测/实盘里 agent 配置的「卡片」从**占位料**变成**真能用、符合逻辑、零看未来**的决策上下文,并**删除所有没接入的假料**。

一句话定义:**卡 = 一个每决策步在 PIT 面板上真重算的东西,不是一句静态文字。**

## 背景:现状哪些是假的(审计)

回测 decide 当前喂给 agent 的"配方"里:
- **经验卡**:种子卡 `card_reversal/north/pead/distrib/diverge/smallcap` 全 `demo:true` 硬编;`distillToCard` 复盘回灌卡的 `ic = 0.02 + max(0,sharpe)*0.008`(`luozi-app.jsx:510`)是**伪 IC**(把夏普换算的好看数,非截面/时序 IC)。
- **研报**:demo 的「(示例)」研报(`rs_pead` 等)无 `path`,只喂标题;实证(立昂微 7 买 run,50 笔)**全程喂同一份 `多家公司 Q3 业绩超预期(示例)`,excerpt_n=0** —— 一份占位标题反复喂 50 天。
- **配方因子(recipe_factors)**:只是因子名字符串,喂 LLM 当提示,**不参与任何确定性计算**(旧红线"不冒充回测")。
- **DecisionCard 证据层(`evidenceFor()`,`luozi-data.jsx:387`)**:回测的「触发因子 / 研报 / 经验卡 / regime」仍是模板 mock(`luozi-panels.jsx:1247/1361` "复盘仍 mock / 示例值")。
- **regime**:回测 decide 里 `regime=null`(防今日快照看未来),所以**完全没有大盘层**。

两个根本逻辑硬伤:
1. **无 as-of 日期 → PIT 漏洞**:卡/研报不带"哪天才已知",喂给回测某天 = 可能用未来知识。
2. **卡不进信号、只是文字**:配方因子从不真算,无法度量地改变回测。

## 核心架构:三层 PIT 上下文(每步重算,壳期各异)

每个决策步(每根 bar:30min=每30分钟、10min=每10分钟)组装三层上下文,全部 as-of D(只用 ≤D 数据):

### 第 1 层 · 量化卡(常驻 · 进信号)
- **定义**:一个**因子表达式**(`factor.expr`,复用工作流因子库语法,含 `correlation`/`tsic` 等算子,支持单票与**共振/大盘相对**)。
- **per-step 求值器带市场面板**:能拉 `本票 + 指数 + 相关票` 面板,按 PIT 算当下因子值(回测=整段面板预加载一次、每步切 ≤D 窗口;实盘=每决策步拉实时面板)。这是当前 decide(只算单票)缺的新基建。
- **vintage IC 定向**:量化卡带逐日滚动 IC(只用 ≤D 数据算的真 OOS;单票即 `tsic` 的 vintage 版);IC 符号给因子值定向(IC<0 翻号)。
- **进信号(加权混合)**:`决策偏向 = (1-w)·LLM分 + w·因子z分`,`w∈[0,1]` 每策略可调(**w=0 = 现状纯 LLM**)。回测记**两条净值**(纯 LLM / 混合)做归因;开关某卡或调 w → 净值可度量变化 = 真进信号。
- **绑定**:手动绑到策略(agent 的持久信条)。
- **PIT 闸门**:`as_of ≤ D` 才生效;因子值与 IC 按当天算。

### 第 2 层 · 叙事卡(研报结构内容 · 共享日期池)= 每日研报
- **合并**:叙事卡与"每日研报"是**同一件事**,不再两套。研报/复盘/新闻进**共享带日期池**,每条带 `as_of=落款/发布日 + 关联票/行业 + 情绪`。
- **按日浮出**:回测第 D 天自动浮出 `as_of≤D + 关联本票/行业 + 近N天窗口 + topK`(新闻窗短如近5-10天、深度研报窗长如近60天,每天限 K 张防淹没 LLM)。游标前进 → 新卡冒出、老卡沉底 = "每日不同研报"。
- **只喂 LLM**,`signal:false`。
- **研报按壳期拆**:研报的**结构内容**(基本面/行业逻辑/估值)→ 叙事卡(长窗);研报的**大盘预测段**→ 不复用,归第 3 层。

### 第 3 层 · 每日大盘/节奏(平台逐日产物)
- **来源**:平台已有的逐日 PIT 产物 `breadth / 主线(mainline) / 节奏 / v4`(regen 历史产出,逐日)。
- **按当天 PIT 喂**:第 D 天喂当天的 breadth/主线/节奏(只用 ≤D 数据)→ **修掉之前 `regime=null`**(当时 null 是因为只有"今日快照"会看未来;历史逐日产物是 PIT 的,可按天喂)。
- **背景层**,不进信号,只给 LLM 大盘环境。

## 横切原则

- **严格滚动 PIT**:vintage IC + as_of 闸门 + 面板/日产物全部 as-of D,零看未来。
- **重算频率 = 决策频率 = 所选 TF 每根 bar**。
- **诚实降级**(沿用全仓口径):无 as_of/无真实出处的卡 → `status:draft`,**进不了信号、不自动浮出**;因子算失败 → 当天 factor 分=0(退纯 LLM)+记录;某天无相关叙事卡 → **空着,绝不补假**;vintage IC 样本不足 → 标「样本不足」(沿用 model_health 口径);伪 IC 复盘卡 → 降 draft。
- **不冒充**:加权混合显式、w 可见、纯 LLM/纯因子双基准都画;绝不把因子悄悄塞进去假装。

## 假料清除(用户硬指令:把所有没接入 假的东西删掉)

新三层是真实现,**替换**而非并存于旧 mock:
1. **删 `distillToCard` 伪 IC**(`luozi-app.jsx:510`):复盘回灌卡不再编 `ic=0.02+sharpe*0.008`;改为 `status:draft`、无 IC(要进信号须走 validation 真验证)。
2. **删 `evidenceFor()` 回测 mock 证据层**(`luozi-data.jsx`)及 DecisionCard 回测 regime/触发因子 mock(`luozi-panels.jsx:1247/1361`):由真三层(大盘日产物 + 真 per-step 因子 + 叙事卡)取代;无真数据则诚实空,不回退 mock。
3. **demo 种子料退出真路径 + UI 诚实空态(已定)**:`card_*/rs_*` demo 物料**永不进信号、永不自动浮出、永不进 decide**;默认策略剥掉 demo 研报 ref(`rs_pead` 示例);**校场料库/图谱不再以 demo 填充,改诚实空态**(如「尚无真卡 · 去验证区生成」),不再保留"示例"卡作演示填充(不强删 GL seed 本体,但真路径与 UI 列表都不取 demo)。fresh 默认 agent 无假研报(诚实空到有真卡)。
4. **recipe_factors 文字提示**:量化卡上线后,真因子(带 expr+vintage IC)取代"文字因子名";纯文字 recipe_factors 退役或仅作 draft 提示。

## 数据现实 caveat(诚实)

叙事卡池"每日密度"取决于源:研报 `out/`(38 篇带落款日)、akshare 个股新闻**停在 03-30**、复盘提炼。**03-30 之后的回测日叙事卡主要靠研报+复盘;没有相关卡的那天诚实空**。这不影响架构,只是历史回填数据另说(回填不在本设计范围)。

## 分期(三个子项目,逐个 spec→plan→实现)

- **P1 = 每日 PIT 上下文(叙事流 + 大盘日产物)** —— 本设计首期,自包含、直接交付"每日不同研报+大盘",不依赖 vintage IC/混合。
- **P2 = 量化卡 vintage IC** —— 逐因子滚动真 OOS IC(单票 tsic vintage + 截面/共振 vintage)。
- **P3 = 加权混合进信号** —— per-step 面板因子求值器 + 加权混合 + 双线归因。

### P1 详细(首期实现范围)

**数据模型**(GL `type:'card'` 扩展 + 叙事池):
- 叙事卡:`{id, type:'card', tier:'narrative', title, insight, as_of:'YYYY-MM-DD', codes:[关联票], industry, kind:'研报|复盘|新闻', source:{from, path?}, sentiment?, signal:false}`。
- 无 `as_of` 或无 `source` → `status:'draft'`,不入池、不浮出。

**后端(`guanlan_v2/seats/api.py` + 新 `seats/narrative.py` 薄模块)**:
- 叙事卡池读取:从 GL 镜像(`/archive`,type=card & tier=narrative)+ `out/` 研报(带落款日)装配统一池。
- `surface_narratives(code, as_of, k, windows)`:纯函数,按 `as_of≤D + (code in codes or industry match) + 近N天(按 kind 分窗) + topK` 选卡。**PIT 红线:绝不取 as_of>D**。
- decide 接线:`freq=day|30min` 决策时,调 `surface_narratives` 取当天叙事卡,拼进 prompt(替换当前固定 `research` 透传);落盘记 `narratives_surfaced:[ids]`(可审计每天喂了哪些)。
- 大盘日产物接线:`regime_asof(date)` 读 breadth/主线/节奏的逐日 PIT 值(≤date),拼进 prompt;替换 `regime=null`。失败→诚实空。

**前端(`luozi-*.jsx`)**:
- `runRealThink` 不再把固定 `rcp.research` 逐日重复喂;research 改由后端 `surface_narratives` 按 `bar.date` 当天取(前端只传 code+date,后端选卡)。
- 复盘 RunDecCard / 决策流水:显示**当天真实浮出的叙事卡**(落盘 `narratives_surfaced`)+ 当天大盘日产物,替换 mock 证据层。
- `distillToCard` 改 draft 无 IC;demo 研报 ref 从默认策略剥离。

**测试(TDD)**:
- `surface_narratives` PIT:as_of>D 的卡绝不出现;近N天窗口/topK/关联票过滤正确;空窗口 → 空(不补假)。
- `regime_asof` PIT:只用 ≤date 数据;无产物 → None(不回退今日快照)。
- decide freq=day/30min:落盘 `narratives_surfaced` 随 date 变化(逐日不同);demo/draft 卡不出现。
- 回归:现有 220+ 测试全绿。

### P2 详细(量化卡 vintage IC,2026-06-14 与用户拍板:全量 442+56 / 截面+单票都做 / 挂 regen 批算)

**问题**:因子卡现挂的 `ic` 是 `compute_catalog_ic` 算的**静态 60 日截面 rank-IC,算到 regen 最新日**(`factor_ic.parquet asof=end`)。回测某天 D 决策时 decide 给 agent 看的 `IC=...`(`seats/api.py` rf_line)用了 D 之后的数据 = **IC 数值本身看未来**。P2 把它换成**逐日 vintage IC(as-of D、真 OOS)**。

**口径(两种,用户要都做)**:
- **截面 vintage IC**(csi300):每因子一条**逐日截面 rank-IC 序列** `cs_ic[id][t] = dir·Spearman(factor_t 截面, fwd_h_t 截面)`(就是 `ic_analysis`/`compute_catalog_ic` 内层那条,只是不 mean、全留)。
- **单票 tsic-vintage**(watch-pool 票 × factorlib56):每 (code, factor) 存逐日 `(fval_t, fwd_h_t)`;`tsic_asof = Spearman` over trailing 窗。

**真 OOS 闸门(PIT 红线)**:逐日 IC 行带 `realized_date = cal[pos(t)+horizon]`(该日 fwd 实现/可知日)。`vintage_asof(D, window, horizon, min_n)` 只取 `realized_date ≤ D` 的行(D 当天已知的真 OOS),再取 date 最近 `window` 条求均值(cs)/ 求 Spearman(tsic);有效条数 `< min_n` → `None`(诚实「样本不足」,沿用 model_health ≥10 口径)。**绝不取 realized_date>D**。

**离线批产物(挂 regen step 3.6,失败不阻断)**:新 `guanlan_v2/screen/factor_vintage.py`,**一次面板加载 + 一遍因子编译**同时产两表(复用 `compute_catalog_ic` 的 `load_panel_cached`+`_inject_market_refs`+`compile_factor`+fwd 算法):
- `var/factor_vintage_cs_ic.parquet`:列 `[id, date, ic, n, realized_date]`(全 catalog 442+56;逐日截面 ≥30 票才算)。
- `var/factor_vintage_tsic.parquet`:列 `[code, id, date, fval, fwd, realized_date]`(`SEATS_POOL_CODES` 固定盘 7-8 票 × factorlib56;动态票未入常量则无 tsic=诚实降级)。
- 历史窗:默认 ~2 年(覆盖现实回测窗;`start` 可配,越长越慢)。写盘原子(.tmp→os.replace),与三产物同范式。

**读取端(同模块,mtime 缓存)**:`cs_vintage_asof(id, date, window=60, horizon=5, min_n=10)→{ic,n,dir,asof}|None`;`tsic_vintage_asof(code, id, date, …)→{ic,n,asof}|None`;`load_*` 缺文件→{}(诚实降级)。

**decide 接线(`seats/api.py`)**:rf_line 构建处,每个 recipe 因子先 resolve 成 catalog `id`(① 前端显式 `id` ② `expr` 匹配 ③ `name`/`short` 匹配,反查索引从 `FACTOR_DEFS` 建一次缓存);再 `优先 tsic_vintage_asof(code,id,asof) 退 cs_vintage_asof(id,asof)`;命中 → `名(IC@D=X·OOS·n=N·{本票|截面})`,未命中/样本不足 → `名(IC 样本不足)`(**不再喂静态看未来 IC**)。落盘补 `recipe_factors_vintage:[{name,id,ic,n,kind,asof}]`。

**前端**:① 料库(factorlib 列表)因子卡显**最新 vintage IC**(`cs_vintage_asof(id, 最新日)`,无看未来,标「OOS·csi300」)替静态 `meta.ic`;② `RunDecCard` 显当天 `recipe_factors_vintage`(as-of D 真 OOS,本票/截面标);③ `recipeForStrategy` 透传因子 `id`(供后端 resolve);④ 清残留伪 IC(P1 已删 distillToCard,核查无他处编造)。bump ?v。

**P2 测试(TDD)**:cs/tsic vintage as-of 只用 `realized_date≤D`(造 realized_date>D 行验证绝不入选)、trailing 窗、`min_n` 不足→None、dir 定向;decide rf_line 随 date 变(逐日不同 IC)+ tsic 优先 cs + 样本不足诚实;regen 产两表(tiny 合成或 smoke);回归 262+ 全绿。

**P2 非目标**:加权混合进信号(P3);per-step 实时面板求值器(P3,本期 vintage 是离线批的逐日序列,decide 只做 as-of 查表不实时算因子值);tsic 扩到全 catalog/全市场(本期限 pool×56)。

### P3 详细(加权混合进信号,2026-06-14 与用户拍板:等权裁剪因子分 / sgn(bias)+死区 / w=0 严格纯LLM)

**目标**:把因子 z 分按权重 w 真正**混进决策**(非仅喂 prompt),`决策偏向 = (1-w)·LLM分 + w·因子z分`;回测同记「纯 LLM 净值」与「混合净值」做归因。这是唯一改变交易信号的一期。

**复用 P2 不建新求值器**:因子值 `fval` 已在 P2 的 `var/factor_vintage_tsic.parquet`[code,id,date,fval,fwd,realized_date] 逐日存好(pool 票 × factorlib 因子,PIT 安全:fval 在其 date 当日已知)。P3 因子 z 分直接读它,**无需 per-step 面板求值器**(实盘逐步求值 + 全市场留 P3 后)。

**因子 z 分(PIT)** —— 新 `factor_vintage.py:factor_z_asof(code, factor_id, asof, window=60, min_n=10)`:取本票本因子 `date≤asof` 的 fval(fval 无需 realized_date 闸门,当日即知),当前值=最后一条、trailing 窗求 `z=(当前fval−mean)/std`;`<min_n` 或 std=0 → None。**方向**用 vintage IC 符号(`cs/tsic_vintage_asof` 的 `dir`)给 z 翻号。

**因子分合成(用户选:等权裁剪)** —— 每因子 `clip(dir·z, -1, 1)`,多因子**等权平均**成单一 `factor_score∈[-1,1]`;只纳入**同时有 z 且有 vintage IC 方向**的因子(无 IC 无法定向→剔除,诚实);全无→`factor_score=None`(无因子信号)。

**LLM 分** —— `llm_score = sgn(direction)·confidence/100 ∈[-1,1]`(买+/卖−/观望0)。

**混合 + 决策(用户选:sgn(bias)+死区,忠于公式)**:
- `hybrid_bias = (1-w)·llm_score + w·factor_score`(`factor_score=None` → bias=llm_score)。
- `hybrid_direction`:**w==0 或 factor_score=None → 直接透传 LLM `direction`(不经死区)**=保证 w=0 严格等于纯 LLM(否则死区会把低置信 LLM 买改成观望,破坏「w=0=现状」);w>0 且有因子信号 → `bias>τ 买 / bias<−τ 卖 / 否则观望`,τ=0.15 死区。
- w 越大因子越能改写甚至翻转 LLM 方向(忠于公式)。

**w 存储 + 传递**:GL `type:'strategy'` 加 `w`(0..1,**默认 0=纯LLM 安全**);`luozi-foundry.jsx` 新建/编辑表单加滑块「因子权重 w」;`runRealThink`/实盘 decide payload 透传 `w`;decide 读 `payload.get("w")`。

**decide 接线**:在 P2 `_rf_vintage_line` 基础上,decide 算 `llm_score`/`factor_score`(每因子 `factor_z_asof`+vintage dir)/`hybrid_bias`/`hybrid_direction`;响应 + 落盘加 `w`/`llm_score`/`factor_score`/`hybrid_bias`/`hybrid_direction`,并把每因子 `z`/`score` 补进 `recipe_factors_vintage` 记录。

**双线净值(回测归因)**:decide 返回 `hybrid_direction`;`runDecs` 同时带 `direction`(LLM)与 `hybrid_direction`;`runBacktest(runDecs, bars, useHybrid)` 跑两遍 → `eq_llm`/`eq_hybrid` + 各自 `metricsOf`;复盘净值图画两条线 + 归因(hybrid total − llm total);`RunDecCard` 显 `hybrid_direction`/`factor_score`/`w`(w>0 时)。w=0 两线重合=诚实退化。

**范围/诚实降级**:本期**回测**、限 `SEATS_POOL_CODES × factorlib`(tsic 覆盖面);非 pool/非库因子/样本不足 → factor_score=None → 退纯 LLM(hybrid==llm),前端标「无因子信号·纯LLM」。

**P3 测试(TDD)**:`factor_z_asof` PIT(date≤asof·trailing·min_n/std=0→None);因子分(clip/等权/dir定向/无IC剔除/全无→None);`llm_score` 映射;`hybrid_direction`(w=0 严格==LLM 透传不经死区 / w>0 死区 / 因子翻转);decide(w 透传 + hybrid 落盘);回归全绿。

**P3 非目标**:实盘逐步实时面板求值器(本期回测用离线 tsic);全市场/全 catalog tsic(限 pool×factorlib);因子分二层权重(等权);w 自动寻优(手动设)。

## 非目标(YAGNI / 本期不做)

- P2/P3(vintage IC、加权混合进信号)—— 各自 spec。
- 历史新闻回填(akshare 停更后的源)—— 数据工程另案。
- 实盘三层(本设计先回测;实盘对称在 P3 后统一)。
- 叙事卡自动从研报正文**拆条目**(结构 vs 大盘)的 NLP —— 首期人工/规则粗拆,精拆另案。

## 关键文件(P1)

- 改:`guanlan_v2/seats/api.py`(decide 接线)、新 `guanlan_v2/seats/narrative.py`(池+浮出纯函数,TDD)
- 改:`ui/seats/luozi-app.jsx`(runRealThink research 改后端按日取 / distillToCard 降级 / 默认策略剥 demo)、`ui/seats/luozi-data.jsx`(删 evidenceFor 回测 mock)、`ui/seats/luozi-panels.jsx`(RunDecCard 显真叙事+大盘,删 regime/因子 mock)
- 测:`tests/test_seats_narrative.py`(新)
