# overseas-market-scanner — 阈值规则

## 风险偏好判读 (risk_tone)

| 状态 | 触发条件 | A 股早盘倾向 |
|---|---|---|
| **risk_on** | 美股 + 港股 avg >+0.3% OR 美股 +0.3% + VIX<18 | 大盘高开, 成长/科技偏好 |
| **risk_off** | 美股 + 港股 avg <-0.3% OR VIX>22 OR 美股大跌单方向 | 大盘低开, 防御/红利占优 |
| **mixed** | 其它 (美港股反向 / VIX 18-22 / 数据缺失) | 不明朗, 等待 9:25 集合竞价 |

## 传导经验

1. **美股大跌 (-1.5% 以上)** + **VIX > 25** → A 股第二日大概率低开 1% 内
2. **纳指大涨 (+1.5%)** → A 股 TMT 链 (半导体/CPO/算力) 跟随
3. **港股早盘强 (HSI +1%)** → A 股大盘股 (尤其 AH 股) 顺势走强
4. **VIX 突破 25 + 持续 3 日** → 系统性风险, 减仓信号
5. **美股 vs A 股脱节** (美跌 A 不跌或反弹) → A 股内生韧性, 但要警惕 follow-through 补跌

## 国际指数代号 (tencent qt.gtimg.cn)

- 美股: `usDJI` 道指 · `usIXIC` 纳指 · `usINX` 标普500 · `usVIX` 恐慌指数
- 港股: `hkHSI` 恒生 · `hkHSTECH` 恒生科技

DXY / 商品 / 汇率 暂未接入 (tencent 不暴露相关 endpoint), v2 接 sina finance.
