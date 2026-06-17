# Intraday Verdict Rules

Per-stock at lunch break, classify into one of three actions:

## 撤离 (Exit now)
Trigger any of:
- `current_low <= prev_stop` (stop_loss already touched today)
- `pct_change_since_asof < -8%` (extreme adverse move regardless of stop)
- `prev_action == "buy"` AND `pct_change_since_asof < -5%` (thesis broken)
- Negative news event severity >= 2 (from f10-reader, not in this pass but flag manually)

afternoon_action: "止损/清仓, 开盘后市价清"

## 警惕 (Watch)
Trigger any of:
- `prev_action == "buy"` AND -5% < pct_change_since_asof < -2% (mild adverse)
- `prev_action == "sell/avoid"` AND pct_change_since_asof > +3% (bear thesis weakening)
- `current_low` within 3% of `prev_stop` (approaching stop)
- 量能异常但价格未动

afternoon_action: "盯紧 14:30 是否破 X.XX, 破则清"

## OK
Direction matches:
- buy + ret > 0
- hold + |ret| < 2%
- sell/avoid + ret < 0

afternoon_action: "继续按昨日 plan 操作, 关注收盘价"

## 整体仓位纪律 (lunch-break specific)
- 不在 11:35-13:00 增持 (开盘 30 分钟和午盘流动性差)
- 撤离单可以排队到 13:00 开盘后市价
- 加仓单等下午 14:00 后再考虑
