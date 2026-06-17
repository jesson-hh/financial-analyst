# 因子表达式 · 概念 → DSL 范例库(grounding)

> 给「炼」的大模型做 grounding:把交易/经验概念翻成**本引擎能编译**的因子表达式。
> 下列范例**均已经 `/factor/report` 实测可编译**(universe=csi_fast)。只用引擎白名单字段/算子(见基础 prompt 规则 4)。
> 维护:新增范例前务必先用 `/factor/report` 跑一遍确认 `status=ok`,再写进本表。

## 一、概念 → 可编译范例(照着仿写,别自创字段/函数)

| 概念 | 表达式 |
|---|---|
| 价格上穿 20 日均线(均线金叉) | `cross(close, ts_mean(close,20))` |
| 双均线金叉(5 上穿 20) | `cross(ts_mean(close,5), ts_mean(close,20))` |
| 5 日反转 | `rank(-delta(close,5))` |
| 20 日动量 | `rank(ts_sum(returns,20))` |
| 量价背离 | `-correlation(rank(close), rank(volume), 10)` |
| 低换手 | `-rank(turnover_rate)` |
| 高股息 | `rank(dv_ttm)` |
| 小市值 | `rank(-circ_mv)` |
| 低估值(低 PE) | `-rank(pe_ttm)` |
| 放量(量比) | `rank(volume / ts_mean(volume,20))` |
| 缩量(近 5 日均量 / 20 日均量) | `-rank(ts_mean(volume,5) / ts_mean(volume,20))` |
| 行业中性动量 | `indneutralize(ts_sum(returns,20), industry)` |
| 接近 60 日新高 | `rank(close / ts_max(close,60))` |
| 低波动 | `-rank(stddev(returns,20))` |
| 缩量企稳反转(组合) | `rank(-delta(close,5)) * (ts_mean(volume,5) < ts_mean(volume,20))` |

## 二、技术指标(MACD / RSI / KDJ / BOLL / WR 等)—— 大多可重建,见「TA 指标范例」块

本引擎虽无 `macd`/`dif`/`dea`/`kdj`/`rsi`/`boll` 这类**现成命名**字段,但有 `sma(x,n,m)`(GTJA 递归平滑 = EMA,平滑系数 α = m/n,故 **P 日 EMA = `sma(x,P+1,2)`**)外加 `ts_min`/`ts_max`/`ts_mean`/`stddev`/`delta`/`max_pair`/`cross`,**可重建大部分技术指标**。常见指标的**已验证写法**见下方「TA 指标范例」块,照着仿写,别自创 `macd`/`dif` 等不存在的字段。

例:MACD 金叉 `cross(sma(close,13,2)-sma(close,27,2), sma(sma(close,13,2)-sma(close,27,2),10,2))`;RSI / KDJ / BOLL / WR 同理可拼(见范例块)。

**真正无法表达的(缺底层原语,遇到就 `expr` 留空,并在 reply 说明缺哪种原语):**
- `OBV`:缺"自上市累加"的 expanding cumsum;
- `CCI`:缺平均绝对偏差(mean absolute deviation);
- `SAR`:缺抛物线递归(path-dependent)。

需要上述三类原语的经验,一律 `expr` 留空,reply 写"该指标缺 XX 原语,本引擎暂不支持量化"——不要硬编不存在的字段(如 `obv`/`cci`/`ret`)。

## 三、组合多个条件的写法

- 用 `*` 把布尔条件当 0/1 相乘,**不要用 `and` / `or`**(它们作用在 Series 上会报 `truth value of a Series is ambiguous`)。
  - ✅ 对:`rank(-delta(close,5)) * (ts_mean(volume,5) < ts_mean(volume,20))`
  - ❌ 错:`rank(-delta(close,5)) and (volume < ts_mean(volume,5))`
- 反向(看空 / 越小越好)用前置 `-`;截面强弱用 `rank(...)`;时序窗口算子第二个参数是整数天数(如 `ts_sum(returns,20)`)。
