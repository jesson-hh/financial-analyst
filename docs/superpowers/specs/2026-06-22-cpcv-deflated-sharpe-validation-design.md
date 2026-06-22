# CPCV + Deflated Sharpe 验证层 设计(MVP-A)

- **日期**:2026-06-22
- **状态**:② 已实现并真机验证(feat/cpcv-validation);① 据此 reconcile(**见 §12,口径优先于上文 v4-only 措辞**)→ 转 writing-plans
- **关联**:`[[v4-model-workshop]]`、`[[backtest-cards-design]]`、`[[quant-wiki-gap-audit]]`;记忆 `rl-for-stock-selection-research`(本功能是 RL 研究里"GPU/算力先用于严格验证"结论的第一步落地)
- **背景研究**:工作流 run `wf_49c55fce-0c7`(RL 研究)、`wf_d146fcaf-b1b`(GPU 重估)

---

## 1. 背景与动机

现状:v4 = LightGBM 预测 5 日前向收益,验证只有**单一末尾留出**(`model_train.holdout_split`:最后 20 个交易日 + purge 5)产出一个 IC/IR 数字。两个隐患:

1. **一条路径 = 又少又靠运气**:那 20 天可能正好是某种行情;单数字说明不了"多种行情下稳不稳"。
2. **多重检验偏差**:模型工坊里训过很多变体、试过很多因子组合,挑"最好"的那个 → 越试越可能挑中运气。回测好看、实盘崩,头号死法。

`model_health.py` 现有产物已诚实区分"回看 IC(训练窗内·偏乐观)"与"vintage 真 OOS IC",但都还是**点估计**,且没有对"试了很多次"做校正。

本功能补一个**独立验证层**:把"一个可能撞运气的点"换成"**一个分布 + 一个经过运气校正的显著性**",且**完全不碰交易信号生成**(纯测量)。这是后续任何新模型(深度模型 MVP-B、RL MVP-D)的统一验收闸门。

**为什么是第一步(最高 ROI)**:不碰信号、零风险;先确认 v4 的边是真的还是回测幻觉,再谈在其上叠任何东西;直接打在量化头号死因(回测过拟合)上;算力密集、高度并行(但本功能 CPU 即可,GPU 留给 MVP-B 深度模型)。

---

## 2. 目标与非目标

**目标**
- 两档验证:**快速档**(秒级、复用已积累的真 OOS 冻结快照、零重训、不动 build_v4)+ **严格档**(~1h、全历史 retrain-CPCV、可选按需)。
- 两档都产出:标准化模型组合的**夏普分布** + **Deflated Sharpe(DSR)** + IC 分布;严格档另产 **PBO(回测过拟合概率)**。
- 结果落独立 artifact,呈现在模型工坊抽屉(每变体 DSR/PBO 徽章 + 「严格验证」按钮)与 TopBar 体检卡。

**非目标(YAGNI,留以后)**
- 深度模型 / RL 接入(MVP-B / MVP-D)。
- 把验证当"硬闸门"自动卡变体上线(本期只展示,不自动拦)。
- 超参自动寻优、组合优化器、N/k 可视化调参面板(本期固定 N=6/k=2,代码留参数入口)。
- GPU 加速(本功能 CPU 即可;LightGBM GPU 已确认不值)。

---

## 3. 已锁定的设计决策

| # | 决策 | 选定 |
|---|---|---|
| 1 | 评估指标 | **模型多空组合夏普 + IC**(config-free,不依赖 blend/topN/行业约束);细化:**头条=多头超额夏普**(top decile − 全域等权,贴 A 股可交易),**多空价差**(top−bottom decile)作信号单调性诊断,**RankIC** 作预测力诊断 |
| 2 | CPCV 切分 | **N=6 段 / k=2** → C(6,2)=**15 条路径**(严格档);CPU,~1~1.5h |
| 3 | 净化/隔离 | **purge=5 交易日**(=标签窗,红线不可改)+ **embargo=5 交易日** |
| 4 | DSR 试验数 N | **= model_registry 变体数**,带下限(≥路径数),并诚实标"这是下界"(因子/blend 微调未全入账) |
| 5 | 触发/呈现 | 模型工坊**按需异步按钮**(严格档,镜像 `/model/train`);快速档秒级可自动算并显示;结果显示在模型工坊每变体旁(DSR/PBO 徽章)+ TopBar 体检卡 |
| 6 | 范围 | **两档都做**(快速默认 + 严格可选) |
| 7 | 架构 | **方案 1**:抽出 v4 训练核心 + 新建 cpcv.py + model_health 存/读 + 异步端点 + 前端填充 |

---

## 4. 架构总览

```
                   ┌─────────────────────────────────────┐
  快速档 ──────────┤  cpcv.py  (验证引擎 · 单一职责)        │
  (秒级·读快照)     │  • quick_validate:读冻结快照→分布+DSR │
  严格档 ──────────┤  • strict_validate:15路径重训→        │
  (~1h·retrain)    │      分布+DSR+PBO                     │
                   │  • 公共:多空组合/IC/DSR/PBO 计算      │
                   └───────────────┬─────────────────────┘
                                   │ 落 model_cpcv.parquet/.json
                   ┌───────────────▼─────────────────────┐
                   │  model_health.py (存/读)              │
                   │  + write_cpcv / load_cpcv_summary     │
                   └───────────────┬─────────────────────┘
        /screen/run 带回快速摘要 ───┤       │ 异步端点
                   ┌───────────────▼──┐  ┌──▼───────────────┐
                   │ TopBar 体检卡     │  │ screen/api.py     │
                   │ 分布+DSR+PBO 徽章  │  │ POST /model/validate│
                   └──────────────────┘  │ GET  /model/validate/status│
                   ┌──────────────────┐  └───────────────────┘
                   │ 模型工坊抽屉       │  快速档内联(秒级);
                   │ 「严格验证」按钮    │  严格档子进程+轮询
                   │ + 变体旁 DSR/PBO  │
                   └──────────────────┘
```

依赖关系:`cpcv.py` → 读 `model_health` 三产物(快速档)/ 调 `v4` 训练核心(严格档);`model_health` 存读 artifact;`screen/api` 调 cpcv + 起子进程;前端读 `/screen/run` 与 `/model/validate/status`。

---

## 5. 组件与接口

| 组件 | 文件 | 职责 | 改动 |
|---|---|---|---|
| 验证引擎 | `guanlan_v2/strategy/compute/cpcv.py` | split 生成 + purge/embargo + 两档入口 + 多空组合/IC/DSR/PBO | **新建** |
| 训练核心 | `guanlan_v2/strategy/compute/v4.py` | 抽出"显式 train行/test行 → 训 LGB、出 test 段 lgb_pct"核心;**build_v4 末尾留出路径零改** | 加能力 + 守卫 |
| 存/读 | `guanlan_v2/strategy/model_health.py` | `write_cpcv()` / `load_cpcv_summary()`,落 `model_cpcv.parquet` + 摘要 | 加函数 |
| 异步端点 | `guanlan_v2/screen/api.py` | `POST /model/validate`(严格档子进程,镜像 `_run_model_train_subprocess`)+ `GET /model/validate/status`;快速档摘要进 `/screen/run` 与 `/model/status` 回包 | 加端点 |
| 子进程入口 | `cpcv.py __main__` | `python -m ...compute.cpcv <spec.json>`(严格档子进程跑) | 新建 |
| 前端 | 模型工坊抽屉 + TopBar 体检卡 | 「严格验证」按钮 + 进度 + 每变体 DSR/PBO 徽章 + 体检卡分布 | 填充现有 UI |

**核心接口契约:**
```python
# 快速档:纯读冻结快照,秒级,零看未来
cpcv.quick_validate(model_id: str | None = None) -> dict
#   -> {sharpe, dsr, ic_dist:[...], n_oos_days, ready: bool, note}
#   ready=False(天数<阈值)时诚实显"证据不足,随时间变厚"

# 严格档:全历史 retrain-CPCV,~1h
cpcv.strict_validate(model_id: str | None = None, n_groups=6, k=2,
                     purge=5, embargo=5) -> dict
#   -> {paths:[{test_groups, sharpe, ic}...], sharpe_dist:{median,std,p05,p95},
#       dsr, pbo, n_trials, asof, note}

# v4 训练核心(抽出;严格档每路径调一次)
v4.train_predict_core(provider, feature_cols, extra_panel,
                      train_mask, test_mask) -> Series[lgb_pct on test rows]
```

---

## 6. 数据流(两档)

**快速档(秒级,零重训,零看未来):**
1. 读 `model_score_history.parquet`(每日冻结 lgb_pct 快照)+ `model_vintage_ic.parquet`(已实现真 OOS IC)。
2. 每个**已实现**快照日:按 lgb_pct 取 top decile 等权 → 5 日前向收益;减全域等权 = 多头超额(快照分数冻结于预测时点 = 真 OOS)。
3. 把已实现 OOS 天数切成组合子区间 → 夏普分布 + DSR;IC 分布取自 vintage。
4. 诚实门:OOS 天数 < 阈值(沿用 vintage 的 ≥10 天)→ `ready=False`,不强出数。
5. **限制(诚实标注)**:快速档只覆盖已积累天数、单模型谱系;**PBO 跨变体需严格档**(快速档只给单模型子区间一致性,不冒充 PBO)。

**严格档(~1h):**
1. 取全历史(start=2022-01-01 → `_latest_trade_date`)交易日 → 切 6 段 → C(6,2)=15 组合。
2. 每组合:测试段 = 选中 2 段;训练段 = 其余;每个 train/test 边界做 purge=5(挖标签窗重叠样本)+ embargo=5。
3. 调 `v4.train_predict_core`:train 段训 LGB → test 段出 lgb_pct → 建 top-decile 多头超额收益 + RankIC。
4. 15 条路径聚合 → 夏普/IC 分布(median±std,p05/p95)+ DSR + PBO。
5. 落 `model_cpcv.parquet`(逐路径)+ 摘要 json。

---

## 7. 指标定义(诚实口径)

- **标准化组合(config-free)**:按 lgb_pct **top decile(前 10%)等权多头**,**非重叠**每 5 个交易日换仓(持有 horizon=5 日)→ 收益序列(非重叠保证观测近似独立,DSR/夏普口径干净)。
  - **头条指标 = 多头超额** = top-decile 收益 − **全域等权**收益(全域 = 当日有 lgb_pct 的全部票);A 股做空难,长多可交易、相关。夏普按 5 日持有周期年化(√(年交易日/5))。
  - **多空价差** = top−bottom decile(学界标准,不可交易,作信号单调性诊断)。
- **RankIC** = lgb_pct 与未来 5 日收益的截面 spearman(预测力诊断)。
- **Deflated Sharpe Ratio(DSR)**(Bailey & López de Prado):输入 = 组合夏普、收益偏度/峰度、样本长度 T、试验数 N(决策 4)、试验间夏普方差(由路径/变体夏普估);输出 = **"真夏普 > 0 的概率"**。样本不足时诚实显低置信。
- **PBO(回测过拟合概率)**:**仅严格档**——多配置在同区间的组合收益矩阵 → 组合式 IS/OOS 切分 → IS 最优配置落到 OOS 中位数以下的比例。

---

## 8. 错误处理与红线

- **PIT 不看未来**:快速档天生零看未来(快照冻结);严格档 **purge 必须完整覆盖 5 日标签窗** + embargo,边界泄漏在单测里钉死。**绝不**用"当前已训模型预测历史"(那是泄漏)。
- **诚实缺席**:产物缺 / OOS 天数不足 / 截面太薄(< 100 票,沿用现有阈值)→ 返回 None 或 ready=False,前端不显卡,绝不编数。
- **产物只读**:验证只读快照与历史,**绝不改写** `model_health` 既有三产物;CPCV 落独立 `model_cpcv.parquet`。
- **零变化守卫**:`build_v4` 默认调用(末尾留出路径)结果逐位不变(回归测试钉死)。
- **不碰交易信号**:CPCV 不进 `/screen` 选股逻辑、不改 blend/排名,纯测量。
- **看门狗**:严格档子进程 ~1h,沿用 9999 看门狗模式;异常 fail 显形,不静默吞。

---

## 9. 测试计划(TDD)

单测(`tests/`,引擎 fork 路径,见 conftest 顶层 prepend engine):
1. **split 生成**:N=6/k=2 出 15 组合;每组合 train∩test=∅;并集覆盖全历史。
2. **purge/embargo 正确性**:构造已知重叠样本,断言标签窗(5 日)重叠的训练样本被挖、embargo 段被剔。**(防泄漏命门)**
3. **DSR 已知值**:对合成收益(已知夏普/偏度/峰度/N)断言 DSR 命中解析值。
4. **PBO**:合成"过拟合配置"(IS 最优、OOS 垫底)→ PBO 高;"稳健配置"→ PBO 低。
5. **快速档**:合成 `model_score_history` / `model_vintage_ic` parquet → `quick_validate` 出夏普 + DSR;天数不足 → `ready=False`。
6. **serving 摘要**:`load_cpcv_summary` 缺产物 → None。
7. **build_v4 零变化守卫**:抽核前后同输入逐位一致。

---

## 10. 实施风险与缓解

- **风险:抽 v4 训练核心**——`build_v4` 现仅支持末尾留出(`holdout={"k","horizon"}`),CPCV 需任意 train/test 掩码。
  - **缓解**:把 LGB 训练+预测步抽成 `train_predict_core(...,train_mask,test_mask)`;`build_v4` 内部改为"末尾留出 = 构造对应掩码后调核心",对外签名/默认行为零改 + 第 9.7 守卫测试。先读 `v4.py` 确认特征面板/LGB 步可干净抽出,不行则降级为"在 v4 内加显式 mask 分支"(仍守零变化)。
- **风险:严格档 ~1h 子进程**——沿用 `_run_model_train_subprocess` 同款线程 + 子进程 + 状态机 + 9999 看门狗。
- **风险:快速档数据浅**——诚实 `ready` 门,随 regen 天数积累自然变厚(同 vintage 逻辑)。

---

## 11. 验收标准

1. `quick_validate` 在真实已积累快照上秒级返回夏普 + DSR + IC 分布;天数不足时 `ready=False`。
2. `strict_validate` 在真 LGB 上跑完 15 路径,返回夏普分布 + DSR + PBO;落 `model_cpcv.parquet`。
3. 模型工坊「严格验证」按钮异步跑通 + 轮询进度;每变体显示 DSR/PBO 徽章;TopBar 体检卡显示分布。
4. 全部单测(第 9 节)绿;`build_v4` 零变化守卫绿。
5. 红线核验:purge 覆盖 5 日窗(单测证)、产物只读、不碰 `/screen` 信号、诚实缺席。

---

## 12. 与 ②(统一注册表)的衔接 —— 最新口径,优先于上文 v4-only 措辞

② 已实现(`feat/cpcv-validation`):`model_registry` 带 provenance(`source/kind/recipe/retrainable`),工作流模型经 `/model/promote` 入库,`load_v4_ranking` 变体加载补齐 V4_COLUMNS。① 据此从"只验 v4"升级为"**验证任意 registry 模型**":

- **验证对象** = 任意 registry 模型(prod v4 + 工坊 v4 变体 + 工作流 lightgbm/xgboost/rf),由 `model_id` 指定。
- **两档边界 = `retrainable`(② 写入 meta):**
  - **快速档**:适用任何**有冻结快照**的模型(读 `model_score_history`/`model_vintage_ic`)。现状:只有 prod v4 在 regen 时 `append_score_history` 积累快照;变体/工作流模型暂无逐日快照 → 对它们 `ready=False`(诚实"未积累快照",非 bug;让 regen 给选中变体也积累快照=挂账,不在 ①)。
  - **严格档**:适用 `retrainable=True`(② 保证有 `recipe`)的模型。retrain 不再只 v4,改为 **`retrain_core(kind, recipe, train_mask, test_mask)` 按 kind 分派**:
    - `kind="v4-lgb"`(prod/工坊变体)→ 抽出的 `v4.train_predict_core`(§5/§10)。
    - `kind ∈ {lightgbm,xgboost,rf}`(工作流模型)→ 复用 `workflow.api._materialize_xy`(recipe→`ModelTrainIn`)+ `_build_model`,在 train_mask fit、test_mask predict → lgb_pct;**与 `compute/model_workflow.train_promote` 的 fit/predict 抽成共享 helper,避免两份**。
    - `retrainable=False`(老变体无 recipe)→ 严格档诚实拒绝(`note="无 recipe,不可重训"`),只给快速档。
- **端点**:`POST /model/validate {id, tier:"quick"|"strict", n_groups, k, purge, embargo}` + `GET /model/validate/status`;`id` 默认 prod。
- **不变**:purge=5/embargo=5、N=6/k=2、指标(多头超额夏普+多空价差+RankIC+DSR+PBO)、DSR 试验数=registry 变体数、全部红线(PIT/产物只读/不碰选股算法/`build_v4` 零变化)沿用上文。
- **并发**:① 实施在基于 `feat/cpcv-validation` 的独立 git worktree 做,不占主工作树(让给并发 dl-ensemble 会话)。
