# GAT 深度学习源(DL 集成层第 3 个源)设计

> **MVP-B**:把图注意力网络(GAT)作为统一 DL 集成层的**第 3 个生产级深度源**接入 v4
> (DL 层整体 = 叠加在 LGB 之上的"第 2 个 alpha 来源";本 spec 在该层内加第 3 个源)——
> 与 FinCast(零样本时序基础模型)、LSTM(单票时序)互补,GAT 提供**横截面关系(个股间相关图)**这一独立信息维度。
> 一期目标:**打通端到端 + 过 ① 的 CPCV 闸**,**不追性能**。

**日期**:2026-06-27
**分支**:`worktree-gat-dl-source`(从本地 main `b4693ab` 切,独立 worktree 隔离,保护并行会话共享树)
**状态**:设计已获用户口头批准("继续"),本文为正式 spec,待用户复审后转 writing-plans。

---

## 1. 目标(Goal)

新增一个 GAT 深度模型,离线 GPU 训练 + 推理,产出全市场每日 `pred_ret_5d` 预测表
`var/dl_pred_gat.parquet`(沿用 Spec1 DL 集成层契约);经 ①(CPCV + Deflated Sharpe)质量闸验证后,
以**一行** `DLSource` 注册进 `default_dl_sources()`,由现有 `apply_dl_ensemble` 自适应加权 z-混合进 v4 score。

一句话:**GAT = dl_ensemble 的第 3 个源,插拔式接入,LGB 恒主导(≥0.5),过闸才注册,无料诚实退纯 LGB。**

---

## 2. 背景与定位(为什么是 GAT,且不与已有源重叠)

DL 集成层(Spec1)已就位,现有两源:

| 源 | 信息维度 | 训练方式 | 产出 |
|----|----------|----------|------|
| FinCast | 单序列时序(零样本基础模型) | 预训练,无训练 | `var/v4_fincast_pred.parquet` |
| LSTM | 单票多因子时序 | 在线训练(并行会话 Spec3) | `var/dl_pred_lstm.parquet` |
| **GAT(本 spec)** | **个股间横截面关系图** | **在线 GPU 训练** | **`var/dl_pred_gat.parquet`** |

GAT 的独立价值 = **关系维度**:节点 = 个股,边 = 个股收益相关性,图注意力让每只股票的预测吸收"相关同侪"的信息。
这是 FinCast(只看自身序列)与 LSTM(只看自身时序)都缺的维度。三源信息正交 → 集成有意义。
**不碰 Spec3(LSTM)的任何代码/产物**,GAT 是平行的第 3 个源。

### 架构选型(已与用户确认)

**纯 PyTorch 的掩码注意力(masked attention)实现 GAT 层**,不引入 PyG / DGL。
- 规避 Blackwell(RTX 5090,sm_120)上 PyG/DGL 预编译 wheel 的兼容风险;
- GAT 层 = 标准 `h_i' = σ(Σ_{j∈N(i)} α_ij · W h_j)`,注意力分数对非邻居掩成 `-inf` 后 softmax——
  几十行纯张量算子,完全自控,与 FinCast adapter 一样落在 GPU 脚本里。

---

## 3. 数据流(Data Flow)

```
引擎二进制(QlibBinaryLoader,close+volume)
    │  conda stocks GPU 解释器
    ▼
scripts/gat_predict.py  ──读──►  gat_io.py(纯函数:PIT 特征 / 相关图 / 标签)
    │                                   │
    │  每个换仓日 (X_d 节点特征, A_d 邻接掩码, y_d=fwd5d 标签[仅已实现日])
    ▼
GAT nn.Module(纯 torch,2 层掩码图注意力)── 训练(MSE on 横截面 z 标签)
    │
    ▼  eval_date 前向 → 每只股票 pred_ret_5d
fincast_io.write_pred_rolling(OUT, eval_date, codes, preds, keep_days=60, train_cutoff=<末标签日>)
    │
    ▼
var/dl_pred_gat.parquet   (契约: eval_date / instrument / pred_ret_5d / train_cutoff)
    │
    ├──► ① 闸:cpcv.validate_dl_source(path) → {ready, sharpe, dsr, ic_mean, passes_gate}
    │         DSR ≥ 0.5 且 ready ──► 保留该 parquet(=激活 GAT);否则删除/不留 → GAT 经"无料诚实退出"休眠
    │
    └──► gat 行实现期即在 default_dl_sources()(parquet 缺失 → 诚实跳过,字节等价);
              regen.py 已调 default_dl_sources() → build_v4 → apply_dl_ensemble
              自适应权重(近期 ICIR) z 混合进 score(总 DL 权重封顶 0.5,LGB 主导)
              provenance 落 v4_dl_provenance.json → /screen 徽章自动显 "LGB + … + gat(w)"
```

---

## 4. 组件设计(Components)

### 4.1 `guanlan_v2/strategy/compute/gat_io.py`(新建,纯函数)

**职责**:从 close/volume 面板产出 GAT 训练/推理所需的三件套,**只 numpy/pandas,无 torch、无引擎依赖**
(与 `fincast_io.py` 同款约束:guanlan 主 env 与 conda stocks 都能 import,可在主 env 单测)。

公开函数:

- `compute_node_features(close_panel, volume_panel, date, *, factors=DEFAULT_GAT_FACTORS) -> (codes: list[str], X: np.ndarray)`
  - 在 `date` 的横截面快照算固定一组 PIT 价量因子(只用 `≤ date` 数据);
  - 末值/窗口非空才入选 `codes`;每因子**横截面 z-score**;返回 `(N, F)` float32。
  - `DEFAULT_GAT_FACTORS`(一期固定 ~8 个,均可由 close+volume 算,无需引擎因子目录):
    `mom_5, mom_20, mom_60`(动量)、`rev_1`(1 日反转 = `-(close/close.shift(1)-1)`)、
    `vol_20`(20 日日收益标准差)、`ma_gap`(`close/MA20 - 1`)、`turn`(`volume / volume.rolling(20).mean()`)、
    `amihud_20`(非流动性近似 = `mean(|日收益| / (close·volume + ε))`,一期只用 close+volume,不取 amount 面板)。
- `build_corr_graph(close_panel, date, codes, *, window=60, topk=20) -> np.ndarray`
  - 用 `≤ date` 的末 `window` 日日收益算 `codes` 两两 Pearson 相关;
  - 每个节点保留 `topk` 个最相关邻居(按相关绝对值,排除自身)→ 返回 `(N, N)` 布尔/0-1 邻接掩码(对称化:`A = A | A.T`);
  - 自环置 1(节点可注意自身)。窗口不足/全空 → 返回单位阵(只自注意,诚实退化)。
- `forward_label(close_panel, date, codes, *, horizon=5) -> np.ndarray`
  - `codes` 在 `date` 起未来 `horizon` 个**交易日**收益(`close[t+h]/close[t] - 1`);
  - **仅用于训练日**(`date` 的 `t+h` 已存在于面板末日之前);返回 `(N,)` float32,缺失置 `nan`。
- `rebalance_dates(panel_index, *, horizon=5, start=None) -> list[Timestamp]`
  - 从 `start`(缺省 = 面板首日)到 `面板末日 - horizon` 的非重叠 `horizon` 日换仓训练日序列
    (PIT:只含标签已实现日,即 `date + horizon ≤ 面板末日`)。
- (复用)写盘统一走 `fincast_io.write_pred_rolling`,**不在 gat_io 重复实现写逻辑**。

**PIT 命门**:所有函数对 `close_panel` 一律 `.loc[:date]` 截断后再算,绝不看未来;`forward_label` 的未来收益只在
训练阶段对**已实现**日取用,推理日(eval_date)不取标签。

### 4.2 `scripts/gat_predict.py`(新建,conda stocks GPU 脚本)

**镜像 `scripts/fincast_predict.py` 的骨架**(sys.path 插 `_REPO`+`engine`、`QlibBinaryLoader` 直读、
`list_all_instruments`、`_latest_trade_date`、`NO_PROXY`、CLI `--date`/`--device`/...)。

跑法:`D:/app/miniconda/envs/stocks/python.exe scripts/gat_predict.py --date 2026-06-27`

GAT 模型(torch)抽到**可 import 的 `guanlan_v2/strategy/compute/gat_model.py`**(主 env 有 torch 2.10 CPU,
故可 CPU 单测掩码注意力正确性;GPU 脚本 import 之。gat_io 仍 torch-free)。脚本本身只做编排:

- `gat_model.py` `class GAT(nn.Module)`:2 层纯 torch 掩码图注意力(`_GATLayer`:线性投影 + `LeakyReLU(a·[hᵢ‖hⱼ])` 注意力,
  非邻居 `-inf` 掩码 + softmax + 邻居加权聚合;一期单头/隐层 32),末层 `Linear→1`;`forward(X, A) -> (N,)`。
- `gat_model.py` `train_gat(X_list, A_list, y_list, *, device="cpu", epochs=60, lr=1e-3, seed=0) -> GAT`:
  遍历每日图 `(X_d, A_d, y_d)`,损失 = **横截面 z 标签上的 MSE**,Adam;返回训练好的模型。
- `gat_model.py` `predict_gat(model, X, A, *, device="cpu") -> np.ndarray`:单日前向 → `(N,)` 预测。
- `scripts/gat_predict.py`:引擎读 close/volume → gat_io 构每日 `(X,A,y)` → `train_gat`(`--device` 缺省 `cuda` 可退 `cpu`)
  → 对 `eval_date` 构 `(X_eval, A_eval)`(只用 `≤ eval_date` 数据)→ `predict_gat` → `pred_ret_5d`。
- `train_cutoff = ` 训练用到的**最末已实现标签日**(`= eval_date` 回退 `horizon` 个交易日)→ 传给
  `write_pred_rolling(..., train_cutoff=cutoff)`;`apply_dl_ensemble` 据此算 `lookahead = (eval_date ≤ cutoff)`,
  因 `cutoff < eval_date` 恒为 `False`(无前视),诚实显形。
- 产出:`var/dl_pred_gat.parquet`(`OUT = _REPO/var/dl_pred_gat.parquet`)。
- **命门**:GPU 训练/推理一律离线;9999 请求路径绝不跑模型(同 FinCast/LSTM)。

### 4.3 `cpcv.validate_dl_source`(在 `cpcv.py` 新增,纯函数,复用 ① primitives)

**这是 ① 的 CPCV 闸用于"验证产出源"的轻量入口——不重训、不碰 `retrain_core`/`strict_validate`。**

```python
DL_GATE_DSR = 0.5        # 注册门槛:DSR ≥ 0.5(真夏普>噪声基准的概率过半)
DL_SOURCE_TRIALS = 8     # DSR deflate 的试验数(DL 架构候选:fincast/lstm/gat + 调参,保守取 8)

def validate_dl_source(path: str, score_col: str = "pred_ret_5d",
                       n_trials: int = DL_SOURCE_TRIALS) -> dict:
    """读 DL 源预测表 → 用真已实现 fwd5d(PIT)算 多头超额夏普 + DSR + RankIC。
    复用 _fwd_returns_for_snapshots / decile_metrics / sharpe / deflated_sharpe(零新算法)。
    缺文件/不足 → ready=False(诚实)。"""
```

实现要点(全部复用 ① 已有函数,无新统计算法):
1. `pd.read_parquet(path)` → 取 `eval_date, instrument, score_col`;改名 `date, code` 对齐 `_fwd_returns_for_snapshots` 入参契约(`hist[date, code]`)。
2. `fwd = _fwd_returns_for_snapshots(hist)`(① 已有,PIT 真前向收益,引擎 close bins)。
3. 建 panel `[date, code, lgb_pct = 组内 rank(score_col, pct=True), fwd]`(把"被排名的分"塞进 `lgb_pct` 列即可复用 `decile_metrics`,无需改 `decile_metrics`)。
4. `m = decile_metrics(panel)`;`sharpe(m["long_excess_ret"])`;`deflated_sharpe(m["long_excess_ret"], n_trials)`;`ic_mean = mean(m["rank_ic"])`。
5. `n_oos_days = panel["date"].nunique()`;`< MIN_OOS_DAYS(=10)` → `ready=False`。
6. 返回 `{ready, model_id?, path, n_oos_days, sharpe, dsr, ic_mean, ic_dist, n_trials, passes_gate, note}`,
   其中 `passes_gate = bool(ready and dsr is not None and dsr >= DL_GATE_DSR)`。

**闸的语义**:`passes_gate` 是**建议性**信号,**注册仍是人工复审的 1 行代码编辑**(见 4.5)——
机器算 DSR,人决定是否加源。这与 ① 的"不替用户拒/放行只看人"取舍一致。

### 4.4 `scripts/gat_validate.py`(新建,极薄)

`python scripts/gat_validate.py`(主 env 即可,不需 GPU):调 `cpcv.validate_dl_source(var/dl_pred_gat.parquet)`
并 print 结果(`ready / n_oos_days / sharpe / dsr / ic_mean / passes_gate`)。一期 DL 源验证走脚本/函数,
**不接入工坊 UI 的"快验/严格验证"按钮**(那是后续阶段;② 的 `/model/validate` 只验 v4 变体,不动)。

### 4.5 `dl_ensemble.default_dl_sources()`(+1 行,实现期即加,字节安全)

**实现期**就在现有列表**追加一行**(紧跟 `lstm` 行,**不改 fincast/lstm 行**)——下述三道防线保证"注册≠盲信",
故加行与"是否信任 GAT"解耦,可先接线、激活留给真机 DSR 闸:

```python
DLSource(model_id="gat", path=str(var / "dl_pred_gat.parquet"),
         score_col="pred_ret_5d", weight_mode="adaptive"),
```

1. **缺料诚实退出**:`dl_pred_gat.parquet` 不存在 / 当日无预测 / 匹配 `< MIN_MATCH(50)` → `_load_dl_for_date`/`dl_mix_scores` 退出该源,**不影响其余源**。
2. **CPCV 激活闸(人工)**:仅当 `validate_dl_source` 出 `passes_gate=True`(DSR≥0.5)才**保留** GAT parquet 在 `var/`(=激活);不过 → 删/不留 → GAT 经第 1 道休眠。**注册行常在,激活与否由 parquet 是否存在决定。**
3. **运行期自适应权重**:`weight_mode="adaptive"` 按近期 ICIR(`_adaptive_w_fc`)定权;GAT 近期表现差 → 权重自动趋 0。

**字节等价**:gat parquet 不存在时 `apply_dl_ensemble` 把 gat 记 missing,有效源集合与加 gat 前完全一致 → 生产行为零变化(单测守护)。

---

## 5. 集成点(均已就位,本 spec **零改动**)

| 接点 | 现状 | 本 spec 是否改 |
|------|------|----------------|
| `v4.py` `build_v4`(line 280-289 `if dl_sources: apply_dl_ensemble(...)`) | 已支持任意 N 源 | **不改** |
| `regen.py:181-190` `default_dl_sources()` → build_v4 + 写 `v4_dl_provenance.json` | 已调注册表 | **不改**(加源自动生效) |
| `apply_dl_ensemble` / `dl_mix_scores`(N 源加权 z 混合 + 封顶 0.5) | 已泛化 | **不改** |
| `screen/api.py` 徽章读 provenance 拼 "LGB + … (w)" | reason 串通用 | **不改**(自动显 gat) |

---

## 6. 红线(Red Lines)

1. **PIT 无前视**:gat_io 全 `.loc[:date]`;标签只在训练用已实现日;`train_cutoff` 诚实落盘,`lookahead` 恒 False。
2. **诚实缺席**:缺文件/不足/无当日预测 → `ready=False` / 源退出 / 退纯 LGB,**绝不编造数字**。
3. **`v4.py` / `v4_fincast.py` 零改**:只 import 其 primitive(`_zscore` 等)与既有集成口;不改 `retrain_core`/`strict_validate`/`build_v4`。
4. **LGB 恒主导 ≥ 0.5**:沿用 `MAX_TOTAL_DL_W=0.5`,加 GAT 不改该约束。
5. **不碰 Spec3(LSTM)**:不改 `lstm` 行、不改 LSTM 任何代码/产物;GAT 是平行第 3 源。
6. **离线推理**:GPU 训练/推理只在 conda stocks 离线脚本;9999 请求路径绝不跑模型。
7. **激活是人工闸**:`passes_gate` 仅建议;是否**保留** GAT parquet(=激活该源)由人工复审 DSR 后决定;注册行本身字节安全、实现期即加。

---

## 7. 测试策略(Testing)

**单测(主 env,`pytest`;torch 仅需 CPU 2.10,无需 GPU)**:
- `tests/test_gat_io.py`(纯 numpy/pandas,无 torch):
  - `compute_node_features` 横截面 z(均值≈0)、只用 `≤date` 数据(注入未来值不改结果)、缺失剔除;
  - `build_corr_graph` 对称 + 自环 + topk 度数上限 + 窗口不足退单位阵;
  - `forward_label` 真前向收益值正确 + 末日附近无未来标签(PIT)。
- `tests/test_gat_model.py`(torch CPU):
  - `GAT.forward(X,A)` 输出形状 `(N,)`、有限;
  - **掩码生效**:把某节点的非邻居换任意值,该节点输出不变(注意力只看邻居)——这是图注意力正确性的命门测试;
  - `train_gat` 在合成可学数据上 loss 单调下降(≥ 训练有效)。
- `tests/test_dl_source_validate.py`:`validate_dl_source` 桩掉 `_fwd_returns_for_snapshots`(同 ① 既有 cpcv 测法),
  验:正向预测 → `dsr` 高 + `passes_gate=True`;反向预测 → 低 + False;缺文件 → `ready=False`;OOS<10 → `ready=False`。
- 扩 `tests/test_dl_ensemble*.py`(若存在则扩,不新建重复):`default_dl_sources()` 含 `gat`;
  `dl_pred_gat.parquet` 缺失 → gat 记 missing、有效源不变(字节等价);present 桩 → 参与混合且总 DL 权重 ≤ 0.5。
- 全量回归 `pytest` 绿(注:worktree 因 junction 读 main 的 breadth parquet,`test_vendored_hashes_match` 为已知**环境性**失败,非 GAT 回归,需在主树或单独核验)。

**集成测(真机,conda stocks GPU)**:`gat_predict.py` 真跑产 parquet(零样本不可,GAT 必训练)→ `gat_validate.py` 出 DSR
→ (过闸)注册 → `regen` → `/screen` 徽章。属第 6 阶段"真机实验",非单测。

---

## 8. 范围(Scope)

**一期(本 spec)只追两件**:
1. **打通**:gat_io 纯函数 + GPU 脚本 + 闸 + 注册接线,端到端能产 parquet 并被 v4 读到。
2. **过闸(激活)**:`validate_dl_source` 能对真 GAT 产物算出 DSR;注册行实现期即加(字节安全),**激活以 DSR≥0.5 为前提**——过则保留 parquet(GAT 真参与选股),不过则删 parquet(GAT 休眠,选股退回与现状字节等价)。

**明确不做(Out of scope / 挂账)**:
- 不追 GAT 性能/调参(特征集、层数、头数、损失只取一组稳妥默认);
- 不接工坊 UI 验证按钮(DL 源验证一期走脚本);
- 不做跨源 PBO/CSCV(① 挂账,沿用);
- 不做 GAT 的工作流节点(③ 是 v4 变体验证节点,DL 源是另一条线,后续再议);
- 不改 regen 为自动产 GAT(GAT 训练耗时,沿用 FinCast 式离线手动/定时脚本)。

---

## 9. 环境与运行(Environment)

- 训练/推理:conda `stocks`(`D:/app/miniconda/envs/stocks/python.exe`,torch GPU,RTX 5090 cu128)。
- 单测/验证:guanlan 主 env(`pytest`;`gat_validate.py` 纯函数无需 GPU)。
- 数据:worktree 经目录 junction 共享主树 `var/`+`vendor/`;`dl_pred_gat.parquet` 写入共享 `var/`(并行会话不用,无冲突)。
- 引擎只读:`QlibBinaryLoader(DEFAULT_PROVIDER)` 直读 close/volume 二进制(同 FinCast 路径)。

---

## 10. 文件清单(File Manifest)

| 文件 | 动作 | 责任 |
|------|------|------|
| `guanlan_v2/strategy/compute/gat_io.py` | 新建 | 纯函数(numpy/pandas,无 torch):PIT 节点特征 / 相关图 / 标签 / 换仓日 |
| `guanlan_v2/strategy/compute/gat_model.py` | 新建 | torch GAT nn.Module + `train_gat` + `predict_gat`(CPU 可单测) |
| `scripts/gat_predict.py` | 新建 | conda stocks GPU 编排:引擎读数 → gat_io → gat_model 训练/推理 → 写 parquet |
| `scripts/gat_validate.py` | 新建 | 薄壳:调 validate_dl_source 打印闸结果 |
| `guanlan_v2/strategy/compute/cpcv.py` | 改(加) | `validate_dl_source` + `DL_GATE_DSR`/`DL_SOURCE_TRIALS`(纯加,不动既有) |
| `guanlan_v2/strategy/compute/dl_ensemble.py` | 改(+1 行) | `default_dl_sources()` 追加 gat 源(实现期即加,字节安全) |
| `tests/test_gat_io.py` | 新建 | gat_io 纯函数 PIT/正确性 |
| `tests/test_gat_model.py` | 新建 | GAT 形状 + 掩码生效 + train_gat loss 下降(torch CPU) |
| `tests/test_dl_source_validate.py` | 新建 | validate_dl_source 桩测 |
| `tests/test_dl_ensemble.py` | 扩 | 注册表含 gat + 缺文件字节等价 |
