# 统一深度学习集成层(DL Ensemble Layer)· 设计文档

**日期**:2026-06-22
**状态**:设计已确认,待写实施计划
**前置**:DL 使用状态审计(6-agent workflow,2026-06-22)—— 结论:生产选股唯一引擎是 LightGBM v4(传统树);纯 DL(LSTM/MLP)是工作流研究节点零生产调用;FinCast(深度生成 FM)的 B3 集成已接进 v4 read-side 但当前 `v4_b3_provenance.json` = `active:false`(预测停更→退化纯 LGB)。
**相关**:[[quant-wiki-gap-audit]](#7 FinCast 在线化)、[[backtest-cards-design]](P3 加权混合)

---

## 1. 背景与目标

选股的因子 + LightGBM(v4)已投入生产;深度学习仍处初级——LSTM/MLP 只在工作流画布里能跑(无产物、无生产调用),FinCast 虽接进 v4 却休眠。本项目把「让搭建好的 DL 模型参与选股」从单源 FinCast **泛化成一个统一的多源 DL 集成层**:任何 DL 模型按统一契约离线产出预测表,即可加权混进 v4 排名,并在选股界面显形其参与。

**目标(Phase 1)**:建后端统一 DL 集成层(多源预测表契约 + 加权 z 混合进 v4_total + 泛化 provenance)+ 选股界面 DL 显形;以 **FinCast 为首个真实源**端到端验证(复活它)。LSTM 升格作 Phase 2。

**诚实铁律**:serving 只读缓存,**绝不在 HTTP 请求里跑模型/GPU**(沿用 FinCast 命门);LGB 永远 ≥0.5 权重(可解释引擎主导);无预测/匹配不足→诚实退纯 LGB(字节等价);PIT 透传 look-ahead 标注。

## 2. 范围

**做(Phase 1)**
- 新 `guanlan_v2/strategy/compute/dl_ensemble.py`:DL 源契约 + 多源加权混合 + 泛化 provenance
- `v4.py`:`build_v4` 接受 `dl_sources`,调 `apply_dl_ensemble`(无则回退现有单源 `fincast_path`,向后兼容)
- `regen.py`:建源注册表传入;写 `v4_dl_provenance.json`
- `screen/api.py`:读 `v4_dl_provenance.json`(回退旧 `v4_b3_provenance.json`)→ 暴露多源 `v4_provenance`
- 前端 `screen-app.jsx`:DL 徽章泛化成多源显形
- FinCast 复活(离线预测→sync→regen)作端到端验证

**不做(Phase 2 / 范围外)**
- 工作流 LSTM 升格(新建 LSTM 离线训练+预测生产器、PIT、持久化)
- 其它 DL 模型(MLP/Transformer/新 FM)的离线生产器
- 落子 decide 注入 DL / 研报接 DL
- DL 模型自身的训练/调参(本层只消费预测表,不训练)

## 3. 架构 / 数据流

```
离线 regen(非请求路径):
  各 DL 模型离线产「预测表」(契约见 §5)→ var/dl_pred_<model_id>.parquet
       ↓
  build_v4(..., dl_sources=注册表) → apply_dl_ensemble(pred, ld, sources, data):
       多源加权 z 混合写回 pred['score']:  mixed = w_lgb·z(LGB) + Σ wᵢ·z(DLᵢ)
       ↓
  泛化 provenance(每源 active/weight/n_matched/lookahead/reason)→ var/v4_dl_provenance.json
       ↓
serving(请求路径,只读):
  /screen/run → load_v4_ranking 读混合后的 v4_ranking_latest.parquet(已含 DL 贡献)
  /screen 响应附 v4_provenance(读 v4_dl_provenance.json)→ 前端徽章显形每个 DL 源
```

**命门**:`apply_dl_ensemble` 与所有源加载只 `pd.read_parquet`,绝不跑模型;`build_v4` 由 regen 离线调用;在线服务读缓存。

## 4. 组件详述

### 4.1 新 `guanlan_v2/strategy/compute/dl_ensemble.py`

通用多源 DL 集成层。复用 `v4_fincast.py` 的 `_zscore` / `recent_fc_icir` / `_adaptive_w_fc`(import 复用,不复制)。

- **`DLSource`(契约,dataclass 或 dict)**:`{model_id: str, path: str, score_col: str, weight_mode: "adaptive"|"fixed", fixed_w: float|None}`。`score_col` 默认 `pred_ret_5d`。
- **`default_dl_sources() -> list[DLSource]`**:返回配置的源。Phase 1 只含 FinCast:`{model_id:"fincast", path: var/v4_fincast_pred.parquet, score_col:"pred_ret_5d", weight_mode:"adaptive"}`。LSTM 等以注释占位,Phase 2 加一行。
- **`_load_dl_for_date(path, ld, score_col) -> (series|None, df|None, cutoff|None, reason|None)`**:泛化 `_load_fincast_for_date`(列名参数化:`eval_date`/`instrument`/`score_col`[+可选 `train_cutoff`];缺文件/缺列/无当日/读失败→诚实 None+reason)。
- **`dl_mix_scores(score_lgb, dl_scores: dict[model_id→series], weights: dict[model_id→float], min_match) -> (mixed, info)`**:多源 z 混合(§6)。每源 reindex 到 LGB 索引,`<min_match` 的源退出(weight=0);`mixed = w_lgb·z(LGB) + Σ wᵢ·z(DLᵢ)`,`z` 与 fillna 镜像 `b3_mix_scores`(`z_fc = _zscore(fc.fillna(fc.mean()))`)。**单源时与 `b3_mix_scores` 输出字节等价**(回归守护)。
- **`apply_dl_ensemble(pred, ld, sources, data=None, min_match=50) -> dict`**:编排——逐源 `_load_dl_for_date` + 自适应权重(给 `data['label']` 才算 ICIR,否则默认)+ 总权重封顶 + `dl_mix_scores` 写回 `pred['score']` + 返回泛化 provenance(§7)。任一异常该源退出,绝不拖垮排名。

### 4.2 `v4_fincast.py`(保留,降为 FinCast 源适配器)
不删 `apply_fincast_ensemble` / `b3_mix_scores` / `recent_fc_icir` / `_adaptive_w_fc` / `_zscore` / `_load_fincast_for_date`(旧 `test_v4_fincast.py` 继续绿)。`dl_ensemble.py` 复用其 helpers。

### 4.3 `v4.py`(build_v4)
`v4.py:229` 签名加 `dl_sources: Optional[list] = None`。`v4.py:279-288` 块改:`dl_sources` 给 → `apply_dl_ensemble(pred, ld, dl_sources, data=data)`;否则 `fincast_path` 给 → 现有 `apply_fincast_ensemble`(向后兼容);都无 → 跳过(纯 LGB,字节等价)。provenance 写进传入的 `b3`/新 out-dict。

### 4.4 `regen.py`
`regen.py:180-194`:建 `dl_sources = default_dl_sources()` 传入 `build_v4`;写 `var/v4_dl_provenance.json`(`{date, **dl_prov}`)。`v4_b3_provenance.json` 不再主写(screen 回退仍能读旧档,过渡安全)。

### 4.5 `screen/api.py`
`screen/api.py:851-861`:优先读 `v4_dl_provenance.json`(多源),不存在则回退旧 `v4_b3_provenance.json`(单 FinCast 格式),暴露成 `v4_provenance`。

### 4.6 前端 `ui/screen/screen-app.jsx`
现有单 FinCast 徽章泛化:读 `v4_provenance.sources[]`,渲染 `v4 · LGB + FinCast(w0.17) + …`,每源带 active/weight/⚠前视 title;无源活跃→「v4 · 纯 LGB」;null→不显。诚实可区分(沿用现有徽章样式)。

## 5. DL 预测表契约(统一)

扁平列(离线工具产出,镜像 FinCast):
- `eval_date`:预测评估日(YYYY-MM-DD 或 Timestamp)
- `instrument`:代码(qlib 口径,如 `SZ000001`,与 build_v4 一致)
- `<score_col>`:模型分(默认 `pred_ret_5d`,未来 5 日收益预测;可配)
- `train_cutoff`(可选):ckpt 训练截止日 → 用于 PIT look-ahead 标注

落点 `var/dl_pred_<model_id>.parquet`(FinCast 沿用现有 `var/v4_fincast_pred.parquet`,其 model_id="fincast" 的 path 指它)。

## 6. 多源权重(诚实铁律:LGB 主导)

- 每源自适应权重 `wᵢ = _adaptive_w_fc(ICIRᵢ) ∈ [0.1, 0.5]`(给 `data['label']` 才算近 20 日 RankICIR;否则 `DEFAULT_W_FC=0.4`);`weight_mode="fixed"` 则用 `fixed_w`。
- **总 DL 权重封顶 `MAX_TOTAL_DL_W = 0.5`**:`Σwᵢ > 0.5` → 按比例缩到和为 0.5;`w_lgb = 1 − Σwᵢ ≥ 0.5`。
- 某源当日匹配 `n_matched < MIN_MATCH(50)` → 该源退出(wᵢ=0,reason 记),不拖累其它源与 LGB。
- 所有源都退出 → 纯 LGB(active=False,字节等价)。
- **单源(仅 FinCast)时**:封顶不触发(单源 w≤0.5),= 现有 `b3_mix_scores` 行为(字节等价,测试守护)。

## 7. Provenance 格式(`v4_dl_provenance.json`)

```json
{
  "date": "2026-06-20",
  "active": true,
  "w_lgb": 0.71,
  "sources": [
    {"model_id": "fincast", "active": true, "weight": 0.29, "n_matched": 5033,
     "n_total": 5719, "lookahead": null, "fc_icir_recent": -0.146,
     "reason": "B3 集成启用:w=0.29(5033/5719 有预测)"}
  ],
  "reason": "DL 集成:LGB 0.71 + 1 源(fincast 0.29)"
}
```
`active` = 任一源活跃;无源活跃时 `active:false, w_lgb:1.0, sources:[每源 active:false+reason]`。

## 8. FinCast 复活(端到端验证;**层 producer-agnostic**)

> **范围注**:把 FinCast 生成栈港进 guanlan(4GB 权重 + adapter + GPU)是**独立的 Spec 2**(见 §13),不在本 spec。本 spec 的集成层只读预测表、不在乎预测从哪来;验证沿用**现有生产器**(stocks 侧 GPU 跑 + `sync_fincast` 搬过来)。

- 跑离线 GPU 预测(stocks 侧 conda `stocks` 环境,`fincast_daily_predict.py --date <total_mv 覆盖日>`)→ `python scripts/sync_fincast.py` → `regen <date>`。
- 验证:`v4_dl_provenance.json` `active:true`、FinCast 源 weight>0、n_matched 合理;live `/screen` 的 `v4_provenance` 显 FinCast 参与;前端徽章实心显形。
- **依赖**:stocks 侧 GPU/conda 可用(2026-06-15 跑过,RTX 5090,可行)。**降级路径**:若实施时 GPU 不可用,用现有(可能陈旧的)`var/v4_fincast_pred.parquet` 验证混合机制 + provenance + UI(active 取决于是否有当日预测,无则诚实显「纯 LGB」),复活预测留 ops 步。

## 9. PIT / 诚实合约(红线)

1. **serving 零模型推理**:`apply_dl_ensemble` 与源加载只 read_parquet;build_v4 由 regen 离线调。
2. **LGB ≥0.5 主导**:总 DL 权重封顶 0.5,可解释引擎不被 DL 淹没。
3. **诚实退化**:无文件/无当日/匹配不足/异常 → 该源或整体退纯 LGB,字节等价旧行为。
4. **PIT look-ahead**:每源透传 `train_cutoff`,`lookahead = (eval_date ≤ cutoff)`,UI ⚠ 显形。
5. **DL 是补充非替代**:方向以实测验真,不预设 DL 一定更好。

## 10. 测试(TDD)

新 `tests/test_dl_ensemble.py`:
- `dl_mix_scores` 单源与 `b3_mix_scores` **字节等价**(同 lgb/fc 输入,allclose)——核心回归守护
- 多源混合:2 源各 z 加权,`w_lgb = 1−Σwᵢ`
- 总权重封顶:2 源各 0.4 → 缩到和 0.5(各 0.25),`w_lgb=0.5`
- per-source 退化:一源匹配 <50 退出(weight=0)、另一源仍活、LGB 不变
- 全退化:所有源 <50 → 纯 LGB(score 原样)
- `apply_dl_ensemble`:tmp parquet 多源 → 写回 pred['score'] + provenance sources[] 正确;缺文件源诚实 inactive
- provenance 格式:active/w_lgb/sources[] 字段齐
旧 `test_v4_fincast.py` 保持绿(FinCast 适配器不破)。

## 11. 验证(真数据)
- regen(含 FinCast 源)在 csi300/全市场跑通,`v4_dl_provenance.json` 产出;live `/screen` 带多源 `v4_provenance`;前端徽章渲染。
- 旧路径回归:无 dl_sources/无预测 → v4 排名与纯 LGB **字节等价**(`v4_ranking_latest.parquet` 不变)。

## 12. 风险与坑
- **FinCast 复活的 GPU 依赖**(§8 降级路径已备)。
- **provenance 迁移**:`v4_dl_provenance.json`(新)vs `v4_b3_provenance.json`(旧)——screen 回退读旧档保过渡;regen 重跑后产新档。
- **engine/serving 改动须重启 9999** 拉新代码。
- **字节等价是硬约束**:单源 FinCast 与旧行为必须 allclose,否则破坏已验证的 v4 排名。
- **薄数据**:FinCast 预测覆盖/新鲜度影响 active;诚实显形,不伪造。
- **测试用引擎 fork 路径**(`tests/conftest.py` prepend engine)。

## 13. 后续(独立 spec,「guanlan 自有 DL 生产器」系列)

本 spec(集成层)producer-agnostic;以下生产器各自独立立项,都把同契约预测表写进 `var/dl_pred_<model_id>.parquet` 并加进 `default_dl_sources()` 即接入:

- **Spec 2 · FinCast 生成港进 guanlan**(用户已定本项目要做,层之后接着做):vendor `FinCastAdapter` + FinCast 架构代码、**3.97 GB 权重**(`Vincent05R/FinCast` 的 `v1.pth`,放 guanlan `vendor/`/models 目录·gitignore 不入库)、`exp_config` 常量(context_len=512/horizon=5/batch=64)、改读 **guanlan 自己的 close**(原脚本走 qlib `D.features($close)`)、GPU 推理脚本直接写 `var/v4_fincast_pred.parquet` → 去掉 stocks 依赖 + sync。**重(4GB 模型 + GPU + 自定义推理代码 vendoring),故独立成 spec。**
- **Spec 3 · 工作流 LSTM 升格**:LSTM 离线训练+预测生产器(产 `var/dl_pred_lstm.parquet` 同契约)、PIT(序列窗不看未来)、持久化。
- 其它 DL(MLP/Transformer/新 FM)同理(加注册项 + 离线生产器)。
