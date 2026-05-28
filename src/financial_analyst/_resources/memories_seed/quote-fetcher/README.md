# quote-fetcher — Tier-1 行情数据 fetcher

**无需 LLM 调用**. 通过 `data/loaders` 拉历史 OHLCV + 估值, 输出结构化 dict.

Memory 目录占位 (一致性) — 大部分场景 agent 不读取 memory.

## 何时改这里

仅限"数据源行为"层面的提示, 例如:
- 复权 (qfq/hfq) 默认策略
- 当 Tushare 拉不到时 fallback 顺序 (qlib_binary → tushare → pytdx)
- 字段单位归一化约束 (volume in 手 not 股)

实际行为靠代码控制, memory 仅作 BYOM 用户 customize 时的参考.

## 不需要做的事

- 不要在这里写"个股研报经验" — 那是 fundamental/technical/whale-analyst 的事
- 不要在这里 hardcode 真实行情数据
