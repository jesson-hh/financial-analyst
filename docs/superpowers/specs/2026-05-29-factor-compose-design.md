# 多因子合成模型 (Factor Composite Model) 设计 · SP-D

> 状态: 已批准, 待落 plan
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-D ("机器模型 → 回测 → 结论" 骨干)

## 目标

把 N 个因子 (内置 alpha + forge 用户因子, 名或表达式) 合成一个**综合打分** (4 法: equal / ic_weighted / linear / lgbm), 在**样本外 (OOS)** 用 SP-A 的组合回测评测综合分, 并对比成员因子 → 回答"合成是否增益"。即流水线的"机器模型 → 回测 → 结论"骨干。自包含。

## 背景与定位

流水线: A 评测✅ / B 炼因子✅ / B.1b 基本面✅ / **D 多因子合成 (本文)** / E 研究档案 / C 工作台UI / B.2 事件。

### 现状基线 (已勘察)
- **复用路径成立**: `build_report(panel, compute, config, factor_label, family)` (`report.py:114`) 接受任意 `compute: PanelData->Series` → **综合分当作单因子评测**。成员因子: `registry.get(name).compute` (`registry.py:17`, `AlphaFn=Callable[[PanelData],Series]`) / `compile_factor(expr)` (`expr.py:28`)。
- **依赖**: `lightgbm>=4` 是**核心依赖** (`pyproject.toml:57`) → ML 合成无需新依赖/懒加载。`scikit-learn`/`scipy` **缺** → linear 用 numpy `lstsq` (免新依赖, 不引 sklearn)。
- **lgb_momentum 不可复用**: 它是 per-stock 时序模型 (在单只票自身 250 天历史上现训现测), **轴向错** (`models/lgb_momentum.py`)。合成需**截面**模型 (factor-matrix → fwd-return) → **新写小截面训练器**, 仅借用其 lgb 参数配方。
- **无现成多因子合成**: selector 只排名单因子名, snapshot 只出 long 形最新值 — 都不合成。因子矩阵需新建 (~5 行: 逐成员 compute → `concat(axis=1)`)。
- **前瞻收益**: `forward_simple_returns(panel, n)` (`report.py`) / `bench_runner._forward_returns` 给对齐标签。
- **预处理**: SP-A `preprocess.winsorize`/`zscore` (每日截面)。

## 范围

### 做 (in-scope)
1. 新建 `factors/compose/` 模块: `matrix.py` (因子矩阵) + `combine.py` (4 合成器) + `compose.py` (编排 + OOS 评测)。
2. 4 合成法: equal / ic_weighted / linear (numpy lstsq) / lgbm (截面 LightGBM)。
3. OOS 训/测纪律: 单次 split (前 train_frac 训练拟合, 后段测试评测)。
4. `factor_compose` 对话工具。
5. 确定性单测。

### 不做 (out-of-scope)
- 持久化/版本化综合模型 → SP-E 研究档案。
- walk-forward (多窗滚动) → D 后续增强 (v1 单次 split)。
- 正交化 (Gram-Schmidt / 对称正交) → 后续。
- sklearn 依赖 (linear 用 numpy lstsq)。
- UI (C) / 事件 (B.2)。

## 架构

综合产物 = 一个 `PanelData->Series` 的综合打分 → 喂 `build_report` (复用 SP-A 单因子引擎)。OOS 纪律靠"综合分只在测试段有值 (训练段 NaN)" → `build_report` 的 dropna 自然只评 OOS。

### 模块布局 `src/financial_analyst/factors/compose/`
```
__init__.py    导出 compose_factors, ComposeResult, build_factor_matrix
matrix.py      build_factor_matrix(panel, members) -> (DataFrame, names)
combine.py     4 合成器 + dispatch combine(...)
compose.py     ComposeResult + compose_factors() (I/O 编排)
```

## 数据模型

```python
@dataclass
class MemberOOS:
    name: str            # 成员因子名/表达式
    rank_ic: float       # OOS RankIC
    sharpe: float        # OOS 多空 Sharpe

@dataclass
class ComposeResult:
    method: str                 # equal / ic_weighted / linear / lgbm
    members: list[str]
    weights: dict               # 因子→权重 (equal/ic/linear) 或 因子→重要度 (lgbm)
    train_frac: float
    n_train_dates: int
    n_test_dates: int
    composite: FactorReport      # 综合分的 OOS 评测 (SP-A build_report)
    member_oos: list[MemberOOS]  # 各成员同窗 OOS 指标
    verdict: str                 # "综合分 OOS Sharpe X vs 最佳单成员 Y → 增益/无增益"
    warnings: list[str]
    status: str                  # ok / too_few_factors / empty_universe / load_error / fit_error
    error: str
```

## matrix.py
`build_factor_matrix(panel, members) -> (pd.DataFrame, list[str])`:
- 逐 member 解析: 先试 `registry.get(member).compute`; KeyError 则 `validate_expr` + `compile_factor(member)` (custom)。
- 各 compute(panel) → Series; 每日截面 `winsorize` (q=0.01) + `zscore` (复用 SP-A preprocess)。
- `pd.concat(series_list, axis=1, keys=names)` → `(datetime,code) × factor` 矩阵 (列名=member 名)。
- 跳过 compute 失败的成员 (记 warning), 返回成功的列 + 名单。

## combine.py — 4 合成器
统一: `combine(matrix, fwd, method, train_mask, test_mask, **kw) -> (pd.Series, weights_dict)`。综合分 Series **仅 test 段有值, train 段 NaN**。各法在 train 段拟合:
- **equal**: 各列等权和 (列已 zscore)。weights = 均匀。综合分 = test 段 行均值。
- **ic_weighted**: 训练段每列对 fwd 的 RankIC → 权重 = IC (或 |IC| 归一, 保号)。综合分 = test 段 Σ w_i·factor_i。
- **linear**: 训练段 pooled OLS `fwd ~ factors` via `numpy.linalg.lstsq` (加截距) → 系数。综合分 = test 段 X·β。(NaN 行先 drop 再拟合。)
- **lgbm**: 训练段 pooled `LightGBM` 回归 (factors→fwd, lgb 参数借 lgb_momentum 配方: num_leaves/lr/feature_fraction 等, num_boost_round~100) → predict test 段。weights = feature_importance 归一。
- 退化守卫: 训练样本太少 (<某阈值) / 单因子 → 友好降级或 warning。

## compose.py — 编排
`compose_factors(members, config: EvalConfig, method="lgbm", train_frac=0.6) -> ComposeResult`:
1. `len(members) < 2` → status="too_few_factors" (合成至少 2 个)。
2. 解析 universe (`resolve_universe_codes`) — 空 → empty_universe; 加载日频面板 (`PanelData.from_loader`, try/except → load_error)。
3. `build_factor_matrix(panel, members)` → 矩阵 + 名单 (有效成员 <2 → too_few_factors)。
4. `fwd = forward_simple_returns(panel, config.effective_fwd_days())`。
5. 调仓日 `rebalance_dates(panel.dates(), config.freq)`; 按 train_frac 切 → train_dates / test_dates (各取调仓日前/后段); 矩阵/fwd 限制到调仓日。
6. `combine(matrix_reb, fwd_reb, method, train_mask, test_mask)` → 综合分 (test 段非 NaN) + weights。try/except → fit_error。
7. 综合分评测: `build_report(panel, lambda p: composite_series, config, f"composite[{method}]", "composite")` (dropna → 仅 OOS test)。
8. 成员对比: 每个成员构造"仅 test 段"的单因子 Series → `build_report` → 取 OOS rank_ic + sharpe (MemberOOS)。(成员少, N 次 build_report 可接受, minutes 工具。)
9. verdict: 综合分 OOS sharpe vs `max(member sharpe)` → "增益 (+X)"/"无增益"。
10. 返回 ComposeResult。

**OOS 切分**: 调仓日按时间排序, 前 `floor(train_frac × n)` 为 train, 余为 test。综合分只在 test 调仓日有值。所有 4 法同一 test 窗 → 可比。

## 工具 `factor_compose` (buddy/tools.py)
- 参数: `members: list[str]` (必填, ≥2), `method="lgbm"` (enum equal/ic_weighted/linear/lgbm), `universe="csi300_active"`, `freq="month"`, `since/until` 或默认窗口, `train_frac=0.6`。
- 调 `compose_factors`; status≠ok → is_error 友好说明。
- 渲染中文: 方法 + 成员 + 权重/重要度 + 综合分 OOS (RankIC/ICIR/Sharpe/年化/回撤) + 成员对比表 + verdict + warnings。
- cost_hint="minutes", confirm_required=True。

## 错误处理
- 成员 <2 (输入或有效) → too_few_factors。
- universe 空 / 加载失败 → empty_universe / load_error (结构化, 仿 factor_report)。
- combine 拟合抛错 (lstsq 奇异 / lgbm 失败) → fit_error + error; 不崩。
- 训练/测试段任一为空 (窗口太短) → warning + 尽力 (可能综合分全 NaN → 报告 OOS 为空)。
- 成员 compute 失败 → matrix 跳过 + warning。

## 测试策略 (确定性单测, 合成面板, mock LLM 不涉及)
1. **build_factor_matrix**: N 成员 (注册名 + 表达式混合) → N 列, 每日 zscore (列均值≈0); compute 失败成员被跳过 + warning。
2. **4 合成器形状**: 各法 fit(train)+predict → 综合分 **仅 test 段非 NaN, train 段 NaN**; weights dict 键=成员。
3. **完美成员增益**: 成员含一个 = 未来收益的因子 → lgbm/linear/ic_weighted 综合分 OOS rank_ic 高 (>0.3), verdict=增益。
4. **OOS 不偷看**: 构造 train 段强、test 段反的因子 → ic_weighted 权重由 train 段定 (验证 split 正确, 权重不受 test 段影响)。
5. **linear lstsq**: 已知线性关系 (fwd = 2·f1 - f2) → 拟合系数接近 [2,-1] (训练段)。
6. **lgbm 跑通**: 小面板训练/预测不崩, 综合分 test 段有值。
7. **too_few_factors / empty_universe / fit_error**: 结构化, 不抛。
8. **compose_factors 端到端** (stub loader): 2 成员 + method=equal → ComposeResult status=ok, composite.status=ok, member_oos 有 2 项, verdict 字符串非空。
9. **工具**: mock loader + universe → factor_compose is_error False + 含综合分指标 + verdict; method=lgbm 跑通; 不污染全局注册表 (不用 _clear_registry_for_tests)。
10. **回归**: factor_eval / factor_zoo / factor_forge / buddy 不回归。

## 验收标准 (Definition of Done)
- `compose_factors(["alpha名A","表达式B"], cfg, method=...)` 返回 OOS ComposeResult (综合分 FactorReport + 成员对比 + verdict)。
- 4 法都跑通, 综合分**只在 OOS test 段评测** (训练段 NaN, 不偷看)。
- 完美成员 → 综合分 OOS 强 + verdict 增益。
- `factor_compose` 工具在觀瀾跑通。
- 自包含 (lightgbm 核心依赖, linear 用 numpy lstsq, 不引 sklearn); 上述 10 组单测全绿; 现有 eval/zoo/forge/buddy 不回归; 不污染注册表。
