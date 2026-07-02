# 市场风格 regime 条件化选股:因子族动态权重(设计 spec)

- **日期**:2026-07-02
- **状态**:待用户复审
- **来源**:设计评审 panel(3 独立设计视角 × 3 对抗评审镜头,6 agent)。视角 B「per-factor
  jump-penalty 动态因子权重」三票全胜(43/41/43),本 spec = B + 三镜头嫁接的修补条款。
  视角 C「regime 路由 v4 变体」被两票一票否决(见 §11 挂账,立此存照)。
- **上游证据**:深研(106 agent、24 源、20 确认/5 推翻)关键结论已内嵌各节,标注【证据】。

---

## 1. 目标与判据

用户最终目标:**在不同市场风格(regime)下用不同的选股策略,使选股 IC 相对静态基线有
可度量的 OOS 提升**。

实现形式:不做离散的"换模型/换策略"切换(数据只给每档 regime 3-5 段独立行情,统计上
撑不住,见 §11 视角C 否决理由),而做**连续的因子权重倾斜**——对每个因子族独立判
"当前灵不灵"(regime),动态调整其在选股页 α 混合通道中的权重。每个 regime 状态下生效
的因子权重画像不同,等效于"不同风格用不同选股配方",且统计上可验证。

**成功判据(硬指标)**:激活闸(§7)全条件通过。**"0 个族过闸"是合法结局**——交付
"该范式在本仓因子上无 OOS 增量"的结论,开关保持关闭,不算失败。

**诚实口径(必须写进 UI/文档)**:提升只在 α<1 的混合用法里兑现;用户默认纯 v4(α=1)
时本层对最终排序零影响——这是"不动默认选股算法"红线的必然代价。

## 2. 证据基础(设计为何长这样)

- 【坑·3-0 确认】市场级常数 regime 特征灌进横截面树模型基本无效(个股特征吃掉
  39-56% 重要性)→ **regime 永不进 v4 特征列**。
- 【范式·3-0】per-factor regime / 动态因子加权是有证据的集成范式 → 本设计主体。
- 【告诫·3-0】AQR:风格择时极难,倾斜必须保守(向静态收缩、设上限);宣称 regime
  gating 带来 Sharpe 数倍提升的说法全被推翻(0-3)→ η=0.5 收缩 + tilt clip。
- 【方法·3-0】jump-penalty 统计模型判 regime 显著优于裸 HMM(年切换 ~0.8 vs 2+,
  whipsaw 少一个量级),特征纯价格即可 → §5.2。
- 【信号·3-0】CSV 横截面收益离散度 = 选股机会空间连续代理 → 共享协变量。
- regime 检测固有 ~半个月延迟 → 闸内显式计价(§7 延迟敏感性臂)。
- A股特有指标(涨停家数/连板/赚钱效应)无外部证据 → 不进 v1,仓内自测后才准入(§11)。

## 3. 非目标(红线)

1. **v4.py / v4_fincast.py 零改动**;/screen 默认选股算法不动——一切条件化 opt-in,
   默认路径行为**逐字节不变**(硬回归测试守护)。
2. regime 不作为 v4 特征列(证据坑)。
3. 不做市场级离散切换、不做按 regime 训练/路由模型变体(评审否决,§11)。
4. 不依赖基本面/市值数据(管线暂坏):一期因子白名单限 6 个纯价量族
   {动量反转, 技术, 波动率, 流动性, 共振, 跟随}(catalog `_KEEP_FAMILIES` 的纯价量子集;
   估值/财务质量/成长/规模因基本面依赖排除,情绪/资金面因数据源未审计排除,均列挂账)。
5. 不新建前端页面(只填充现有选股页因子卡/控件区)。

## 4. 架构总览

```
QlibBinaryLoader(close/volume 面板)
        │
        ▼
[P0] factor_ls.py ──→ factor_ls_returns.parquet         (族多空收益序列,PIT available_date=t+1)
        │
        ▼
[P1] jump_model.py + factor_regime.py
        ──→ factor_regime.parquet + factor_regime_meta.json   (每族 p_fav 连续概率,walk-forward PIT)
        │
        ▼
[P2] regime_gate.py ──→ factor_regime_gate.json          (walk-forward ΔIC 主判据 + CPCV 辅 + BH-FDR
        │                                                  + 安慰剂/合成对照 + 代理池口径,人工激活)
        ▼
[P3] screen/api.py opt-in 接线
        ScreenIn.regimeWeights=False(默认)
        apply_regime_weights:w_eff = w_user·((1−η) + η·tilt), tilt=clip(2·p_fav, 0.5, 1.5), η=0.5
        响应 regime_weights 透明徽章 + GET /screen/regime 只读端点
[P4·可选] 因子×市场档 IC 图谱(纯展示叙事产物,不驱动权重)
```

数据流单向:上游产物只读、请求期零重算(毫秒级查表)。任一环节缺失 → 显式降级回
静态权重并打标(诚实缺席)。

## 5. 组件设计

### 5.1 `guanlan_v2/strategy/compute/factor_ls.py` — 族多空收益序列(P0)

- **成员**:catalog `FACTOR_DEFS` 中 family ∈ 白名单 6 族且有 `expr` 的因子
  (`compile_factor` 编译不过 → 该因子诚实跳过并记日志)。
- **序列构造**:csi800 截面,逐日按因子值(目录已预定向 dir=+1)排序,
  top-bottom quintile 等权**次日**收益差 = 该因子当日 L/S 收益;族内成员等权平均 → 族序列。
- **PIT 命门**:t 日因子值只用 ≤t 数据(表达式本就 trailing);t 日 L/S 收益在 t+1 收盘
  才 realized → 产物带 `available_date = t+1` 列,**下游 regime 特征在 t 只允许用
  available_date ≤ t 的行**。
- **产物**:`vendor/artifacts/factor_ls_returns.parquet`
  (列:date, family, factor_id, ls_ret, available_date;原子写 .tmp→os.replace)。
- **算力与锁纪律(合入前置条件,评审镜头3)**:全历史物化(~40 因子 × 800 码 × ~2500 日)
  预计 10-30 分钟,**贴近 regen 锁 `_LOCK_MAX_AGE=1800` 接管红线**(regen.py L80/L97:
  锁龄≥30min 即被第二进程夺锁,会复活并发写事故)。故:
  - **全量回填/周频全量**:独立子进程 + 独立锁(照 cpcv `__main__` 子进程先例),
    **不进 regen 锁临界区**;
  - **日频增量**(只算最新交易日,秒级):挂 regen_all 非阻断步(照 factor_ic 步
    L215-223 try/except 先例)。

### 5.2 `guanlan_v2/strategy/compute/jump_model.py` — jump-penalty 统计跳变模型(P1)

- 纯 numpy,~150 行,零新依赖。
- **目标函数**:标准化特征序列上
  `min Σ_t ‖x_t − μ_{s_t}‖² + λ·Σ_t 1[s_t ≠ s_{t−1}]`,K=2 状态(有利/不利),
  质心 k-means 型迭代 + 状态序列动态规划求解。
- **K=2 而非 3(样本量定的,不是偏好)**:段才是有效样本;~2300 交易日切 3 档则少数档
  <400 天且仅 3-4 段;K=2 下典型 60/40 分布 ≈ 8-10 段/族,状态均值差检验 t≈1.5-4,
  可检验。K=3 仅诊断输出。
- **λ**:网格 {50, 100, 200},按"OOS 年均切换 ≤1.5 次"定标(证据:jump-penalty
  年切换 ~0.8 vs 裸 HMM 2+)。λ 网格属 trials,入计数器(§7)。

### 5.3 `guanlan_v2/strategy/compute/factor_regime.py` — walk-forward 拟合 + p_fav + 权重变换(P1/P3)

- **特征(每族)**:族 L/S 序列的 EWM 下行波动(halflife=10d)、EWM Sortino(20d、60d
  双窗)+ 共享协变量 CSV(全市场逐日截面收益 std)。全部由 close/volume 派生。
- **PIT 在线拟合工程化**:
  - warmup 最短 **500 交易日**,不足 → 该族不产 regime(诚实缺席);
  - 每 **21 交易日(月频)** 用 expanding window(仅 ≤t 数据)重拟合 (μ, λ 选择);
  - 两次重拟之间只做在线状态过滤(新日特征按上一版参数 + jump penalty 增量 DP,O(K));
  - 每次重拟的参数快照连同 fit_date 存 meta → 全序列可重放;
  - **守护测试 = 截断不变性**:把未来数据截掉重跑,历史 regime 逐位不变。
- **输出连续值**:`p_fav(t)` = 状态后验软概率(质心距离 softmax,温度由拟合残差定),
  不输出硬开关;另落 `confirmed_since` 列(状态连续维持起点,供延迟诊断,嫁接自视角C)。
- **权重变换(P3 接线用)**:`apply_regime_weights(sup)`:对每个因子按其 family 查
  factor_regime.parquet 最新行,
  `w_eff = w_user · ((1−η) + η·tilt)`,`tilt = clip(2·p_fav, 0.5, 1.5)`,`η = 0.5`
  → 有效乘子 ∈ [0.75, 1.25](p_fav=0.5 中性时 w_eff≡w_user)。η 与 clip 是 meta 里
  可审计常数,**不许运行期调参**。
- **产物**:`vendor/artifacts/factor_regime.parquet`
  (date, family, p_fav, state, confirmed_since, feat_dvol, feat_sortino20,
  feat_sortino60, feat_csv, fit_asof, model_version, source='factor-regime-jm')
  + `factor_regime_meta.json`(λ/η/clip、warmup、refit 日程、参数快照、trials 计数器、
  spec_hash)。
- 与 market_status.py 的 lite regime **并行不复用、互不触碰**(它是展示口径,被帷幄/
  研报/九视角消费,属默认路径下游——评审镜头3 对视角C 的否决理由之一)。

### 5.4 `guanlan_v2/strategy/compute/regime_gate.py` — 评估协议 + 激活闸(P2)

范式复刻 cpcv.py 先例(validate_dl_source / make_splits / decile_metrics /
deflated_sharpe;GAT 全市场 −0.029 拒、csi1000 +0.254 激活的同一套)。

- **对照**:基线 = 同因子集静态权重复合的逐日截面 rankIC(口径与 factor_ic.py 一致:
  csi800、fwd 5d、截面 ≥30 才算);处理 = 同因子集经 apply_regime_weights
  (仅用 available_date ≤ t 的 regime)后的复合 rankIC。
- **主判据 = 真 walk-forward**:warmup(500d)之后每一天都是真 OOS(regime 参数月拟合
  只见 ≤t);ΔIC(t) 配对序列,主指标 = 非重叠 5 日换仓(rb = dates[::horizon],
  strict_validate 先例)上的 mean ΔrankIC + Newey-West(lag=5) t 值;
  评估窗 ≈ 2300−500 = 1800 日 ≈ **360 个非重叠期**(功效充足)。
- **辅判据 = CPCV 15 路径**:make_splits(n_groups=6, k=2, purge=horizon+1=6, embargo=5);
  **修补(评审镜头2)**:每路径的 jump 模型在 train 折的**最长连续段**上重拟合
  (非连续折上拟合切换罚模型违反连续采样假设)——若实现困难则整档降级为报告性指标,
  在 gate 报告中把 walk-forward 明确标为主判据。
- **安慰剂臂(嫁接自视角C 孪生消融)**:同幅度 tilt 但 p_fav 按时间块打乱
  (block shuffle 保自相关)的"假 regime"臂;真臂 ΔIC 须显著优于安慰剂臂,
  才把提升归因于 regime 信息而非权重扰动机械效应。
- **延迟敏感性臂(报告性)**:p_fav 滞后 20 日重算 ΔIC,把 ~半月检测延迟的成本单独报数。
- **代理池口径(修补池失配,评审镜头3 判"必做")**:激活闸除 csi800 全截面 ΔIC 外,
  增报"每日按静态复合分取 top 200 代理候选池"内的 ΔIC——生产 blend 的 comp_score 只在
  v4 候选池内起作用,闸认证与生产兑现必须是同一总体的近似;**两个口径都过才建议激活**。
- **多重检验**:n_trials = λ 网格(3) × η 档(2) × 族数(6) ≈ 36 预注册,喂
  deflated_sharpe;**trials 持久化计数器从 P0 起记**(λ 校准、K 选择、特征消融等一切
  格点),DSR 按 max(36, 实际累计) deflate;族级 6 假设做 **Benjamini-Hochberg FDR
  q=0.10**;个体因子(56 检验)激活留二期。
- **闸自证(嫁接自视角C,进测试套件)**:合成数据双向测——注入已知 regime 依赖信号的
  合成 L/S 序列 → 闸必须放行(阳性对照);纯噪声权重 → 必须拒(阴性对照)。
  证明闸既不是橡皮闸也不是永拒闸。
- **纪律**:gate 文件带 spec_hash 与评估窗指纹;同 spec 复跑幂等;变 spec 强制递增
  n_trials;基础产物变更 → gate 标 stale(防"陈闸放新画像")。`passes_gate` 仅建议性,
  **激活仍人工确认**(validate_dl_source 先例)。
- **产物**:`vendor/artifacts/factor_regime_gate.json`(每族:静态 IC/动态 IC/ΔIC/NW-t/
  CPCV 15 路径分布/DSR/BH 结论/安慰剂差值/代理池 ΔIC/切换次数,+ activated 族列表
  + spec_hash + asof)+ 可读 summary。

### 5.5 `guanlan_v2/screen/api.py` 接线(P3)

唯一改动面(挂点已核实:ScreenIn L41 / blend L46 / _panel_enrich L354 /
_screen_via_v4 L685 / 混合重排 L761-781):

- `ScreenIn` 新增 `regimeWeights: bool = False`——**默认 False 时代码路径逐字节不变**
  (回归测试断言默认请求响应与改前一致)。
- 开启时:_panel_enrich 组 `sup=[(fid, fw)]` 之前调 `apply_regime_weights(sup)`,
  仅当**双闸**同过:① 因子 family ∈ factor_regime_gate.json 的 activated 列表
  (未过闸的族即使开了开关也用静态权重);② 产物新鲜
  (**asof ≥ 最新交易日 − 3**,嫁接自视角A 收紧)。
- 只动 (1−α)·因子复合那一半;v4 分位、lgb_pct、评级、护盾全部不碰。
- **透明徽章(诚实缺席)**:响应新增
  `regime_weights: {applied: bool, fallback_reason: str|null, regime_asof,
  per_factor: [{id, family, w_user, w_eff, p_fav}]}`——产物缺/stale/未过闸/warmup 不足
  四种降级全部 applied=false 带 reason,绝不静默。
- 新增只读端点 `GET /screen/regime`:下发各族 p_fav + confirmed_since + 闸状态 + asof,
  供前端因子卡展示与帷幄后续消费(ww 工具接入列挂账,§11)。

### 5.6 regen 接线

- regen_all 在 factor_ic 步之后加两个**非阻断**步(try/except 先例 L215-223):
  ① factor_ls 日频增量;② factor_regime 增量(在线过滤 + 到期月频重拟)。
- 全量回填/重放走独立子进程(§5.1 锁纪律)。
- 默认 regen 耗时增量控制在秒-分钟级(全量重放 <10min 目标,不含因子物化)。

## 6. 产物清单

| 产物 | 路径(ARTIFACTS_DIR 下) | 关键列/字段 |
|---|---|---|
| 族多空序列 | factor_ls_returns.parquet | date, family, factor_id, ls_ret, **available_date** |
| 族 regime | factor_regime.parquet | date, family, **p_fav**, state, confirmed_since, feat_*, fit_asof, source |
| regime meta | factor_regime_meta.json | λ/η/clip, warmup, refit 日程, 参数快照, **trials 计数器**, spec_hash |
| 激活闸 | factor_regime_gate.json | 每族全指标, **activated 列表**, spec_hash, asof |

`strategy/paths.py` 加对应常量。全部原子写(.tmp → os.replace)。

## 7. 激活闸条件全表(全过才可入 activated,人工确认)

| # | 条件 | 阈值 | 性质 |
|---|---|---|---|
| 1 | walk-forward mean ΔrankIC(非重叠 5d) | ≥ +0.005 且 NW-t ≥ 2.0 | 主判据 |
| 2 | BH-FDR(6 族,q=0.10) | 该族存活 | 主判据 |
| 3 | CPCV 15 路径(最长连续段拟合) | median ΔIC > 0 且 p05 > −0.005 | 辅判据* |
| 4 | 动态多空 DSR(n_trials=max(36,实际)) | ≥ 0.5(同 DL_GATE_DSR) | 辅判据 |
| 5 | 真臂 − 安慰剂臂(block-shuffle p_fav) | > 0 且 t ≥ 2 | 归因 |
| 6 | 代理池(静态复合 top200)ΔIC | ≥ 0(do-no-harm) | 池失配修补 |
| 7 | OOS 年均切换 ≤2 次 且 状态事后吻合率 ≥70% | 每族 | whipsaw 护栏 |
| — | 延迟敏感性(p_fav 滞后 20d ΔIC) | 只报数不判 | 报告性 |

\* 若最长连续段拟合实现受限,#3 降级为报告性并在 gate 报告显著标注。

## 8. 测试计划

- `tests/test_factor_ls.py`:PIT(t 行只含 ≤t+1 realized)、重跑确定性逐位一致、
  编译失败因子诚实跳过、默认 /screen/run 响应回归不变。
- `tests/test_factor_regime.py`:**截断不变性**(删未来数据历史 regime 逐位不变)、
  每族年均切换 ≤1.5 次实测、warmup 不足诚实缺席、p_fav∈[0,1] 且中性=0.5→w_eff≡w_user。
- `tests/test_regime_gate.py`:合成阳性对照必过 / 阴性对照必拒、安慰剂臂生效、
  同 spec 幂等、任一条件不满足即 rejected 落盘(桩数据双向)。
- `tests/test_screen_api.py` 增例:regimeWeights 缺省/False 逐字节回归;True 且过闸时
  w_eff 按公式生效且响应显形;产物缺/stale/未过闸三种降级 applied=false 带 reason。

## 9. 分期交付(每期独立可测,过闸前 P3 开关不生效)

| 期 | 内容 | 工期 | 验收要点 |
|---|---|---|---|
| P0 | factor_ls.py + 产物 + regen 增量步 + 子进程全量回填 | 1-2天 | PIT 测试过;锁纪律落实;默认回归不变 |
| P1 | jump_model.py + factor_regime.py + 产物 | 2-3天 | 截断不变性;切换频率;诚实缺席;重放 <10min |
| P2 | regime_gate.py + gate 产物 + 可读报告 | 2-3天 | 阳/阴对照;安慰剂;代理池口径;BH/DSR 齐 |
| P3 | api.py opt-in 接线 + 徽章 + GET /screen/regime + 前端 toggle/因子卡填充 | 1-2天 | 逐字节回归;降级显形;prod 兜底核验 |
| P4(可选) | 因子×市场档 IC 图谱纯展示产物(填现有因子卡,n_days<60 显「—」,FDR 标记) | 1天 | 只展示不驱动权重 |

## 10. 风险与诚实口径

1. **episode 稀缺是统计天花板**:每族 ~8-10 段 regime 行情,检验功效边缘(t≈1.5-4);
   若 6 族全不过 BH → 结论"无增量、不激活",算合法交付。
2. **~半月检测延迟**:快速反转市(如 2024-09 型)动态权重可能短暂反向;
   η=0.5 + clip 已把伤害封顶 ±25% 有效权重;延迟成本在闸内单独报数。
3. **blend 通道天花板**:条件化只作用于 (1−α) 因子腿,α=1 默认用法无感——UI 文档明示。
4. **多重检验纪律**:先族级后个体的顺序不可逆;gate schema 强制记假设总数,
   防实施期偷跑 56 个体因子挑好看的激活。
5. **共振/跟随族依赖指数参照列**(_inject_market_refs,399300.SZ 有停更前科):
   该两族 L/S 序列尾窗缺数时 regime 更频繁诚实缺席,削白名单但不造数。
6. **csi800 全历史物化算力**(10-30min):锁纪律(§5.1)是合入前置条件,不是优化项。

## 11. 挂账(不在本 spec 范围)

- **视角C「regime 路由 v4 变体」——评审否决,立此存照**:每档 3-5 个独立 episode
  撑不住变体差异;三层保守叠加(prod≥0.5 × 概率收缩 50% × 软加权)后扰动量级过不了
  自设激活闸(自锁);变体每日刷新在现 registry 架构(不存 booster)下不成立;
  改 market_status source 超出 opt-in 隔离面。若未来重启,须先解决以上四点。
- 56 个体因子 BH 扩展激活(t≳2.5,预期只剩个位数)。
- 估值/财务/成长/规模族:基本面管线修复后并入白名单,**须重新过闸**(spec_hash 指纹防陈闸)。
- 情绪/资金面族:数据源审计后决定是否并入。
- 涨停家数/连板/赚钱效应等 A股特有协变量:仓内 PIT 自测通过后准入。
- 帷幄 `ww_market_regime` 工具(读 GET /screen/regime):涉及 CONSOLE_ALLOWED 白名单/
  specs/_SYSTEM_PROMPT/守护计数四处同步,单独立项。
- w(blend α)寻优、与 dl_ensemble regime 条件化混合权。
