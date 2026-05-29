# 基本面字段进 DSL (Fundamental Fields in Factor DSL) 设计 · SP-B.1b

> 状态: 已批准, 待落 plan
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-B.1b (扩 DSL 词表, B 炼因子的紧接跟进)

## 目标

给 `PanelData` + 因子表达式 DSL (`FACTOR_VOCAB` / `compile_factor`) 加 7 个 daily_basic 基本面字段 (`pe_ttm`/`pb`/`ps_ttm`/`dv_ttm`/`total_mv`/`circ_mv`/`turnover_rate`), 让"高股息 / 低估值 / 小盘"这类**估值/股息/规模因子**能被表达。所有共用 expr DSL + PanelData 的工具 (forge / factor_test / factor_report / alpha_compare) 自动受益。

## 背景与定位

量化流水线: A 评测引擎 ✅ / B 炼因子 ✅ / **B.1b 基本面字段 (本文)** / D 多因子合成模型 / E 研究档案 / C 工作台UI / B.2 事件信号 (依赖顺序)。

B (炼因子) v1 只支持价量 DSL, 用户的旗舰例 "高股息+低负债" 当场被 forge 判 out_of_vocab。B.1b 把 daily_basic 估值/股息/规模字段补进 DSL, 解锁这类 forge + 直接惠及所有因子工具。

### 现状基线 (已勘察)
- **数据 + 取数路径已就绪**: `QlibBinaryLoader` 已有 `DAILY_BASIC_FIELD_MAP` (`qlib_binary.py:53`) 和 `fetch_daily_basic(code, start, end)` (`:249`), 读 7 个 `.bin` 字段 (pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate, day 频)。`fetch_daily_basic` 是 `BaseLoader` 接口方法 (`base.py:24`), 所有 loader 都有。
- **差距**: `PanelData.from_loader` (`panel.py:133`) 只调 `loader.fetch_quote` (OHLCV, `QUOTE_FIELD_MAP`), 从不调 `fetch_daily_basic` → 面板/DSL 看不到基本面。
- **可选字段范式**: `PanelData.industry` (`panel.py:78`) / `.benchmark_close` (`:88`) 在列缺失时返回填充 series ("未知"/NaN)。新基本面字段照此范式。
- **DSL 单一源**: `factors/zoo/expr.py` 的 `FACTOR_VOCAB` (字段+算子白名单) + `compile_factor` 的 `ns` dict。forge 的 `_SYSTEM` prompt 内嵌 `FACTOR_VOCAB` → 改词表自动改 forge 认知。
- **forge out_of_vocab 措辞**: `forge.py:_SYSTEM` 现有句 "若想法需要表中没有的字段 (基本面 pe/pb/股息/ROE/市值, 或事件条件)" — pe/pb/股息/市值 即将进词表, 这句要改。
- **单位契约** (data_contract.md): `total_mv`/`circ_mv` = **万元**; `turnover_rate` = %; `dv_ttm` = 股息率 %; pe_ttm/pb/ps_ttm = 倍数。

## 范围

### 做 (in-scope)
1. `PanelData` 加 7 个基本面字段属性 (缺列→NaN series, 仿 `.industry`)。
2. `PanelData.from_loader` 在 **day 频** 合并 `fetch_daily_basic` 的 7 列 (guarded)。
3. `expr.py`: `FACTOR_VOCAB` + `compile_factor` ns 加这 7 个字段 (+ 单位注释)。
4. `forge._SYSTEM`: 把 pe/pb/股息/市值 从 out_of_vocab 例子移除 (改为"已支持估值/股息/规模"); ROE/负债 (需财报) + 事件型 仍 out_of_vocab。
5. 确定性单测。

### 不做 (out-of-scope)
- **财报字段** (ROE/净利润/负债率等, 需 `parquet/financials`, 非 daily_basic) → 以后按需 (SP-B.1c?)。
- **派生便利字段** (如市值转亿) → 不做; 入面板原值, 因子作者自己 rank/zscore 归一。
- **opt-in 取数开关** → 不做 (见决策: 总是取)。
- 事件信号 (B.2) / UI (C) / 合成模型 (D)。

## 关键决策: 总是取 daily_basic (day 频)

`from_loader` 在 day 频**总是**取 daily_basic 并合并, 不加 opt-in 开关。
- **理由**: QlibBinaryLoader 是微秒级 bin 读, 每 code 多 7 次读可忽略 (面板只建一次); 让基本面因子在所有工具开箱即用, 无需调用方记得开开关。
- **代价**: 现有 OHLCV-only 调用方 (run_bench / 旧 alpha 工具) 面板多 7 列, 不用即无害。
- **被否**: opt-in `with_daily_basic=True` — 省那点 I/O 不值得, 且要求每个基本面场景的调用方都记得开, forge/factor_test 必须默认开 (用户表达式可能含基本面), 等于总是开。
- **频率**: 仅 day 频合并 (daily_basic 只有 day 频)。5min/1min 面板跳过 (因子评测引擎面板恒为 day 频, 基本面在此可用)。

## 设计

### 1. PanelData 字段属性 (`panel.py`)
对 7 个字段各加一个 property, 仿 `industry`:
```python
@property
def pe_ttm(self) -> pd.Series:
    if "pe_ttm" in self.df.columns:
        return self.df["pe_ttm"]
    return pd.Series(float("nan"), index=self.df.index, dtype=float)
# pb / ps_ttm / dv_ttm / total_mv / circ_mv / turnover_rate 同理
```
(7 个重复 property; 可用一个 `_optional_field(name)` 私有助手减少重复, 但保持与现有 industry/benchmark 一致的显式 property 风格也可。实现时择一, 不强制。)

### 2. PanelData.from_loader 合并 (`panel.py:133`)
现有: 每 code `loader.fetch_quote(...)` → df (单层 datetime index) → 加 code → concat。
新增: `freq == "day"` 时, 每 code 在 fetch_quote 后:
```python
if freq == "day":
    try:
        db = loader.fetch_daily_basic(code, start, end)
        if db is not None and len(db):
            db = db.set_index("trade_date") if "trade_date" in db.columns else db
            # 对齐到该 code 的 datetime 索引, 合并 7 列
            for col in ("pe_ttm","pb","ps_ttm","dv_ttm","total_mv","circ_mv","turnover_rate"):
                if col in db.columns:
                    df[col] = db[col].reindex(df.index)
    except Exception:
        pass  # 缺基本面 → 该 code 就没这些列, 属性回退 NaN
```
(具体合并按 from_loader 现有 per-code df 构造方式对齐; 关键: 合并发生在该 code 的 df 上, 用 datetime 索引对齐, 缺失→NaN, 不抛。)

### 3. expr.py DSL 词表 + 命名空间
- `FACTOR_VOCAB` 字段段加: `pe_ttm pb ps_ttm dv_ttm total_mv circ_mv turnover_rate` (+ 注释: total_mv/circ_mv=万元, dv_ttm=股息率%, turnover_rate=换手%)。
- `compile_factor` 的 ns dict 加: `"pe_ttm": p.pe_ttm, "pb": p.pb, ...` 7 项。

### 4. forge._SYSTEM 措辞 (`forge.py`)
把 out_of_vocab 例子从 "基本面 pe/pb/股息/ROE/市值" 改为类似:
"估值(pe_ttm/pb/ps_ttm)、股息(dv_ttm)、规模(total_mv/circ_mv)、换手(turnover_rate) **已支持**; 仍不支持: 财报字段(ROE/净利润/负债率, 需财报数据) 和事件型条件(连续/金叉/突破)→ 这些设 out_of_vocab。"
(few-shot 可选加一条基本面例子, 如 "高股息"→`rank(dv_ttm)`。)

## 错误处理
- 某 code 无 daily_basic bins → `fetch_daily_basic` 返回空 df → 该 code 无基本面列 → 属性回退 NaN series (不抛)。
- loader 不支持/抛错 → from_loader 的 try/except 吞掉, 退化为无基本面列。
- 因子用基本面字段但面板全 NaN (数据缺) → IC/分位/多空 的现有 dropna 自然处理 (结果可能空/NaN, 不崩)。

## 测试策略 (确定性单测)
1. **PanelData 属性**: 构造带 daily_basic 列的合成 df → `panel.pe_ttm` 等返回该列; 不带时返回 NaN series。
2. **compile_factor 基本面表达式**: `rank(-pe_ttm)` (低估值) / `rank(dv_ttm)` (高股息) / `rank(-total_mv)` (小盘) 在带基本面的合成面板上编译产出 Series, 形状对。
3. **FACTOR_VOCAB**: 含 pe_ttm/dv_ttm/total_mv 等 7 字段。
4. **from_loader 合并**: stub loader 的 `fetch_quote` 给 OHLCV + `fetch_daily_basic` 给 daily_basic → 合并后的 PanelData 同时有 close 和 pe_ttm; 另一 stub 的 fetch_daily_basic 返回空 → 面板只有 OHLCV, `panel.pe_ttm` 全 NaN 不崩。
5. **forge 不再 out_of_vocab**: mock LLM 对 "高股息" 返回 `{expr:"rank(dv_ttm)", out_of_vocab:false}` → forge_factor compile_ok=True (验证 dv_ttm 在 ns 里能编译; 即基本面字段确实进了 DSL)。
6. **回归**: factor_test/alpha_compare/factor_report 用基本面表达式 (如 `rank(-pb)`) 不崩; 现有 test_factor_zoo / test_factor_eval / test_factor_forge 不回归 (尤其别污染全局注册表)。

## 验收标准 (Definition of Done)
- 带 daily_basic 的合成面板上 `rank(-pe_ttm)`/`rank(dv_ttm)`/`rank(-total_mv)` 编译产出 Series。
- `PanelData.from_loader`(day) 自动带上 7 个基本面列 (stub loader 测试); 缺数据时 NaN 不崩。
- forge 对 "高股息"/"低估值"/"小盘" 类想法能炼出基本面表达式 (不再一律 out_of_vocab)。
- FACTOR_VOCAB + compile_factor ns 含 7 字段; 单位有注释。
- 上述 6 组单测全绿; 不引入新依赖; 现有因子工具 + zoo + eval + forge 测试不回归。
