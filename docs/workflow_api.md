# 观澜 · AI 工作流 — 接口说明书(LLM 自动搭图 grounding)

> 用途:让「观澜」界面里的 LLM 能据此**把一句话目标编译成一张可运行的节点图(graph JSON)**,
> 前端渲染上画布、用户运行。这既是 LLM 的「工具说明 + 词表」,也是人读的接口参考。
> 配套:节点机制见 [node_recipe.md](node_recipe.md);后端落点见 [module_map.md](module_map.md);
> 总计划见 [workflow_buildout_plan.md](workflow_buildout_plan.md)。
> 真相源:`ui/factor/workflow.jsx`(SPECS/NODE_EXEC)、`guanlan_v2/workflow/api.py`(端点)、
> `engine/financial_analyst/factors/zoo/{expr,operators}.py`(DSL)。

---

## 0. 架构:LLM 怎么「用」这套工作流(结论先行)

三种可能,**推荐第 2 种**:

| 方案 | 做法 | 评价 |
|---|---|---|
| A. LLM 写后端代码 | LLM 每次生成 Python 跑因子/模型 | ❌ **不要**。后端端点是**固定基建**(§5,已建好/已验真/安全)。让 LLM 临时写后端 = 要代码沙箱、慢、不安全、还重复造轮子。**新能力 = 开发者按 [node_recipe.md](node_recipe.md) 加一个节点类型**(SPECS+executor+端点),不是每请求 codegen。 |
| **B. LLM 当「图编排器」** | LLM 从**固定节点目录**(§2)选节点、填参、连边 → 输出 **graph JSON**(§1)→ 前端 `importJSON` 渲染上画布 → 用户审阅/调参/点运行 | ✅ **推荐**。受约束生成(只能选目录里的节点、按 dt 规则连边、按 §4 白名单写表达式),安全(不执行任意代码)、快、用户掌舵。前端**已有** `importJSON` / `generateFromText` / `chain` 渲染通道——把关键词匹配换成真 LLM 调用即可。 |
| C. LLM 只给文字提示 | LLM 说「拖个公式节点、敲 X、连到特征工程…」,用户全手搭 | ⚠️ 能用但弱,丢了自动化价值。可作 B 的降级。 |

**结论:能实现,正解是 B —— LLM 输出图 JSON,后端一行不写,前端直接渲染,用户运行。** 本文件就是 B 所需的「地面真值 / 词表」。

**B 的人机分工**(默认 human-in-loop):
1. 用户说目标(自然语言)。
2. LLM 据本文件输出 `{nodes, edges}`(合法节点类型 + dt 合法边 + 合法 DSL)。
3. 前端 `importJSON` 渲染上画布。
4. 用户审阅、调参、点「▶ 运行工作流」→ 固定后端端点执行 → 结果抽屉出真报告。

变体:**全自动**=渲染后自动点运行;**助手**=LLM 只解释建议。默认带审阅。

---

## 1. 图 JSON 契约

工作流 = `{ nodes: [...], edges: [...] }`(与前端 `exportJSON`/`importJSON` 同构,与 `chain()`/`seedGraph()` 同形)。

```jsonc
{
  "nodes": [
    { "id": "n1", "type": "formula", "x": 60,  "y": 150, "params": { "expr": "rank(-delta(close,5))" } },
    { "id": "n2", "type": "feature", "x": 342, "y": 150, "params": { "tag": "IC" } }
  ],
  "edges": [
    { "from": ["n1", "out"], "to": ["n2", "feat"] }
  ]
}
```

- `id`:图内唯一字符串(`n1`,`n2`…)。
- `type`:§2 的节点 type(`formula`/`feature`/`xgb`/`lstm`/…)。
- `x`,`y`:画布坐标(像素)。**布局约定**:一条链从左到右 `x` 每级 +280;`y` 取 120–500;并行支路分不同 `y`。坐标只影响观感,**不影响运行**。
- `params`:该 type 的参数(§2 每节点列了 id + 默认值);缺省键走默认。
- `edges[].from = [源节点id, 源输出端口id]`,`to = [目标节点id, 目标输入端口id]`。端口 id 见 §2。
- ⚠️ **edges 不经 dt 校验**(`importJSON` 直信),所以 **LLM 必须自己保证 dt 合法**(§3)。(用户手动连线时画布 `workflow.jsx:597` 会拦截不匹配的 dt。)

---

## 2. 节点目录(LLM 只能从这里选)

格式:**`type`** 标题 — 入:`端口id:dt` · 出:`端口id:dt` · 参数 · 后端。

### 01 · 基础工具(输入层,无前序)
- **`source`** 数据源 — 入:无 · 出:`data:series` · 参 `scope`(个股/自选/小池/全市场)、`code`、`universe`(§6)· 后端:无。**特殊**:它只**全局设定 universe**(取其 `params`,见 §6),**不需要连边**;图里放一个即可,省略则全局默认 `csi_fast`。
- **`formula`** 公式输入 — 入:无 · 出:`out:series` · 参 `expr`(一条 §4 DSL 表达式)· 后端:无(透传表达式)。**这是「造因子」的主入口。**
- **`python`** Python 代码 — 入:无 · 出:`out:series` · 参 `code` · 后端:无(**当前透传,不执行自定义代码**;不建议 LLM 用)。
- **`factorlib`** 因子库 — 入:无 · 出:`out:series` · 参 `query`(模糊匹配)、`name`(精确名)· 后端:GET `/factorlib/list`(退 `/factor/list`)。从已迁移/注册因子里选一个。

### 02 · 特征工程
- **`feature`** 特征工程构建 — 入:`feat:series`、`label:series`(可选)· 出:`fe:fe` · 参 `tag`(`IC`/`fwd_ret`/留空 → 前向收益;或连 `label` 用公式标签)· 后端:POST `/feature/build`。把上游公式在 universe 面板物化成真 X/y;其输出 `fe` 块是后续 ML/PCA 的入口。**`feat` 口可接多条边**(唯一多入边例外):多个 `formula`/`factorlib` 各连一条边即聚合为多特征(保序去重),供多特征 ML/PCA/Spearman;其余任何输入口多条边仅最后一条生效并记警告。

### 03 · 机器学习(入都是 `fe:fe`,出都是 `model:model`)
- **`xgb`** XGBoost — 参 `trees,depth,lr,sub` · POST `/model/xgboost`
- **`lgbm`** LightGBM — 参 `leaves,lr` · POST `/model/lightgbm`
- **`svm`** SVM — 参 `c` · POST `/model/svm`
- **`rf`** 随机森林 — 参 `trees` · POST `/model/rf`
- **`nn`** MLP 神经网络 — 参 `hidden,layers,lr,epochs,alpha` · POST `/model/mlp`
- **`lstm`** LSTM 序列网络 — 参 `seq_len,hidden,layers,lr,epochs` · POST `/model/lstm`(PyTorch 真序列)

### 04 · 因子相关
- **`pca`** PCA 因子构建 — 入 `fe:fe` · 出 `factor:factor` · 参 `k` · POST `/factor/pca`
- **`spearman`** Spearman 因子 — 入 `fe:fe` · 出 `factor:factor` · 参 无 · POST `/factor/spearman`
- **`mf`** 多因子构建 — 入 `m1:model,f1:fe,m2:model,f2:fe` · 出 `factor:factor` · 参 `bt_start,bt_end` · 消费上游模型 OOS 报告 → 复合因子(无模型则退化 `/factor/compose` 合成公式)。**把模型预测变成可分析/回测的因子(model→factor 的唯一桥)。**
- **`iccalc`** 因子 IC 计算 — 入 `factor:factor` · 出 `ic:ic`(终端)· 参 `period` · POST `/factor/report`(取 IC 块)
- **`analysis`** 因子分析 — 入 `factor:factor` · 出 `report:report`(终端)· 参 `rebal,groups,dir` · POST `/factor/report`(完整三段报告:IC 时序 + 十分位 + 净值)

### 05 · 回测
- **`backtest`** 向量化回测 — 入 `factor:factor` · 出 `result:result`(终端)· 参 `cash,topn` · POST `/backtest/vector`(逐调仓期 TopN 等权净值 + 年化/Sharpe/回撤/Calmar)

---

## 3. 端口类型 dt 与连线规则

**铁律:一条边 `from`端口.dt 必须 === `to`端口.dt。**(画布手连会拦不匹配;LLM 出图须自校验。)

| dt | 谁产出 | 谁接收 |
|---|---|---|
| `series` | source.data · formula.out · python.out · factorlib.out | feature.feat · feature.label |
| `fe` | feature.fe | xgb/lgbm/svm/rf/nn/lstm.fe · pca/spearman.fe · mf.f1 · mf.f2 |
| `model` | xgb/lgbm/svm/rf/nn/lstm.model | mf.m1 · mf.m2 |
| `factor` | pca/spearman/mf.factor | iccalc/analysis/backtest.factor |
| `ic`(终端) | iccalc.ic | → 结果抽屉 |
| `report`(终端) | analysis.report | → 结果抽屉 |
| `result`(终端) | backtest.result | → 结果抽屉 |

- **终端 dt = `{report, ic, result}`** → 运行后进结果抽屉。**一张图至少要有一个终端节点**(analysis/iccalc/backtest)才出结果。
- **`model` 不能直连 `factor` 口** → 模型要分析/回测,必须先经 `mf`(model→mf→factor)。

---

## 4. 因子表达式 DSL(`formula` 节点的 `expr`)

引擎用**受限 `eval`(无 builtins)**,**只认下列字段 + 算子**;禁含 `__` / `import` / `lambda`。

**字段(价量,日频)**:`close open high low volume vwap amount returns industry`
**字段(基本面,日频)**:`pe_ttm pb ps_ttm dv_ttm`(股息率%)`total_mv circ_mv`(总/流通市值,万元)`turnover_rate`(换手%)

**算子**(都是 panel Series→Series;时序算子按 code 分组,不跨股票串窗;`ts_*` 窗未满发 NaN):
- 截面:`rank(x)`(分日百分位 [0,1])· `scale(x,a=1)` · `indneutralize(x, industry)`
- 时序窗(带整数 n):`ts_mean ts_sum stddev ts_max ts_min ts_argmax ts_argmin ts_rank delta delay product`
- 双序列窗:`correlation(x,y,n)` · `covariance(x,y,n)`
- 加权/均线:`decay_linear(x,n)` · `wma(x,n)` · `sma(x,n,m)`(m<n)
- 逐元素:`signedpower(x,p)` · `power(x,p)` · `log(x)` · `sign(x)` · `abs(x)` · `max_pair(a,b)` · `min_pair(a,b)`
- 信号:`filter_where(x,mask)` · `cross(x,y)`(x 上穿 y → 1)
- 运算符:`+ - * / **`、比较、`()`

**例**:
| 想法 | 表达式 |
|---|---|
| 5 日反转 | `rank(-delta(close,5))` |
| 20 日动量 | `rank(ts_sum(returns,20))` |
| 量价背离 | `-correlation(rank(close), rank(volume), 10)` |
| 低换手 | `-rank(turnover_rate)` |
| 均线突破 | `cross(close, ts_mean(close,20))` |
| 行业中性动量 | `indneutralize(ts_sum(returns,20), industry)` |

> 也可直接用**已注册因子名**(GET `/factor/list`,如 `alpha101_*` / `gtja191_*` / 迁移的 `factorlib` 因子)放进 `formula` 或 `factorlib` 节点——后端「注册名优先,否则编译表达式」。

---

## 5. 后端 REST 端点

base = 页面同源(`http://127.0.0.1:9999` 或 `:9998`)。**失败一律 `{ok:false, reason}`(HTTP 200)**,不抛 500;前端据此降级。

### 仓内 workflow 端点(`guanlan_v2/workflow/api.py`)

**POST `/feature/build`** — 物化特征工程
- req:`{ features:[expr], label?:str, fwd_days?:5, universe?:"csi_fast", start?, end?, freq?:"day", winsorize?:true, standardize?:true }`
- resp:`{ ok, n_dates, n_codes, coverage, feature_names, ic:[{feature,expr,rank_ic_mean,icir}], preview, fe:{…可复算 spec} }`
- 前端 `feature` 节点把返回的 **`fe` 块原样回灌**给下游 ML/PCA/Spearman 端点(重建同一训练集)。

**POST `/model/{xgboost|lightgbm|svm|rf|mlp|lstm}`**(或 `/model/train` 带 `kind`)— 训练 + OOS 评测
- req:`{ …fe(上游 feature 的 fe 块), train_frac?:0.7, params:{超参} }`
  - 超参 `params`:`xgb {trees,depth,lr,sub}` · `lgbm {leaves,lr}` · `svm {c}` · `rf {trees}` · `mlp {hidden,layers,lr,epochs,alpha}` · `lstm {seq_len,hidden,layers,lr,epochs}`
- resp:`{ ok, kind, model:{kind,lib,hyperparams}, n_train, n_test, metrics:{rank_ic,rank_icir,oos_r2,sharpe,ann_return}, feature_importance, meta, ic, quantile, portfolio, characteristics, report, composite:true, fe }`
  - (`composite:true` + `ic/quantile/portfolio` 顶层块 → 经 `mf` → `analysis` 可出完整报告。)

**POST `/factor/pca`** — req `{ …fe, k?, component? }` → resp:`/factor/report` 同顶层形 + `explained_variance_ratio, k` + `composite:true`
**POST `/factor/spearman`** — req `{ …fe }` → resp:同上 + `spearman_weights`
**POST `/backtest/vector`** — req `{ …fe(或 features:[expr]), topn?:30, cash?:1e6, long_short?:false, cost_bps?:0 }`
- resp:`{ ok, portfolio:{ann_return,sharpe,max_drawdown,calmar,volatility,turnover,win_rate,nav_series,benchmark_nav}, ic, quantile, characteristics, backtest:{topn,cash,portfolio_kpi}, trades, composite:true }`

### 借用引擎端点(只读/求值)

**GET `/factor/list`** · **GET `/factorlib/list`** → `{ registered:[{name,family,formula}], user:[{name,expr}] }`
**POST `/factor/report`** — req `{ expr_or_name, universe?, freq? }` → `{ status, meta:{n_dates,n_codes}, ic:{rank_ic_mean,icir,…}, quantile:{n_groups,…}, portfolio:{sharpe,ann_return,max_drawdown,calmar,nav_series,benchmark_nav}, characteristics }`
**POST `/factor/compose`** — req `{ members:[expr], method:"equal", universe?, interpret:false }` → `{ status, composite:{…report} }`

---

## 6. universe 枚举(股票池)

`csi300_active`(沪深300 活跃)· `csi_fast`(快测小池,**默认**)· `csi300_2024h2` · `csi500` · `csi800` · `all`(全市场)· `etf` · `sample30`。
`source` 节点 `universe='自动'` 时按 `code` 关键词推断:含 500→`csi500`,含 800→`csi800`,含 300→`csi300_active`,scope=全市场→`all`,否则 `csi_fast`。

---

## 7. 配方模板(NL 目标 → 图 JSON)

### 模板 A — ML 预测因子 → 分析(已控制端验真)
目标:「反转因子 + XGBoost,看报告 沪深300」
```jsonc
{ "nodes": [
  { "id":"s",  "type":"source",   "x":60,  "y":250, "params":{ "universe":"csi300_active" } },
  { "id":"f",  "type":"formula",  "x":60,  "y":150, "params":{ "expr":"rank(-delta(close,5))" } },
  { "id":"fe", "type":"feature",  "x":342, "y":150, "params":{ "tag":"IC" } },
  { "id":"m",  "type":"xgb",      "x":624, "y":150, "params":{ "trees":120, "depth":3, "lr":0.08 } },
  { "id":"mf", "type":"mf",       "x":906, "y":190, "params":{} },
  { "id":"an", "type":"analysis", "x":1188,"y":210, "params":{ "groups":10 } }
], "edges": [
  { "from":["f","out"],    "to":["fe","feat"] },
  { "from":["fe","fe"],    "to":["m","fe"] },
  { "from":["m","model"],  "to":["mf","m1"] },
  { "from":["fe","fe"],    "to":["mf","f1"] },
  { "from":["mf","factor"],"to":["an","factor"] }
] }
```

### 模板 B — LSTM 序列 → 回测
目标:「close 序列 LSTM,TopN30 回测」:把模板 A 的 `xgb` 换 `lstm`(params `{seq_len:10,hidden:32,layers:1,epochs:40}`),末端 `analysis` 换 `backtest`(params `{topn:30,cash:1000000}`,入口 `factor`)。

### 模板 C — 纯因子 → 分析(无 ML)
目标:「换手率因子做 IC 体检」:`formula(-rank(turnover_rate))` → `feature` → `spearman` → `analysis`。
(注:单特征的 `pca`/`spearman` 退化为该因子自身的报告;多因子合成走 `mf` 或在 `formula` 写好复合表达式。)

---

## 8. LLM 出图自校验清单

1. 每个 `node.type` ∈ §2 目录(共 17 种)。
2. 每条 edge `from.dt === to.dt`(§3 表),端口 id 准确。
3. 至少一个**终端节点**(`analysis`/`iccalc`/`backtest`)。
4. ML / PCA / Spearman 节点的入口是 **`fe`** → 上游必须有 `feature` 节点(不能让 `formula` 直连模型)。
5. **`model` 要分析/回测,必须经 `mf`**(model 不能直连 factor 口)。
6. `formula.expr` 只用 §4 字段 + 算子,不含 `__`/`import`/`lambda`;`universe` ∈ §6。
7. 放一个 `source` 节点设 universe(或省略 → `csi_fast`);universe 在全图统一。
