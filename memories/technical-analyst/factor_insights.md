# 因子经验库

## V7 扩容 — R27 市场宽度残差 broadcast (2026-04-26, v4_ranking 38 → 40 维)

[ic_probe_market_breadth.py](../tsfm_exp/scripts/ic_probe_market_breadth.py) 重建 R27 路线图的滚动 60 日残差化, 用 [strategy/research/market_breadth_panel.parquet](../strategy/research/market_breadth_panel.parquet) (1567 日, ~6 年):
```
lu_resid  = limit_up_total - OLS(limit_up_total ~ total_amount, win=60).fitted
amt_resid = total_amount - OLS(total_amount ~ limit_up_total, win=60).fitted
*_pct60   = 上述 60 日滚动百分位 [0, 1]
```

### 时序 IC vs 市场未来 5 日均值收益 (Spearman 全样本 1447 日)

| 信号 | ρ | ≥0.95 spread | win | n | ≤0.05 spread | win | n |
|---|---:|---:|---:|---:|---:|---:|---:|
| lu_resid_pct60 | +0.101 | +1.06pp | 64.8% | 108 | -0.17pp | 50.0% | 72 |
| **amt_resid_pct60** | **+0.228** | **+1.54pp** | **72.8%** | 173 | **-1.45pp** | **33.3%** | 117 |
| lu_count_pct_60d (panel 自带) | +0.382 | — | — | — | — | — | — |
| amount_pct_60d (panel 自带) | +0.323 | — | — | — | — | — | — |

**amt_resid_pct60 双向 A/S 级信号** (>=0.95 多, <=0.05 空), R27 D46 描述的"量价分化 / 惜售强势"机制.

### 集成方式: broadcast 到每只股票
[v4_ranking.py:147-167](../strategy/v4_ranking.py) 把每日 `(lu_resid_pct60, amt_resid_pct60)` 同值 broadcast 给当日所有股票. LGB feature_importance 实测 gain: amt_resid_pct60 **32,120 (rank #1)**, lu_resid_pct60 **30,993 (rank #2)**, 个股层因子全部 ≤ 2,652.

### ⚠ 重要 caveat: 这个 gain 不是 cross-sectional alpha

**Broadcast 特征同日所有股票同值** → 当天 LGB tree split 时全部股票走同一分支, **对当日横截面排序贡献 = 0**. gain 极高反映的是: 训练时这两维度上回报水平差异显著 (regime 之间均值漂移), LGB 用它"切换子模型" 在不同市场环境下用不同子树.

**风险**: 模型可能高度依赖训练集 regime 分布. 训练里没见过的 regime 时退化风险高. **缓解**: B3 (FinCast 40%) 独立信号稀释 + 自适应权重层跟随 regime 变化.

**后续优化方向** (V8 候选): 把 broadcast 特征**移出 LGB**, 改造为外层 regime gate (`amt_resid_pct60 >=0.95 时 LGB 分数 +α; <=0.05 时 -α`), 比塞进特征向量更干净, 防过度拟合.

## 大小盘线性交互项失败 (2026-04-26, 负面 — 不加 LGB)

[ic_probe_size_interaction.py](../tsfm_exp/scripts/ic_probe_size_interaction.py) 测了 4 个 base 因子 × `mv_quintile_norm` (Q1=-2 ... Q5=+2) 的乘积:

| 交互项 | base IC | 交互 IC | 结果 |
|---|---:|---:|---|
| rev_20 × mv | +0.029 | +0.009 | 削弱 |
| bias_ma20 × mv | -0.037 | **+0.003** | 几乎归零 |
| vol_20 × mv | -0.056 | -0.022 | 削弱 |
| turnover_pct_60 × mv | -0.040 | -0.011 | 削弱 |

**分位 IC 看到的非线性差异** (vol_20 / turnover_pct_60 在 Q5 大盘显著弱化):

| 因子 | Q1(小) | Q2 | Q3 | Q4 | Q5(大) |
|---|---:|---:|---:|---:|---:|
| vol_20 | -0.062 | -0.061 | -0.057 | -0.049 | **-0.028** |
| turnover_pct_60 | -0.036 | -0.055 | -0.042 | -0.038 | **-0.017** |

vol_20 / turnover_pct_60 在大盘 (Q5) 明显减弱, **但线性交互项乘积无法捕捉这种非线性差异** — LGB tree 本身就能在 mv 维度 split 后用不同子树学不同因子权重, 再加乘积是冗余 + 稀释信号. **结论: 不加交互项**. 已有的 v4 市值分层评级 (大盘归零 / 中盘打折 / 小盘满分, 见 rating_system.md) 是在 LGB 之外的外层调整, 不在 LGB 内部.

## V6 扩容 — 行业相对强度 (2026-04-26, v4_ranking 37 → 38 维)

承接 4-25 的"中性化全军覆没"负面结论, 反向尝试: **不抹掉行业 beta, 而是把行业整体强度作为附加特征**.
脚本 [ic_probe_industry_strength.py](../tsfm_exp/scripts/ic_probe_industry_strength.py) 测了 5 个行业级聚合因子 (csiall × 60 日 fwd_5d Spearman IC):

| 因子 | IC mean | ICIR | ρ vs 个股版 | 判断 |
|---|---:|---:|---:|---|
| ind_ret_5d | -0.019 | -0.138 | 0.41 | IC 不足 |
| ind_ret_20d | -0.030 | -0.192 | 0.37 | IC 临界 |
| **ind_turnover** | **-0.045** | **-0.314** | **0.42** | ✅ **三项全过** |
| ind_vol_20 | -0.018 | -0.107 | 0.45 | IC 不足 |
| ind_breadth_up_5d | -0.014 | -0.097 | 0.37 | IC 不足 |

### ind_turnover — 行业平均换手率
- 计算: 每个 (date, industry) 对 `$turnover_rate` 取均值, 回填到该行业每只股票
- 行业映射: tushare_stock_basic.parquet (110 一级行业, 99.9% 覆盖)
- IC -0.0447, ICIR -0.314, +IC 率 38.3%, ρ vs 个股 turnover = 0.42 (不冗余)
- 集成 [v4_ranking.py:122-145](../strategy/v4_ranking.py) 后 LGB feature_importance: **rank #2 gain 2294 仅次于 bias_ma20 (2652)**, 一进来直接成主导因子之一
- **机理**: 行业整体换手过热 → 主线行情即将退潮, 成员股近 5 日 fwd_ret 系统性下行
- 与个股 `turnover_pct_60` 互补: 一个抓个股层面 (相对自身均值), 一个抓行业层面 (整个板块换手). 两者 ρ=0.42, 在 LGB 里独立贡献

### 经验总结 — 行业因子的正反两面
- **抹掉行业 beta (中性化) 在 A 股全部失败** — A 股截面 alpha 主要来自全市场, 不是行业内
- **加入行业 beta (相对强度) 可以工作** — 行业整体换手是有效的市场环境信号
- 一句话: A 股不是 "stock vs industry", 而是 "stock × industry strength" 联合定价

## 行业中性化失败 (2026-04-25, A 股负面发现 — 暂不加 LGB)

[ic_probe_industry_neutral.py](../tsfm_exp/scripts/ic_probe_industry_neutral.py) 测了 6 个 LGB top importance 因子的"行业内残差版本":
`F_in[i,t] = F[i,t] - mean_{j∈industry(i)}(F[j,t])`, 行业用 tushare 一级 (110 类, 99.9% 覆盖).

**结果 (csiall 最近 60 日 fwd_5d IC)**:

| 因子 | 原始 IC | 中性化 IC | ρ(中性, 原始) | 结论 |
|---|---:|---:|---:|---|
| bias_ma20 | -0.037 | -0.018 | 0.852 | **削弱 -0.020** |
| rev_5 | +0.019 | +0.014 | 0.841 | 削弱 |
| vol_20 | -0.056 | -0.043 | 0.823 | 削弱 (但 ICIR -0.36→-0.47 改善) |
| stock_sharpe_60 | -0.037 | -0.017 | 0.882 | **削弱 -0.020** |
| turnover_pct_60 | -0.040 | -0.010 | 0.847 | **大幅削弱 -0.030** |
| rev_20 | +0.029 | +0.013 | 0.875 | 削弱 -0.016 |

**6/6 全部 IC 削弱**, 仅 vol_20_in 的 ICIR 单独改善 (但 IC 绝对值仍降). ρ 全部 0.82-0.88, 中性化没有真正"分离"出新信号.

**机理推断**:
1. **A 股行业 beta 同步性极强**: 涨跌停 / 主线轮动让同行业内方差小, 减均值后剩下的几乎是噪音
2. **一级行业过粗**: 110 个一级行业里同一个"专用机械" 包含锂电设备 / 半导体设备 / 矿山机械, beta 完全不同
3. **真截面 alpha 在全市场层**: rev_20 / bias_ma20 之所以 IC 显著, 来自于 A 股全市场反转规律, 不是行业内反转

**与美股经验对比**: 美股 (Fama-French / Barra) 行业中性化通常是 alpha 增强标配; A 股则相反 — 这是 A 股特殊的 "beta 即 alpha" 结构 (主线行情决定 alpha 来源, 不是个股 vs 行业的 selection).

**追加验证 (2026-04-25 当晚)**: 又测了 **行业内 rank** ([ic_probe_industry_rank.py](../tsfm_exp/scripts/ic_probe_industry_rank.py)) 作为减均值的替代 (rank 对偏态稳健, 也许能在均值法失效时残留信号). **同样全军覆没**: 6/6 IC 削弱, ρ(rank, raw) 0.84-0.90 比减均值版**更冗余**. 唯一亮点: vol_20_rk ICIR -0.501 vs raw -0.357 改善, 但 IC 绝对值仍降. **A 股行业内残差化 (减均值 / rank 两种思路) 全部失败**, 关闭此方向.

**结论**: **不加入 v4_ranking**. 但留下负面经验, 防止后续重复探索:
- 一级 (110) 行业中性化 (减均值 / rank) → **均失败, 放弃**
- 二级 / 概念板块中性化 → 待试 (但需要先采到稳定的 stock→concept 多对多映射)
- 真正可能有用的方向: **行业相对强度** (industry_boards 的板块 ret/turnover 当作市场环境因子, 而非中性化个股)

**规则沉淀**:
- A 股 LGB 特征工程, **不要**默认加上"行业中性化版本", 每个候选都要单独 IC 验证
- 美股 / 港股研究的中性化经验**不能直接迁移**到 A 股
- 若做行业纠偏, 改用 **"行业内排名 (rank within group)"** 而非"减均值", 因为 A 股截面 SNR 太低均值噪音大

## V5 扩容 (2026-04-24, v4_ranking 34 → 37 维)

近期 IC 探查 ([tsfm_exp/scripts/ic_probe_new_factors.py](../tsfm_exp/scripts/ic_probe_new_factors.py), csiall 最近 60 个交易日) 后新加 3 维 A 股专属因子:

### turnover_pct_60 — 换手率相对 60 日均值偏离
- 表达式: `$turnover_rate / (Mean($turnover_rate, 60) + 1e-8) - 1`
- IC -0.040, ICIR -0.354, +IC 率 36.7% (负相关稳定)
- 与 bias_ma20 ρ=0.52, 与 rev_20 ρ=-0.50 (中等相关但不冗余)
- LGB 集成后 feature_importance rank 16 (gain 811, **超过 rev_20 的 597**)
- **规范来源**: R27 D45 "动态分位 > 绝对阈值". 高换手相对自身均值偏离 = 关注度/筹码换手激增 = 短期顶部信号
- **跟踪**: 若 ICIR 跌破 -0.2 持续 3 个月, 需复查是否 regime 漂移

### ps_clip — 市销率 clip 到 (-100, 200)
- 表达式: `$ps_ttm.clip(-100, 200)` (`$ps_ttm` 原始已落 bin 文件)
- IC -0.034, ICIR -0.280
- **与 rev_20/bias_ma20 Spearman ρ ≈ 0 完全正交** (独立估值维度)
- LGB rank 24 (gain 385), 与 pe_clip (266) 互补
- **说明**: v4 原先只有 pe_ttm/pb, ps_ttm 提供"销售维度"估值, 与盈利估值 (PE) / 账面估值 (PB) 三维互补

### dv_ttm — 股息率
- 表达式: `$dv_ttm` (原始)
- IC +0.029, ICIR +0.173 (弱但正)
- 与 vol_20 ρ=-0.43, 其它 baseline ρ≈0 (独立)
- LGB rank 31 (gain 250), 最弱但有增量
- **说明**: 防御/价值信号, 熊市/震荡市场相对胜率提升

### 验证淘汰
- **pullback_20d** `$close/Max($high,20)-1`: IC +0.015 但与 bias_ma20 ρ=0.69 过度冗余, 弃
- **circ_ratio** `$circ_mv/$total_mv`: IC -0.003 无信号, 弃

### 加载决策规则 (v4_ranking 扩容时的硬判据)
- `|IC mean| ≥ 0.03 AND |ICIR| ≥ 0.3` → 直接加
- `|IC mean| ≥ 0.02 AND max|ρ(与 baseline)| < 0.3` → 正交独立, 弱也加
- `max|ρ| > 0.7` → 冗余, 即便 IC 强也弃 (LGB 自动处理冗余但会浪费 split 预算)

---

## V1 发现 (2026-04-05, 数据 2016-2025)

### 核心规律: A 股是反转市场
- 反转因子 (rev_20/10/5) 是最稳定的 alpha 来源
- ICIR +0.3~+0.5, 胜率 60~71%, 10/10 年一致为正
- 动量因子全面为负 = 追涨杀跌在 A 股是亏钱策略
- 20 日反转最强, 5 日反转最弱
- **事件维度再验证 (R8, 2026-04-17)**: "过去 20 日无涨停 + ret_20d ∈ [15%, 40%] + MA 多头" 趋势事件 51,403 个, fwd_5d 跑输 baseline **-0.16pp (p=0.0001)**, 月度 6/13 胜率不稳定. 因子面 `breakout_20` ICIR=-1.02 在事件维度被印证. 详见 [research/sentiment_round_8.md](research/sentiment_round_8.md).
- **量能 regime 信号 (R9, 2026-04-17)**: 全市场 1.27M 事件, `tr_surge_60 = MA5(turnover)/MA60(turnover)` **>=2.5 爆量负向 A 级** (月度 13/13, -1.14pp, p=0.0004). 高度 regime-dependent: up_strong+爆量 **-1.42pp** (派发), down_mild+爆量 **+0.94pp** (反转启动), down_strong+缩量 **+0.85pp** (跌无可跌). tr_surge_60 与 ret_20d Spearman 0.541 共线, 但 regime 分层后独立增量. 详见 [research/sentiment_round_9.md](research/sentiment_round_9.md).
- **ICIR 残差化验证 (R10, 2026-04-17)**: 对 R9 的 volume-regime 信号做 OLS 残差化 (控制 rev_20 + turnover_rate + vol_20): **distr flag 残差 RankICIR 0.55 (保留 53%)**, **bounce flag 0.20 (保留 66%)** 均 A 级独立增量; 但 signed combo 残差 0.004 (99% 衰减) 被吃干放弃. 双 flag 抵抗线性残差的原因: AND 交叉触发是非线性交互. 详见 [research/sentiment_round_10.md](research/sentiment_round_10.md).
- **涨停 5min 封板微结构 R12 翻案 (2026-04-17)**: 全市场 15,933 涨停事件 × 10 微结构特征. **seal_bar (封板时间) S 级**: 秒板 (bar 0) monster 率 30.6% vs 尾盘 (bar 43-47) 7.9%, spread 跨度 22pp, 月度 **13/13** mean +11.15pp. vol_before_seal Cohen's d=-0.48 最强. **seal_at_close=False 剔除器**: 收盘破板 monster 率 2.6% 几乎必然 oneday. 一字板 (gap_open≥9%) monster 39%. **R1 n=13 结论被彻底推翻** (F12 方法论教训: n>5000 才做微结构). 建议 board_scorer v5 第五维 score_seal_micro(-3~+3). 详见 [research/sentiment_round_12.md](research/sentiment_round_12.md).
- **5min 日内形态 (R11, 2026-04-17)**: 全市场 1.25M 日度特征 (5,354 股 × 253 天). **E1 尾盘爆量拉升** (`ret_close_30m>2% AND vs_close_30m>18%`) fwd_5d **-1.40pp** (月度 12/13 S 级, Q4 大盘 -2.00pp). **E5 小盘 V 字反转** +1.13pp (开盘跌+尾盘涨+Q1). 连续因子 `intraday_ret` 10 分位单调反转 (bin 0→9 spread +0.53→-0.25pp, 日内 rev_1). 形态发现: **日内高点在尾盘 -0.28pp / 日内低点在午后 +0.64pp / 长下影线反跑输 -0.27pp**. 尾盘三信号 (涨幅 + 量占比 + 高点位置) 方向一致. 详见 [research/sentiment_round_11.md](research/sentiment_round_11.md).

### 低换手/低波/低流动性溢价
- turnover_20 反向 ICIR=-0.49 (低换手好)
- vol_20 反向 ICIR=-0.34 (低波好)
- amihud_20 正向 ICIR=+0.29 (流动性差→溢价)
- 冷门股有超额, 但实盘需注意冲击成本

### 技术指标几乎无效 (截面选股)
- RSI/MACD 的 IC 绝对值小, 信号不稳定
- 但 RSI 在个股研报中仍有参考价值 (超买超卖信号)

### 持仓周期
- 20 天目标 IC 强于 5 天
- 但实际交易用 5 天换仓更灵活

## V2/V3 验证 (2026-04-05)

### 自定义因子 >> Alpha158
- 15个精选因子年化 +57%, 158个因子年化 -24%
- 核心: 反转 + 均线偏离 + RSI + 量价
- LightGBM 特征重要度: bias_ma60 > avg_amplitude > bias_ma20 > rev_5 > log_mv

### 估值因子有效
- PE/PB 加入后 2023 年从 -0.4% 提升到 +29.3%
- 低 PE 加分、亏损股降权的逻辑可行

## 主力行为因子 (2026-04-06 新增)

### OBV (On Balance Volume)
- 量价共振: OBV 趋势与价格一致 → 趋势可靠
- 顶背离: 价涨但 OBV 跌 → 主力出货信号
- 底背离: 价跌但 OBV 涨 → 主力吸筹信号

### VR (Volume Ratio 涨跌量比)
- 20日上涨日成交量 / 下跌日成交量
- VR > 2.0: 强势, 上涨放量下跌缩量 → 主力控盘
- VR < 0.5: 弱势, 下跌放量 → 恐慌抛售

### 下影线比例
- (min(open,close) - low) / (high - low)
- 近10日均值 > 30%: 下方承接力强, 主力护盘
- < 10%: 卖压弱或无抵抗下跌

### MFI (Money Flow Index)
- 典型价格 × 成交量的 RSI
- > 80: 资金过热(可能见顶)
- > 60: 资金流入
- < 40: 资金流出
- < 20: 资金枯竭(可能见底)

### 筹码位置
- 价格在 20 日高低区间的百分比
- > 80%: 近顶部, 套牢少但追高风险
- < 20%: 近底部, 浮亏多但超卖

### 波动率变化
- 5日波动率 / 20日波动率
- < 0.5: 缩波, 可能酝酿变盘
- > 2.0: 扩波, 行情活跃

### 主力综合判断
- 吸筹 = OBV底背离 + VR偏强 + 下影线长 + MFI流入 + 缩波
- 出货 = OBV顶背离 + VR偏弱 + MFI过热 + 大阴线多
- 实测: 奥瑞德检测到吸筹(验证偏多), 平安检测到资金流出(验证偏空)

## 实战教训

### "过度谨慎"是最大的敌人
- 规则分析师(+23%) 远不如纯模型(+503%)
- 反转策略天然选"看起来危险"的股票, 过滤掉就等于否定策略
- 只在系统性暴跌时空仓, 平时相信模型

### 回测收益打折
- V3 回测年化 57%, 自我复盘实测年化 ~15%
- 幸存者偏差 + 未来信息泄露 + 涨跌停 + 滑点 → 至少打对折
- 真实预期: 年化 15~25%, 最大回撤 30~40%

## 5min 因子 (10轮挖掘, 2026-04-10 完成, 300只×200天)

### Tier 1: 确认有效 (|IC|≥0.04, 跨轮复现)
- **hl_range_norm** IC=-0.063, ICIR=-0.563: 日内振幅越大→后续越差 (最强单因子)
- **true_range_enhanced** IC=-0.062, ICIR=-0.598: ATR增强版 (与hl_range_norm高度冗余)
- **max_bar_return** IC=-0.065, ICIR=-0.581: 最大单bar涨幅→后续差 (R20复现✓)
- **open_amp_10bar** IC=-0.059, ICIR=-0.544: 开盘10bar振幅大→后续差
- **rank_opening_calm** IC=+0.050, ICIR=+0.631, 胜率73%: 开盘平静复合排名 (最高ICIR!)
- **stability_x_low_vov** IC=-0.055: 趋势稳定×低VoV交互因子
- **trend_stability** IC=+0.051, ICIR=+0.47: 日内趋势一致性 (新维度,日线无法捕捉)
- **intraday_drawdown** IC=+0.048, ICIR=+0.43: 日内回撤小→后续好 (R20复现✓)
- **open_bar_amplitude** IC=-0.041, ICIR=-0.55: 首bar振幅 (R20复现✓)
- **gap_abs** IC=-0.043: 跳空越大→后续越差 (R20复现✓)

### Tier 2: 可用 (0.03≤|IC|<0.04)
- **volume_entropy** IC=+0.039 (R20复现✓): 量分布越均匀→越好
- **first_bar_morning_ratio** IC=-0.038: 首bar量/早盘量→集中越差
- **volume_half_life** IC=+0.035: 量衰减半衰期→衰减慢好
- **amihud_intraday** IC=+0.035, 20d IC=+0.052: 日内流动性低→溢价
- **vol_of_vol** IC=-0.032: 波动率的波动高→差

### ❌ 复现失败 (10轮教训!)
- ~~morning_volume_ratio IC=+0.069~~ → R20复现 IC=-0.029 **方向翻转!不可用**
- ~~abnormal_vol_bars IC=+0.060~~ → R20复现 IC=-0.008 **崩塌至零!不可用**
- ~~kurtosis_vol_weighted IC=-0.035~~ → R20复现 IC=+0.007 **方向翻转!不可用**
- **教训**: 单次高IC不可信, 必须跨样本/时间窗口复现

### 正确的 5min 逻辑 (10轮验证)
- **核心=低波动+反转**: 和日线完全一致
- 日内波动越大(振幅/极端bar/跳空) → 后续越差
- 趋势越稳定(不是涨多跌多, 是走势平顺) → 后续越好
- 开盘越平静(小振幅/低量) → 后续越好
- 尾盘放量 ≠ 吸筹, Smart Money ≠ 机构 → 不可用
- **新增维度**: trend_stability 和 open_bar_amplitude 是日线因子无法捕捉的

### 推荐组合 (去冗余, 5个核心 + 3个复合)
1. hl_range_norm (波动维度)
2. trend_stability (趋势质量维度, 日线新维度)
3. open_bar_amplitude (开盘行为维度, 日线新维度)
4. amihud_intraday (流动性维度)
5. intraday_drawdown (控盘维度)
6. rank_opening_calm (复合, ICIR最高)
7. ic_weighted_composite (复合)
8. rank_full_composite (复合)

### 详细报告: strategy/factors/5min_10round_report.md

## 日线主力行为因子 (2026-04-06 验证, 100只×60评估日)

### 有效 (|IC| > 0.03)
- **big_dn_ratio** IC=+0.081: 近期大阴线越多→后续越涨 (最强!又是反转)
- **vol_change** IC=-0.038: 波动率收缩→后续好 (对了)
- **body_ratio** IC=-0.037: 十字星/小实体多→后续好
- **big_up_ratio** IC=+0.034: 大阳线多→继续涨 (动量延续)
- **obv_diverge** IC=+0.031: 底背离→后续涨 (对了)

### ⚠ 验证推翻的因子
- ❌ **MFI 资金流** IC=+0.002 → **完全无效**, 之前当核心信号用的
- ❌ **筹码位置** IC=-0.000 → **完全无效**, 删掉
- ❌ **下影线** IC=+0.011 → **几乎无效**, 之前说"承接力强=主力护盘"不成立
- ❌ **连续涨跌** IC=+0.007 → **无效**

### 勉强有用 (0.02 < |IC| < 0.03)
- VR 涨跌量比 IC=+0.026: 弱正, 方向对了
- OBV 趋势 IC=+0.025: 弱正
- 上影线 IC=+0.023: 弱正

### 核心结论
日线"主力行为"因子大部分是无效的。真正有用的还是**反转逻辑**: 大阴线多=超跌=反弹。
所谓的"吸筹/出货"判断, MFI和筹码位置完全没有预测力, 不应该出现在研报里。

## 主题联动信号全证伪 (2026-04-14 walk-forward)

`strategy/theme_report.py` / `src/analysis/relational.py` 生成的 4 条主题级信号,
经 2022-01..2026-04 walk-forward 全部证伪. 数据 / 脚本见 [strategy/research/2026-04-14_theme_*.md](research/).

### ❌ 主题相对强度 top20% 做多 / bot20% 做空
- 7 簇 K=20: net Sharpe -0.46 (医药, 最不差) ~ -1.41 (中游制造), mean -0.57%~-1.81%, winrate 0.31~0.47
- 7 簇 K=5: net Sharpe -2.27 ~ -3.28 (滑点 4×)
- **gross 依然全负**: long 桶 2022-2026 mean fwd20 **-0.25%**, short 桶 **+0.40%** — 被做空的"上期输家"反而比"上期赢家"涨得更好
- 根因: 与 A 股 rev_20 反转主导完全一致. 本信号 = **反向 rev_20**, 必然亏
- 医药 +17% smoke test 是 2026Q1 局部主题行情 cherry-pick (14 obs 正, 其余 337 obs 全负)
- **方向正确的版本是"簇内反转 long bot short top", 但增量于全市场 rev_20 未验证, 生产上就用 LGB 里的全市场 rev_20**

### ❌ 领先-滞后 pair-by-pair 交易
- 消费/医药/TMT 3 簇 top 80 流动性, window=60 max_lag=2 min_rho=0.4, 0.2% 滑点
- 4,895 pair records 95,670 trades: **net daily Sharpe -3.55~-4.62** 全部深度负
- **gross hit rate 48.7~50.3%** 硬币, 消费 TMT gross 仅 +0.2 bp/天 医药负
- **配对稳定性 T→T+1 仅 16~20%** (>80% 20 日后消失)
- **致命诊断**: in-sample ρ 与 OOS gross return 的 corr **≈ 0** (-0.07/+0.03/-0.02), 选 ρ 这个标准本身与盈亏无关
- 结论: `lead_lag_pairs` 是描述性统计工具, **不可做交易信号**. 想救活此信号需要 (a) 要求同一对跨多个识别窗口持续 (b) ρ 阈值升到 0.6+ (c) 聚合到组合层面而非 pair 层

### ❌ 协整对 (60d Engle-Granger + |z|>2)
- 簇 1/2/3 top 80, |corr|>0.7 候选, ADF 预筛 + coint p<0.05, 触发 |z_current|>2, 持仓 20 天或 |z|<0.5 提前出
- 单边滑点 0.3%, 双腿覆盖: 2×0.003×(1+|β|)
- n=96 trades, **平均净 -1.06%**, per-trade Sharpe -0.107, 胜率 56%, z 回归出场仅 **56%** (相当部分一路走到 20 天硬时停)
- 结论: 60d 窗口的协整显著性无法支撑 20 天均值回复, 滑点吞噬全部 gross alpha. 符合"60d coint + 20d 持仓 A 股失效"的经典判断

### ❌ 分散度 regime 规则 (UP→RS / DN→EQ)
- 消费簇 top 100, 10d rebalance, 20d 横截面 std 最近 10d 斜率做 regime
- RS in UP Sharpe **-3.51** vs DN **-2.02** — **方向反了**, UP 里 RS 更差不是更好
- gated 年化 -53.3% 比 always-EQ -34.8% 更差 (绝对负里 ~30pp 是 4×0.3% 全换仓硬滑点, 5pp 是消费熊)
- 结论: 条件规则方向反 = 过拟合故事, 理论上"分散度上升做 RS"的直觉在 A 股未兑现

### 候选 2 后续: 领先组合聚合 IC — 主假设失败, 医药簇独活 (2026-04-14)
- 设置: 7 簇各取流动性 top 60, 每 20d 重评. 领先票打分 `leader_score(i) = Σ_lag∈{1,2} Σ_j max(0, corr(r_i(t), r_j(t+lag)))`, 取 top 5 leaders score>0. 每日 `leader_signal = mean(r_leaders(d))`, 预测 follower 组合 `fwd_5d`. 对照 `control_signal = mean(r_全60(d))`
- 样本: 7 簇 × 1028 日 = 7196 obs, 52 rebalance
- 5d ΔIC (leader - control): 金融 +0.003 / 消费 +0.00002 / **医药 +0.0364** / TMT -0.005 / 周期 -0.018 / 制造 +0.009 / 基建 +0.014
- 1d 尺度: 5/7 簇 ≤ 0 → 没有日内领先, 任何信号都是 3-5 日扩散尺度
- 主假设通过簇数: 1/7 (阈值要求 ≥4), **主假设 FAIL**
- **医药簇独活**: ΔIC +0.0364, 跨 5 年 **全部为正** (+0.019 / +0.050 / +0.032 / +0.096 / +0.076), 和之前医药 +17% smoke cherry-pick **不一样** (那是单点行情, 这是时序 IC 跨年稳定)
- 假说: 医药子板块异质性大 (创新药/CXO/器械/中药), 龙头当日反应 = 子板块切换的 3-5 日扩散信号
- 知识增量: **领先滞后聚合信号不能作为通用 7 簇因子**, 但医药簇可能有真正的结构性领先扩散, 值得做专项 walk-forward 验证过拟合
- 待办: 医药簇 leader_signal 切真 train/test walk-forward 验证, 确认不是 1028 天 IC 过拟合
- → [research/2026-04-14_leader_portfolio_ic.md](research/2026-04-14_leader_portfolio_ic.md)

### 候选 1 后续: 簇内反转 (rev_20_cluster) 无独立 IC 增量 (2026-04-14)
- 实验: 2022-01..2026-04, 203 截面, 全 A, 7 簇 (排除综合), 每簇做 rev_20 的截面 z-score 得 `rev_20_cluster`, OLS 残差对全市场 rev_20 正交化
- 结果:
  - IC global +0.075 / ICIR +0.59 / winrate 76% ✓
  - IC cluster raw +0.068 / ICIR +0.64 / winrate 74% — 看似强, 但与 global 共线 β≈1
  - **IC cluster resid (⟂ global) -0.006 / ICIR -0.06 / winrate 50.7%** — 紧贴 0 轻微负
  - 跨年度: 仅 2023 resid +0.012, 2022/2024/2025 全负 (1/5 为正)
- 结论: **"簇内反转是新信号"假设证伪**. 方向对 (raw 同号), 但它不是新信号 — LGB 已有全市场 rev_20 的截面排序**完全吸收**了簇内相对位置. 加 `rev_20_cluster` = 喂同一个信号两次.
- 知识增量: A 股反转效应**不依赖行业维度**, 是全市场跨截面规律, 不需要做行业中性化切片
- → [research/2026-04-14_cluster_reversal_ic.md](research/2026-04-14_cluster_reversal_ic.md)

### 可以做的事
- 主题层面的相对强度/联动/分化**仅作格局描述**, 写进研报给研究员看, 不作为买卖指令
- 主题景气度 (gm20/gm60 超额 + 胜率) 作为个股 LGB + v4 评级的**上下文提示**, 不替代最终定价
- 最终买卖决策由 `strategy/v4_ranking.py` (34 因子 + 市值分层评级) 下的单票研报 (`report_v2.py`) 负责

### 研究线关停决定: 基于簇统计结构的多股因子 (2026-04-14)

**决定**: 关停"按簇切片 + 统计信号 (相关 / 协整 / 反转 / 领先滞后)" 的多股因子研究方向.

**证据链** (6 组实验, 2026-04-14 当日跑完):
1. 相对强度 top/bot: 7 簇 net/gross 全负, 方向反 rev_20
2. pair 领先滞后: Sharpe -3.55~-4.62, in-sample rho 与 OOS 无关, 配对 T→T+1 稳定性 16~20%
3. 协整 60d+20d+|z|>2: n=96 Sharpe -0.11 (小样本, 单参数)
4. 分散度 regime: 单簇 n=99 方向反 (小样本)
5. **簇内反转 (rev_20_cluster)**: 残差 IC -0.006 ≈ 0 — **核心根因** → A 股主 alpha (rev_20) 是**全市场截面规律, 不依赖行业维度**, 按簇切片本质是冗余 rev_20
6. leader portfolio 聚合: 6/7 簇 ΔIC ≈ 0, 医药 +0.036 孤立
7. 异质性假说: Spearman -0.39 p=0.38 不显著, 医药 mean_corr 排 4/7 不是最低, 独活无结构性解释

**为什么这条路不通**:
- A 股反转结构是跨行业统一的, 行业维度不是 alpha 来源
- 簇内相关 / 协整 / 价差 / 领先滞后 都是**既有 rev_20 信号的不同投影**, 减掉 rev_20 后剩下的是噪声
- 医药 leader +0.036 可能是 7 次实验里的假阳性 (Bonferroni 后勉强保本), 没有机制解释前不上产品

**留档**:
- `src/analysis/relational.py` / `strategy/theme_report.py` / `agent_prompts.THEME_SYNTHESIZER_PROMPT` 代码保留, 作为"主题可视化"工具, 但输出只作研究员参考, 不接 LGB, 不生产交易信号
- 医药 leader_signal 作为已知异常留在 [research/2026-04-14_leader_portfolio_ic.md](research/2026-04-14_leader_portfolio_ic.md), 如果将来找到机制 (集采/药审/子板块 phase lag) 再重启

### 后续多股因子探索方向 (换机制)

基于"簇统计结构" 这条路被证伪, 未来多股探索必须跳出相关 / 协整 / 反转这套信号形式, 换本质不同的机制:

1. **事件驱动**: 公告 / 业绩 / 集采 / 政策催化在簇内的扩散路径 (事件时间对齐而非日历时间)
2. **资金流结构**: 北向持仓变化 / 公募重仓调整 / 融资余额相对变化, 从资金视角看簇内切换
3. **跨资产联动**: 债券收益率 / 美元指数 / 大宗商品对 A 股簇的条件影响 (周期簇最可能有信号)
4. **期权隐含信息**: 如果有 50ETF / 300ETF / 500ETF 期权 IV 数据, 看簇级恐慌/贪婪切换
5. **机构抱团结构**: 同一基金持仓网络 → 共同抛售/建仓风险

以上 5 条共同特征: **不是单纯的价格统计, 而是引入新的信息源**. 只有引入新信息才能产生相对于 rev_20 正交的 alpha. 继续在价格统计空间里挖是浪费时间.

---

## 待验证
- 情绪因子 (新闻情感, 社交媒体情绪)
- 国际环境因子 (美股/美债/汇率/大宗商品)
- 行业轮动因子 (板块强弱切换)
- 资金面因子 (北向资金流向/融资余额变化)
- 5min因子 + 日线因子组合效果
- **簇内反转** (long 簇内 bot / short 簇内 top) 是否比全市场 rev_20 提供独立 IC 增量
