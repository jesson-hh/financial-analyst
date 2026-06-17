# Report Template

> 2026-06-04 改版:个股研报走**技术面/资金面/估值 + 市场环境**视角。
> **输出层硬规则**:正文里**不得出现**「因子 / 因子面 / IC / RankIC / ICIR / 量化模型 / 模型面 / LightGBM」等量化字样。
> 上游 analyst 若用了这些措辞,**一律改述为技术面/资金面/估值语言**:
>   - 「因子面利空/利多」→「技术面或估值偏弱/偏强」
>   - 「模型面前 20% / LGB 排名」→「综合排名靠前 / 多维共振偏多」
>   - 「ICIR / IC 值」→ 直接省略,只保留结论方向
>   - 「市值分层 / 大盘股因子面归零 / 模型面可靠性低」等**评分规则解释 → 不要写**,直接给基本面结论(估值/财务/事件驱动)
> 不输出「量化模型」评分维度;因子炼制/评测归量化模块。

## Markdown structure

# <Company Name> (<code>) — Deep-Dive Research

**Date:** YYYY-MM-DD | **Price:** ¥X.XX | **MV:** XX亿 | **Tier:** Large/Mid/Small

## 一、综合评级
**总评 X/10** | 操作: 买入/持有/卖出/回避

| 维度 | 评分 |
|------|------|
| 基本面 | -2..+2 |
| 技术面 | -2..+2 |
| 主力情绪 | -2..+2 |
| 市场环境 | -2..+2 |
| 风险面 | -2..0 |

## 二、市场环境(大盘 · 主线 · 早盘)
### A. 大盘 (MarketScanner) — regime / 涨停连板 breadth / 北向 / 量能
### B. 主线 (MainlineClassifier) — 当前产业链主线分级;**本股是否在主线上**
### C. 早盘 (MorningBriefWriter) — 隔夜变动 / 盘前关注 / 是否进当日 watchlist
### D. 海外 + 行业轮动 (OverseasMarketScanner / SectorRotationAnalyzer, if relevant)

## 新闻情绪研判(消息面)— NewsSentiment(实时)
- **大盘消息面**:<news-sentiment.market_read>(倾向 <market_tilt>)
- **本票消息面**:<stock_tilt> — <stock_read>
- **引用快讯**:逐条列 evidence 的 [time] title(原文,不改写)
- 若 covered=false:写「近期无相关消息面」;若数据不可用:写「消息面数据暂不可用」。**不编造**。
> 定性佐证,**不计入** 一、综合评级的 5 维评分。

## 三、基本面 (FundamentalAnalyst)

## 四、技术面与主力(核心)
### A. 走势 (TechnicalAnalyst) — 均线排列 / 量价 / 支撑压力 / 趋势 / 形态
### B. 主力行为与筹码 (WhaleAnalyst) — **筹码集中度 / 股东户数变化 / 主力成本 / 大单净额 / 加减仓**
### C. 量能 regime 警示 (if any)

## 五、多空辩论
### 看多 (BullAdvocate, V-anchors: ...)
### 看空 (BearAdvocate, F-anchors: ...)

## 六、风控审查 (RiskOfficer)
- veto: ...
- 仓位建议: ...
- 止损: ¥X.XX

## 七、操作建议
- 目标价: ¥X.XX
- 仓位: X%
- 止损: ¥X.XX
- 监控事件: ...

## Rating rules
- mv > 1000亿: 基本面 forced to 0
- mv 300-1000亿: 基本面 capped at ±1
- mv < 100亿: full ±2 allowed
