# global-news-aggregator — 传导 channel 速查

## 6 个核心 channel

| Channel | 触发指标 | 对 A 股影响 | 受影响板块 |
|---|---|---|---|
| **us_equity** | 美股 (SPX/NDX/DJI) ±0.5% 以上 | A 股第二日 follow / VIX 衡量强度 | 半导体 / CPO / AI / 中概 |
| **fed_policy** | Fed 决议 / Powell 讲话 / UST10Y 突破关键点位 | 流动性预期 → 估值锚 | 银行 / 地产 / 大盘价值 |
| **geopolitical** | 中美关系 / 俄乌 / 中东 / 台海 | 短期情绪冲击 + 长期供应链 | 军工 / 能源 / 稀土 |
| **commodity** | 原油 ±3% / 黄金 ±2% / 铜 ±2% | 上游成本传导 | 三桶油 / 有色 / 化工 / 黄金股 |
| **china_specific** | 美对中关税/制裁/MSCI/外资 | 外资进出 + 政策博弈 | 出海股 / 半导体设备 / 北向重仓股 |
| **fx_rates** | DXY 突破 105/107 / USDCNY 破 7.20 | 外资流向 + 跨境套利 | 大盘价值 / 出海消费 |

## 经验规则

1. **美股大涨 + VIX 跌** → 全球 risk_on, A 股大概率高开, 但要看 9:25 集合竞价是否承接
2. **美股大跌 + 港股弱** → A 股大概率低开, 避险 (红利 / 黄金 / 电力)
3. **美元突破 + UST10Y 高** → 外资流出预警, AH 股大盘价值股承压
4. **商品涨 (原油 + 铜 + 金)** → 通胀链行情 + 全球流动性紧张
5. **VIX > 25** + **持续 3 日** → 系统性风险, 减仓信号

## 输出约束

- overall_narrative 100-150 字
- impacts 数组 3-5 个
- 每个 impact 的 affected_sectors 最多 5 个
- key_channels 1-3 个 (今日最关键)
