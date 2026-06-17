# factor — 因子 · 工作流(A2)

本模块单页:**AI 工作流**(可视化节点编排,拖拽连线即出真数据)。

> 原「量化工作台」(观澜 · 量化工作台.html / quant.jsx)及其接线设计稿 `factor_wiring_design.*` 已于 2026-06-04 删除;因子评测/合成等能力并入下方 AI 工作流(连对节点即得)。盯盘(原 WatchMode)如需保留,另起独立「盯盘台」立项。

## 页面:AI 工作流
| 项 | 值 |
|----|----|
| 页面 | 观澜 · AI 工作流.html(`workflow.jsx?v=27`) |
| 入口组件 | `WorkflowApp`(workflow.jsx) |
| 全局导航条 | 有(是 factor 模块的 nav 入口) |
| 后端 | 借引擎 `/factor/*` + 仓内 `/factorlib/*`(P1)+ 仓内 `/feature/build`(P2)+ 仓内 `/model/train`(P3 `kind=xgboost\|lightgbm\|svm\|rf`,P6 `kind=mlp`,P7 `kind=lstm`→`/model/lstm`)+ 仓内 `/factor/{pca,spearman}`(P4)+ 仓内 `/backtest/vector`(P5)(`guanlan_v2/workflow/`) |

职责:可视化工作流编排——`CATALOG` 算子节点,拖拽/连线/撤销/导入导出。

**进展(2026-06-04)**:
- **P0 · 执行器(已接)**:`run()` 升级为通用 DAG 执行器(`topoOrder` + `runGraph`)+ 节点三件套机制(`SPECS`/`CATALOG`/`NODE_EXEC`,配方见 [../../docs/node_recipe.md](../../docs/node_recipe.md))。公式输入 / 因子分析 → `/factor/report`、多因子 → `/factor/compose` **已出真数据**;占位节点(`__pending`)永不当结果,杜绝谎报。加节点只扩三件套,既有组件零改;改 workflow.jsx → bump HTML `?v=`。
- **P1 · 输入层(落地中)**:数据源节点(选 universe → `_universeOf` 映射,串喂下游)+ 因子库节点(查 `/factor/list`,含仓内 `guanlan_v2/factorlib/` 注册的迁移/自挖因子,详见 [../../docs/factor_library.md](../../docs/factor_library.md))。基础因子从 G:/stocks **译写迁移**(Qlib-DSL → 引擎 zoo-DSL)。
- **P2 · 特征工程(接真)**:特征工程节点接仓内 `POST /feature/build`(新薄壳 `guanlan_v2/workflow/api.py` 之 `build_workflow_router()`,仿 factorlib/seats/cards;函数体内延迟 import 引擎 primitive 在 universe 面板物化真 X/y → 返回 `n_dates/n_codes/coverage/IC/预览` + 可复算 `fe_spec`,供 P3 ML 重建训练集;数据走 `get_data_paths`,诚实失败 `ok:False`)。前端只把 `feature` 节点的 `__pending` 占位换真调用 + 把 `params.tag`(`IC`/`fwd_ret`)映射端点 `label`;`fe` 端口非终端不进抽屉(`TERMINAL_DT` 不动),既有节点/组件/executor/抽屉**零改**。详见 [../../docs/module_map.md](../../docs/module_map.md) 之「workflow 计算端点」。
- **P3 · 基础 ML(接真)**:XGBoost·LightGBM·SVM·RF 4 节点接仓内 `POST /model/train`(续入**同一** `guanlan_v2/workflow/api.py`,追加式,不改 `engine/`)。单端点 + `kind`(`xgboost`/`lightgbm`/`svm`/`rf`)分发:消费 P2 `fe_spec` 重建训练集 → 时序 OOS 切分 → fit/predict → **预测分=截面因子 Series → 同款 `build_report` 出 OOS 报告**(ic/portfolio/quantile),故与结果抽屉天然兼容、零改终端逻辑。**lightgbm 复用引擎 `_combine_lgbm`**。库实测(引擎 venv,2026-06-04):`lightgbm 4.6.0` ✅ 开箱即跑;`xgboost 3.2.0` + `scikit-learn 1.9.0`(svm·rf 依赖)实现期 `pip install` 装入 ✅ → **4 模型全可真训练**;库缺失时仍诚实回 `ok:False`(不崩、不谎报)。前端把 `xgb`/`lgbm`/`svm`/`rf` 4 个 `__pending` 占位换真调用、各传 `kind`,产出 OOS 报告进抽屉;既有节点/组件/executor/抽屉**零改**,改 jsx → bump `?v=`(P3 期 `?v=6`)。
- **P4 · 因子构建/分析(接真)**:PCA·Spearman·IC 3 节点。`pca`/`spearman` 接仓内 `POST /factor/pca` / `POST /factor/spearman`(续入**同一** `guanlan_v2/workflow/api.py`,追加式,不改 `engine/`):**复用 P2 `_materialize_xy` 物化真特征矩阵 X**(MultiIndex(datetime,code)×特征,已 winsorize/zscore)→ PCA 取主成分得分 / Spearman 按特征-前向收益秩相关符号加权 → **因子=截面 Series → 同款 `build_report` 出 OOS 报告**(ic/quantile/portfolio,与 `/factor/report` 同顶层形),故与结果抽屉天然兼容、零改终端逻辑。入参 = P2 fe spec 透传(`label` 可空)+ PCA `k`/`component`;诚实失败 `ok:False`。`iccalc`(因子 IC 计算)**P4 前已调真** `/factor/report` 取 ic 块(终端 `dt='ic'`→抽屉),P4 不改、仅入计划闭环。库实测(引擎 venv,2026-06-04):`scikit-learn 1.9.0`(PCA 依赖)✅、`scipy 1.17.1`(Spearman 秩相关备选,随 sklearn 装入)✅ —— PCA·Spearman 库全就绪可直接验真。前端把 `pca`/`spearman` 2 个 `__pending` 占位换真调用、回灌 fe spec + `k`/`component`,产出 `dt='factor'` 经下游 `iccalc`/`analysis` 出终端报告;既有节点/组件/executor/抽屉**零改**,改 jsx → bump `?v=`(本期 `?v=7`)。
- **P5 · 向量化回测(接真)**:`backtest` 节点接仓内 `POST /backtest/vector`(续入**同一** `guanlan_v2/workflow/api.py`,追加式,不改 `engine/`;`server.py` 不动 —— `build_workflow_router()` 已挂载,新端点随车上线)。入参模型 `BacktestVectorIn(ModelTrainIn)` = P2 fe spec 透传(`label` 可空)+ 回测专有 `cash`(初始资金)/ `topn`(每期持仓只数)+ 上游因子来源(`expr` / `fe_spec` / P4 PCA·Spearman 产出)。流程:**复用 P2 `_materialize_xy` 物化截面因子** → 按 `rebalance_dates`/`forward_simple_returns`(引擎 `eval/report.py`)在调仓日取因子 **TopN 等权多头**(引擎 `long_short_portfolio` 是多空,故 TopN long-only **仿写** `portfolio_stats`+`PortfolioResult`〔`eval/portfolio.py`〕)→ 出 **NAV/基准曲线 + ann_return/sharpe/max_drawdown/calmar**(`PortfolioResult`,`nav_series`/`benchmark_nav` 为 `[date_str, float]` 对)。结果是普通 JSON 载荷(`portfolio` + `_compose`),`dt='result'` 直落终端 → `ResultsDrawer` 读 `result.portfolio.{ann_return,sharpe,max_drawdown,calmar,nav_series,benchmark_nav}`,故与抽屉天然兼容、零改终端逻辑;诚实失败 `ok:False`。库实测(引擎 venv,2026-06-04):回测复用纯 `numpy`/`pandas` primitive,**无新增三方依赖**,开箱可跑。前端只把 `backtest` 节点的 `__pending` 占位(全图唯一未接的占位)换真调用、回灌上游因子 + `cash`/`topn`;既有节点/组件/executor/抽屉**零改**,改 jsx → bump `?v=`(本期 `?v=8`)。
- **P6 · 神经网络(接真)**:`nn`(MLP 神经网络)节点接仓内 `POST /model/mlp`(续入**同一** `guanlan_v2/workflow/api.py`,追加式,不改 `engine/`;`_MODEL_LIB` 登记 `mlp:sklearn` 即得库门禁/`kind` 分发)。**选型 = 前馈多层感知机 `sklearn.neural_network.MLPRegressor`(adam)**:库实测(引擎 venv,2026-06-04)`torch` **未装** → 不走 LSTM;`scikit-learn 1.9.0` 含 `MLPRegressor` ✅ → 走 MLP,**零新依赖**(免装 torch ~2GB)。MLPRegressor 原生满足 `_train_eval` 写死的 `.fit(X,y)`/`.predict(X)` 契约,与 4 个 ML 节点**完全同形**:消费 P2 `fe_spec` 重建截面特征矩阵 X → 时序 OOS 切分 → fit/predict → **预测分=截面因子 Series → 同款 `build_report` 出 OOS 报告**(ic/portfolio/quantile),故与结果抽屉天然兼容、零改终端逻辑。超参 `hidden`(隐层神经元)/`layers`(隐层数)→ `hidden_layer_sizes=(hidden,)*layers`、`lr`→`learning_rate_init`、`epochs`→`max_iter`、`alpha`→L2 正则(替 dropout);训练行超 `_SVM_TRAIN_CAP=6000` 自动下采样守 30s;MLP 无 `feature_importances_` → 特征重要度空表降级(同 SVM)。前端新增**一个** `nn` 节点(`SPECS`/`CATALOG`03/`NODE_EXEC` 三件套),既有节点/组件/executor/抽屉**零改**,改 jsx → bump `?v=`(P6 期 `?v=9`)。
- **P7 · LSTM 序列网络(接真,2026-06-04)**:`lstm` 节点接仓内 `POST /model/lstm`(`_lstm_eval`;`/model/train` `kind=lstm` 亦分发至此)。**PyTorch 真序列建模**(非 MLP 退化):复用 P2 `_materialize_xy` 物化真特征 → 逐 code 按日期滑窗 `seq_len` 期构 `(样本, seq_len, n_features)` 序列、标签=窗末标签日的前向收益 → `nn.LSTM(batch_first)` → `Linear(hidden→1)`,adam/MSE 训 `epochs` 轮 → 预测 test 标签日 → 预测分截面因子 → 共享 `_oos_model_response` 走同款 `build_report` 出 OOS 报告(与 4 ML 节点同形)。venv 装入 `torch 2.12.0+cpu`(CPU 版,免 GPU/2GB);控制端验真 `ok kind=lstm lib=torch n_train=6000 n_test=7252 lookback=10`。守秒级:csi_fast 小池 + 短窗 + 训练样本 `_SVM_TRAIN_CAP=6000` 定种子下采样。前端新增**一个** `lstm` 节点(三件套),既有零改,`?v=9→10`。
- **报告完整化(#2,2026-06-04)**:5 个 ML 节点(xgb/lgbm/svm/rf/mlp)+ lstm 的 OOS 返回**统一补齐完整报告顶层块**(`meta/ic/quantile/portfolio/characteristics` + `report` + `composite`),「单模型 → 因子分析」可直接出整张三段报告(此前仅标量 `metrics`,要经 `mf` 合成)。共享 `_report_blocks`/`_oos_model_response`;既有 4 模型零行为变更(控制端 `mlp` 复验 `composite=true`、`ic/quantile/portfolio` 非空)。
- **二期 W1a · 数据源真实体检(2026-06-07,`?v=15`)**:数据源节点接仓内 `GET /data/universes`(真解析 10 个股票池 + 成分数)+ `POST /data/probe`(选定池 + 时间窗 → 真实票数/交易日/可用字段/覆盖率;走 `get_data_paths` 延迟 import,**engine 未改**)。UI 仅扩 `SPECS.source`(中文名真池下拉 + 起始/截止/频率参数)+ `SourceProbe` 组件(「数据体检」按钮真打后端)。验真:csi300 真出 300只·61交易日·覆盖100%·15字段;坏池 HTTP200+ok:false。二期总计划(W1–W8,对标 quant-wiki)见 [../../docs/workflow_buildout_plan.md](../../docs/workflow_buildout_plan.md)。
- **二期 W2 · 公式输入可用化(2026-06-07,`?v=16`)**:formula 节点接仓内 `POST /factor/preview`(`validate_expr` + 小池 `compile_factor` 求最新截面真值样本 + 统计;延迟 import,**engine 未改**)。UI 新增 `FormulaPanel`(字段/算子真值表对齐 `expr.py` + 10 条真字段例子 + 「校验·预览」)注入 formula 节点;**订正假字段** `ret`→`returns`、`north_hold`→动量、`eps_surprise`→低波、`turnover`→`turnover_rate`(CARD_GRAPH/generateFromText/EXP_CARDS/seed)。验真:`rank(-pb)` 真出截面样本;坏表达式 HTTP200+ok:false;浏览器内「校验·预览」真显截面/覆盖/样本,点例子真载入。**W1b(接财务)用户暂缓**,故字段面板/例子本期只用真实可用的量价+估值字段。
- **二期 W3 · 因子库可浏览(2026-06-07,`?v=19`)**:因子库节点「浏览因子库 ✦」→ 全屏目录(portal)。**研报精选**(仓内 `GET /factor/catalog` 预置 30 个 A 股因子,6 大类,带中文名/方向/说明/表达式)+ **全部因子**(引擎 `/factor/list` 真 481 = alpha101+gtja191+qlib158+仓内 library/ta)。「用此因子」→ 写节点 name+expr,下游直接用。验真:catalog 30 因子抽样 `/factor/preview` 全编译;浏览器内研报精选 30 / 全部 481 / 用此因子真写入。后端 `_FACTOR_CATALOG`,前端 `FactorLibModal`+`FactorLibPanel`,engine 未改。W1b 暂缓→预置不含财务类(诚实)。
- **二期 W4 · 因子检验 alphalens 化(2026-06-07,`?v=21`)**:因子分析抽屉(`ResultsDrawer`)升级——**纯前端**,数据本就在 `/factor/report`(及模型 composite 经 `_report_blocks` 的完整块)里,engine/后端未改。关键指标 6→10(加 IC胜率/IC-t值/换手率/单调性)+ IC 时序去 24 期截断·条宽自适应 + 十分位加单调性·多空价差标注 + **新增 IC 月度热力图**(前端按年-月聚合 `ic.ic_series`)。验真:种子图出 composite 报告,抽屉真显 IC胜率 46%/IC-t -1.2/换手 11%/单调性 0.14/多空 93.7% + 8 个真实月度热力格。遗留:`analysis` 的 groups/rebal/dir 仍未透传后端(需改引擎 ReportReq);逐期换手/逐期RankIC/分布QQ 需引擎补字段。
- **后续**:GRU/MTL 序列网络、期货回测 —— 网上找算法信息再填。总计划见 [../../docs/workflow_buildout_plan.md](../../docs/workflow_buildout_plan.md)。

## 数据
因子真数据(alpha101 等),经引擎 `/factor/*`。盯盘走 `/watch/*`(引擎盯盘 + signal_pack)。

## 开放项
1. 「Python 代码」节点暂为透传(运行时不执行自定义代码;真任意代码沙箱需单独立项 + 安全评审)。
2. 盯盘(原 quant.jsx WatchMode,随量化工作台一并删除)如需保留,另起独立「盯盘台」立项。
3. ML 节点 OOS 报告已统一返回完整三件套(#2,2026-06-04 已闭)。

**2026-06-13 · 导航摘除注记**:本页已从导航摘除;通过帷幄 `ww_show_page` 工具(口头调出右栏视图)或直链(`/ui/factor/观澜 · AI 工作流.html`)访问;`?embed=1` 嵌入卫生一期已就绪(见下方融合批)。

**2026-06-12 · 帷幄融合批(`workflow.jsx?v=73`)**:
- **WW 旗**:`?embed=1` 时品牌区(masthead 印章+标题+状态 chips)隐藏,与帷幄顶栏不重复;`?legacy=1` 可找回。
- **agent 入口全局隐藏**:「AI 生成 ✦」文本生成入口、「AI 闭环 ✦」按钮默认不显示;`?legacy=1` 找回(对应 spec §3.7;后端 critique 能力保留,仅删页面入口);确定性「一句话生成」(直接搭图无 LLM)保留。
- **`take('workflow')` 接 WW cfg 驱动**:帷幄通过 `GL.handoff('workflow', {expr, name})` 驱动右栏工作流 iframe 重算——payload 支持 `{expr}` 精确表达式直建图(确定性,不走 LLM 关键词搜索)。

**2026-06-11 · 互通批(P0④/P1⑥,`workflow.jsx ?v=71`)**:
- **P0④ handoff 接精确表达式**:`GL.take('workflow')` 有 expr 时改 `tplG` 确定性直建图(source→formula→feature→analysis,expr 逐字进 `formula.params.expr`)——旧版把 expr 拼进 generateFromText 关键词正则,验证的是模板因子不是原因子;只有名字才退回关键词建图。**顺修既有 bug**:?v=65 会话恢复 effect 无条件铺回上次图,把任何交棒/?q 预填覆盖 → 加 `prefilledRef` 让位。验真:手放 handoff(换手稳定性 expr)→ 画布 formula 节点表达式逐字一致、会话恢复不再抢画布。
- **P1⑥ 关联**(后端):「存入因子库」的因子现已并入选股目录(screen/catalog.py 合并 factorlib,目录 56→95,/screen/factors 入口热刷新)——工作流验证的因子从此选股可选、可混排、下次 regen 自动算实测 IC。

**2026-06-11 · P2-B 沉淀为经验卡(`workflow.jsx?v=72`)**:结果抽屉底部洞见条新增「⊕ 沉淀为经验卡」(与「存入因子库」并排):取 expr(result 链优先,画布 formula 兜底)+ 真指标快照(RankIC/ICIR/Sharpe/回撤,口径同 exportReport)→ POST /cards(**status:draft 留人审**,批准后即可被对话/研报 wisdom_search 引用)+ GL 同后端 id 入档。验真:真点击产出 EV-014(ic=-0.0309 · insight 含完整指标 · GL real:true)。P2-E 关联:factorlib store 现下发 meta.ic 的 RankIC(`/factorlib/list` 行可选 `ic` 键,base 库无快照诚实缺席)。
