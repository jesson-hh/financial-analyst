# 因子评测引擎 (Factor Evaluation Engine) 设计 · SP-A

> 状态: 已批准, 待落 plan
> 日期: 2026-05-28
> 子项目: 量化研究流水线 SP-A (单因子业内标准评测)

## 目标

在 `financial-analyst` 包内**自包含**地实现单因子的业内标准评测 (Alphalens / 聚宽因子分析 那一类): 给一个因子 (已注册名 或 表达式), 在一个股票池/频率/区间上, 产出 **IC 全套 + 分位回测 + 多空组合净值** 的结构化报告. 纯后端、可单测、不依赖 `G:/stocks` 的任何重模型.

## 背景与定位

### 全套流水线 (终点) 与拆分

完整的量化研究流水线 (= 用户要的"全套, 业内标准"):

1. 想法 → 因子 (经验/记忆卡片 → 因子表达式, 炼因子)
2. **单因子评测** (IC / 分位 / 多空组合) ← **本子项目 SP-A**
3. 因子预处理 (去极值 / 标准化 / 中性化)
4. 多因子合成 (相关性 / 正交化 / 合成)
5. 合成模型 (截面 ML, 自包含)
6. 策略回测 → 结论
7. 风险/归因
8. 迭代 (归档 / 版本 / 对比)

拆分为可独立交付的子项目:

| 子项目 | 内容 | 依赖 |
|---|---|---|
| **A. 因子评测引擎 (本文)** | 第 2、3 步标准评测, 纯后端可单测 | 无 (地基) |
| B. 炼因子闭环 | 经验卡片→LLM→表达式→速测→入库 | 轻依赖 A |
| C. 因子工作台 UI | `quant.jsx` 接成真页面 + 直连 REST 端点 | A、B |
| D. 多因子 + 合成模型 | 第 4、5、6 步, 内置 ML 合成器 | A |
| E. 研究档案 / 迭代 | 第 8 步, 版本/对比/结论沉淀 | B |

### 已锁定的关键决策

- **自包含**: pip 用户没有 `G:/stocks`, 量化能力必须能独立发布运行. 不 import / 不 subprocess 进 `G:/stocks` 的 LGB/FM/v4_ranking. (现状已是完全解耦, 见下.)
- **v1 范围 = 标准版**: IC 全套 + 分位回测 + 多空组合净值 + 去极值/标准化. **中性化 (行业+市值) 不进 v1**, 留到 A.2 (有数据依赖, 单独一块更稳).
- **不碰 UI**: SP-A 只到"引擎 + 能在对话里跑", REST 端点和量化页面归 SP-C.

### 现状基线 (已勘察)

- `factors/zoo/bench_runner.py:run_bench` 只算**截面 IC**: `ic / rank_ic / ir / rank_ir / hit_rate / state(健康度)`. 形状是"扫一堆因子→宽表".
- **组合回测 (净值/Sharpe/回撤/十分位) 全包不存在** — 无 `backtest`/`portfolio` 模块. SP-A 是净新增, 不是重构.
- 因子表达式 DSL 在 `buddy/tools.py:_factor_compute` + `_FACTOR_VOCAB` (字段 close/open/high/low/volume/vwap/amount/returns/industry; 算子 rank/ts_rank/delta/delay/ts_mean/ts_sum/ts_max/ts_min/ts_argmax/ts_argmin/stddev/correlation/covariance/decay_linear/sma/wma/signedpower/log/sign/abs/power/scale/indneutralize/max_pair/min_pair/filter_where; `eval` 受限命名空间; 拒 `__`/`import`/`lambda`).
- 数据面板 `factors/zoo/panel.py:PanelData.from_loader(loader, codes, start, end)` 经 `data.loader_factory.get_default_loader()` (QlibBinaryLoader 读共享 `stock_data/`), 可选 industry_loader/benchmark_loader.
- 股票池解析 `cli.py:_resolve_universe` / `buddy/tools.py:_resolve_universe_codes`: 只 bundled `csi300_active.txt`/`csi300_2024h2.txt`/`sample30.txt`. **`csi500`/`csi1000`/`all` 当前解析为空**. 但 `data/updaters/f10.py:resolve_universe` 已能从指数成分 parquet 解析 `csi300/csi500/csi800/all` — SP-A 复用它解锁股票池.
- 包内已有自带轻量模型 `models/lgb_momentum.py` — "自包含 ML" 已是既有范式.

## 范围

### 做 (in-scope)

1. 新建 `factors/eval/` 引擎包 (preprocess / ic / quantile / portfolio / report / config).
2. 小重构: 抽 `_factor_compute` + `_FACTOR_VOCAB` 到 `factors/zoo/expr.py`, 引擎与 buddy 工具共用.
3. 复用 `f10.resolve_universe` 解锁 `csi300/csi500/csi800/all` 给因子评测.
4. 新增 agent 工具 `factor_report`, 让评测**现在就能在觀瀾对话里跑**.
5. 确定性单元测试.

### 不做 (out-of-scope, 明确推后)

- **中性化** (行业 + 市值): A.2 (`preprocess.neutralize()` 本期只留接口签名占位, 不实现).
- **直连 REST 端点 + 量化 UI 页面**: SP-C.
- **炼因子 (NL→表达式 forge)**: SP-B.
- **多因子合成 / 合成模型 / 策略组合回测**: SP-D.
- **研究档案 / 版本 / 对比**: SP-E.
- **信号回放 (事件型单股买卖点)**: 与截面因子是两种范式, 后续单议.
- **风格暴露 / 拥挤度**: 风格暴露依赖中性化基础 (A.2 后); 拥挤度无数据源, 暂不做.

## 架构

**原生 `factors/eval/` 模块** (自写标准公式, 复用 `PanelData`/`operators`). 约 300~400 行, 零新重依赖, 边界干净, 完全可单测.

被否决的备选:
- **套 Alphalens**: 半弃维 (pandas 新版兼容差)、依赖重、迁就其数据形状 — 对精简 PyPI 包是负担.
- **塞进 `bench_runner`**: bench 是"扫多因子→宽表", 单因子深报告是"一因子→嵌套结果", 混用会膨胀模块.

## 模块布局

```
src/financial_analyst/factors/eval/
  __init__.py     导出 factor_report, EvalConfig, FactorReport
  config.py       EvalConfig 数据类
  preprocess.py   winsorize() / zscore() / neutralize()(A.2 占位, 抛 NotImplementedError)
  ic.py           ic_analysis() + ic_decay()
  quantile.py     quantile_backtest()
  portfolio.py    long_short_portfolio()
  report.py       factor_report() ← 总编排
```

新建 `factors/zoo/expr.py`: 从 `buddy/tools.py` 迁来 `FACTOR_VOCAB` (常量) + `compile_factor(expr) -> AlphaFn` + `validate_expr(expr)` (拒 `__`/`import`/`lambda`). `buddy/tools.py` 改为从此处 import (保持其对外行为不变).

## 数据模型 (FactorReport)

全部字段 JSON-safe (纯 float / str / list), 方便 SP-C 直接序列化给前端.

```python
@dataclass
class ReportMeta:
    factor: str            # 名或表达式
    family: str            # alpha101/gtja191/qlib158/custom
    universe: str
    freq: str              # day/week/month
    start: str; end: str
    n_dates: int; n_codes: int
    fwd_days: int
    preprocess: dict       # {"winsorize_q":0.01,"standardize":True,"neutralize":False}

@dataclass
class IcResult:
    ic_mean: float; ic_std: float; icir: float
    ic_tstat: float; ic_win_rate: float
    rank_ic_mean: float; rank_icir: float
    ic_series: list[tuple[str, float]]        # [(date, ic)] → mock「月度IC序列」
    ic_decay: list[tuple[int, float, float]]  # [(horizon, ic, rank_ic)] → 新增「IC衰减」

@dataclass
class QuantileResult:
    n_groups: int
    group_ann_return: list[float]   # 长度 n_groups, 年化 → mock「十分位」
    group_nav: list[list[float]]    # 每组累计净值序列 (始终计算, 长度 n_groups × n_dates)
    monotonicity: float             # Spearman(组序号, 组年化)
    long_short_spread: float        # top 年化 - bottom 年化

@dataclass
class PortfolioResult:
    nav_series: list[tuple[str, float]]            # [(date, nav)] → mock「长短组合净值」
    benchmark_nav: list[tuple[str, float]] | None  # 池等权基准 (参考线)
    ann_return: float; sharpe: float; max_drawdown: float
    volatility: float; turnover: float; win_rate: float; calmar: float

@dataclass
class FactorChar:
    coverage: float          # 平均每期非 NaN 占比
    autocorr_1: float        # 滞后 1 期截面 rank 自相关
    half_life: float         # 自相关半衰期 (期); 超出扫描范围记为 -1
    top_group_turnover: float

@dataclass
class FactorReport:
    meta: ReportMeta
    ic: IcResult
    quantile: QuantileResult
    portfolio: PortfolioResult
    characteristics: FactorChar
    warnings: list[str]
    status: str              # ok / compute_error / bad_output / empty_universe
    error: str
```

KPI 条 = `ic` + `portfolio` 字段直接取.

## 回测口径与默认 (精确)

- **面板粒度 vs 调仓频率 (关键)**: 面板**始终按日频加载** (`PanelData.from_loader(..., freq='day')`). `freq` 参数**只**决定: ① 调仓日程 (day=每个交易日 / week=每周最后交易日 / month=每月最后交易日, 由日历 resample 得到) ② 年化系数 ppy ③ `fwd_days` 默认值. 因子在调仓日用截至当日的日频面板算; IC / 分位 / 多空 / `ic_series` 都**只在调仓日上计算**, 因此 `n_dates` = 调仓次数. (这样 IC 衰减横轴 {1,3,...,42} 才有日级意义.)
- **前瞻收益**: 简单收益 (pct), `fwd_ret(t) = close(t+fwd)/close(t) - 1` (t 为调仓日, t+fwd 为其后 fwd 个交易日), 按 code 分组 shift. (现有 bench 用 log; 引擎统一改简单收益, IC 符号不受影响.)
- **fwd_days 默认**: 跟调仓周期: day→1, week→5, month→21 (交易日). 可显式覆盖.
- **IC 衰减横轴**: `decay_horizons = (1,3,5,10,21,42)`.
- **预处理顺序** (每个截面日, 仅对当期横截面): winsorize → (neutralize: A.2 跳过) → zscore. winsorize 默认按分位 `[q, 1-q]`, `q=0.01`; zscore `(x-mean)/std`; `standardize=True` 默认.
- **分位**: `n_groups=10` (十分位), 按因子值升序分组, 丢 NaN, 组内**等权**. 组序号 1=最低分位, n=最高分位.
- **多空组合**: 多头 = top 组等权 (+1/N_top), 空头 = bottom 组等权 (-1/N_bottom), 市值中性 (多 100% / 空 100%), 按 freq 调仓.
  - 单期多空毛收益 `ls(t) = mean(fwd_ret | top) - mean(fwd_ret | bottom)`.
  - 净值 `nav = cumprod(1 + ls_net)`.
- **成本**: 默认 `cost_bps=0` (毛收益, 业内分析工具默认口径). 传入则 `ls_net(t) = ls(t) - turnover_oneway(t) * cost_bps/1e4 * 2` (多空两边). 换手率始终照算并展示.
- **换手率**: 每次调仓 `turnover_oneway = |持仓权重变化|之和 / 2`; 报告取均值. 多空两边分别算后相加再平均.
- **年化**: `ppy = {day:252, week:52, month:12}[freq]`.
  - `ann_return = nav[-1] ** (ppy / n_periods) - 1` (几何).
  - `volatility = std(ls_net) * sqrt(ppy)`.
  - `sharpe = mean(ls_net) * ppy / volatility` (rf=0); volatility=0 → NaN.
  - `max_drawdown = min(nav/cummax(nav) - 1)`.
  - `calmar = ann_return / abs(max_drawdown)`; mdd=0 → NaN.
  - `win_rate = fraction(ls_net > 0)`.
- **IC 统计**: 每期 `ic(t)=Pearson(factor(t), fwd_ret(t))`, rank 用 Spearman.
  - `ic_mean=mean; ic_std=std; icir=ic_mean/ic_std`.
  - `ic_tstat = ic_mean / ic_std * sqrt(n_dates)`.
  - `ic_win_rate = fraction(sign(ic(t)) == sign(ic_mean))`.
- **因子特征**:
  - `coverage = mean_t( 非NaN因子数 / 池内股票数 )`.
  - `autocorr_1 = mean_t Spearman(factor(t), factor(t-1))` (截面对齐同一批 code).
  - `half_life`: 扫 lag∈{1,2,3,5,8,13,21}, 取首个 `mean rank-autocorr < 0.5` 的 lag; 都 ≥0.5 记 -1.
  - `top_group_turnover`: top 组成员换手 (离开数/N).
- **默认窗口**: `universe='csi500'`, `freq='month'`, 近 2 年 (对齐 mock); start/end 缺省由"今天往前推 2 年".
- **基准**: `benchmark_nav` = 池内全体等权净值 (参考线); 多空本身市值中性, 参考线主要给前端对照.

## EvalConfig

```python
@dataclass
class EvalConfig:
    universe: str = "csi500"
    freq: str = "month"               # day/week/month
    start: str | None = None          # None → 今天 - 2y
    end: str | None = None            # None → 今天
    fwd_days: int | None = None       # None → 按 freq (1/5/21)
    n_groups: int = 10
    cost_bps: float = 0.0
    winsorize_q: float = 0.01
    standardize: bool = True
    neutralize: bool = False          # A.2; True 暂抛 NotImplementedError
    decay_horizons: tuple = (1, 3, 5, 10, 21, 42)
```

## 暴露方式

- 引擎库 `factors/eval/factor_report(spec_or_expr: str, config: EvalConfig) -> FactorReport`.
- 新增 agent 工具 `factor_report` (`buddy/tools.py` 注册到 TOOLS):
  - 参数: `expr_or_name: str`, `universe='csi500'`, `freq='month'`, `start=None`, `end=None`.
  - 内部: 解析 universe → 建 PanelData → 编译/取因子 → `factor_report()` → 渲染中文摘要 (KPI + 十分位单调性 + 多空 Sharpe/回撤/换手 + warnings).
  - `confirm_required=True` (minutes 档, 与 alpha_bench 一致).
- **不做** REST 端点 (SP-C).

## 数据 / 性能 / 错误处理

- **universe**: 复用 `f10.resolve_universe` 拿 `csi300/csi500/csi800/all` 成分; 仍支持 bundled `.txt` 与显式路径 (沿用现有解析链, 末位回退到新解析器).
- **性能**: 单因子 csi500×~500 日 ≈ 25 万格, 向量化 pandas 秒级; 全市场×多年几十秒, 默认走指数池, 文档注明.
- **错误** (结构化, 不抛异常, 仿 `bench_one`):
  - 编译错 / 因子非 Series / 全 NaN → `status='compute_error'/'bad_output'`, `error` 填类型+消息, 其余字段 NaN/空.
  - 池为空 → `status='empty_universe'`, 明确 error.
  - 样本太短 (n_dates < 12) / 覆盖太低 (coverage < 0.5) / 符号反向 → 进 `warnings`, 仍返回部分结果.
  - 零方差窗口 RuntimeWarning → 沿用 bench 的 `warnings.catch_warnings()` + `np.errstate` 抑制.

## 测试策略 (确定性单测, `tests/factors/eval/`)

合成 `PanelData` (内存构造, 不读真实 `stock_data/`):

1. **完美因子** (因子 = 未来收益): IC≈+1, rank_icir 大, 十分位单调 (monotonicity≈1), 多空 sharpe>0, ann_return>0.
2. **随机因子** (与未来收益独立): |IC|<0.05, monotonicity≈0, 多空 sharpe≈0.
3. **反号因子** (因子 = -未来收益): IC≈-1, monotonicity≈-1, long_short_spread<0.
4. **预处理数值**: winsorize 把极值压到分位边界; zscore 后每期 mean≈0/std≈1.
5. **年化/回撤数学**: 喂已知 ls 序列, 断言 ann_return/sharpe/max_drawdown/calmar 与手算一致.
6. **换手率**: 已知成员轮动, 断言 turnover 数值.
7. **错误路径**: 坏表达式 → status='compute_error' 不抛; 空池 → 'empty_universe'.
8. **expr 重构回归**: `factors/zoo/expr.compile_factor` 与原 `buddy/tools._factor_compute` 对同一表达式产出一致; buddy 的 `factor_test`/`alpha_compare` 仍通过.

## 验收标准 (Definition of Done)

- `factor_report()` 对一个已注册因子 (如 alpha101 某支) 在 csi500 月频近 2 年返回填满的 `FactorReport`, 耗时 < ~10s.
- 上述 8 组单测全绿.
- `factor_report` agent 工具能在觀瀾对话里跑通并渲染中文报告.
- 不引入新的重依赖 (沿用 numpy/pandas).
- `buddy/tools.py` 的 `factor_test`/`alpha_compare` 行为不回归 (expr 抽取后).
