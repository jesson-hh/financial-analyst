# 工作流 LSTM 升格为生产 DL 源(Spec 3)· 设计文档

**日期**:2026-06-24
**状态**:设计已确认(用户选 A = DL 集成源 + 两界面打通),待写实施计划
**前置**:[[dl-ensemble-layer]] Spec 1(统一 DL 集成层)+ Spec 2(FinCast 港 guanlan)已交付合本地 main。本 Spec 3 把工作流里的 **LSTM 研究节点**升格成**生产 DL 源**,像 LGB 一样在**工作流界面 + 选股界面打通**。

---

## 1. 背景与目标

工作流页 ([ui/factor/workflow.jsx]) 有个 LSTM 节点(`/model/lstm` → `_lstm_eval`),只做**请求期 OOS 评测**(训完即弃·零生产产物)。DL 集成层(Spec 1)producer-agnostic,只读 `var/dl_pred_<id>.parquet`(契约 `eval_date/instrument/pred_ret_5d`),`default_dl_sources()` 现仅 FinCast。

**目标**:把 LSTM 从「请求期评测节点」升格成**离线生产 DL 源**,产 `var/dl_pred_lstm.parquet` → 经 DL 集成层混进 v4(LGB 仍是底座,占 ≥0.5),并**像 LGB 一样两界面打通**:
- **工作流界面**:LSTM 节点保留 eval + 新增「发布为 DL 源 ⤓」按钮(镜像树模型的「存入模型库」),一键起离线生产训练 + 折进 v4。
- **选股界面**:LSTM 自动浮现在多源徽章 `v4 · LGB+fincast+lstm`(Spec 1 已建),排名真被三源混合驱动。

**LGB 的「打通」回路(本 Spec 镜像)**:工作流 ML 节点 train+eval → 「存入模型库」([workflow.jsx:1770-1805] 仅树模型)→ `/model/promote` 异步子进程生产重训 → 研究库 → 选股页 picker 选中 → `load_v4_ranking` 排名 → 体检/provenance 徽章。LSTM 今天只有 eval 那半,缺「发布 → 选股排名」那半 —— 本 Spec 补齐。

## 2. 范围与诚实边界

**做(本增量)**
- `guanlan_v2/strategy/compute/lstm_io.py`:纯函数(序列窗 + PIT 标签闸 + 截面预测输入·可 TDD·无 torch)
- LSTM 生产器 `scripts/lstm_predict.py`(guanlan 主 env·torch CPU·复用 `build_feature_panel` + `fincast_io.write_pred_rolling`)
- `fincast_io.write_pred_rolling` 小扩展:可选 `train_cutoff` 列(LSTM 诚实显形;FinCast 默认 None → 列不变·字节等价)
- `default_dl_sources()` 注册 lstm 源
- `/model/publish_dl`(+ `/model/publish_dl/status`)端点(workflow/api.py·镜像 `/model/promote` 异步子进程 + 单飞锁):训练 LSTM → 写 parquet → 跑 regen 折进 v4
- 前端 workflow.jsx:LSTM 节点「发布为 DL 源 ⤓」按钮 + 轮询 status(镜像树模型「存入模型库」组件)

**诚实边界(关键 —— LSTM 与 FinCast/LGB 的差异)**
- LSTM **是训练的**(有真 `train_cutoff`),**不是** FinCast 那样零样本(lookahead=null)。PIT 命门:`train_cutoff = eval_date − horizon`(最后一个前向收益已实现的标签日 < eval_date)→ provenance `lookahead = (eval_date ≤ cutoff) = False`(诚实无前视)。
- LSTM 是 **DL 源(混入 v4)**,**不是** LGB 那样的**变体排名(替换底座)**。它产 5 日收益预测,经集成层加权 z 混进 v4 score;LGB 恒 ≥0.5 主导;与 FinCast 共享 `MAX_TOTAL_DL_W=0.5` 封顶(两源总权重 >0.5 按比例缩)。
- **选股界面打通 = 复用 Spec 1 已建的多源徽章**(自动显 lstm 源 + tooltip 权重/匹配数/lookahead)。本 Spec **选股侧零必需新前端**;可选加一个 lstm 源「体检」小徽章作显著性(范围外·见 §9)。

**不做(范围外)**
- LSTM 当替换底座的变体排名(用户选 A 否决 B)
- GPU 训练(用户选 CPU·guanlan 主 env 自包含)
- LSTM 超参自动寻优 / 多变体注册表
- 选股页 lstm「体检」专用徽章(多源徽章 tooltip 已含 ICIR;独立 chip 留后续)

## 3. 架构 / 数据流

```
工作流界面(用户一键):
  LSTM 节点「发布为 DL 源 ⤓」→ POST /model/publish_dl {recipe(features/label/universe/params)}
    → 单飞抢锁 → 起子进程(异步,立即返回 + 轮询 /model/publish_dl/status):
        阶段1 训练:python scripts/lstm_predict.py --date <D> --universe <U> --seq-len ... --epochs ...
              build_feature_panel(训练 universe) → lstm_io.build_sequences(PIT 闸 t≤eval_date−horizon)
              → nn.LSTM 训练(CPU·seed 固定)→ build_feature_panel(全 universe)
              → lstm_io.predict_index(eval_date 截面)→ 预测 pred_ret_5d
              → fincast_io.write_pred_rolling(var/dl_pred_lstm.parquet, train_cutoff=eval_date−horizon)
              → 存 var/models/lstm/latest.pt
        阶段2 折进 v4:python -m guanlan_v2.strategy.compute.regen <D>
              (default_dl_sources 现含 lstm → apply_dl_ensemble 多源混合 → v4_dl_provenance.json)
    → 完成。重启 9999 刷 LRU(或提示用户)。

选股界面(读已 regen 的缓存):
  POST /screen/run → v4_provenance.sources 含 {fincast, lstm} → 多源徽章
  v4 · LGB+fincast(0.13)+lstm(0.11)  (Spec 1 已建·零新前端)

命门(同 FinCast):训练/推理只在离线子进程(workflow 端点起的 subprocess);9999 请求路径绝不训练或加载模型。
```

**为何 publish 链 regen**:DL 源经 regen 折进 v4 缓存排名(serving 零推理读缓存)。一键「发布」= 训练 + 折进 + 生效,才是「跟 LGB 一样打通」的完整回路(LGB 的 promote 也是一次异步子进程做完整生产重训)。训练 CPU 数分钟 + regen ~5min,异步 status 显两阶段。

## 4. 组件详述

### 4.1 `guanlan_v2/strategy/compute/lstm_io.py`(纯函数·可 TDD·无 torch)
抽 `_lstm_eval` 的序列/标签逻辑成可测纯函数(只 numpy/pandas):
- `build_sequences(panel, feature_cols, label_col, seq_len, horizon, cutoff) -> (X[N,seq_len,F], y[N], index[(date,code)])`:逐 code 按日期排序,每个标签日 t 取前 `seq_len` 期特征窗为样本、`label_col[t]`(前向 horizon 日收益)为目标;**PIT 闸**:只收 `realized_date = t + horizon ≤ cutoff` 的样本(防标签泄漏/前视)。窗内不足/特征 NaN 过多者剔。
- `predict_index(panel, feature_cols, seq_len, eval_date) -> (X_pred[M,seq_len,F], codes[M])`:取每 code 截至 `≤ eval_date` 的末 `seq_len` 期窗为预测输入(末值非空才入选);**绝不含未来**。
- 标签 = 前向 horizon 日收益(`close[t+h]/close[t] − 1`),由生产器在 panel 上预算列(或 lstm_io 提供 `add_forward_return(panel, horizon)` helper)。

### 4.2 `scripts/lstm_predict.py`(guanlan 主 env·CPU·CLI 可独跑,同 FinCast 范式)
- argparse:`--date`(缺省 `_latest_trade_date`)、`--universe`(训练池·默认 csi800)、`--seq-len/--hidden/--layers/--lr/--epochs`(对齐前端 SPECS.lstm)、`--horizon`(5)、`--batch-size`、`--min-valid-frac`、`--provider`、`--sample-cap`(训练行上限·守 CPU·默认 ~6000 同 `_SVM_TRAIN_CAP`)。
- 流程:`build_feature_panel(loader, 训练 codes, start, end)` → `lstm_io.add_forward_return` + `build_sequences(cutoff=eval_date−horizon)` → `nn.LSTM`(`torch.manual_seed(0)` 复现·CPU)训练 → `build_feature_panel(全 universe)` → `predict_index(eval_date)` → 预测 → `write_pred_rolling(OUT, eval_date, codes, preds, train_cutoff=eval_date−horizon)` + 存 `var/models/lstm/latest.pt`。
- 复用既有:`build_feature_panel`(v4.py·同款多因子 PIT 面板)、`list_all_instruments`、`_latest_trade_date`、`resolve_universe_codes`(训练池过滤)、`fincast_io.write_pred_rolling`。

### 4.3 `fincast_io.write_pred_rolling` 扩展(train_cutoff 列)
加可选参数 `train_cutoff=None`:若非 None,给写出表加常量列 `train_cutoff`(datetime64)。FinCast 调用不传 → 无该列 → 与现有字节等价(回归守护:现有 6 测不破 + 新增「带 cutoff 列」测)。`_load_dl_for_date` 已读 `train_cutoff` 列(line 84-89)→ provenance lookahead 自动显形。

### 4.4 `default_dl_sources()` 注册 lstm
在 [dl_ensemble.py:105] append:
```python
DLSource(model_id="lstm", path=str(var / "dl_pred_lstm.parquet"),
         score_col="pred_ret_5d", weight_mode="adaptive"),
```
parquet 不存在时 `_load_dl_for_date` 诚实返 missing → 源 inactive(不破现有·FinCast 单源仍正常)。两源都活 → `dl_mix_scores` 共享 `MAX_TOTAL_DL_W=0.5` 按比例缩,LGB ≥0.5 守住。

### 4.5 `/model/publish_dl`(+ status)· workflow/api.py(镜像 `/model/promote`)
- `POST /model/publish_dl {kind:"lstm", name, recipe:{features,label,fwd_days,universe,params}}`:校验 `recipe.features` 非空 → 单飞抢锁(新 `_PUBLISH_DL_LOCK`/`_PUBLISH_DL_STATE`,与 promote 锁独立)→ 起子进程(daemon thread)→ 立即返回 `{ok,started,state}`。
- 子进程(`_run_publish_dl_subprocess(spec)`,镜像 `_run_promote_subprocess`):阶段1 跑 `scripts/lstm_predict.py`(参数来自 recipe/params)→ 阶段2 跑 `regen <date>`;逐行回填 `state.lines/phase/step`;成功 `ok=True` 落 `var/models/lstm/latest.pt` + `var/dl_pred_lstm.parquet`。
- `GET /model/publish_dl/status` → `{ok, state}`(phase/label/step/lines/ok/error)。

### 4.6 前端 workflow.jsx「发布为 DL 源 ⤓」按钮
镜像树模型「存入模型库」组件([workflow.jsx:1770-1805]):**仅 LSTM 节点渲染**。据上游静态导出 recipe(features/label/fwd_days/universe/params)→ `POST /model/publish_dl` → 轮询 `/model/publish_dl/status` 至 done → notify(「已发布 DL 源 · 已折进 v4 · 重启 9999 生效」)。文案诚实:「起全市场 PIT 生产训练 + 折进 v4(异步·分钟级)」。

### 4.7 选股界面(零必需新前端)
多源徽章 [screen-app.jsx:564-583] 已 `Array.isArray(p.sources)` 泛化:lstm 源活跃即自动显 `v4 · LGB+fincast(..)+lstm(..)`,tooltip 含每源 w/匹配数/lookahead(lstm 走 PIT → 不亮 ⚠前视)。**本 Spec 选股侧无需改前端** —— 这正是 Spec 1 泛化徽章的意义。

## 5. PIT / 诚实合约(红线)
- **serving 零推理**:LSTM 训练/推理只在 workflow 端点起的离线子进程;9999 请求路径绝不训练/加载模型(沿用 Spec 1/2 命门)。
- **PIT 无前视**:训练样本 `realized_date = t + horizon ≤ eval_date`(即标签日 `t ≤ eval_date − horizon`);预测输入特征窗 `≤ eval_date`。`train_cutoff = eval_date − horizon` 写进 parquet → provenance `lookahead = (eval_date ≤ cutoff) = False`(诚实)。
- **LGB 主导**:`MAX_TOTAL_DL_W=0.5` 由 `dl_mix_scores` 强制(已有);lstm + fincast 双源共享该封顶。
- **契约一致**:输出 `eval_date/instrument/pred_ret_5d`(+ 可选 `train_cutoff`)与 Spec 1 `_load_dl_for_date` 读的逐字一致。
- **诚实失败**:parquet 缺/匹配 < `MIN_MATCH`(50)→ 源 inactive,纯 LGB(不冒充)。

## 6. 测试
- **纯函数单测**(无 torch·两 env 可跑):`build_sequences` 的 PIT 闸(`realized_date > cutoff` 样本被剔)、序列形状 `(N,seq_len,F)`、窗内不足剔除、`predict_index` 截面只取 ≤eval_date 末窗;`add_forward_return` 的 horizon 对齐。
- **`write_pred_rolling` 扩展测**:带 `train_cutoff` → 列存在且 datetime64;不带 → 与现有字节等价(现有 6 测不破)。
- **`default_dl_sources` 测**:含 lstm 源(model_id/path/score_col 对);lstm parquet 缺 → 源 inactive、fincast 仍活(`apply_dl_ensemble` 不破)。
- **集成验证**(有 torch CPU·见 §7):跑真生产器 → parquet 契约 + train_cutoff → regen 双源 active + lookahead:false → live /screen 三源徽章。

## 7. 验证(集成·真数据·CPU)
1. `python scripts/lstm_predict.py --date 2026-06-22 --universe csi800` → 产 `var/dl_pred_lstm.parquet`:当日 ~N 条、列 `eval_date/instrument/pred_ret_5d/train_cutoff`、`train_cutoff = 2026-06-22 − 5 交易日`、值有限。
2. `regen 2026-06-22` → `v4_dl_provenance.json`:`sources` 含 `fincast` + `lstm` 双源、各 weight>0、`w_lgb ≥ 0.5`、lstm `lookahead:false`(诚实非 null)、`n_has` 合理。
3. 重启 9999 → live `/screen/run`:`v4_provenance.active:true`、`sources[].model_id` 含 `lstm`、徽章 `v4 · LGB+fincast(..)+lstm(..)`、`chosen` 真被三源混合驱动。
4. 工作流页 LSTM 节点点「发布为 DL 源 ⤓」→ 轮询 status 走完两阶段(训练 → regen)→ done;复跑步骤 2-3 由 UI 一键驱动(端到端打通)。
5. 回归:`pytest`(lstm_io / fincast_io / dl_ensemble / screen / ranking / provenance / v4_fincast)全绿。

## 8. 风险与坑
- **CPU 训练耗时**:全市场逐 code 序列样本多 → 训练池用 csi800 + `--sample-cap`(~6000 行)定种子下采样守分钟级;预测全 universe(只前向传播·快)。
- **train_cutoff lookahead 语义**:provenance `look = (eval_date ≤ cutoff)`;cutoff 必须 `< eval_date`(= eval_date − horizon)否则误亮前视。务必传 `eval_date − horizon`,非 eval_date 本身。
- **跨 env 产物坑(承 Spec 2 教训)**:`write_pred_rolling` 已修跨 pandas/pyarrow(标量 Timestamp 广播 + datetime64 序列化);train_cutoff 列同走该路径(常量列 → 同样需 to_datetime,纯函数测覆盖)。
- **publish 链 regen 长任务**:训练 + regen 异步分钟级;单飞锁防并发重入(镜像 promote 锁去重入死锁的已知坑 [[v4-model-workshop]])。status 必须 fail 显形(子进程非零退出 → ok:false)。
- **default_dl_sources 注册 lstm 后**:即使没发布过,regen 也会列 lstm 为 missing 源(inactive)—— 诚实但徽章主文案只显 active 源,不污染。
- **不打架**:本机并行会话(feat/cpcv-validation)在独立 worktree;每次提交前 `git branch --show-current` 确认在 main;若共享树被切走则停。

## 9. 范围外 / 后续
- 选股页 lstm 源专用「体检」chip(多源徽章 tooltip 已含 ICIR;独立 chip 为显著性增强)。
- 工作流页「撤下 DL 源」(删 parquet + regen 移除·与 publish 对称)。
- LSTM 超参寻优 / 多 LSTM 变体注册表 / GPU 加速。
- 训练池扩到全市场(性能优化后)。
