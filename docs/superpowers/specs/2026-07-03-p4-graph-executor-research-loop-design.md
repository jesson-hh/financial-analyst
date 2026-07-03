# P4 · 研究回路全图执行升级 — 设计文档

日期:2026-07-03 · 状态:已获用户批准(四决策+方案A+八节设计)
上游:P0 `6aac973` / P1 `2b805e8` / P2 `b9c969e` / P3 `c9989d6` / 停滞守卫修复 `b6e7841` 全部合 main。

## 0. 目标与决策记录

**目标**:研究回路的求值从「小灶三道菜」(只认 formula/factorlib 表达式)升级为**后端完整图执行**,回路可调动 guanlan 几乎所有资源:ML 训练(6 种)、自选因子组合、组合构建、回测、样本外收益;达标产物走三通道入库(全 draft 人审),出生后进前向真实收益跟踪。

**用户拍板的四决策**(2026-07-03):
1. **节点覆盖:24 类全支持**。过门指标只从主终端(回测/分析/IC)抽;7 个诊断终端(tsic/event/relstat/risk/garch/attrib/tvbeta)照跑存档不参与过门。
2. **过门口径升级:加 Sharpe>0 联合门**。门 = `rank_ic ≥ min_rank_ic 且 oos_verdict=="robust" 且 sharpe > 0`(堵住 2026-07-03 真机 Sharpe=-0.98 仍入 draft 的教训)。
3. **产物入库:三通道**。单因子→factorlib draft(现状);多因子合成→factorlib 组合 draft(权重物化线性表达式);ML 图→model_registry draft 变体(复用工坊 + P1 promote 门)。
4. **「看未来股票收益」= 两者**。回路内=回测/报告的样本外指标(即时反馈);draft 出生后纳入 vintage 前向跟踪(真实收益回流,喂后续经验迭代)。

**架构方案 A(获批)**:Python 镜像执行器,直调模块级计算函数(P2 三道菜的推广),不走 HTTP 自调、不碰引擎。B 案(逐节点 HTTP 自调)弃:慢、占请求池;其「参数零漂移」优点由镜像守护测试弥补。

**红线(贯穿,与 P0-P3 同)**:达标产物一律 draft、采纳永远人审;诚实失败逐节点显形;合并零行为变化(无新开关、无定时器);绝无自改代码/提示词;UI 只填充不重建。

## 1. 执行器模块 `guanlan_v2/workflow/executor.py`(新)

单一职责:把一张 graph JSON 在服务端跑完,返回逐节点结果与主终端指标。**纯同步**,设计为在 daemon 线程(研究回路)或 FastAPI 线程池(`POST /workflow/run`)中运行;绝不在事件循环协程内调用(仓级红线)。

### 1.1 对外接口

```python
def run_graph(graph: dict, overrides: dict | None = None) -> dict
# graph     : {nodes:[{id,type,x,y,params}], edges:[{from:[nid,port], to:[nid,port]}]}
# overrides : {universe, freq, start, end, oos_frac} —— 研究回路传入,权威压过图内 source 参数
#             (保证轮次间可比);canvas-parity 调用传 None → 完全按图内 source 解析。
# 返回:{ok, terminal: {kind, node_id, payload}|None, metrics: {...}|None,
#        node_results: {nid: {ok, dt, summary}}, node_errors: [{nid, type, error}],
#        warnings: [str], elapsed_sec}
```

诚实合约:节点失败**不中断**(镜像前端 runGraph:记入 node_errors 继续跑,下游因缺输入自然失败);跑完无任一主终端产出 → `ok:false, reason` 显形。`metrics` 只从主终端抽;抽不出(如只有诊断终端)→ `metrics:null` + warning,绝不编数。

### 1.2 拓扑与股票池(镜像前端,Python 复刻)

- `topo_order(nodes, edges)`:Kahn 算法,入度 0 按 x 坐标排序入队,同层按 x 决序;有环兜底把漏掉节点按 x 追加(与 workflow.jsx:715-738 逐语义一致)。
- `universe_for_node(nid, nodes, edges)`:沿入边 BFS 反向回溯最近上游 source 节点 → 取其 `universe/start/end/codes/benchmark/leader/oos_frac/wf_refit`(与 workflow.jsx:747-765 一致);未接 source → 回退 overrides 或默认 `csi_fast`,记 `fell_back` warning。
- overrides 语义:overrides 里给出的键**逐键压过**回溯结果(研究回路场景 universe/freq/oos_frac 恒由 run 参数锁定;start/end 给了才压)。

### 1.3 节点分发表(24 类逐一,全部直调模块级函数)

产物沿边端口对端口传递(`outputs[nid][port] → inputs[to_port]`),载荷形状镜像前端 NODE_EXEC:

| 节点 | 行为 | 直调目标(全部 `guanlan_v2/workflow/api.py` 模块级) |
|---|---|---|
| source | 纯透传:输出 universe/窗口/参照元数据 | 无(不算数) |
| formula | 纯透传:`{expr}` | 无 |
| factorlib | 纯透传:params.expr(浏览器选中已写入)或按 name 查 factorlib store 精确/模糊匹配;查无 → 节点诚实失败 | `factorlib.store.LibraryFactorStore.list()`(本地,不经 HTTP) |
| feature | **纯 Python 构 fe spec**(不调闭包端点 /feature/build,跳过展示性统计物化——ML/PCA 后端本来就按 spec 重物化权威 X/y):收集 feat 口表达式 + label 口表达式或 params.tag(IC/fwd_ret/空→前向收益 label=None)→ `{features:[...], label, universe, start, end, oos_frac, wf_refit}` | 无 |
| xgb/lgbm/svm/rf/nn/lstm | fe spec + hpMap 翻译超参(逐字段镜像前端 `_trainModel` 表:xgb `{n_estimators:trees, max_depth:depth, learning_rate:lr, subsample:sub}`,lgbm `{num_leaves:leaves, learning_rate:lr}`,svm `{C:c}`,rf `{n_estimators:trees}`,nn `{hidden,layers,lr,epochs,alpha}`,lstm `{seq_len,hidden,layers,lr,epochs}`)→ ModelTrainIn → 训练出 OOS 报告,model 口输出报告+`_kind` | `_train_eval(body, kind)`;lstm → `_lstm_eval(body)` |
| pca / spearman | fe spec (+k) → 报告因子(顶层 ic/portfolio + `composite:true`) | `_pca_factor` / `_spearman_factor` |
| mf | 双路镜像前端:①上游 m1/m2 带模型报告 → 透传首个为 factor 口(composite);②否则收集 ≥2 表达式 → compose(method=params.combine,默认 equal)→ composite 报告 + weights | `_factor_compose(body)` |
| analysis | 上游 composite → 透传为终端报告;单表达式 → report2(**params.rebal/groups/dir/neutral 真生效**——旧盲区在此消失) | `_factor_report2(body)` |
| iccalc | 同 analysis 双路;period→fwd_days+freq 映射(≥20 month/≥5 week/else day) | `_factor_report2(body)` |
| backtest | 优先 pf 口(组合)否则 factor 口;fe spec 或 [expr] + cash/topn/weighting/vol_forecast → 回测报告 | `_backtest_vector(body)` |
| portfolio | factor 口 → 最新期目标持仓(终端) | `_portfolio_build(body)` |
| tsic/event/relstat/risk/garch/attrib/tvbeta | 表达式 + 各自 params → 诊断终端载荷(照跑存档,不参与过门) | `_factor_tsic/_factor_event/_factor_relstat/_factor_risk/_garch/_attrib/_tvbeta` |

约定:各直调函数返回 JSONResponse → 统一经 `_resp_json`(镜像 research/loop.py:51)解包;`ok:false` 或 `status∉{None,'ok'}` → 该节点失败显形。`model`/`validate` 两类画布节点不在 `_CATALOG` 24 类内(LLM 提案不出),执行器本期不支持,遇到 → 节点诚实失败「不支持的节点类型」。

### 1.4 主终端选择与指标抽取

- 主终端优先级:**backtest.result > analysis.report > iccalc.ic**;同类多个取拓扑序最后一个(镜像前端 lastResult 语义)。
- `metrics_of_terminal(payload)`:泛化现 `_metrics_of`(rank_ic 三层回退 headline_ic→ic.rank_ic_mean→metrics;composite dict 先展开;portfolio.sharpe/ann_return;oos.verdict;n_dates)——ML/PCA/compose 报告均为「report2 兼容顶层形」(前端注释坐实),同一抽取器通吃。

## 2. 过门升级(`research/loop.py:_gate`)

```python
passed = (rank_ic 数值有效 且 rank_ic ≥ min_rank_ic
          且 oos_verdict == "robust"
          且 isinstance(sharpe, (int,float)) 且 sharpe == sharpe 且 sharpe > 0)
```

gate dict 增记 `sharpe_required: true` 供档案/前端显形。旧档案行无此键 → 前端按现状渲染(向后兼容,零 UI 改动)。

## 3. 回路升级(`research/loop.py`)

- 求值段整体替换:`_pick_dish + 三道菜` → `executor.run_graph(graph, overrides={universe, freq, start, end, oos_frac: 0.3})`。轮次行新增 `terminal_kind`、`node_errors`(截断至前 5 条);`exprs`/`dish` 保留(由执行器返回的图内表达式集合派生,前端零改动)。
- **停滞守卫升级**:比较键从「(dish, exprs)」改为**规范化图签名** `_graph_signature(graph)` = 对 nodes 按 (type, sorted(params.items())) 排序序列化 + edges 排序序列化的 sha1。理由:全图执行后任何参数变化都可能改指标,守卫只拦「整图零变化」;仍是 重批一次→仍不变→诚实中断「批判环停滞」。
- **`_CRITIQUE_CONSTRAINTS` 改写为新现实**:「本图由后端全图执行:所有节点参数均真实生效(ML 超参/回测 topn/分析 rebal·groups·dir 等);股票池与调仓频率由回路参数固定,图内 source 的 universe 不生效;改进可落在:因子表达式、特征组合、模型类型与超参、合成方式、回测参数。」
- `/research/*` 四端点契约不变;ResearchLoopCard/待审区/工作流深链零改动。
- 运行时长:ML 训练轮分钟级,整 run 可达 10-20 分钟——progress 逐阶段显形(新增 label:「② … 图执行中:<当前节点类型>」),无超时中断(max_rounds≤5 已钳)。

## 4. 产物三通道(达标后,全 draft 人审)

按达标轮的图形状路由(单轮只入一件产物):

1. **单 formula 表达式 + 无 ML 节点** → 现状:`_save_draft` → factorlib draft(不变)。
2. **≥2 formula 且无 ML(compose 路)** → **权重物化线性表达式**:compose 报告返回 `weights` → 构 `expr = "w1*(e1) + w2*(e2) + ..."`(权重四舍五入 4 位;equal 时权重=1/n)→ factorlib draft,`family="library_mined"`,meta 记 `{members, method, weights, run_id, round}`。可复算、可上架、选股直接可用。
3. **含 ML 节点(模型路)** → 镜像前端 `deriveRecipeForNode`(workflow.jsx:779-807)提 recipe:features(上游 feature 节点 feat 口表达式保序去重)/label/universe(回路 run 参数权威)/超参(hpMap 反向映射)→ 复用工坊训练管线产 ranking → `model_registry.save_variant(vid, ranking_df, meta)`,**meta.status="draft"**、`meta.source="research_loop"`、meta 记 run_id/round/metrics。人审=工坊现有 draft 转正机制(P1 `GUANLAN_PROMOTE_MIN_OOS_IC` 语义不变);draft 不进 `/screen/models` 默认列表、set_default 拒(P1 已有)。
   - 训练管线复用面在 plan 阶段按 `ww_model_train` 现行实现逐字对齐(同一批函数,绝不复制训练代码)。
   - 失败诚实:训练/入库失败 → `promoted.status="save_failed"` + reason(P2 五分支教训文案已覆盖)。

`promoted.status` 枚举扩:`draft | draft_compose | draft_model | save_failed | null`(`skipped_multi` 退役,compose 路取代)。前端 ResearchLoopCard promoBadge 沿用三态样式,新增两态文案小填充(与 §5 徽章同任务)。

## 5. 前向真实收益跟踪

- **draft 因子(单因子+组合)**:扩 `screen/factor_vintage.compute_factor_vintage` 的扫描面——现只扫 `FACTOR_DEFS`(选股目录,draft 被过滤);增补「并入 factorlib store 中 status=draft 的因子(id, expr)」。度量不上架:draft 仍不进选股目录,不碰红线。刷新节拍=vintage 再生现状(手动 regen / `GUANLAN_REGEN_DAILY` opt-in,默认关)——**不新增定时器**。
- **draft 模型**:save_variant 后自然进工坊变体列表(带 draft 徽章,P1 已有),model_health/CPCV 现有机制可用,无需新代码。
- **UI 小填充**:选股页待审区 DraftFactorSection 行尾加 vintage IC 徽章(`cs_vintage_asof(draft_id, today)` 有值才显示,无值不渲染——诚实空态);数据经现有 `/factorlib/list` 响应扩一个可选 `vintage` 字段(后端组装,前端零新请求)。

### 5b. 研究回路卡迁移选股页 + 顶栏恢复选股(2026-07-03 用户增补拍板)

用户裁定:研究回路在**选股**上发挥作用、不在买卖点上——定位修正,非重建(UI 红线不冲突):
1. **落子右栏 ResearchLoopCard 彻底移除**(组件+挂载+luozi-data 两个数据函数一并清,落子回归买卖点/盘面)。
2. **卡迁入选股页左栏因子库区**,紧挨「待审 draft」区上方:研究回路逐轮流水 → 产物待审 → 转正上架,一条动线从上到下。组件与数据函数照抄进 screen-app.jsx(跨页无 import 机制,照抄是仓例),promoBadge 两态(draft_compose/draft_model)直接改在迁入后的代码里。
3. **顶栏加「选股」**:`_shared/guanlan-nav.js` MODULES 顺序=帷幄/席位·落子/选股/AI投研;改 _shared js 必全站 bump `?v=`(8 个 html,已知坑)。

## 6. 接口面

- **新增 `POST /workflow/run`**:`{graph, universe?, freq?, start?, end?, oos_frac?}` → 线程池跑 `executor.run_graph` → 返回执行器结果(§1.1 形)。用途:执行器 e2e 测试门面 + P5(产业链再打分)复用。诚实失败 HTTP 200 `{ok:false,reason}`。**无新 ww_ 工具**(回路内部直调;帷幄经 ww_research_loop 已够)——四处同步计数不动(44/69/48)。
- `/research/*`、factorlib 后端契约不变;前端按 §5b 迁移(落子右栏移除研究卡、选股页迁入、顶栏加选股)。

## 7. 测试计划

1. **镜像守护测试**(新 `tests/test_workflow_executor.py`):分发表 24 类逐类——hpMap 逐字段与前端对照;topo_order 决序与环兜底;universe 回溯多跳+未接回退;节点失败不中断+node_errors 显形;主终端优先级;metrics 抽取三层回退+composite 展开。全部假计算函数(monkeypatch 直调目标),零引擎数据。
2. **回路集成**(扩 `tests/test_research_loop.py`):executor 打桩——ML 图达标走模型通道 / compose 图走权重物化通道 / Sharpe≤0 被新门拦 / 图签名停滞守卫(参数变化不算停滞;整图不变算)。
3. **vintage 扩面**(扩 vintage 测试):draft 因子进扫描面、不进 FACTOR_DEFS。
4. **端点**:`POST /workflow/run` 空图/坏图/正常图三态。
5. **真机 e2e@9998**(隔离 FA_CONFIG_DIR + deepseek-v4-pro,亲手执行不转包):目标含「用机器学习模型」字样 → 提案出 ML 图 → 真训练真过门/真不过门 → draft 变体入工坊(人审待命)→ 还原现场;全量回归 ≥840 基线。生产 9999 全程不碰,收尾重启吃新代码。

## 8. 展望(本期不做,记档)

- **P5**:选股池再打分——产业链逻辑(industry 聚合)+ 新闻情绪(news_pulse)与 v4/因子分并轨打分。
- **P6**:经验自迭代扩面——keyed 教训从「因子/模型」扩到「产业链/情绪/市场风格」维度。
- 定时自主开跑(goal 池 job runner)维持未立项;`model`/`validate` 画布节点纳入 `_CATALOG` 与执行器,待 P4 稳定后议。
