# 变体五维评级 + 市场温度进决策层 Implementation Plan

> **For agentic workers:** 本计划由 Workflow 串行流水线执行(impl→对抗评审→修复 per task),分支 `feat/variant-fivedim-market-temp`。

**Goal:** ① 变体(工作流模型)也产五维评级(v4_total/v4_layer)→ 选股页 ② 决策不再恒空;② 决策层接入市场情绪数据接口(全球情绪温度计/打板温度/大盘资金流/LLM大盘判读)作护盾 v4.4。

**Architecture:**
- 五维中 factor/technical/volume/utility 四维=股票属性(全市场因子截面分位),model 维=模型自身分位。prod regen 顺手落分项侧产物 `v4_dims_latest.parquet`(code/date/layer/mc/fs/ts/vs/ud/eligible);变体 train_promote/retrain 时 join 它(**日期必须相同**,否则诚实跳过)+ 自身 lgb_pct 映射 ms → 变体 parquet 升 7 列 == prod 契约。`/screen/run` 零改动自然走 v4_rated 池。
- 市场温度:新 `guanlan_v2/screen/market_temp.py` 组装 4 块上下文(全读缓存/快照,请求路径零网络阻塞,缺块=None 诚实);`decision.converge` 加 `market_temp=None` 参(缺省行为逐位不变):risk_off → 仓位区间减半+护盾警示;overheat → 分化警示;**绝不动星级/排序/剔票**(playbook:勿用滞后单维否决前瞻)。

**红线:**
- prod 排名输出逐位不变(`_score_top200` 不动,dims 走新函数 `compute_dims`)。
- dims 与变体排名日期不同 → 不附着,meta.v4_rating 诚实 reason;绝不跨日冒充。
- 市场温度任一源缺失 → 该块 None + note;全缺 → gate=None 护盾休眠。
- 决策层护盾只调仓位档(band)与警示,不改 stars/不剔票。
- 绝不 `git add -A`;不碰并发遗留未跟踪文件。

---

### Task 1: dims 侧产物(compute_dims + build_v4 out-param + regen 落盘)
**Files:** `guanlan_v2/strategy/compute/v4.py`(新 `compute_dims`,`build_v4` 加 `dims=None` out-param)/ `guanlan_v2/strategy/paths.py`(`V4_DIMS_PARQUET`)/ `guanlan_v2/strategy/compute/regen.py`(`_write_atomic` dims,失败不阻断)/ 测试 `tests/test_v4_dims.py`。
- `compute_dims(pred, name_map)`:全截面向量化,分位语义 == `_score_top200` 的 `(x < v).mean()`(rank(method="min")-1)/n,NaN→0.5;fs 封 ±fc、ts ±2、vs、ud、mc、layer 同规则;eligible = mv>30亿 & 3<close<500 & 非ST & close/mv notna。

### Task 2: 变体附着五维(model_workflow.py)
**Files:** `guanlan_v2/strategy/compute/model_workflow.py` / 测试。
- train_promote 的 rank_df 补 `lgb_score`(原始预测值)+`lgb_rank`;`_attach_v4_dims(rank_df)`:dims 缺/日期不匹配 → `(原样, {attached:False, reason})`;匹配 → eligible∩榜内按 lgb_pct 降序取 200,ms=rp 阈值表(rp=1-lgb_pct;<0.05→2/<0.15→2/<0.3→1/<0.5→1/<0.7→0/<0.85→-1/else→-2,clip ±mc),v4_total=fs+ts+ms+vs+ud,v4_layer=layer;meta["v4_rating"]=info。附着失败绝不 fail 训练。

### Task 3: 市场温度上下文 + 护盾 v4.4(market_temp.py + decision.py + api.py)
**Files:** 新 `guanlan_v2/screen/market_temp.py` / `guanlan_v2/strategy/decision.py`(converge 加参)/ `guanlan_v2/screen/api.py`(组装传入)/ 测试。
- `build_market_temp()` 四块:global(macro snapshots.jsonl 末行:temps 均值+astock_temp+ts+stale_min,不调 build_pulse 防触网)/ board(read_tape derived:zt/zb/break_rate/promotion_rate+age)/ flow(fundflow read_live("industry").market 主力净额→亿+pulled_at)/ llm(sentiment.latest_market)。
- gate 规则(保守):risk_off = astock_temp≤25 或 主力净额≤-300亿;overheat = astock_temp≥85 且 break_rate≥0.35;astock_temp 与主力净额全缺 → gate=None;否则 neutral。
- converge(market_temp=None 缺省逐位不变):risk_off → final 各项 band lo/hi 减半(int)+ shield {id:"v4.4",level:"warn"};overheat → shield info 分化警示;decision["market_temp"]=market_temp;notes 加口径行。

### Task 4: 前端(DecisionPanel 市场温度条 + 变体诚实文案 + ?v bump)
**Files:** `ui/screen/screen-app.jsx` / `ui/screen/观澜 · 选股.html`(?v=20260711t8)。
- DecisionPanel 顶部市场温度条:全球温/A股打板温/主力净流/LLM判读 四格(缺=—,带龄期),gate 徽章(risk_off 红「风险规避·仓位减半」/overheat 金「过热分化」/neutral 灰);
- 旧 3 列变体空态文案改诚实:「该变体排名无五维评级(旧快照)→ 顶栏『↻ 重训到最新』重训后自动生成」;
- 变体有五维时小字口径:「五维评级 · model维=本变体分位 · 其余四维=全市场因子截面(与 prod 同日)」。

### Task 5: e2e(主会话手动)
套件全绿 → 合 main → 重启 9999 → 手动 regen 出 dims → 重训变体验 7 列 → 浏览器验 ② 决策 有持仓 + 市场温度条。
