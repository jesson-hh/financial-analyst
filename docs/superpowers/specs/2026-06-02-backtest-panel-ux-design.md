# Agent 回测 Panel UX 改造 (P0+P1+P2) · 设计

> 状态: 设计审中
> 日期: 2026-06-02
> 子项目: financial-analyst 量化工作台 — Agent 回测 panel
> 工作量: ~1.5 天 (P0 半天 + P1 半天 + P2 半天)
> 触发事件: 用户 2026-06-02 反馈 "这个在回测什么我都不知道" → 全档 UX 透明化

## 目标

当前 `BacktestMode` 把数据通路跑通了 (mock 秒级, real 6min, 出 KPI + 净值 + 交易表),
但**用户看不懂在回测什么**:
- 没说清 Mock 和 Real 的差别 → 以为 Mock 是策略
- 候选池是哪个池子? 排序因子是哪个? 窗口怎么自动填? → 不知道
- 交易理由是一行 ellipsis, 看不到 LLM 完整决策 / decisions 全部 legs / market_view
- 8 个 KPI 顶部光秃秃, 不知道 Calmar/逐笔胜率/波动率怎么算
- 控件只能调 4 个 (start/end/cash/topn + mock|real), 改不了池子/持有期/因子/止损止盈

本 spec 一次做完 **3 档透明化**:
- **P0** 让用户**看懂在回测什么** (banner + 横条 + 交易 modal)
- **P1** **完整透明** (Real LLM 全文 + KPI 公式 tooltip + 候选池过滤显示)
- **P2** **参数完整可调** (后端扩字段 + 前端控件)

## 范围

### 做
- 后端 `BacktestRunReq` 扩 4 字段 + `_MockAgent` 接 `hold_days` 参数 + `CandidateConfig` 接 `pool` 字段
- `quant.jsx::BacktestMode` 完整重构 (banner / 横条 / 交易 modal / KPI tooltip / 4 个新控件)
- `quant.html` 缓存 buster ?v= 升版
- Playwright 真浏览器烟测

### 不做 (留下轮)
- ❌ WatchPanel (实时盯盘) 同款改造 — 下次单独立项, 不在本轮 scope
- ❌ 后端落盘 LLM 原始 prompt / response / latency — 第一版前端只显示现有 `Decision.raw` (已存解析后 JSON), prompt 透传留 backend P1.4
- ❌ 因子下拉完整暴露 442 alpha — 只白名单 6-8 个常用 (rev_20 / mom_20 / vol_60 / turnover_pct_60 / pe_clip / pb_clip / ps_clip / dv_ttm), 完整列表后续接 `/factor/list`
- ❌ pool=all 真跑 (~5500 只, mock 3 日窗口都要 30s+) — UI 暴露但 backend 校验拒绝 (返 400 "全市场池请用 csi800 替代")
- ❌ 真 PIT-safe 因子值替代 `inp.rev20_rank` 硬编码 — 当前 mock 决策只看 rev20, 改成可选因子需要重构 `engine.py:_prepare_decision_input` (留下轮)

---

## P0 让用户看懂在回测什么 (~半天)

### P0.1 顶部"策略说明" banner

`BacktestMode` 控件条下方加固定栏, 根据 `mode` 切换文案:

**Mock 模式**:
```
┌────────────────────────────────────────────────────────────────────┐
│ 📊 Mock 模式 · 演示数据通路, ⚠ 不是盈利策略                          │
│                                                                    │
│ 每次空仓时买入候选池中 rev_20 分位最低 (跌得最惨) 的 1 只,          │
│ 持有 N 个交易日后无条件了结. 0 次 LLM 调用, 确定性, 可手算核对.    │
│                                                                    │
│ 用途: 验证 数据→决策→撮合→净值 链路通畅. 真实策略请切 Real LLM 模式│
└────────────────────────────────────────────────────────────────────┘
```

**Real LLM 模式**:
```
┌────────────────────────────────────────────────────────────────────┐
│ 🤖 Real LLM · 真实策略回测 (慢, 单日窗口 ~6min)                     │
│                                                                    │
│ 每日盘前调用 qwen3.5-plus, 输入:                                   │
│ • 候选池 Top-N (按 rev_20 排序)                                    │
│ • 当前持仓                                                          │
│ • 候选股票的 rev_20 分位                                           │
│ • 该日新闻摘要 + 事件摘要 (PIT-safe, 不超过 as_of)                 │
│                                                                    │
│ 输出 5 档动作 (buy/add/hold/reduce/sell), 每条带 reason.            │
│ 决策被 prompt 哈希缓存 — 同样输入只调一次 LLM (.fa/decision_cache) │
└────────────────────────────────────────────────────────────────────┘
```

样式: 单 Panel, light-paper 底色, 边框 `--line`, 中文 serif 标题 + mono 细节.

### P0.2 候选池 + 因子来源横条 (运行后顶部)

回测完成后 (`d` 有值), 在 "组合表现" Panel 上方加一行 chip 串:

```
┌──────────────────────────────────────────────────────────────────┐
│ 候选 N=20  ◀ 池: csi300 (300 只)  ◀ 排序: rev_20 ↑                │
│ 窗口: 2026-05-20 → 2026-06-03 (10 个交易日)                      │
│ 模式: Mock · 持有期: 3 日 · 撮合: day                            │
└──────────────────────────────────────────────────────────────────┘
```

让用户**一眼看清池子/因子/窗口/持有期**. 这一行来自 `d.params` (后端 BacktestRunReq.model_dump() 已透传).

### P0.3 交易理由可点击展开 modal

**现状**: `trades` 表 "理由" 列是单行 ellipsis, hover 不出, 看不到完整 reason / market_view / 当日 decisions 其它 legs.

**改造**:
1. 表头加 "理由" 列宽 + 改 `cursor:pointer` + 末尾加 "🔍" 图标
2. 点击行 → 弹 modal:

**Mock 模式 modal**:
```
┌────────────────────────────────────────────────┐
│ 2026-05-23 · SH600519 · buy             ×     │
├────────────────────────────────────────────────┤
│ 当日 market_view                                │
│ ┃ mock 决策(确定性)                            │
│                                                │
│ 本笔 reason                                    │
│ ┃ mock: rev20 分位最低(SH600519), 反转介入      │
│                                                │
│ 当日全部决策 (1 条)                            │
│ ┃ [1] buy SH600519 50% pct, stop_loss=0       │
│ ┃     reason: mock: rev20 分位最低...          │
│                                                │
│ 当日候选池 Top-5 (rev20 升序)                  │
│ ┃ 1. SH600519 (rev20=−0.082) ← 本笔             │
│ ┃ 2. SZ000858 (rev20=−0.064)                   │
│ ┃ 3. SH601318 (rev20=−0.058)                   │
│ ┃ ...                                          │
│                                                │
│ 当日持仓快照 (空仓)                            │
│ ┃ —                                            │
└────────────────────────────────────────────────┘
```

**Real LLM 模式 modal** (扩展, P1.2 完成):
```
┌────────────────────────────────────────────────┐
│ 2026-05-23 · SH600519 · buy             ×     │
├────────────────────────────────────────────────┤
│ 当日 market_view                                │
│ ┃ 短线情绪偏弱, 但白酒龙头跌出反转机会...      │
│                                                │
│ 本笔 reason                                    │
│ ┃ 茅台连续 5 日下跌 8%, MA20 跌破后...          │
│                                                │
│ LLM 返回原文 (raw JSON)                         │
│ ┃ {                                            │
│ ┃   "market_view": "短线情绪偏弱, 但...",       │
│ ┃   "decisions": [                              │
│ ┃     {"code":"SH600519","action":"buy",...}    │
│ ┃   ],                                          │
│ ┃   "warnings": []                              │
│ ┃ }                                            │
│ ┃ [展开/折叠]                                    │
│                                                │
│ 当日候选池 Top-5 (rev20 升序)                  │
│ ┃ ...                                          │
│                                                │
│ 当日持仓快照                                   │
│ ┃ ...                                          │
└────────────────────────────────────────────────┘
```

数据已存在: `d.decisions[t.date]` (后端已透传 decisions_by_date 含 market_view + legs + raw + warnings). 前端只需切片显示.

---

## P1 完整透明 + 公式说明 (~半天)

### P1.1 KPI tooltip

8 个 KPI 鼠标悬停 → 浮层显示公式 + 中文说明:

| KPI | tooltip 内容 |
|-----|--------------|
| 年化 | 年化收益率 = (1 + 区间总收益)^(250/区间交易日) − 1 |
| Sharpe | 夏普比率 = 年化收益 / 年化波动率 (假设无风险=0) |
| 最大回撤 | 最大回撤 = max((peak − trough) / peak), 滚动统计 |
| Calmar | 年化收益 / \|最大回撤\| · 抗回撤能力指标 |
| 波动率 | 年化波动率 = std(日收益) × √250 |
| 换手 | 区间总成交额 / 期末总资产 / 年化系数 (来自 portfolio.py) |
| 胜率(日) | 净值正收益日数 / 总交易日数 |
| 逐笔胜率 | 盈利卖单 / 总卖单 (action='sell' 且 pnl>0) |

实现: 加 `<Kpi tooltip="...">` prop, hover 显示固定栏 (复用 NodeTooltip 模式, 但简化).

### P1.2 Real LLM 模式完整透明 (P0.3 modal 扩展)

第一版只显示**现有 `Decision.raw`** (后端已存解析后 JSON dict). 不要求 backend 落 prompt/latency.

modal 内显示:
- **解析后 raw** (markdown code-fence JSON 展示, 默认折叠到 200 字符, 点 "展开" 看全文)
- **解析状态**: 如果 `raw._error == "json"`, 顶部标黄字 "LLM 输出非合法 JSON, 已 fallback (原始文本见 `_raw`)"
- **缓存命中**: 第一版不显示 (后端 DecisionCache 默认不暴露 hit/miss). 未来后端加 `_cache_hit: bool` 后再扩.

### P1.3 候选池过滤逻辑展示

P0.2 横条 "池: csi300 (300 只)" 部分**点击** → 浮层:

```
候选池构造流程:
1. 全 csi300 成分股 (300 只, 来自 stock_data/parquet/index_components.parquet)
2. 排除停牌 (T-1 收盘 quote_state ≠ 'normal' 视为停牌)
3. 排除 ST (name 含 'ST'/'*ST')
4. 排除非 PIT-安全 (上市不满 60 日)
5. 按 rev_20 升序排列 (factor 名可在控件区切换, P2.1)
6. 取前 N=20

实际入选 N=20 (剩余 230 只未入候选)
```

数据来源: 后端 `CandidateConfig.build_candidates()` 已经做了 1-6 步, 第一版前端**只展示静态说明文案**, 不实际计数 (剩余 230 只). 后续后端在 `BacktestResult.warnings` 加 `candidate_filter_stats: {n_pool, n_excluded_suspend, n_excluded_st, n_excluded_young, n_final}` 后再实数化.

---

## P2 参数完整扩展 (~半天)

### P2.1 后端 `BacktestRunReq` 扩字段

```python
class BacktestRunReq(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    init_cash: float = 1_000_000.0
    candidate_topn: int = 20
    mode: str = "mock"
    match_freq: str = "day"
    # 新增 ↓
    pool: str = "csi300"                          # csi300|csi_fast|csi500|csi800
    hold_days: int = 3                            # mock 持有期 (替代 _MOCK_HOLD_DAYS)
    factor_name: str = "rev_20"                   # 候选排序因子 (第一版只支持 rev_20)
    stop_loss_pct: Optional[float] = None         # 默认 None (不触发)
    take_profit_pct: Optional[float] = None       # 默认 None (不触发)
```

校验:
- `pool ∈ {csi300, csi_fast, csi500, csi800}` (拒 'all', 第一版不开)
- `hold_days ∈ [1, 60]`
- `factor_name == "rev_20"` (第一版白名单, 后续放开)
- `stop_loss_pct ∈ (0, 0.5]` 或 None
- `take_profit_pct ∈ (0, 2.0]` 或 None

### P2.2 后端 `run_backtest` 接住新字段

`buddy/backtest_run.py:run_backtest()` 改动:
1. `CandidateConfig(topn=req.candidate_topn, pool=req.pool)` — **要求 candidate.py 加 `pool` 字段**, 默认 csi300, 解析方式同 universe.py
2. `_MockAgent(hold_days=req.hold_days)` — 把现 `_MOCK_HOLD_DAYS=3` 改成 `__init__` 参数, 默认 3
3. `req.factor_name` 第一版断言 == "rev_20", 不动 engine. (engine.py 已经 hardcoded 注入 rev20_rank, 改其它因子需要重构 _prepare_decision_input, 下轮做)
4. `req.stop_loss_pct / take_profit_pct` → 传给 `_MockAgent`. 第一版**仅在 mock agent 内部判定** (`decide()` 拿持仓的 `inp.holdings[code].unrealized_pct` 判断是否触发), 不动 broker.py. 如 broker.py 已有 EOD stop_loss 支持, implementer 可选透传; 没有就只 mock agent 实现, 真 LLM 模式不触发止盈止损 (由 LLM 自己决定动作).
5. `_MockAgent` 决策优先级: 持仓收益 ≥ take_profit_pct → sell ▷ 持仓收益 ≤ -stop_loss_pct → sell ▷ 持有 ≥ hold_days → sell ▷ 否则 hold/buy

### P2.3 候选池 `CandidateConfig.pool` 字段

`backtest/candidate.py` 改动:
```python
@dataclass
class CandidateConfig:
    topn: int = 20
    pool: str = "csi300"   # 新增, 同 universe.py 解析

    def build_candidates(self, as_of: pd.Timestamp, paths: DataPaths) -> list[str]:
        # 1. 加载池子成分股 (调 financial_analyst.data.universe.resolve_universe_codes)
        codes = resolve_universe_codes(self.pool, as_of, paths)
        # 2. 过滤停牌/ST/上市不满 60 日 (现已实现)
        # 3. 按 rev_20 升序取 Top-N
        ...
```

复用 `data/universe.py:resolve_universe_codes` (P1.6 整合后已统一入口), 不重复实现.

### P2.4 前端控件扩展

`BacktestMode` 控件条改成两行:

**第一行** (基础):
```
[起始日] [结束日] [初始资金] [候选 N] [Mock | Real]  [起回测 ▶]
```

**第二行** (新增, 折叠态默认显示, 点 "高级 ▾" 切换):
```
[池: csi300 ▾] [持有期: 3 日] [因子: rev_20 ▾] [止损: 5% ☐] [止盈: 10% ☐]
```

- 池 dropdown: csi300 (默认) / csi_fast (~100 大盘) / csi500 / csi800
- 持有期: number input, 1-60
- 因子 dropdown: 第一版只 rev_20 (灰显 + tooltip "更多因子下轮接 /factor/list")
- 止损/止盈: checkbox + number, 默认 disabled

---

## 跨切关注

### 数据契约

后端返回的 `BacktestResult` JSON 顶层字段保持向后兼容. `d.params` 已经透传 `BacktestRunReq.model_dump()`, 加字段不破坏现有调用方.

新增的 `d.decisions[date]` 结构 (已存在, 第一版不动):
```json
{
  "2026-05-23": {
    "market_view": "...",
    "decisions": [{"code":"...","action":"buy","weight_pct":50,"reason":"...","stop_loss":0}],
    "warnings": [],
    "raw": { ... 完整原始 LLM JSON 或 mock 结构 ... }
  }
}
```

前端 `reasonFor(t)` 已经从这里取 reason, modal 复用同一份数据.

### 提交策略

- 单分支 `feat/backtest-panel-ux` (从 main `73571b8` 派生)
- 3 个 commit (按 task 分):
  1. `feat(backtest): BacktestRunReq 扩 4 字段 + CandidateConfig pool + _MockAgent hold_days`
  2. `feat(ui): BacktestMode banner / 横条 / 交易 modal / KPI tooltip / 高级控件`
  3. `test(e2e): Playwright 烟测 + 回归 (mock 4 日 csi_fast 看 banner/横条/modal/tooltip)`
- 不推 origin (保留等一起推, 按用户既定流程)

### 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_backtest_run_req.py` | `BacktestRunReq` 新字段校验 (pool/hold_days/factor_name/stop_loss_pct/take_profit_pct), 拒 'all' 池, 拒非白名单因子, hold_days 边界 |
| `tests/test_candidate_pool.py` | `CandidateConfig(pool='csi_fast')` 跑 mock as_of → ~100 codes; `pool='csi300'` 跑 → ~300 codes |
| `tests/test_mock_agent_hold_days.py` | `_MockAgent(hold_days=5)` 持有 5 日后无条件 sell (覆盖原 _MOCK_HOLD_DAYS=3 行为) |
| `tests/test_mock_agent_stop_loss.py` | `take_profit_pct=0.1` → 持仓收益 10% 触发 sell, 不等 hold_days |
| Playwright | `tests/test_backtest_panel_ux.py` (新): 跑 mock 4 日 csi_fast → 看 banner 文案 / 横条 chip / KPI tooltip / 点击交易出 modal / 高级控件折叠展开 |

### 验收 DoD

- [ ] `POST /backtest/run` 接受 `pool=csi_fast hold_days=5 take_profit_pct=0.1` → 200
- [ ] `POST /backtest/run` 拒 `pool=all` → 400 "全市场池请用 csi800 替代"
- [ ] `_MockAgent(hold_days=5)` 跑 6 日窗口产 buy@day1 + sell@day6 (硬编码 3 → 5 生效)
- [ ] 前端 Mock banner 显示 "演示数据通路, ⚠ 不是盈利策略" 红字
- [ ] 前端 Real banner 显示 LLM 输入字段列表
- [ ] 运行后顶部横条显示 "候选 N=20 ◀ 池 csi_fast (100 只) ◀ 排序 rev_20 ↑ 窗口 2026-05-30 → 2026-06-03"
- [ ] 点击交易表任一行 → modal 显示 market_view + 当日全部 legs + 候选池 Top-5
- [ ] hover KPI "Calmar" → tooltip 显示 "年化收益 / |最大回撤|"
- [ ] 高级控件 "持有期" 改 5 → 跑出来真的 5 日才 sell (端到端)
- [ ] Playwright 烟测全过
- [ ] `quant.html` ?v= bump (旧浏览器拿新 jsx 不踩缓存)
- [ ] 全量回归 不破
- [ ] 工作分支 feat/backtest-panel-ux, main 不动, 不推 origin

### Workflow 编排

3 阶段串行 (避免 Phase 0 并行原罪):
1. **T1 后端** (1 agent): BacktestRunReq + run_backtest + candidate.py pool + _MockAgent hold_days/stop/profit + 4 个 backend 测试
2. **T2 前端** (1 agent): quant.jsx BacktestMode 完整重构 + quant.html ?v= bump
3. **T3 Playwright 验证** (1 agent): 端到端烟测 + 全量回归 + 修

各阶段独立 commit, controller 串联. 不并行 (后端和前端有 shape 依赖, 写完接口才能写 UI).
