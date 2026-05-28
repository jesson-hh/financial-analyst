# model-predictor — Tier-1 量化模型推理

**无需 LLM 调用**. 加载注册的模型 (LGB / FlowMatch / FinCast 等), 输出 prediction + rank_pct.

Memory 目录占位 — 大部分场景 agent 不读取 memory.

## 何时改这里

仅限"模型选择 / 集成"层面的提示, 例如:
- 默认模型集成顺序 (LGB → FM → FC)
- 模型 fallback 规则 (FM checkpoint 缺失时只用 LGB)
- 自适应权重阈值 (ICIR 负值时 FC 权重下调)

## 不需要做的事

- 不要在这里写"模型预测准确度历史" — 那是 quant-analyst 的事
- 不要在这里 hardcode model checkpoint 路径 — 走 `config/models.yaml`
