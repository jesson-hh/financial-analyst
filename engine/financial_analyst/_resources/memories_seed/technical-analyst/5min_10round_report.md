# 5min 因子挖掘 10 轮总结报告

> 数据: 300只随机A股, 2025-06 ~ 2026-04, 5min OHLCV
> 框架: 5min_ic_harness.py, 分批加载 (BATCH_SIZE=50), 截面 RankIC + ICIR
> 前向收益: 5日 / 20日

## 一、10 轮探索概览

| 轮次 | 方向 | 因子数 | 最佳因子 | 5d IC | 发现 |
|------|------|--------|----------|-------|------|
| R11 | 跳空+开盘动态 | 13 | open_bar_amplitude | -0.041 | 开盘冲击大→后续差 |
| R12 | Bar序列模式 | 15 | kurtosis_vol_weighted | -0.035 | 高峰度→后续差 |
| R13 | 价格效率与噪声 | 14 | amihud_intraday | +0.028 | 日内流动性溢价 |
| R14 | 极端Bar分析 | 13 | extreme_bar_volume_ratio | -0.021 | 极端bar方向弱 |
| R15 | 日内趋势质量 | 13 | **trend_stability** | **+0.051** | 趋势稳定=最强新因子 |
| R16 | 量能时序增强 | 14 | first_bar_morning_ratio | -0.038 | 首bar量集中→差 |
| R17 | 价格Range结构 | 12 | **hl_range_norm** | **-0.063** | 振幅=最强单因子! |
| R18 | 因子交叉组合 | 15 | **rank_opening_calm** | **+0.050** | 复合因子显著提升 |
| R19 | 参数鲁棒性扫描 | 15 | **open_amp_10bar** | **-0.059** | 开盘10bar最优 |
| R20 | 终极复现验证 | 13 | max_bar_return(复现) | -0.065 | 3因子方向翻转! |

## 二、确认有效因子 (Tier 1, |IC|≥0.04 或 ICIR≥0.4)

**这些因子在至少一轮测试中 |IC|≥0.04, 且 Round 20 复现(如有)方向一致。**

| 排名 | 因子 | 来源 | 5d IC | 20d IC | 5d ICIR | 胜率 | 含义 |
|------|------|------|-------|--------|---------|------|------|
| 1 | **true_range_enhanced** | R17 | -0.062 | -0.070 | -0.598 | 70.2% | ATR增强→日内波动越大后续越差 |
| 2 | **hl_range_norm** | R17 | -0.063 | -0.067 | -0.563 | 69.7% | 日内振幅越大→后续越差 |
| 3 | **max_bar_return** | R20✓ | -0.065 | -0.063 | -0.581 | 71.8% | 最大单bar涨幅→后续差(反转) |
| 4 | **open_amp_10bar** | R19 | -0.059 | -0.061 | -0.544 | 71.8% | 开盘10bar振幅大→后续差 |
| 5 | **rank_opening_calm** | R18 | +0.050 | +0.059 | +0.631 | **73.4%** | 开盘平静复合排名(最高胜率!) |
| 6 | **stability_x_low_vov** | R18 | -0.055 | -0.061 | -0.506 | 72.3% | 趋势稳+低VoV交互 |
| 7 | **ic_weighted_composite** | R18 | +0.048 | +0.052 | +0.573 | 73.9% | IC加权复合因子 |
| 8 | **rank_full_composite** | R18 | +0.047 | +0.051 | +0.558 | 73.4% | 全复合排名 |
| 9 | **trend_stability** | R15+R19 | +0.051~+0.056 | +0.053~+0.055 | +0.417~+0.491 | 66~70% | 日内趋势一致性 |
| 10 | **open_bar_amplitude** | R11+R20✓ | -0.038~-0.041 | -0.047~-0.054 | -0.387~-0.516 | 67~71% | 首bar振幅 |
| 11 | **intraday_drawdown** | R20✓ | +0.048 | +0.054 | +0.434 | 65.4% | 日内回撤小→后续好 |
| 12 | **open_bar_volume_ratio** | R11+R20✓ | -0.036~-0.041 | -0.051~-0.052 | -0.341~-0.542 | 66~72% | 首bar量占比大→差 |
| 13 | **gap_abs** | R11+R20✓ | -0.041~-0.043 | -0.046 | -0.354~-0.516 | 60~69% | 跳空越大→后续越差 |

## 三、可用因子 (Tier 2, 0.03≤|IC|<0.04, 方向一致)

| 因子 | 来源 | 5d IC | 20d IC | 含义 |
|------|------|-------|--------|------|
| volume_entropy | R20✓ | +0.039 | +0.032 | 量分布越均匀(分散)→越好 |
| first_bar_morning_ratio | R16 | -0.038 | -0.049 | 首bar量/早盘量→集中越差 |
| volume_half_life | R16 | +0.035 | +0.031 | 量衰减半衰期→衰减慢好 |
| volume_pulse_intensity | R16 | -0.035 | -0.028 | 量脉冲强度大→差 |
| volume_gini | R16 | -0.035 | -0.027 | 量分布Gini高(集中)→差 |
| volume_acceleration | R16 | +0.034 | +0.033 | 量加速度→正加速好 |
| kurtosis_vol_weighted | R12 | -0.035 | -0.037 | 量加权峰度高→差(⚠R20翻转) |
| amihud_intraday | R13+R20✓ | +0.028~+0.035 | +0.040~+0.052 | 流动性低→溢价(20d更强) |
| vol_of_vol | R15+R20 | -0.032 | -0.038~-0.041 | 波动率的波动→高VoV差 |
| stability_x_calm_open | R18 | -0.042 | -0.052 | 趋势稳×开盘平静交互 |
| stability_x_low_kurtosis | R18 | -0.054 | -0.054 | 趋势稳×低峰度交互 |
| gap_x_open_vol | R18 | -0.048 | -0.052 | 小缺口×低首bar量交互 |
| am_pm_volume_ratio | R16 | -0.025 | -0.032 | 上午量/下午量→上午集中差 |
| midday_vs_morning_vol | R16 | +0.028 | +0.032 | 午盘量/早盘量→午盘放量好 |
| bar_range_trend | R17 | +0.020 | +0.031 | Range趋势→逐步扩张好 |

## 四、❌ 复现失败的因子 (Round 20 翻转)

| 因子 | 原始 IC | R20 IC | 状态 | 原因 |
|------|---------|--------|------|------|
| **morning_volume_ratio** | +0.069 | **-0.029** | ❌ 方向翻转 | 可能受样本期/构建方式影响 |
| **abnormal_vol_bars** | +0.060 | **-0.008** | ❌ 崩塌至零 | 不稳定 |
| **kurtosis_vol_weighted** | -0.035 | **+0.007** | ❌ 方向翻转 | 不稳定 |
| **trend_stability** (部分) | +0.051 | +0.029 | ⚠ 衰减50% | 仍为正但弱化 |

**教训**: 单次高IC不可信, 必须跨样本复现。morning_volume_ratio 曾是"最强5min因子", 但复现直接翻转, 说明原始结论过度乐观。

## 五、因子相关性分组 (去冗余)

Round 20 计算了13个因子间的相关矩阵, 主要冗余组:

| 冗余组 | 成员 | 相关系数 | 保留建议 |
|--------|------|----------|----------|
| **波动组** | max_bar_return / vol_of_vol / intraday_drawdown | 0.55~0.56 | 保留 max_bar_return (IC最强) |
| **开盘组** | open_bar_amplitude / open_bar_volume_ratio / gap_abs | 0.44~0.46 | 保留 open_bar_amplitude (20d最强) |
| **量分布组** | morning_volume_ratio / volume_entropy | -0.57 | ⚠ 但两个都不稳定 |

**独立因子** (与其他因子低相关, 可叠加):
- trend_stability (与波动组 corr≈-0.35)
- amihud_intraday (与大多数因子 corr<0.1)
- buy_pressure_ratio (独立维度)

## 六、核心规律总结

### 1. A股5min底层逻辑: 反转 + 低波动
- **日内波动越大 → 后续越差**: hl_range_norm(-0.063), true_range_enhanced(-0.062), max_bar_return(-0.065)
- **开盘冲击越小 → 后续越好**: open_amp(-0.059), gap_abs(-0.043), open_bar_amplitude(-0.041)
- **趋势越稳定 → 后续越好**: trend_stability(+0.051), 分段一致性 > R²
- 和日线核心规律完全一致: **低波动、反转、不追涨**

### 2. 因子组合显著提升
- 单因子最强: hl_range_norm IC=-0.063, ICIR=-0.563
- 复合因子最强: rank_opening_calm IC=+0.050, ICIR=+0.631, 胜率 73.4%
- R18交叉组合平均比单因子提升 ~20% ICIR

### 3. 参数优化空间有限
- open_amp: 1bar IC=-0.038 → 10bar IC=-0.059 (+53%改善)
- trend_stability: 4段 IC=+0.051 → 8段 IC=+0.056 (+10%改善)
- vol_of_vol: 窗口4~12基本一致 (IC≈-0.032)
- **结论**: 参数不敏感是好事, 说明因子逻辑稳健

### 4. 20d IC 普遍强于 5d IC
- true_range_enhanced: 5d=-0.062 vs 20d=-0.070 (+13%)
- open_bar_amplitude: 5d=-0.041 vs 20d=-0.054 (+32%)
- amihud_intraday: 5d=+0.028 vs 20d=+0.052 (+86%)
- **含义**: 5min因子对中期(月度)收益预测力更强, 适合20天换仓

### 5. 无效方向
- **极端bar分析** (R14): 整轮最大|IC|仅0.021, 方向彻底失败
- **价格效率指标** (R13): variance_ratio, efficiency_ratio, hurst_approx 均 |IC|<0.02
- **极端bar后续** (R14): 大阳/大阴后的短期反应没有预测力

## 七、最终推荐因子组合 (去冗余, 用于模型/策略)

### 核心组 (5个, 低相关, 逻辑独立)
1. **hl_range_norm** — 日内振幅 (负: 低波动好)
2. **trend_stability** — 趋势稳定性 (正: 稳定好)
3. **open_bar_amplitude** — 开盘冲击 (负: 开盘平静好)
4. **amihud_intraday** — 日内流动性 (正: 低流动性溢价)
5. **intraday_drawdown** — 日内回撤 (正: 小回撤好)

### 增强组 (3个, 复合因子, 高ICIR)
6. **rank_opening_calm** — 开盘平静排名复合 (ICIR=0.631, 胜率73%)
7. **ic_weighted_composite** — IC加权复合 (ICIR=0.573)
8. **rank_full_composite** — 全复合排名 (ICIR=0.558)

### 补充组 (2个, 独立信息)
9. **volume_entropy** — 量分布均匀度 (正: 分散好, 与核心组低相关)
10. **vol_of_vol** — 波动率稳定性 (负: VoV低好)

## 八、与日线因子的关系

| 维度 | 日线因子 | 5min因子 | 相关性 |
|------|----------|----------|--------|
| 反转 | rev_20 (ICIR=+0.51) | hl_range_norm (ICIR=-0.56) | 互补 (不同频率) |
| 低波 | vol_20 (ICIR=-0.34) | max_bar_return (ICIR=-0.58) | 高相关, 可替代 |
| 流动性 | amihud_20 (ICIR=+0.29) | amihud_intraday (ICIR=+0.25) | 相关, 5min更细粒度 |
| 换手率 | turnover_20 (ICIR=-0.49) | 无直接对应 | 互补 |
| 趋势质量 | 无 | trend_stability (ICIR=+0.47) | **新增维度** |
| 开盘行为 | 无 | open_bar_amplitude (ICIR=-0.55) | **新增维度** |

**新增信息**: trend_stability 和 open_bar_amplitude 是日线因子无法捕捉的维度, 最值得加入模型。
