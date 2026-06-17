# 观澜 V2 模块映射表

## 模块 × 页面 × 入口 × 后端 × 数据

| 模块 | 文件夹 | 页面 HTML | 入口组件 | 背后 jsx | 后端端点 | 数据来源 | 状态 |
|------|--------|-----------|----------|----------|----------|----------|------|
| 研究图谱 | graph/ | 观澜 · 研究图谱.html | GraphApp | graph.jsx | 无 | GL 档案库 | 设计稿渲染 |
| 对话·研报 | chat/ | 观澜 · 交互原型.html | ObservatoryApp | app.jsx, agent-adapter.jsx | /run /quotes /report /comments /concepts /upload | 引擎工具(实时腾讯 + stock_data) | 设计稿已引端点 |
| 因子·工作流 | factor/ | 观澜 · AI 工作流.html | WorkflowApp | workflow.jsx?v=10 | P0 客户端 DAG;P1 借 `/factor/*` + `/factorlib/*`;P2 仓内 `/feature/build`、P3 仓内 `/model/train`(`kind=xgboost\|lightgbm\|svm\|rf`)、P4 仓内 `/factor/{pca,spearman}`、P5 仓内 `/backtest/vector`、P6 仓内 `/model/train` `kind=mlp`、P7 仓内 `/model/lstm`(`kind=lstm`,PyTorch 真序列)(`guanlan_v2/workflow/`) | 引擎因子层 + factorlib(借引擎求值)+ workflow 计算端点(import 引擎 primitive 物化 X/y、训 ML/LSTM、产预测因子、TopN 向量化回测) | P0 执行器→P7 LSTM **全 7 期就位**;ML/LSTM 的 OOS 均返回完整报告(#2) |
| 经验卡 | cards/ | 观澜 · 经验验证区.html | ValidationApp | validation.jsx | `/cards/list` `/cards` `/cards/{id}/status` `/cards/refine`(guanlan 自有);**借 `/factor/report`(单因子验);verdict 阈值前端 `verdictFromIC`** | guanlan `/cards` 库(`.data/wisdom`,三桶);GL 仍存跨模块 | **KB+左栏未验证接真**(12 视频经验);炼=真 deepseek;**验=真单因子回测(`/factor/report`)** |
| 席位·落子 | seats/ | 观澜 · 落子.html | LuoziApp | luozi-{app,chart,data,fleet,foundry,panels}.jsx | 无 | GL 档案库 | 设计稿渲染 |

## 后端端点用途

### chat(对话·研报)
- `/run`(SSE)— agent 对话主流:plan / tool_start / tool_done / answer_progress / done。经 `agent-adapter.jsx` 的 `GuanlanAgent.run()`。
  - **模块工具边界(2026-06-04)**:对话端 `/run` 传 `profile="research"` → agent **只见研究类工具**(行情/资金流/新闻/产业链/研报/扫描/经验),**因子炼制/评测(`alpha_forge`/`factor_test`/`factor_report`…)被裁掉,归量化模块**。引擎 `_tool_schemas` 按 profile 裁 + 执行 guard 兜底;LLM 误试因子工具会被优雅拒绝并引导去量化模块(非报错)。`/tools?profile=research`(31,含 2026-06-08 加的 `financials` 财务基本面工具 — 取原始 PIT 财报口径 ROE/营收/净利/EPS/成长/负债,非因子 IC/回测,故属研究域)/`?profile=factor`(20)同。机制见 `engine/financial_analyst/buddy/tools.py` `profile_tool_names`。
- `/quotes` — 实时行情(腾讯源)。
- `/report` — `run_report` 深度研报(后台进度 + 全文)。**完整研报接口契约(各研报类型/参数/输出/存储/SSE)见 [report_interfaces.md](report_interfaces.md)。**
- `/comments` — 雪球评论/情绪(需登录 cookie;失败应提醒登录)。
- `/concepts` — 板块/概念联想(Composer 的 ⌗板块)。
- `/upload` — 文件上传(Composer 的 ⊟上传)。

### factor(因子·工作流)
- `/factor/list` 因子清单 · `/factor/bench` 评测 · `/factor/report` 因子报告。
- `/factor/forge` 炼因子(NL→DSL) · `/factor/save` 存因子。
- `/factor/compose` 多因子合成(可带 `/compose/advise` 一句话配方) · `/factor/archive` 研究档案。
- `/run` 分类/建议(复用对话端点)。
- `/watch/*` 盯盘:`stream`(SSE) `start` `stop` `status` `ack` `bars` `history` `item` `hitrate` `outcome`。

#### AI 工作流 节点 × 端点(workflow.jsx 节点图;P0 执行器 + P1 输入层)
节点机制 = `SPECS`(形状)+ `CATALOG`(目录)+ `NODE_EXEC`(执行)三件套 + 通用 DAG 执行器(配方见 [node_recipe.md](node_recipe.md))。加节点只扩这三处,既有组件零改;改 workflow.jsx → bump HTML `?v=`(当前 `?v=9`)。

| 节点 | 期 | 端点/数据 | 状态 |
|---|---|---|---|
| 数据源 | P1 | 客户端选 universe → `_universeOf` 映射(csi500/csi800/csi300_active/all/csi_fast),universe 串喂下游;真数据由引擎按 universe 拉 | 落地中 |
| 因子库 | P1 | `/factor/list`(引擎 buddy server,含 factorlib 注册因子)浏览/搜索 → 拖入;管理/迁移走仓内 `/factorlib/*`(含 TA 指标族 `ta_*`:MACD/RSI/KDJ/BOLL/WR… `sma`=EMA 重建,供经验卡「炼」做因子表达式 grounding) | 落地中 |
| 公式输入 / 因子分析 | ✅ | `expr → /factor/report`(已通) | 已接真 |
| 多因子构建 | ✅ | `/factor/compose`(已通) | 已接真 |
| Python 代码 | P0 | 暂透传 expr(真任意代码后续立项) | 占位 |
| 特征工程 | P2 | `POST /feature/build`(仓内 `guanlan_v2/workflow/api.py`,`build_workflow_router()`)— 延迟 import 引擎 primitive 在 universe 面板上物化真 X/y,返回 n_dates/n_codes/coverage/IC/预览 + 可复算 fe spec(供 P3 ML 重建训练集);`feature` 节点 `params.tag`(`IC`/`fwd_ret`)映射端点 `label`,留空/`IC`/`fwd_ret` → 前向收益 `fwd_days` | 契约落定 |
| XGBoost / LightGBM / SVM / 随机森林 | P3 | `POST /model/train`(仓内 `guanlan_v2/workflow/api.py`,单端点 + `kind=xgboost\|lightgbm\|svm\|rf` 分发)— 消费 P2 `fe_spec` 重建训练集 → 时序 OOS 切分 → fit/predict → **预测分=截面因子 Series** → 同款 `build_report` 出 OOS 报告(ic/portfolio/quantile);lightgbm 复用引擎 `_combine_lgbm`,4 个 `xgb/lgbm/svm/rf` 节点传不同 `kind` | 契约落定 |
| 一个神经网络(MLP) | P6 | `POST /model/train` **`kind=mlp`**(**复用同一端点**,`guanlan_v2/workflow/api.py`)— `sklearn.neural_network.MLPRegressor`(前馈多层感知机);与 4 个 ML 节点**完全同形**:消费 P2 `fe_spec` 重建截面 X/y → `_train_eval` 九步**一行不改**(时序 OOS 切分 → `.fit(X,y)`/`.predict(X)` → 预测分=截面因子 Series → 同款 `build_report` 出 OOS ic/portfolio/quantile)。前端节点 type=`nn`(title「MLP 神经网络」),超参 hidden/layers/lr/epochs/alpha → MLP `hidden_layer_sizes`/`learning_rate_init`/`max_iter`/`alpha`。**选型:torch 未装(引擎 venv `find_spec('torch') is None`)→ 走 MLP(sklearn 1.9.0 已装,零新依赖);LSTM/GRU 待装 torch 后再立项。** | 契约落定 |
| PCA 因子构建 / Spearman 因子 | P4 | `POST /factor/pca` · `POST /factor/spearman`(续入**同一** `guanlan_v2/workflow/api.py`,追加式,不改 engine/)— 复用 P2 `_materialize_xy` 物化真特征矩阵 X(MultiIndex(datetime,code)×特征)→ PCA 取主成分 / Spearman 算特征-前向收益秩相关加权 → **合成截面因子 Series → 同款 `build_report` 出 OOS 报告**(ic/quantile/portfolio,与 `/factor/report` 同顶层形)。PCA 用 sklearn `PCA`,Spearman 用 scipy/pandas 秩相关;诚实失败 `ok:False` | 契约落定 |
| 因子 IC 计算 | P4(已通) | `iccalc` 复用 `/factor/report` 的 ic 块(`workflow.jsx:291-298` 已调真,终端 `dt=ic`→抽屉)— P4 不新增端点,仅入计划闭环 | 已接真 |
| 向量化回测 | P5 | `POST /backtest/vector`(续入**同一** `guanlan_v2/workflow/api.py`,追加式,不改 engine/)— 消费上游因子(P2 `fe_spec` / `expr` / PCA·Spearman 产出)→ **TopN 多头组合**(`portfolio_stats` + 仿 `PortfolioResult`)→ NAV/基准曲线 + ann_return/sharpe/max_drawdown/calmar;`backtest` 节点 `dt='result'` 进抽屉 | 已接真 |

> 因子库后端 `guanlan_v2/factorlib/`(借引擎 primitive 求值、注册进运行期 zoo registry、数据走 `get_data_paths`)详见 [factor_library.md](factor_library.md)。

#### workflow 计算端点 — guanlan 自有薄壳,代码在 `guanlan_v2/workflow/`
AI 工作流里**需要真算的节点**(特征工程 / ML / PCA / Spearman / 向量化回测)统一落一个新薄壳工厂路由 `build_workflow_router()`(仿 `factorlib`/`seats`/`cards`,`server.py` `include_router`,插在 factorlib 之后),**不改 `engine/`、不碰 fa-watch-wt**。

- **`POST /feature/build`(P2,已落契约)**— 收「特征表达式 `features` + 标签 `label` / 前向收益 `fwd_days` + `universe` + `winsorize`/`standardize` 开关 + `start`/`end`/`freq` 窗口」→ 函数体内**延迟 import** 引擎 primitive(`get_default_loader` / `load_panel_cached` / `PanelData` / `compile_factor` / `validate_expr` / `winsorize` / `zscore` / `forward_simple_returns` / `ic_analysis`)在 `resolve_universe_codes` 解出的 universe 面板上物化真 X/y → 返回 `{ok, universe, n_dates, n_codes, coverage, ic, preview, fe_spec}`(`fe_spec` 可复算,供 P3 ML 重建训练集)。数据全经 `get_default_loader()`→`load_panel_cached()`(与 `report.py:188-196` 同链,根由引擎 `get_data_paths` 解析,零硬编码)。诚实失败:异常 / 空 universe / 空面板 → `{ok:False, reason}` HTTP 200(对齐 seats/factorlib)。
  - 前端 `feature` 节点的 NODE_EXEC 占位(`workflow.jsx:233` 之 `__pending`)换真调用;`params.tag`(`IC`/`fwd_ret`)映射端点 `label`(留空/`IC`/`fwd_ret` → 前向收益分支,对齐 PandaAI 蓝本)。`fe` 端口 `dt='fe'` 非终端,不进 `ResultsDrawer`(`TERMINAL_DT={report,ic,result}` 不动);本期靠后端 curl 验真 + 节点 `done` 态。既有节点/组件/executor/抽屉**零改**,改 jsx → bump `?v=`。
- **`POST /model/train`(P3,已落契约)**— 基础 ML 单端点 + `kind` 分发。收「P2 `fe_spec` 同名透传字段(`features`/`feature`/`label`/`fwd_days`/`universe`/`start`/`end`/`freq`/`winsorize(_q)`/`standardize`)+ `kind`(`xgboost\|lightgbm\|svm\|rf`)+ `train_frac` 等模型超参」→ 函数体内**延迟 import** 引擎 primitive,按 `fe_spec` 在 universe 面板**重建训练集 X/y**(与 `/feature/build` 同链:`get_default_loader`/`load_panel_cached`/`compile_factor`/`winsorize`/`zscore`/`forward_simple_returns`)→ **时序 OOS 切分**(`train_frac` 切 train/test,对齐 `compose.py:198-251`)→ `kind` 分发 fit/predict(**lightgbm 复用引擎 `engine/.../factors/compose/combine.py:129-179` `_combine_lgbm`**;xgboost/svm/rf 各自 fit)→ 关键:**预测分写回一个仅 test 行有值的截面因子 Series,`reindex(panel.df.index)` 后喂同款 `build_report`**(对齐 `compose.py:244-251`)→ 出真 OOS `FactorReport`(含 `ic`/`portfolio`/`quantile`,经 `ic_analysis` 取 `rank_ic_mean`/`icir`)。**报告链不靠"模型存盘指针",靠"预测分=截面因子 Series"**,故与 `ResultsDrawer` 终端逻辑天然兼容、零改前端。出参 `{ok, kind, universe, n_train, n_test, ic, report/composite, ...}`;诚实失败 → `{ok:False, reason}` HTTP 200。
  - **库现状(引擎 venv `G:/financial-analyst/.venv`,2026-06-04 实测)**:`lightgbm 4.6.0` ✅(旧装)/`xgboost 3.2.0` ✅(2026-06-04 pip 装入)/`scikit-learn 1.9.0` ✅(svm·rf 依赖,2026-06-04 pip 装入);`numpy 2.4.6`/`pandas 3.0.3`。**4 个 `kind`(xgboost/lightgbm/svm/rf)均可真训练,已控制端验真(n_train≈16308,各模型 feature_importance 不同→真 fit)。**
  - 前端 `xgb`/`lgbm`/`svm`/`rf` 4 个 NODE_EXEC 占位(`workflow.jsx:245-248` 之 `{model:{__dt:'model',__pending,fe}}`)换真调用,各传不同 `kind`;`model` 端口非终端(`workflow.jsx:320`),但产出的 OOS 报告以 `dt='report'`(或 `ic`)落终端 → 进 `ResultsDrawer`(终端判定 `workflow.jsx:345` `payload.ic\|portfolio\|composite\|_compose!=null` 不动)。既有节点/组件/executor/抽屉**零改**,改 jsx → bump `?v=`。
- **`POST /factor/pca` · `POST /factor/spearman`(P4,已落契约)**— 无监督 / 秩相关**因子构建**,续入**同一** `guanlan_v2/workflow/api.py`(追加式,`return router` 前再加 2 个 `@router.post`;不新增后端文件、不改 `engine/`、不碰 fa-watch-wt)。入参 = P2 `fe_spec` 同名透传字段(继承 `ModelTrainIn` 的 `features`/`feature`/`label`/`fwd_days`/`universe`/`start`/`end`/`freq`/`winsorize(_q)`/`standardize`;`label` 可空 —— 因子构建是对 X 降维/加权,不必标签)+ PCA 专属 `k`(主成分数,裁到 `[1, n_features]`)/ `component`(取第几主成分,默认 PC1)。流程:函数体内**延迟 import** 引擎 primitive,经 P2 `_materialize_xy(body, universe, features, start, end)` 物化 `(panel, fe_df, label_s, feature_names)` —— `fe_df` 即已 winsorize/zscore 的特征矩阵 **X**(MultiIndex(datetime,code)×`feature_names`)→ **PCA**:sklearn `PCA` 对 X 降维,取 `component` 主成分得分作截面因子;**Spearman**:逐截面算各特征与前向收益的秩相关(scipy/pandas),按 |秩相关| 符号加权合成因子 → **预测分=截面因子 Series,`reindex(panel.df.index)` 后用 `lambda p: pc_full` 喂同款 `build_report`**(与 P3 完全同款,`api.py:463-470`)→ 出真 OOS `FactorReport`(`ic`/`quantile`/`portfolio`)+ headline `ic_analysis`(取 `rank_ic_mean`/`icir`)。出参与 `/factor/report` 同顶层形(`ic`/`quantile`/`portfolio`/`characteristics`/`warnings`/`status`)+ `{ok, universe, n_dates, n_codes, report, ...}`;诚实失败 `{ok:False, reason}` HTTP 200。**报告链同 P3:靠"因子=截面 Series"而非模型指针,故与 `ResultsDrawer` 天然兼容、零改前端终端逻辑。**
  - **库现状(引擎 venv `G:/financial-analyst/.venv`,2026-06-04 实测)**:`scikit-learn 1.9.0` ✅(`PCA` 依赖,P3 期 pip 装入)/`scipy 1.17.1` ✅(Spearman 秩相关备选,随 sklearn 装入)/`numpy 2.4.6`/`pandas 3.0.3`。PCA·Spearman 所需库**全部就绪**;`PCA` 用 `importlib.util.find_spec("sklearn")` 门禁,缺则诚实 `ok:False`。
  - 前端 `pca`/`spearman` 2 个 NODE_EXEC 占位(`workflow.jsx:288-289` 之 `{factor:{__dt:'factor',__pending,fe}}`)换真调用(POST `/factor/pca`、`/factor/spearman`,回灌 fe spec + `k`/`component`),产出 `dt='factor'`(非终端)→ 经下游 `iccalc`/`analysis` 出终端报告;`iccalc`(`workflow.jsx:291-298`,**已调真** `/factor/report` 取 ic 块,终端 `dt='ic'`→抽屉)P4 不改。`ResultsDrawer`/`TERMINAL_DT`/`runGraph`/`Node`/`SPECS`/其它执行器**零改**,改 jsx → bump `?v=`(本期 → `?v=7`)。
- **`POST /backtest/vector`(P5,已落契约)**— 向量化 **TopN 多头组合**回测,续入**同一** `guanlan_v2/workflow/api.py`(追加式,`return router` 前再加 1 个 `@router.post`;**不新增后端文件、不改 `engine/`、不碰 fa-watch-wt**)。入参模型 `BacktestVectorIn(ModelTrainIn)` —— 继承 P2/P3/P4 全部 `fe_spec` 透传字段(`features`/`feature`/`label`(可空)/`fwd_days`/`universe`/`start`/`end`/`freq`/`winsorize(_q)`/`standardize`)+ 回测专有 `cash`(初始资金)/ `topn`(每期持仓只数)+ 因子来源(上游 `expr` / `fe_spec` / PCA·Spearman 产出的因子 Series)。流程:函数体内**延迟 import** 引擎 primitive,经 P2 `_materialize_xy` 物化 `(panel, fe_df, label_s, feature_names)` 得截面因子 → 按 `rebalance_dates`/`forward_simple_returns`(`eval/report.py`)在调仓日取因子 **TopN** 等权多头(`long_short_portfolio` 是多空,故 TopN long-only 仿 `portfolio_stats(ls, ppy)` + `PortfolioResult` 自建)→ 出 `PortfolioResult`(`nav_series`/`benchmark_nav` 为 `[date_str, float]` 对,经 `_jsonable(asdict(...))`)+ ann_return/sharpe/max_drawdown/calmar。复用 `portfolio_stats`(`eval/portfolio.py:25`)/`PortfolioResult`(`portfolio.py:12`)/`forward_simple_returns`/`rebalance_dates`/`_restrict`/`_benchmark_nav`(`report.py`),模板照 `_factor_eval`(`api.py:641`)+ `_materialize_xy`/`_jsonable`/`_fail_factor`/`_normalize_features`/`_MODEL_DEFAULT_YEARS`(全可复用)。出参 `{ok, universe, n_dates, n_codes, portfolio:{ann_return,sharpe,max_drawdown,calmar,nav_series,benchmark_nav}, _compose, ...}`,诚实失败 `{ok:False, reason}` HTTP 200。**关键架构定论同 P3/P4:回测结果是普通 JSON 载荷(`portfolio` + `_compose != null` + 无 `__pending`),命中 `ResultsDrawer` 终端判定(`workflow.jsx:448`,`TERMINAL_DT` 已含 `result:1`),故与抽屉天然兼容、零改前端终端逻辑。**
  - 库现状:回测复用 `eval/portfolio.py` + `eval/report.py` 的纯 `numpy`/`pandas` primitive(`numpy 2.4.6`/`pandas 3.0.3`,引擎 venv 自带),**无新增三方依赖**。
  - 前端只把 `backtest` 节点的 NODE_EXEC 占位(`workflow.jsx:385`,唯一未接的占位;`backtest` SPEC 入参 `factor`、params `cash`/`topn`)换真调用(POST `/backtest/vector`,回灌上游因子 + `cash`/`topn`),产出 `dt='result'` 直落终端 → 进 `ResultsDrawer`(读 `result.portfolio.{ann_return,sharpe,max_drawdown,calmar,nav_series,benchmark_nav}`,`workflow.jsx:985-1003`)。`server.py`/`ResultsDrawer`/`TERMINAL_DT`/`runGraph`/`Node`/`SPECS`/其它执行器**零改**,改 jsx → bump `?v=`(本期 → `?v=8`)。
- **`POST /model/train` `kind=mlp`(P6「一个神经网络」,已落契约)**— 神经网络节点**不新增端点**,**复用 P3 同一** `POST /model/train`(`guanlan_v2/workflow/api.py`,追加式只动 4 处:`_MODEL_LIB` 登记 `"mlp":"sklearn"` → 自动得库门禁 + `_MODEL_KINDS`〔`api.py:115` `tuple(_MODEL_LIB)`〕含 mlp + `unknown_kind` 判定;`_build_model`〔`api.py:316` `raise ValueError` 前〕插 `mlp` 分支;`ModelTrainIn.kind` 注释补 `| mlp`〔非功能〕;**不新增后端文件、不改 `engine/`、不碰 fa-watch-wt**)。**选型:`sklearn.neural_network.MLPRegressor`(前馈多层感知机 MLP)**。决策树:引擎 venv `G:/financial-analyst/.venv` `find_spec('torch') is None` ⇒ torch 未装 → **不走 LSTM**,走 **MLP**(`from sklearn.neural_network import MLPRegressor` OK,sklearn 1.9.0 P3 期已装,**零新依赖**、免装 torch ~2GB 重库)。MLPRegressor 原生满足 `_train_eval` 写死的 `.fit(X,y)`/`.predict(X)` 契约(`api.py:447`/`456`),`X` 直接用 `_materialize_xy` 出的**截面**特征矩阵,**无需** LSTM 那套按 code 构 `(samples,lookback,n_features)` 序列的面板重排 —— 与现有 4 个 ML 节点(xgb/lgbm/svm/rf)**完全同形**,改动面最小。`_build_model` `mlp` 分支(复用闭包 `_i`/`_f`〔`api.py:272-276`〕对齐前端 SPECS 超参):`hidden_layer_sizes=tuple([hidden]*layers)`(layers 层 × hidden 神经元)/ `learning_rate_init=lr` / `max_iter=epochs` / `alpha`(L2)。`_train_eval` 九步**一行不改**(对所有 kind 共用:`_materialize_xy` 物化 X/y → compose 时序切 OOS → fit → predict → **预测分=截面因子 Series** → `reindex` 后喂同款 `build_report` 出 OOS `FactorReport` ic/quantile/portfolio,经 `ic_analysis` 取 `rank_ic_mean`/`icir`)。出参 `{ok, kind:"mlp", universe, n_train, n_test, ic, report, ...}`,诚实失败 `{ok:False, reason}` HTTP 200。
  - 前端 `nn` 节点**三件套已就位**:SPEC `nn`(`workflow.jsx:26`,title「MLP 神经网络」,cat `ml`,入 `fe`〔dt=fe〕→ 出 `model`〔dt=model〕,params hidden/layers/lr/epochs/alpha)+ CATALOG `03 · 机器学习` 组含 `nn`(`workflow.jsx:38`)+ NODE_EXEC `nn`(`workflow.jsx:292`)→ `_trainModel('mlp', …, {hidden:'hidden',layers:'layers',learning_rate_init:'lr',max_iter:'epochs',alpha:'alpha'})` POST `/model/mlp`,与 xgb/lgbm/svm/rf 走**同一** `_trainModel`(`workflow.jsx:205`)。`model` 端口 `dt='model'` **非终端**(同 P3),产出经 `_modelReport`(`workflow.jsx:368`)喂多因子构建 `mf` → 因子分析出 `dt='report'` 终端 → 进 `ResultsDrawer`。终端判定(`workflow.jsx:480`)键于 `dt ∈ TERMINAL_DT`(`workflow.jsx:455` `{report:1,ic:1,result:1}`)—— mlp 节点**字节级同形 xgb,`TERMINAL_DT`/`ResultsDrawer`/`runGraph`/`Node`/`SPECS`/其它执行器全零改**。**库实测(引擎 venv,2026-06-04):`scikit-learn 1.9.0` ✅(MLPRegressor 依赖,P3 期已装);`torch` ❌(未装,故不走 LSTM/GRU);`numpy 2.4.6`/`pandas 3.0.3`。无新增三方依赖。** 改 jsx → bump `?v=`(本期 → `?v=9`)。

### cards(经验卡)— guanlan 自有(非引擎),代码在 `guanlan_v2/cards/`
- `GET /cards/list?status=approved|draft|rejected|all` — 列经验卡(右栏知识库读它)。
- `GET /cards/{id}` 取单卡 · `POST /cards` upsert(沉淀;无 id→`EV-NNN`,status 默认 approved)· `POST /cards/{id}/status` 迁移状态。
- `POST /cards/refine` — 炼·经验卡+指令 → 引擎大模型(deepseek,带基础 prompt)精炼;操作前端草稿、不读写库,失败前端回退本地规则。基础 prompt 内置**因子表达式 DSL 白名单**,炼出的 `expr` 只能用清单内字段/算子(grounding 源头)。
- **验(单因子)借因子端 `POST /factor/report`**(非 cards 自有端点;`expr_or_name=draft.expr`,`universe` 默认 `csi_fast`)→ 真 `ic`/`portfolio`;verdict 由前端 `verdictFromIC` 按 IC 阈值打(`|ic|≥0.03 且 |icir|≥0.3`→通过 / `|ic|<0.015`→驳回 / 余存疑),非法 expr / 算不出 → compute_error 诚实驳回。完整工作流/多因子/ML 验证经 handoff 跳「因子·工作流」。
- 卡 = UI 量化形状(cat/verdict/conf/ic/expr/insight/src/refs),markdown 落 `GUANLAN_WISDOM_ROOT`(默认 `.data/wisdom`);**非 stock_data**。详见 [../ui/cards/README.md](../ui/cards/README.md)。

## 跨模块数据流(GL 档案库)

```
research ──炼──▶ factor ──验证──▶ card ──装配──▶ seat ──落子──▶ decision
   ▲(chat 产出素材)   ▲(factor 产出)  ▲(cards 产出)  ▲(seats 产出)
   └──────── window.GL.put / link / handoff,localStorage 持久化 ────────┘
研究图谱(graph)= 这张图的总览视图(读 GL.all / byRef / stats)
```

## 导航(guanlan-nav.js)

- 5 个 nav 入口:研究图谱 / 对话·研报 / 经验卡 / 因子·工作流 / 席位·落子。
- **factor 模块的 nav 入口指向「AI 工作流」**(唯一页面;原「量化工作台」子页已于 2026-06-04 删除,因子能力并入 AI 工作流)。
- 高亮:按当前页文件名 basename 匹配(`here === m.file.split('/').pop()`)。
