# 14-Agent Stock-Deep-Dive Architecture

> 取代旧 `docs/architecture.md` (13 agent 三 tier 表述). 当前生产形态是 **14 agent**
> + **4 tier**, 新增 Tier-4 introspector 做 post-mortem 自检与规则提议.

## 总览

```
                              用户请求 (code, asof_date)
                                       │
                                       ▼
                              ┌────── Orchestrator ──────┐
                              │  asyncio.gather + DAG   │
                              └──────────────────────────┘
                                       │
   ┌───────────────────────────────────┼───────────────────────────────────┐
   ▼                                   ▼                                   ▼
[Tier 1 — 数据]              [Tier 2 — 分析师]              [Tier 3 — 决策]              [Tier 4 — 自省]
 5 agents, 并行                4 agents, 并行                 4 agents, 串行              1 agent, 异步
 ────────────────              ────────────────               ────────────────             ────────────────
 quote-fetcher                 fundamental-analyst            bull-advocate                introspector
 factor-computer               technical-analyst              bear-advocate                  (复盘报告本身,
 model-predictor               whale-analyst                  risk-officer                   写提议待 review,
 news-reader (UNTRUSTED)       quant-analyst                  report-writer ⬅ 唯一可写盘     不阻塞主流程)
 f10-reader   (UNTRUSTED)
```

| Tier | 数量 | 并行性 | Trust | 写盘? |
|------|------|--------|-------|-------|
| Tier 1 (数据) | 5 | 全并行 (`asyncio.gather`) | 3 trusted + 2 untrusted reader | 否 |
| Tier 2 (分析师) | 4 | Tier 1 完后全并行 | trusted | 否 |
| Tier 3 (决策) | 4 | bull+bear 并行 → CRO → writer | trusted | **只有 report-writer** |
| Tier 4 (自省) | 1 | report-writer 完后启动, 不阻塞返回 | trusted | 写 `_pending_introspections/` (待审) |

定义来源: `config/swarm/stock-deep-dive.yaml`.

---

## Tier 1 — 数据获取层

**目标**: 从 Qlib bin / Parquet / 网络拉数据, 产 Pydantic-validated JSON.

### `quote-fetcher` (trusted)
**用途**: 当日价格 + 估值 + 动量 + 均线 + 波动率快照.
**依赖**: 无.
**输出 schema** `QuoteOutput`:
```python
code: str           asof_date: str       price: float
pe: Optional[float]  pb: Optional[float]  ps: Optional[float]  dv: Optional[float]
mv_yi: Optional[float]   circ_mv_yi: Optional[float]   turnover_rate: Optional[float]
ret_5d / ret_20d / ret_60d: Optional[float]
ma5 / ma20 / ma60: Optional[float]
volatility_20d: Optional[float]   volume_ratio: Optional[float]
```
**数据源**: Qlib bin (`D.features()`) + Tushare daily_basic.

### `factor-computer` (trusted)
**用途**: 34 量化因子 + 主力行为 + 涨停首板 v5 + 量能 regime + zoo signals + 历史时间线 + 产业链 context.
**依赖**: 无.
**输出 schema** `FactorOutput`:
```python
code: str   asof_date: str
factor_scores: Dict[str, float]    # 34 因子原始值
whale_signals: Dict[str, Any]      # OBV / VR / MFI / shadow / chip
board_score: Dict[str, Any]        # 首板 v5 (五维, -7..+8)
vol_regime: Dict[str, Any]         # super_distr / distr / tail_surge / bounce / neutral
zoo_signals: Dict[str, Any]        # latest snapshot
stock_timeline: str                # 该股累积研报时间线 (markdown)
chain_context: Dict[str, Any]      # 产业链 product + peers + catalyst
```

### `model-predictor` (trusted)
**用途**: 量化模型 (LGB + FM + TSFM B3 等) 输出共识排名.
**依赖**: 无.
**输出 schema** `ModelOutput`:
```python
code: str   asof_date: str
per_model: Dict[str, Dict[str, float]]   # {"lgb": {"rank_pct": 0.769, ...}, "fm_w10": {...}, "b3v2": {...}}
consensus_rank_pct: float                # 综合 0-1
```

### `news-reader` ⚠ **UNTRUSTED**
**用途**: 读最近新闻 (TDX kuaixun / eastmoney / sina), 提取结构化事件 + 数字.
**依赖**: 无.
**输出 schema** `NewsOutput`:
```python
events: List[EventItem]     # {date, title, severity, type}
numbers: List[NumberItem]   # {value, unit, label} — 财报数字
```
**Trust 模型**: 输入是任意网络文本. LLM 提取结果走 Pydantic 字段约束 + 字符白名单
+ 字符串长度上限. 下游 agent 只读 JSON, 不读原文.

### `f10-reader` ⚠ **UNTRUSTED**
**用途**: 读 TDX F10 (15 类, 含公司大事 / 龙虎榜 / 大宗交易 / 游资白名单), 提取结构化事件分类.
**依赖**: 无.
**输出 schema** `F10Output`:
```python
recent_events: List[EventItem]
lhb_seats: Dict[str, List[LHBSeat]]     # 龙虎榜营业部 + 游资 trader_tag
event_classified: Dict[str, List[EventItem]]   # positive/negative/calendar/neutral 四分类
```

---

## Tier 2 — 分析师层

**目标**: 把 Tier 1 的原始数据浓缩成 **五维评分 (-2..+2)** + bull/bear 点 +
**V/F anchors** (V1-V10 视角 / F1-F14 失败模式 — 见 `memories/_shared/playbook_V1_V10.md`).

### `fundamental-analyst`
**依赖**: `quote-fetcher`.
**输出 schema** `FundamentalOutput`:
```python
valuation_score: int = 0  # -2..+2
mv_tier: str              # micro / small / mid / large / mega — 决定因子归零规则
dimension_detail: Dict[str, str]
red_flags: List[str]      # 红牌 (large_cap_factor_neutralized / bearish_ma_alignment / ...)
bull_points: List[str]    # 含 [V#] 锚点
bear_points: List[str]    # 含 [V#] 锚点
```
**关键规则**: `mv_tier in [large, mega]` 时, `valuation_score` 强制归零 (因子 IC 衰减).

### `technical-analyst`
**依赖**: `quote-fetcher`, `factor-computer`.
**输出 schema** `TechnicalOutput`:
```python
technical_score: int       # -2..+2
ma_state: str              # bullish | bearish | neutral
rsi_state: str             # overbought | oversold | neutral
macd_state: str            # bullish_cross | bearish_cross | neutral
factor_consensus: str      # strong_long / weak_long / neutral / weak_short / strong_short
breakout_signal: Optional[str]
bull_points / bear_points: List[str]
```

### `whale-analyst`
**依赖**: `quote-fetcher`, `factor-computer`.
**输出 schema** `WhaleOutput`:
```python
whale_score: int           # -2..+2
sentiment_label: str       # super_distr | distr | tail_surge | bounce | neutral
vol_regime_label: str      # 同上
board_total_score: Optional[int]   # 首板 v5 总分 -7..+8 (无涨停 = None)
alerts: List[str]
bull_points / bear_points: List[str]
```

### `quant-analyst`
**依赖**: `model-predictor`, `factor-computer`.
**输出 schema** `QuantOutput`:
```python
quant_score: int                    # -2..+2
model_consensus: str                # strong_long / weak_long / ...
conviction_level: str               # low | medium | high
anti_signals: List[str]             # 主动列出反向证据
bull_points / bear_points: List[str]
```

---

## Tier 3 — 决策层

**目标**: 多空辩论 + CRO 审查 + 单 writer 落盘.

### `bull-advocate` (V-anchored)
**依赖**: fundamental, technical, whale, quant.
**Memory**: `memories/bull-advocate/factor_insights_long_side.md` + `_shared/playbook_V1_V10.md`.
**输出 schema** `BullOutput`:
```python
thesis_bullets: List[str]         # ≥2 条, 每条 [V#] 开头
catalysts: List[str]              # 待发生事件
target_price_high: float          # 牛市目标
target_price_base: float          # 基准目标 (≠ high)
disproof_signals: List[str]       # 看多论据被推翻的条件
v_anchors: List[str]              # ["V1", "V4-立讯模式", ...] 非空
```
**Hard constraints**: 即便整体看空, 也必须给 ≥2 条逆向/战术 bullet. 单次激进 retry +
`[V0]` 占位兜底.

### `bear-advocate` (F-anchored)
**依赖**: 同 bull.
**Memory mode**: `retrieval` (`memories/bear-advocate/pitfalls.md` 较大, FTS5 top-5 retrieval).
**输出 schema** `BearOutput`:
```python
thesis_bullets: List[str]         # ≥2 条, 每条 [F#] 开头
valuation_concerns: List[str]
technical_breakdown: List[str]
target_price_low: float           # 下行目标
downside_pct: float               # 负百分比 (e.g. -0.20)
f_anchors: List[str]              # ["F2", "F8", ...] 非空
```
**Hard constraints**: 同 bull, retry + `[F0]` 兜底.

### `risk-officer` (CRO)
**依赖**: bull, bear, news-reader, f10-reader, factor-computer.
**Memory mode**: `retrieval` + `borrows_memory: [bear-advocate]` (CRO 借用 bear 的 pitfalls).
**输出 schema** `RiskOutput`:
```python
risk_score: int                   # -2..0 (CRO 永远不打正分, 只约束)
blind_spots: List[str]            # 多空都漏掉的事
position_sizing_advice: str       # "0%" / "1-3%" / "3-5%" / "5-8%"
veto_flags: List[str]             # 非空 → position_pct=0 硬否决
conditional_approval: str         # "OK if stop-loss at 1450; ..."
hard_rule_triggers: List[str]     # 从 memory 命中的硬规则
```

### `report-writer` (唯一可写盘 agent)
**依赖**: 全部 Tier 1 + Tier 2 + bull + bear + risk-officer.
**输出 schema** `ReportOutput`:
```python
output_md_path: str               # out/<CODE>_<DATE>.md
output_json_path: str
rating_overall: int               # -10..+10 (五维加总)
rating_dimensions: Dict[str, int] # {基本面: x, 技术面: y, 主力情绪: z, 量化模型: w, 风险面: v}
action: str                       # buy | hold | sell | avoid
target_price: float
stop_loss: float
position_pct: float               # 0..0.10
```
**写盘范围**: 只能写 `out/<code>_<date>.{md, json, html}` + `out/<code>_progress.json`.
其他路径 import-time 拒绝.

---

## Tier 4 — Post-mortem 自省层

### `introspector`
**依赖**: report-writer + 全部 Tier 2 + bull + bear + risk-officer.
**Memory**: `memories/introspector/introspector_rules.md` (`memory_mode: full`).
**输出 schema** `IntrospectionOutput`:
```python
quality_flags: List[str]                       # 本次研报立即可见的问题
proposals: List[IntrospectionProposal]         # 跨案例归纳的规则提议
summary: str
written_to: Optional[str]                       # 提议 JSON 落盘路径

# IntrospectionProposal:
target_agent: str   # "risk-officer" / "bear-advocate" / "_shared" 等
pattern: str        # 触发特征交集
proposed_rule: str  # 一句话规则
confidence: str     # low / med / high (case 数: 2 / 3-5 / 6+)
rationale: str
```

**关键设计**:
- **不阻塞主流程**: report-writer 已落盘后才跑. 失败也不影响用户拿到报告.
- **不自动 patch memory**: 提议写到 `memories/_pending_introspections/<date>_<code>.json`, 人工
  review 后用 `dream accept/reject` 或手工编辑落盘.
- **倾向加规则到 risk-officer**: CRO 有 veto 权, 加 CRO 规则比削弱任何 analyst 安全.
- **Anti-pattern 黑名单**:
  - "Need more data" (无 pattern → 空 proposals)
  - "Bear was too bearish" (无具体 trigger)
  - 矛盾既有 memory 而不引用 (引用既有规则做反例)

**累积流程**: `dream run` 扫 `out/<CODE>_<DATE>.md` + actual T+5d/T+20d 价格回填, 把
introspector 在多份报告里**重复出现 ≥3 次**的 pattern 升级成 `memories/_proposed/`
正式提议, 然后人工 `accept / reject`.

---

## Memory 注入机制

每个 agent 实例化时加载:

```
memories/<agent>/*.md       (per-agent rule book)
memories/_shared/*.md       (跨 agent 共享 — V1-V10, F1-F14, pitfalls)
```

拼接结果作为 `# Memory` section 追加到 system prompt.

| 模式 | 触发 | 用途 |
|------|------|------|
| `full` (默认) | 文件 < 几 KB | 一次性全注入 |
| `retrieval` | 文件大 (pitfalls.md / large playbook) | SQLite FTS5 top-k=5, query 从上游 JSON 提取 |

`always_include.txt` 白名单文件**永远全注入**, 不走 retrieval.

**热更新**: markdown 改完立即生效 (下次 agent 实例化时读). 不需要重启进程.

---

## Trust 模型

| 边界 | 谁过 | 谁不过 |
|------|------|--------|
| 任意网络文本 → agent | `news-reader` + `f10-reader` (只读 + JSON schema lock) | 其他 agent 看不到原文, 只看 JSON |
| 工具白名单 | reader 只能 `[read, grep]` | 不能 write/exec |
| 字符串长度 | Pydantic Field 约束 + char whitelist | 防 prompt injection |
| 文件写权限 | `report-writer` 写 `out/<code>*` | 其他 agent 0 写权限 |

---

## 失败模式 + 兜底

| Agent | 失败方式 | 兜底 |
|-------|---------|------|
| Tier 1 任一 | 数据缺失 → 返回 Optional 字段为 None | 下游 Tier 2 不会崩, 该字段判 N/A |
| Tier 2 | LLM 调 fail / JSON 解析 fail | 返回默认 Output (score=0, list 空) |
| bull / bear | 空 `thesis_bullets` | retry once (温度 +0.2) → `[V0]/[F0]` 占位 |
| risk-officer | LLM fail | 默认 `position_sizing="0%"`, `veto_flags=["LLM_FAILED"]` (保守) |
| report-writer | 任何上游 fail | 仍可写报告, 缺失项标 "数据缺失" |
| introspector | 任何 fail | 不影响主流程, 报告已落盘. progress.json 显示 `fail` |

---

## DAG 配置 (config/swarm/stock-deep-dive.yaml)

```yaml
name: stock-deep-dive
description: A-share single-stock deep-dive research (14 agents, 4 tiers)

agents:
  # Tier 1 — parallel, no deps
  - {name: quote-fetcher,   deps: []}
  - {name: factor-computer, deps: []}
  - {name: model-predictor, deps: []}
  - {name: news-reader,     deps: []}
  - {name: f10-reader,      deps: []}

  # Tier 2 — parallel after Tier 1
  - {name: fundamental-analyst, deps: [quote-fetcher]}
  - {name: technical-analyst,   deps: [quote-fetcher, factor-computer]}
  - {name: whale-analyst,       deps: [quote-fetcher, factor-computer]}
  - {name: quant-analyst,       deps: [model-predictor, factor-computer]}

  # Tier 3 — bull/bear parallel, then CRO, then writer
  - {name: bull-advocate, deps: [fundamental, technical, whale, quant]}
  - {name: bear-advocate, deps: [...], memory_mode: retrieval}
  - {name: risk-officer,  deps: [bull, bear, news-reader, f10-reader, factor-computer],
                          borrows_memory: [bear-advocate], memory_mode: retrieval}
  - {name: report-writer, deps: [全部 Tier 1+2+bull+bear+risk-officer]}

  # Tier 4 — post-mortem
  - {name: introspector, deps: [report-writer, ...], memory_mode: full}
```

---

## 性能基线 (SH600519, 2026-05-23)

| 阶段 | 耗时 | 占比 |
|------|------|------|
| Tier 1 (quote/factor/model/news/f10) | <0.4s | <1% |
| Tier 2 (fund/tech/whale/quant) 并行 | 51-90s | 22% |
| Tier 3 bull/bear/risk-officer 并行 | 57-96s | 23% |
| Tier 3 report-writer | 137s | 33% |
| Tier 4 introspector | 87s | 21% |
| **总壁钟** | **~7 min** | |

> Tier 3 report-writer 是单点最长, 它要消化所有上游 JSON 写 1500-2500 字 markdown.
> 不可并行化.

---

## 扩展点

| 想加什么 | 怎么加 |
|---------|--------|
| 新 sub-agent | 实现 `SubAgent[OutputModel]`, `register_agent()`, 改 swarm yaml 加 deps |
| 新 memory 文件 | 在 `memories/<agent>/` 加 `.md`, 重启或 `agent.memory.reload()` |
| 新数据源 | 实现 `BaseLoader`, 注册到 `config/loaders.yaml` |
| 新量化模型 | 实现 `BaseModel`, `ModelRegistry.register()`, plugins.yaml 引入 |
| 新 swarm preset | 拷贝 `config/swarm/stock-deep-dive.yaml` 改名 + 改 agent 列表 |

详见 [`docs/byom.md`](../byom.md) + [`docs/extending.md`](../extending.md).
