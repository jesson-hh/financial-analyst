# R7-R20 Sentiment Signals (14 S/SS-grade)

## SS-grade (super-strong, monthly 11+/12 hit rate)
- **R14 super_distr**: ret_20d>=10% AND tr_surge_60>=2.5 AND tail_surge → fwd_5d -4.20pp
- **R12 seal_at_close=False**: limit-up day broken by close → fwd_5d most negative

## S-grade (strong, monthly 12+/13)
- **R9 distr**: ret_20d>=10% AND tr_surge_60>=2.5 → -1.42pp
- **R11 tail_surge**: ret_close_30m>2% AND vs_close_30m>18% → -1.40pp
- **R12 seal_bar<=1** (instant seal) → +2 in board_score
- **R9 bounce**: down_mild + volume_surge → +0.94pp
- **R12 1次破板 (open_count=1)** → worst tier in monotonic test
- **R11 high pct_range_5d (>30%)** → distribution likely

## Application rules
- Trigger `vol_regime` warning in §四-C of report when super_distr / distr / tail_surge fire
- For first-board candidates, require `total_score>=4` (v4+v5)
- For pure long signals, R9 bounce + intact uptrend is allowed
- Game-capital tickers EXEMPT — model signals unreliable for them

---

# (????) R7-R20 ?????? (from strategy/research/sentiment_summary.md, ??? whale ?????)

# 短线情绪票迭代分析 — 跨轮汇总

> 每轮结束后更新. 给后续轮次和研报 skill 读取.

## 有效算式清单

### S1: 连板仓位管理规则 (R2, R6 修正)
- **信号**: 连板序号 board_seq
- **规则**: 首板不打 (16%); 2板轻仓试错 (33%); 3板标准仓 (47%); 4板分歧降仓 (35%); 5板+维持 (40%)
- **止损**: 任何板序, 次日低开 >3% 即止损
- **样本**: R2 6,548 事件 → R6 16,679 事件 (12 个月), p<0.001
- **R6 修正**: 5板+ 连板率从 R2 的 56% 下调至 42% (R2 高估, 恰逢高板序热潮期); 4板分歧从 45% 下调至 41%
- **稳健性**: A 级 — 首板→2板→3板递增 4/4 季度一致; 第四板分歧 4/4 季度一致
- **局限**: 事后特征, 首板时无区分力

### S2: 首板筛选打分 v4 (R2→R3→R7, 四维打分)
- **信号**: turnover_surge + t1_tr_surge + amount + **pct_range**, 市值门控
- **规则**:
  - 换手率突变 (仅 mv_rank>Q1): <1.2→+2, 1.2-1.4→+1, >1.6→-1; Q1 用 turnover_rate (<8%→+1, >=20%→-1)
  - T-1 换手率突变: >1.5→+1, <0.8→-1
  - 成交额: <4亿→+1, >15亿→-1
  - **振幅 (R7 新增)**: <8%→+1, >=13%→-1 (d=-0.354, 全市值有效, S 级)
  - 总分 -4~+5
- **OOS 验证**: +4 分组 OOS 36.0% (IS 32.4%, OOS 更高); 8 档完美单调 7.6→33.9%; spread 26.3pp (旧 11.2pp, +135%)
- **操作映射**: +3~+5 可操作 (仓位 3-5%); +1~+2 观望等 2 板; 0~-4 放弃
- **R6 稳定性**: turnover_surge 月度 13/13 (100%) S 级; pct_range 月度 13/13 (100%) S 级, 月均 spread +13.5pp

### S3: Q1 小盘首板替代信号 (R5, OOS 验证通过)
- **信号**: turnover_rate (绝对换手率), 替代 Q1 失效的 turnover_surge
- **规则**: Q1 (mv_rank<0.25) 用 turnover_rate: <8%→+1, >=20%→-1 (pct_range 已在 R7 升级为独立第四维度)
- **OOS 验证**: tr<8% 组 monster 19.0% vs tr>=12% 组 13.0%, spread +6.1pp (IS +5.7pp → OOS +6.1pp, 无衰减)
- **跨市值互补**: Q1 用 turnover_rate, Q2-Q4 用 turnover_surge, 完美互补
- **局限**: spread (~6pp) 约为 Q2-Q4 主信号 (16pp) 的 1/3; pct_range 待更多 OOS 验证
- **R6 稳定性**: B 级 — 月度方向一致率 10/13 (77%), 3 个月反转 (>=20% 组样本量小); 半衰期 12-18 个月

### S14: R9 × R11 联合派发 (R14 + R17 升级 SS 级跨 regime)
- **信号**: `r9_distr AND r11_tail_surge` (日线派发 + 日内尾盘派发 同时触发)
- **1 年 (R14)**: spread **-4.20pp** 月度 11/12, 交互 -1.66pp
- **2 年 (R17)**: spread **-4.25pp**, 月度 **25/27 (93%)**, 交互 -1.74pp (跨年几乎一致)
- **2024 稳定**: 类牛市下仍 -4.40pp (甚至更强), 不像 R12 seal_at_close 失效
- **样本**: n=1,341 (2.77M 全市场, 触发率 0.05%)
- **市值规律**: Q4 大盘 -6.48pp 最强, Q1 小盘 -4.34pp
- **独立强度**: R11 超强 (vs_close_30m>25%) 且 r9=0 组仅 -0.31pp, 远弱于 both 组 → 超叠加不是单侧极端, 而是跨尺度互补
- **操作**: 持仓股同时检测到日线+日内双派发 → 高置信度止盈, 优先级 > 单独任一信号
- **方法论 D35**: 跨时间尺度 AND 交叉信号非线性协同, 未来优先叠加不同尺度

### S11: seal_bar / vol_before_seal 封板成本 (R12, S 级全市场)
- **信号**: `seal_bar ≤ 6` (10:00 前封板) OR `vol_before_seal < 5%` (开盘秒封)
- **含义**: monster 率 **30-31%** vs 基准 16.8%, 月度 **13/13 (S 级)** mean spread +11.15pp
- **市值规律**: Q1-Q4 全市值通用, Q3 中盘最强 (+12.3pp)
- **效应**: d=-0.390 (seal_bar) / -0.483 (vol_before_seal), spread 跨度 22pp
- **与已有正交**: vs turnover_surge/amount/turnover_rate Spearman 0.21-0.24, 独立增量
- **推翻 R1**: R1 n=13 游资池结论 "封板模式无区分力" 完全错误 (F12)

### S12: seal_at_close 收盘封死 (R12, 极强负向剔除器)
- **信号**: `seal_at_close = False` (收盘破板)
- **含义**: monster 率 **2.6%** vs 基准 16.8% (差 14pp), chi2 p<0.001
- **操作**: 收盘破板的涨停票**必须剔除**, 几乎必然 oneday

### S13: 一字板 gap_open≥9% (R12, 高确定性连板)
- **信号**: `gap_open >= 0.09` (开盘即涨停, 全天一字)
- **含义**: monster 率 **39.2%** vs 基准 16.8%, +22.4pp
- **与 R7 pct_range<8% 一致**, 最高确定性连板信号

### S8: 尾盘爆量拉升 (R11, 5min S 级 intraday)
- **信号**: `ret_close_30m > 2% AND vs_close_30m > 18%` (最后 30 分钟涨幅 >2% 且尾盘量占全天 >18%)
- **含义**: fwd_5d spread **-1.40pp** (p<0.001, n=5,265), 月度 **12/13 (S 级)** mean=-1.48pp
- **市值规律**: Q1 -0.66pp → Q2 -1.31 → Q3 -1.73 → **Q4 大盘 -2.00pp** 单调递增
- **机制**: 与 R9 日线 S5 方向一致但时间尺度不同 — R9 捕捉跨日派发, R11 S8 捕捉单日尾盘派发, **互补不冗余**
- **操作**: 尾盘检测触发即次日集合竞价止盈; 待 R12 做正交性验证后决定联合规则

### S9: 小盘 V 字反转 (R11, A 级限定小盘)
- **信号**: `ret_open_30m < -1% AND ret_close_30m > 1% AND mv_q=Q1`
- **含义**: fwd_5d +1.13pp (p<0.001, n=3,362), 其他市值段不显著
- **机制**: 小盘股开盘杀跌 + 尾盘拉起 = 资金进场
- **操作**: 小盘股建仓确认信号

### S10: intraday_ret 日内反转因子 (R11, A 级连续)
- **连续 10 分位**: bin 0 (最跌) spread +0.53pp → bin 9 (最涨) spread -0.25pp 单调负向
- **性质**: rev_1 (日线 1 日反转) 的 intraday 版本
- **建议**: 作为新因子进 LGB (做残差化验证增量, 类似 R10 方法论)

### S5+S6+S7 ICIR 残差化验证 (R10, 双 flag 分离落地)

**R10 判决**: 用日度截面 IC + OLS 残差化 (控制 rev_20 + turnover_rate + vol_20) 验证 R9 信号独立增量:

| 信号形式 | Raw RankICIR | 残差 RankICIR | 判决 |
|---|---:|---:|---|
| `vol_reg_distr` (派发 flag) | -1.04 | **0.55** | **A 级, 上生产** |
| `vol_reg_bounce` (反弹 flag) | +0.30 | **0.20** | **A 级, 上生产** |
| `vol_reg_combo` (signed 合成) | +0.65 | +0.004 | **被吃干, 放弃** |

**关键**: 两个 flag 分别独立保留 53%/66% 信号强度, 但**合成 signed score 99% 衰减** — 必须保留双 flag 分离形式.

**原理**: AND 交叉触发 flag 是非线性交互, 线性回归无法吸收. signed 合成会和 rev_20 共线性最大化.

### S5: 爆量 + 上涨强劲 = 派发信号 (R9, A 级全市场通用)
- **信号**: `tr_surge_60 >= 2.5 AND ret_20d >= 10%` (tr_surge_60 = MA5 换手 / MA60 换手)
- **含义**: 次 5 日 fwd_5d **-1.42pp vs baseline** (p<0.001, n=29,873)
- **全市场稳定性**: 爆量 (>=2.5x) 总体月度 **13/13 (100%)** 跑输 baseline, mean=-1.213pp, t-test p=0.0004, S 级
- **操作**: 持仓中若触发, 降权或止盈; 量化模型买入信号在此形态下可信度下降
- **共线性**: 与 ret_20d Spearman 0.541, 但 regime 分层后独立增量约 -1pp

### S6: 爆量 + 下跌温和 = 反转反弹 (R9, A 级反直觉)
- **信号**: `tr_surge_60 >= 2.5 AND ret_20d ∈ [-10%, -2%]`
- **含义**: 次 5 日 +0.94pp vs baseline (p=0.0003, n=804)
- **机制**: 超跌温和 + 大资金进场
- **局限**: 样本量小 (800), 不足以独立建仓, 仅作买入确认

### S7: 下跌强劲 + 缩量 = 跌无可跌反弹 (R9, A 级反转信号)
- **信号**: `tr_surge_60 < 0.8 AND ret_20d <= -10%`
- **含义**: 次 5 日 +0.85pp vs baseline (p<0.001, n=113,082)
- **机制**: 与 `turnover_20 ICIR=-0.49` 低换手溢价一致 — 筹码锁死 + 恐慌盘结束
- **操作**: LGB 通过 rev_20/turnover_20 已间接捕捉, 研报可显式标识

### S4: pct_range 涨停日振幅 (R7, S 级, 全市值有效)
- **信号**: pct_range = (high - low) / prev_close × 100, 低振幅 = 强封板 = 好
- **规则**: <8%→+1, >=13%→-1 (全市值, 不需市值门控)
- **效应量**: Cohen's d = -0.354 (全样本), Q1 -0.355, Q2 -0.377, Q3 -0.423, Q4 -0.298 — 全部 >turnover_surge
- **独立性**: vs turnover_surge rho=0.150, vs amount rho=0.166 (近乎正交); 控制已有打分后 5/6 档仍显著
- **OOS 验证**: +4 组 OOS 36.0% (IS 32.4%, 无过拟合); -3 组 OOS 7.5% = IS 7.7%
- **月度一致率**: 13/13 (100%), 月均 spread +13.5pp (turnover_surge +9.7pp 的 1.4 倍)
- **特殊**: 第一个在所有市值段都显著的首板信号 (turnover_surge Q1 失效, turnover_rate Q2-Q4 失效)

## 失败模式清单

### F1: 封板微观特征 (seal_strength / seal_time / open_count) 对次日走势无预测力
- **轮次**: R1 | **样本**: 4 只 / 13 个涨停事件
- **数值**: Spearman rho = -0.018, p = 0.953
- **根因**: 次日走势由 "票的身份" 决定, 不由封板模式决定

### F2: 尾盘量占比在涨停日暴降是废话
- **轮次**: R1 | **原因**: 封板后交易冻结, 物理约束不是信号

### F3: PE 对妖股识别完全无区分力
- **轮次**: R2 | **样本**: 6548 事件
- **数值**: monster PE 中位数 93 vs oneday 91, 差 2, >100 占比差 1.3pp
- **结论**: 游资票涨停博弈完全脱离基本面

### F4: 市场涨停家数 / 板块涨停家数不影响个股连板率
- **轮次**: R2 | **数值**: p=0.406 ns / p=0.167 ns
- **结论**: 连板是个股行为, 不是市场行为; 不要因为"今天市场热"就冲动打板

### F5: 10cm vs 20cm 板块类型对妖股识别无区分力
- **轮次**: R2 | **数值**: monster 8.5% vs oneday 8.2%, 差 0.3pp

### F6: T-1 振幅 (t1_amplitude) 无区分力
- **轮次**: R3 | **数值**: IS p=0.494, Cohen's d=0.064
- **结论**: IS 即不显著, OOS 中出现的方向是假阳性 (IS 无信号 → OOS "发现" = 噪音)

### F7: 换手率突变信号在小盘股 (Q1) 完全失效
- **轮次**: R3→R5 确认 | **数值**: p=0.23 (R5 复验)
- **根因**: 小盘日常换手已高 (中位 9.88%), surge = board/ma5 被基准压缩; 绝对换手率不受此影响

### F8: 板块结构特征 (sector_limit_count / first_in_sector / sector_ratio) 在 Q1 无区分力
- **轮次**: R5 | **数值**: 全部 p>0.1, first_in_sector 仅 1.4% 事件
- **根因**: 小盘首板太分散, "辨识度" 是个股层面概念, 无法被板块结构指标捕捉

### F9: 龙虎榜出现率对首板连板无区分力
- **轮次**: R5 | **数值**: OR=1.10, p=0.75, 仅 17% 首板匹配到龙虎榜
- **根因**: 覆盖率太低 + 滞后公布 + 小盘首板中游资参与率接近 100%, 差异在"什么类型"而非"有没有"

### F14: R13 v5 seal_score 单独作为买入信号效果不显著 (R18 回测)
- **轮次**: R18 | **样本**: 2 年 3,995 笔 S2 (seal_score≥+2)
- **数值**: 净收益 +1.82% vs 基线 +2.05%, **vs baseline -0.24pp**, 胜率 43.7%
- **根因**: v5 只是第五维打分, 必须和 v4 四维合并 total_score≥+4 才有清晰 alpha
- **修正**: v5 是打分组件, 不是独立策略; R19 补齐 v4 重算 total_score 再验

### F13: R12 seal_at_close=False 剔除器 2024 年失效 (R15 regime 警示)
- **轮次**: R15 | **样本**: 2024 年 373 事件 monster 率 26.81% (vs 基准 24%)
- **根因**: 2024 类牛市 (924 行情后) 资金充裕, 次日反包能力强, 封板稳定性非决定因素
- **正常年份** (2025-2026) 该组 monster 率 3-12% 强剔除
- **规则**: R13 v5 的 `seal_at_close=False → -2` 在类牛市年份 (基准 monster 率 >23%) 误杀, 接受 regime 依赖

### F12: R1 所有封板微结构结论被 R12 翻案 (样本量教训)
- **轮次**: R12 翻案 R1 | **样本**: R1 n=13 → R12 n=12,545 (1,226 倍)
- **R1 错误**: "seal_time / seal_strength / open_count 对次日无区分力" p=0.953
- **R12 真相**: seal_bar d=-0.390 (月度 **13/13 S 级**), vol_before_seal d=-0.483, seal_at_close 差 **14pp**
- **根因**: R1 样本 n=13 + 游资票特化, power 不足 + 池特化假象
- **方法论**: 封板微结构研究**必须 n > 5,000**, 游资池 n<50 不做因子验证

### F11: 缩量不是全市场负向信号 (修正 R8 的 D22)
- **轮次**: R9 | **样本**: 540,801 缩量事件 (全市场)
- **数值**: spread +0.01pp (p=0.17 ns), 月度 5/13 (38%)
- **根因**: R8 里的 -0.87pp 是 "过去 20 日涨 15-40% + MA 多头" 子样本特有 (趋势衰竭), 不是全市场规律
- **修正**: D22 (趋势衰竭) 限定在趋势子样本才成立, 全市场缩量票均值和基线持平

### F10: 非涨停趋势事件整体跑输全市场 baseline
- **轮次**: R8 | **样本**: 51,403 事件 (2025-04~2026-04)
- **数值**: 整体 fwd_5d -0.16pp (p=0.0001), 胜率 45.6%, 月度 6/13, spread t-test p=0.29
- **根因**: 与 `breakout_20` ICIR=-1.02 / `new_high_freq` 全负 IC 一致, A 股追涨亏钱的规律在事件维度再次验证
- **等级**: D (不上打分系统)
- **例外**: Q1 小盘 +0.22pp p=0.003 微弱正向, 温和放量 (tr_surge_60 1.0-1.5) 边缘 +0.11pp p=0.083

## 关键发现

### D1: 游资票短期动量惯性 (R1)
- 高 ret5d 组次日 +6.07% / 上涨率 90%; 低 ret5d 组次日 +1.10% / 上涨率 44%
- **含义**: 游资票 "强者恒强" 与 rev_20 反转逻辑矛盾

### D2: 20cm 板次日溢价远高于 10cm 板 (R1)
- 20cm +9.23% vs 10cm +2.24%, 可能含幸存者偏差

### D3: 低换手率突变 = 好 (R2, 反直觉)
- monster 中位数 1.31x vs oneday 1.58x, p<0.001
- **机制**: 缩量涨停 = 筹码锁定 = 分歧小; 放量涨停 = 边拉边出

### D4: 低成交额 = 好 (R2, 反直觉)
- monster 中位数 5.83亿 vs oneday 7.63亿, p<0.001
- 可能是市值代理, 待控制变量验证

### D5: 连板序号单调递增连板率 (R2, R6 修正)
- 首板 16% → 2板 33% → 3板 47% → 4板分歧 41% → 5板+ 42%
- 4板分歧 4/4 季度 100% 一致, 幅度 -0.3pp ~ -18.9pp
- R2 高估了 5板+ (56% → 42%), R2 的 3.5 个月恰逢高板序热潮
- **机制**: 辨识度递增 + 筹码结构优化 + 自我实现的预言

### D6: monster 最优持有期 ~3 天 (R2)
- T+1 +11.06%, T+3 +14.86% (峰值), T+5 +13.74% (微回)
- oneday T+1 -4.24% → T+5 -4.88%, 无反弹窗口, 越早止损越好

### D7: "反直觉" 往往是真信号 (R2 方法论)
- 反直觉的 (低换手好, 市场热度不重要) 全部 PASS
- 符合直觉的 (高 PE 更妖, 热市更多连板, 放量好) 全部 FAIL
- 原因: 符合直觉的已被市场定价, 反直觉的才有 alpha

### D8: 市值是信号有效性的第一分层维度 (R3)
- 换手率突变: Q1 小盘 p=0.56 (失效) → Q2 p=0.028 (弱) → Q3/Q4 p<0.001 (强)
- 与 rating_system.md v4 核心理念一致: 同一信号在不同市值段效力可从 "强显著" 变为 "完全无效"
- **教训**: 不分层 = 噪音稀释 = 低估真实效应量 (全样本 d=0.217 vs 中大盘 d≈0.25-0.35)

### D9: 多个弱信号组合可产生实操有意义的分离 (R3)
- 单个信号 Cohen's d 都 <0.22, 但组合后 +2 vs -2 组连板率差 13.4pp (23.8% vs 10.4%), 相对提升 129%
- 与 34 因子 LGB 体系底层逻辑一致: 没有哪个因子单独能选股, 组合才有超额

### D11: turnover_surge vs turnover_rate 完美互补 (R5)
- Q1: turnover_rate p=0.004, turnover_surge p=0.23 (失效)
- Q3-Q4: turnover_surge p<0.001, turnover_rate p>0.08 (失效)
- **机制**: surge 是相对指标 (涨停/均值), 高基准下被压缩; rate 是绝对指标, 不受基准影响
- **规则**: 不要试图用统一指标覆盖全市值, Q1 用 rate, Q2-Q4 用 surge

### D12: 小盘首板结果更极端 (R5)
- Q1: monster 16.7% + oneday 49.9% + neutral 33.4% (chi2 p=0.004 vs 全样本)
- 中间态更少, 连板或一日游, 分歧解决快

### D13: 涨停日振幅 (pct_range) 是独立的筹码维度 (R5, 待验证)
- p=0.002, d=-0.300, 控制 tr 后 p=0.005
- 低振幅 = 强封板 = 卖方弱, 但 IS 效应弱, 需更多数据

### D10: IS/OOS 一致性是信号真伪的黄金标准 (R3 方法论)
- IS/OOS 曲线几乎平行甚至 OOS 更强 → 真实信号 (换手率突变)
- IS 不显著 → 不需到 OOS 即弃用 (t1_amplitude)
- R2 的 "诚实评估" 过于悲观, OOS 证据表明分层后效应更强

### D14: 冷市涨停质量更高 (R6)
- 涨停家数 low 三分位: monster 率 24.6% vs high 三分位 19.6%
- 冷市能涨停的票筛选更严 = 质量更高; 热市涨停泛滥 = 鱼龙混杂
- 冷市 turnover_surge spread +11.6pp > 热市 +10.6pp, 信号区分力也更强

### D15: turnover_surge 无时间衰减 (R6)
- 前 6 个月 spread 均值 +9.0pp, 后 7 个月 +10.2pp, 略增
- 机制: 反映 A 股涨停板的筹码结构物理规律 (锁仓 vs 出货), 非可套利型 alpha
- 半衰期估计 >24 个月

### D16: 信号不依赖市场环境 (R6)
- turnover_surge 在冷市/中市/热市 spread 全部 >7pp, 弱月/强月全部有效
- 不需要 regime-dependent 调整, 简化了规则维护

### D17: 首板 monster 率有明显月度周期 (R6)
- 波动范围 10.2% ~ 21.1%, 极差 10.9pp, 变异系数 ~18%
- 2025 年中 (5-7月) 最热, 之后逐步回落到 2026-04 最冷
- 基准不稳但信号 spread 稳定 → 信号的相对优势不受基准波动影响

### D18: pct_range 是效应量最大的首板信号 (R7)
- 全样本 d=-0.354, 大于 turnover_surge d=-0.217, 大于 t1_tr_surge d=0.146
- 且是**唯一**全市值有效的信号 (4/4 分位 p<0.001), turnover_surge Q1 失效, turnover_rate Q2-Q4 失效
- Logistic 回归标准化系数 -0.332, 大于已有打分系数 0.183

### D19: 打分维度之间是加法关系 (R7)
- turnover×amount 12 组交互偏差全部 <2pp, 无显著协同或冗余
- 简单相加打分是最优策略, 不需要引入交互项
- 例外: turnover=+2 时 amount 区分力消失 (锁仓 → 成交额自然低, 信息重叠)

### D20: <5% 振幅 (一字板/早封) monster 率 34% (R7)
- 是基准 (16%) 的 2 倍, >=14% 振幅组仅 10%
- 12-14% 区间有回弹 (18.5%), 因为主要是 20cm 涨停板 (创业板/科创板 振幅自然大)
- <8%/>=13% 阈值已绕开这个 10cm/20cm 非线性

### D21: 趋势 + 爆量 = 派发末期 (R8, 负向 U 形)
- tr_surge_60 (MA5/MA60) >= 2.5 时 fwd_5d **-1.08pp** (p<0.001) 胜率 **39.4%** (U 形最差档)
- **操作**: 个股研报若发现"ret_20d ∈ [15%, 40%] + 无涨停 + MA 多头 + tr_surge_60 >= 2.5", 标红减持/回避
- 与 MACD 顶背离 / RSI 超买 的重合度待 R9 验证

### D22: 趋势 + 缩量 = 趋势衰竭 (R8)
- tr_surge_60 < 0.8 时 fwd_5d **-0.87pp** (p<0.001) 胜率 41.3%
- 量能枯竭的趋势票会反转, 不是"洗盘"
- 温和放量 (1.0-1.5x) 是唯一边缘正向区 (+0.11pp p=0.083)

### D24: 爆量信号高度 regime-dependent (R9, 方法论)
- 简单 "爆量=负向" 错误, 必须结合 ret_20d regime:
  - up_strong (ret_20d>=10%) + 爆量 → -1.42pp 派发
  - down_mild (ret_20d ∈ [-10%, -2%]) + 爆量 → +0.94pp 反转
  - flat/up_mild + 爆量 → -0.3~-0.4pp 弱负向
- **方法论含义**: 未来设计量价信号必须先做 regime 分层, 全样本均值容易掩盖交互

### D43: A 股首板涨停持 5 天是正向 alpha 基线 (R18 反直觉)
- 无脑买首板持 5 天 mean +2.05% 胜率 48.6%
- 与 factor_insights "A 股反转" 表面矛盾, 实质: 涨停票是筛出的强势子集
- 情绪信号针对涨停子集分化 (monster/oneday/neutral), 不等同全市场反转

### D44: 卖出信号 alpha > 买入信号 alpha (R18 方法论)
- S2 买入净 +1.82% vs baseline -0.24pp (边缘)
- S5 做空 super_distr 净 +2.44% vs baseline +6.49pp (强)
- 市场对"派发"的反应更剧烈, 尾部下跌事件效应大于中位
- **设计原则**: 未来优先投入**退场/回避信号** ROI 比入场信号高

### D41: 形态累积 vs 微结构信号的 regime 稳定性差异 (R17, 方法论)
- **形态累积 (R9/R11/R14)**: 跨日/跨时段统计趋势, **跨 regime 稳健** (2024 类牛市不失效)
- **微结构 (R12 seal_at_close)**: 单日日内细节, **regime-dependent** (2024 失效 F13)
- **机制**: 形态累积反映供需失衡物理规律; 微结构对市场情绪敏感 (资金托底改变含义)
- **设计原则**: 跨 regime 稳健信号应优先形态累积 + 跨尺度 AND 叠加, 避免纯微结构单点

### D42: 跨尺度 AND 交互项 -1.7pp 跨年稳定 (R17)
- 1 年 R14 交互 -1.66pp / 2 年 -1.74pp, 差异 <0.1pp
- R9 × R11 非线性协同是**物理规律**, 不是样本特化
- 跨尺度 AND 叠加应成为未来信号设计首选范式 (呼应 D35)

### D38: 1 年样本结论 vs 2 年验证的衰减 (R15 方法论)
- R12 (1 年) seal_bar spread 22pp / Cohen's d -0.39
- R15 (2 年) spread 16pp / d -0.17 (减半)
- **根因**: R12 恰好处于信号强时期, 不含 2024 类牛市
- **教训**: 新信号用 2+ 年样本重验必要, 不要凭 1 年结论上满分设置

### D39: 类牛市年份剔除器失效 (R15, regime)
- 2024 基准 monster 率 24% (vs 正常年份 ~19%)
- `seal_at_close=False` / `seal_bar>=24` 在 2024 年 spread 显著减弱
- **机制**: 强市资金充裕, 次日反包能力强, 封板形态不再是决定性因素
- **设计**: 保持 R13 v5 规则整体 OK, 类牛市是低频事件无需调整

### D40: 一字板跨年最稳定 (R15)
- 2024 → 2025 → 2026 一字板 monster 率: 31% → 38% → **51%** 单调增强
- vs 非一字板基准: 23% → 18% → 16% 逐年下降
- 一字板 spread 从 +7.6pp → +35.7pp
- **启示**: A 股唯一跨 regime 稳健的极端态信号, R13 v5 +1 可能偏保守

### D35: 跨时间尺度信号非线性协同 (R14, 方法论)
- R9 日线派发 (20日累涨+日频换手) × R11 日内派发 (尾盘爆量) 联合 spread -4.20pp > 加法 -1.83pp
- **交互项 +1.66pp 超叠加** — 类比 R10 D26 (AND 交叉抵抗线性残差)
- **设计原则**: 未来新信号优先"跨尺度 AND 叠加", 不要单一尺度极端化

### D36: 信号在子样本可能语义反转 (R14, 警示)
- R9 distr 在全市场是"派发" (-1.42pp), 在涨停子集变成"强势参与" (+3.87pp)
- **根因**: 涨停日本身筛掉了"派发失败"样本, distr 捕捉的是筛选相关性而非 alpha
- **教训**: 信号**必须注明有效子集范围**, 跨子集引用结论要重验证

### D37: 超强单信号不需要跨信号确认 (R14)
- R12 一字板 monster 38.56% 单独最强, 叠加 R9 distr 降到 30.36%
- **原则**: spread > 20pp 的极端态信号**单独使用**, 额外叠加可能引入噪声

### D32: 封板时间是 A 股最强微结构信号 (R12)
- `seal_bar` spread 跨度 **22.7pp** (bar 0 秒板 +13.8pp vs bar 43-47 尾盘 -8.9pp)
- **超过 pct_range (11pp) 和 turnover_surge (11pp)**
- 与 pct_range (R7) / turnover_surge (R6) 并列为 top 3 首板 S 级信号

### D33: seal_bar / vol_before_seal / bars_at_limit / ret_before_seal 共线 (R12)
- Spearman |ρ|>0.7 四者同源, 是"封板成本"的四种表达
- **择一即可**, 推荐 seal_bar (最直观, 操作明确)

### D34: 一字板 monster 率 39% 超过任何已知首板单信号 (R12)
- 5min 级别 `gap_open >= 9%` monster 率 39.2%
- 与 R7 pct_range<8% (monster 34%) 一致且更强, **最高确定性连板形态**

### D28: 日内高点位置是独立 alpha (R11)
- `high_pos` 连续 10 分位 spread 从 bin 0 (开盘高) +0.13 → bin 7 (尾盘高) **-0.28pp**
- 日内高点越晚出现, 5 日跑输越多 = "尾盘拉高派发" 的连续版本
- 可作为新因子进 LGB 特征

### D29: 日内低点 0.6-0.7 是反转区间 (R11)
- `low_pos` bin 6 (午后 13:30-14:30 创新低) fwd_5d spread **+0.64pp** (远强于其他 bin)
- 机制: 午后创新低 + 收盘回收 = 主力承接
- 反直觉但样本量大 (n=124,705) 高度显著

### D30: 下影线长反而跑输 (R11, 反常识修正)
- `lower_shadow > 0.7` bin 的 fwd_5d spread **-0.27pp**
- 与传统 "长下影线 = 托底看多" 认知相反
- 机制猜测: 深度下砸后勉强收回 = 卖盘释放不彻底, 派发未完成
- **操作**: 研报和技术分析**不再使用"长下影线 = 看多"** 的经典表述, 至少标明不确定

### D31: 5min 尾盘信号 (ret_close_30m + vs_close_30m + high_pos) 三者方向一致 (R11)
- 尾盘涨得多 → 跑输 (spread 单调负)
- 尾盘量占比高 → 跑输 (bin 9 -0.28pp)
- 日内高点在尾盘 → 跑输 (-0.28pp)
- 三者都指向同一主题: **尾盘主动拉升 = 派发信号**
- 可合成一个 "尾盘拉升强度" 综合指标 (但根据 R10 D27 教训, 合成损失信息, 应保留独立形式)

### D26: 交互 flag 线性残差不衰减 (R10, 方法论)

- `A AND B` 交叉触发 flag 对 `A, B` 线性残差**无法剔除**, 因为 AND 是非线性交互
- `vol_reg_distr = (ret_20d>=10%) AND (tr_surge_60>=2.5)` 残差 ICIR 0.55 保留
- **操作意义**: 未来挖掘新信号优先构造 `A AND B` 离散交叉条件而非合成 score, 容易获得独立增量

### D27: signed 合成损失信息 (R10)

- `bounce - distr` signed score 残差 ICIR 从 0.65 → 0.00 (99% 衰减)
- 原因: signed 操作把两个独立 flag 折叠到同一维度, 与已有连续因子 (rev_20) 共线性最大化
- **多二元信号保留独立比合成更好**

### D25: 爆量与 ret_20d 共线性 (R9)
- Spearman ρ=0.541, 爆量部分是 "已涨多" 的代理信号
- 但 regime 分层后 up_strong + 爆量 -1.42pp 远超 up_strong 基准, 有独立增量
- 排除涨停后 (past_lu_20<=1 + T日非涨停) 爆量 spread 仍 -0.74pp (35% 来自涨停日贡献, 65% 来自非涨停日放量)

### D23: 非涨停趋势票市值规律与涨停事件相反 (R8)
- 涨停事件: Q3-Q4 中大盘最强 (turnover_surge p<0.001), Q1 失效
- 趋势事件: **Q1 小盘** +0.22pp p=0.003 唯一正向, Q3 中盘 -0.45pp 最差
- **机制**: 小盘 "无涨停 20 日涨 15%+" 多是机构慢吸筹 / 题材酝酿, 未被游资注意; 中盘同形态多是主力派发尾声

## 轮次索引

| 轮次 | 主题 | 结论 | 文件 |
|------|------|------|------|
| R1 | 封板力度 | 四假设全 FAIL, 关键在 "票的身份" | [sentiment_round_1.md](sentiment_round_1.md) |
| R2 | 全市场涨停 + 妖股识别 | 连板序号最强 (事后); 换手率突变有微弱事前信号; 市场热度/PE/板块无效 | [sentiment_round_2.md](sentiment_round_2.md) |
| R3 | OOS 验证 + 市值分层 + T-1 特征 | v3 规则 OOS 完美复现 (+2 组 23.8%); 小盘 Q1 失效; t1_amplitude 弃用 | [sentiment_round_3.md](sentiment_round_3.md) |
| R4 | 工程化落地 | board_scorer.py + report_v2.py 集成 §四-B 首板信号 + _agent_ctx 7 字段 | (代码改动, 非研究) |
| R5 | Q1 小盘替代信号 | turnover_rate 替代 surge (p=0.004, 完美互补); 板块/龙虎榜全 FAIL; pct_range 独立但 IS 弱 | [sentiment_round_5.md](sentiment_round_5.md) |
| R6 | 12 个月稳定性验证 | turnover_surge S级 (13/13 月一致, 无衰减); turnover_rate B级 (10/13); 5板+ 从 56%→42% 下调; 第四板分歧 4/4 季度确认 | [sentiment_round_6.md](sentiment_round_6.md) |
| R7 | pct_range 全市场验证 + 信号交互 | pct_range S级 (d=-0.354, 全市值有效, 13/13月一致); 打分升级 v4 四维 (-4~+5); +4组 OOS 36%; 信号间加法关系 | [sentiment_round_7.md](sentiment_round_7.md) |
| R8 | 非涨停趋势票事件验证 | **整体证伪** (D级, fwd_5d -0.16pp p=0.0001, 月度 6/13); Q1 小盘微弱正向 +0.22pp; 量能 U 形发现 — 爆量 -1.08pp / 缩量 -0.87pp / 温和放量 +0.11pp | [sentiment_round_8.md](sentiment_round_8.md) |
| R9 | 量能 tr_surge_60 全市场 + regime 交互 | **爆量 A 级全市场通用** (月度 13/13, -1.14pp, p=0.0004); 高度 regime-dependent — up_strong+爆量 -1.42pp / down_mild+爆量 **+0.94pp 反转** / down_strong+缩量 +0.85pp; **缩量被修正** 不是全市场负向 (F11 修正 D22); 爆量与 ret_20d Spearman 0.541 共线但分层后独立增量 | [sentiment_round_9.md](sentiment_round_9.md) |
| R10 | vol_regime 信号 ICIR 残差化验证 | **distr flag 残差 RankICIR 0.55 A 级**, **bounce flag 残差 0.20 A 级**, signed combo 残差 0.004 被吃干放弃; 双 flag 保留独立, AND 交叉触发抵抗线性残差 (D26); 建议研报层 + _agent_ctx 先落地 (R11) | [sentiment_round_10.md](sentiment_round_10.md) |
| R11 | **5min 日内形态事件研究** (方向 B) | **1.25M 日度特征 × 13 事件**; E1 尾盘爆量拉升 **S 级** 月度 12/13 / fwd_5d -1.40pp / Q4 大盘 -2.00pp; E5 小盘 V 字反转 +1.13pp A 级; intraday_ret 单调反转 A 级连续因子; D28-D31 新方法论 (日内高点晚 / 低点午后 / 下影线反常识 / 尾盘三信号同向); Windows fork bug 需 main() 保护 | [sentiment_round_11.md](sentiment_round_11.md) |
| R12 | **涨停 5min 封板微结构 R1 翻案** (方向 C) | 15,933 涨停事件 (首板 12,545) × 10 微结构特征; **seal_bar S 级月度 13/13 spread +11.15pp** (秒板 30.6% vs 尾封 7.9%, 跨度 22pp); vol_before_seal Cohen's d=-0.48 最强; seal_at_close=False 剔除器 (monster 率 2.6%); 一字板 monster 39%; **R1 n=13 结论完全推翻** (F12); board_scorer v5 第五维设计 -3~+3 | [sentiment_round_12.md](sentiment_round_12.md) |
| R13 | **board_scorer v5 工程化** (落地) | seal_micro.py + board_scorer v5 + report_v2 + CLAUDE.md 数据契约; 单测 4 场景通过; **实战 9 样本 v4 vs v5 判决改变率 44% (4/9), 方向 100% 正确** (观望→放弃, actual 全是 oneday/neutral); 残留假阳性 SH600545 是 R12 bar1-6 组 monster 17% 统计尾部 | [sentiment_round_13.md](sentiment_round_13.md) |
| R14 | **三重信号交叉验证** (方法论突破) | Phase A (全市场 1.27M): **R9 S5 × R11 S8 超叠加 -4.20pp 月度 11/12** (加法预期 -1.83, 交互 -1.66pp SS 级); Phase B (涨停子集): R9 distr 语义反转变正向 (+3.87pp, D36 警示); R12 一字板自足 (D37); R12 broken 绝对剔除器 (monster 3%); 新增 S14 / D35-D37 | [sentiment_round_14.md](sentiment_round_14.md) |
| R15 | **R12 封板微结构 2 年长样本验证** | 40,331 涨停 / 30,579 首板 (2024-01~2026-04 2.44x R12). 月度 **27/28 (96%) S 级**, seal_bar spread 跨度 22pp→**16pp** 衰减但仍最大; 秒板 30.6% 跨年完全稳定; **一字板 2024→2026 单调增强 31%→38%→51%** 跨 regime 超稳; **2024 seal_at_close=False 失效** (F13, monster 27% 比基准高, D39 regime 警示); R12 结论总体成立但 1 年偏乐观 (D38) | [sentiment_round_15.md](sentiment_round_15.md) |
| R16 | **volume_regime 工程化落地** | 新建 [volume_regime.py](../sentiment/volume_regime.py) 综合 R9/R11/R14 返回 5 档 label (super_distr/distr/tail_surge/bounce/neutral), report_v2 加 §四-C 警示段 + `_agent_ctx.vol_regime` 嵌套字段, CLAUDE.md 数据契约更新. 实战 7 样本 SH600488 命中 R9 distr | — (feat only, 见 log.md 2026-04-18 feat R16) |
| R17 | **R9/R11/R14 2 年长样本验证** | 2,786,609 R9 events / 2,780,630 R11 events (2 年 2.2x R9/R11). **R9 distr 月度 28/28 (100%) SS 级!** (含 2024 类牛市不失效); R14 super_distr 月度 **25/27 (93%) SS 级**, spread **-4.25pp** 跨年几乎一致 (1 年 -4.20pp), 交互项 -1.7pp 稳定; **对比 R12 F13**: volume-regime 跨 regime 稳健 (形态累积), 微结构 regime-dependent (F13 失效); 新增 D41/D42 方法论 | [sentiment_round_17.md](sentiment_round_17.md) |
| R18 | **实盘回测模拟** | 2 年 29,646 首板, 5 日持仓 + 1% 滑点. **S2 买入 seal≥2 净 +1.82% vs baseline +2.05% (-0.24pp 无 alpha)**; **S5 做空 super_distr 净 +2.44% 胜率 68.2% vs baseline +6.49pp SS 级**! super_distr 跨年稳定 (2024 +2.78% / 2025 +1.74% / 2026 +4.41%). R14 设计得到回测验证. S2 Q4 大盘最强 +1.71% / Q1 +0.72%. 新增 F14 (v5 单独买入弱) / D43 (首板持 5 天正 alpha) / D44 (卖出信号 alpha > 买入信号) | [sentiment_round_18.md](sentiment_round_18.md) |
