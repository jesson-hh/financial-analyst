# factor-computer — Tier-1 因子计算

**无需 LLM 调用**. 计算 34+ 个 alpha 因子 (rev_20 / mom_20 / rsi_14 / macd_bar / bb_pct_20 / obv_slope_20 等) + chain_context.

Memory 目录占位 — 大部分场景 agent 不读取 memory.

## 何时改这里

仅限"因子定义"层面的提示, 例如:
- 自定义因子表达式 (BYOM 时通过 plugin 加新因子)
- 因子默认 ranking 窗口 (60d / 120d)
- chain_context injection 阈值

## 不需要做的事

- 不要在这里写"因子有效性历史" — 那是 quant-analyst 的事 (`quant-analyst/rules_learned.md`)
- 不要在这里写代码 — 因子 expression 走 `factors/zoo/` Python 注册
