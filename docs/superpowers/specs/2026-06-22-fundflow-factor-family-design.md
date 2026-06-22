# 资金面订单流因子族 · 设计文档

**日期**:2026-06-22
**状态**:设计已确认,待写实施计划
**作者**:对话确认(brainstorming 流程产出)
**前置**:[[quant-wiki-gap-audit]] 立项第一档 ①;数据盘点见同记忆 2026-06-21/22 条目

---

## 1. 背景与目标

对照 LLMQuant/quant-wiki 复审,平台真缺口收敛为「数据接线类」——大量另类数据已采到却没接进分析主链路(`panel.py` 只合 `daily_basic / tech / financials`)。本设计把**东财稠密五档资金流**接进主链路,新增一个**订单流因子族**,让「资金面」从空白变为可用的因子工作流内容。

**目标**:新增 ~6 个订单流因子,走完整因子工作流 —— 数据进 panel → DSL 可寻址 → `_FACTOR_CATALOG` 浮现 → 选股因子库同源浮现 → regen 算真 IC。诚实、PIT 无前视。

## 2. 范围

**做(本增量)**
- `_merge_fund_flow`:东财五档资金流接进 `panel.py`(PIT as-of)
- `expr.py`:注册 10 个新字段(白名单 + VOCAB 文档)
- `_FACTOR_CATALOG`:新增「资金面」族(~6 因子)
- `screen/catalog.py`:同源浮现到选股因子库(改 `FAMILY_ORDER`)
- regen / `factor_ic.py`:自动算真 IC(csi300)

**不做(留后续独立增量)**
- 选股九视角 V8「资金面」接线
- 落子 `decide` 注入资金面证据
- 龙虎榜稀疏资金 `stock_fund_flow_daily`(3 档·2017–2026)的事件/稀疏因子
- xdxr 除权/分红/送转事件接入
- 融资融券 `margin_trading` 因子(数据仅 2 个日期,太薄)

## 3. 数据源

**文件**:`<parquet_root>/eastmoney_stock_fund_flow_daily.parquet`(`parquet_root` 经 `get_data_paths()`,实测 `G:\stocks\stock_data\parquet`)

**字段**(本设计使用的 10 个 + 关键元数据):

| 字段 | 含义 |
|---|---|
| `main_net_amount` / `main_net_pct` | 主力净流入额(元)/ 净占比(%) |
| `super_large_net_amount` / `super_large_net_pct` | 超大单净流入额 / 净占比 |
| `large_net_amount` / `large_net_pct` | 大单净流入额 / 净占比 |
| `medium_net_amount` / `medium_net_pct` | 中单净流入额 / 净占比 |
| `small_net_amount` / `small_net_pct` | 小单净流入额 / 净占比 |
| `code` / `trade_date` | 6 位码 / 交易日 |
| `visible_ts` / `visible_ts_reason` | **PIT 可见时刻**(= trade_date 23:59:59,EOD 可见)/ 来源说明 |

**覆盖(实测 2026-06-22)**:5290 码,trade_date 2025-12-16 ~ 2026-06-19;非空(有 main_net)行 137835 / 4654 码;每股有效天数中位 31 天、4635 票 ≥20 天、仅 8 票 ≥60 天。
**横截面薄**:仅 2026-06-17(3584 票)/06-18(2790 票)两天宽覆盖,其余日期单日票数稀;`visible_ts_reason` 全为 `eastmoney_history_eod`。
**含义**:个股时序信号可算(多数票有 ~30 天);但**截面 IC 样本薄**,会诚实显形,随每日采集累积补强。

## 4. 架构

一个共享地基喂多个出口,沿用 W1b 财务字段的扩展范式(`_merge_financials` 先例)。

```
panel.py: _merge_fund_flow(eastmoney 五档, 按 trade_date 合并, NaN-when-absent)
  → expr.py 注册 10 字段(_FIELD_NAMES + FACTOR_VOCAB;compile 侧靠动态注入自动可见)
     ├─ _FACTOR_CATALOG 新增「资金面」族(~6 截面 rank 因子)
     ├─ screen/catalog.py 自动复用 _FACTOR_CATALOG(改 FAMILY_ORDER 加「资金面」)
     └─ factor_ic.py / regen step3.5 自动算真 IC(csi300)
```

**关键实现事实(已核代码)**
- `expr.py:115` `compile_factor` 内 `for _c in p.df.columns: ns.setdefault(str(_c), p.df[_c])` —— **panel 里任何列自动成为 DSL 可寻址字段**。故 compile 侧零改;只需把字段名加进 `_FIELD_NAMES`(否则 `validate_expr:67` 把合法名误判为「未知字段」而拒绝)+ `FACTOR_VOCAB` 文档(给 LLM/公式面板)。
- `sign` 算子已存在(`expr.py:36,105`),因子 #4 可用。
- `screen/catalog.py:50-53` 直接 `from guanlan_v2.workflow.api import _FACTOR_CATALOG` 并迭代 `(name,expr,fam,_dir,desc)` —— 加进 `_FACTOR_CATALOG` 即自动浮现选股页;另需在 `screen/catalog.py:107 FAMILY_ORDER` 加「资金面」控制显示顺序。

## 5. 组件详述

### 5.1 `_merge_fund_flow(panel, loader, codes, start, end)` — `engine/financial_analyst/factors/zoo/panel.py`

- **位置**:仿 `_merge_financials`(`panel.py:117`),其后定义;在 `panel.py:481`(`_merge_financials` 调用)之后追加调用 `_merge_fund_flow(...)`。
- **读取**:直接 `pd.read_parquet(get_data_paths().parquet_root / "eastmoney_stock_fund_flow_daily.parquet")`(单文件,一次读+过滤,比 `_merge_financials` 的逐码线程更简);文件缺失 → 直接 return(全列 NaN,诚实)。
- **合并口径**:按 `(datetime=trade_date, code)` **精确日合并**(资金流是当日流量值,**不 ffill**——缺失日 = 无数据 = NaN,不沿用陈旧流量)。`panel[col] = ff[col].reindex(panel.index)`,逐列(`_FUND_FLOW_FIELDS` 常量列表,10 字段)。
- **窗口/票过滤**:只取 `code ∈ codes` 且 `start ≤ trade_date ≤ end`,守性能。
- **PIT**:数据自带 `visible_ts = trade_date 23:59:59`(D 日 EOD 可见)。因子用 D 日值预测 D+1 起前向收益 = 标准 EOD 惯例,**不看未来**(与现有 volume/amount/turnover 因子同口径),故按 trade_date 当日放置、不 shift。
- **诚实合约**:文件缺 / 票不在覆盖 / 日无数据 → NaN(镜像 `_merge_financials` 的「缺即 NaN」)。

### 5.2 `expr.py` 字段注册 — `engine/financial_analyst/factors/zoo/expr.py`

- `_FIELD_NAMES`(`expr.py:39`)追加 10 个字段名。
- `FACTOR_VOCAB`(`expr.py:11`)追加一段:`字段(资金面,day频·EOD PIT·缺则NaN): main_net_amount/pct super_large_net_amount/pct large_net_amount/pct medium_net_amount/pct small_net_amount/pct(主力/超大/大/中/小单净流入额与净占比)`。
- **compile 侧不改**(动态注入已覆盖,见架构注)。

### 5.3 `_FACTOR_CATALOG` 资金面族 — `guanlan_v2/workflow/api.py`

- 在 `_FACTOR_CATALOG`(`api.py:285`)追加 6 条 `(name, expr, "资金面", direction, desc)`(见 §6)。
- `_FACTOR_CATS`(`api.py:370`)追加 `"资金面"`。

### 5.4 `screen/catalog.py`

- `FAMILY_ORDER`(`catalog.py:107`)追加 `"资金面"`(放在「流动性」之后较自然)。
- 其余零改(自动复用 `_FACTOR_CATALOG`)。

### 5.5 IC 计算 — `factor_ic.py` / regen step3.5

- 因子进 `_FACTOR_CATALOG` 后,regen 的因子 IC 步骤(csi300·60日·5日 horizon)自动覆盖新因子,产出实测 rank-IC / ICIR。
- **薄样本诚实**:横截面薄 → 部分因子 IC 算不出(NaN)或样本少,前端显「—」/标注,**绝不装饰**。随每日采集累积补强。

## 6. 因子清单(6 个)

全部为**截面 rank 因子**(同动量/估值族口径,配选股/截面 IC)。方向为**假设**,以实测 IC 验真(同 catalog 既有「实测IC验真」惯例)。

| # | 名称 | 表达式 | 方向 | 说明 |
|---|---|---|---|---|
| 1 | 主力净流入强度 | `rank(ts_mean(main_net_pct,5))` | 正向(假设) | 近5日主力净占比均值,主力持续流入 |
| 2 | 超大单倾向 | `rank(ts_mean(super_large_net_pct,5))` | 正向(假设) | 机构/大资金方向 |
| 3 | 主力净流入动量 | `rank(ts_sum(main_net_pct,10))` | 正向(假设) | 10日累计主力净占比 |
| 4 | 连续净流入 | `rank(ts_sum(sign(main_net_amount),10))` | 正向(假设) | 近10日净流入天数(`sign` 已存在) |
| 5 | 资金集中度(大vs散) | `rank((super_large_net_pct+large_net_pct)-(medium_net_pct+small_net_pct))` | 正向(假设) | 大资金净流入 减 中小单净流入 |
| 6 | 散户出逃 | `rank(-ts_mean(small_net_pct,5))` | 反向(假设) | 小单净流出常伴主力吸筹 |

> 表达式在实施计划里逐条对 `validate_expr` + 小池 `compile_factor` 真求值通过后才入库;方向若 IC 验真为负则在 desc 标注或取反。

## 7. PIT / 诚实合约(红线)

1. **PIT 无前视**:按 trade_date 当日放置,数据自带 `visible_ts=EOD`;因子 D 日值预测 D+1 起前向收益,与现有量价因子同口径。
2. **诚实 NaN**:数据缺 / 票不在覆盖 / 日无数据 → NaN,因子诚实退空(镜像 `_merge_financials`)。
3. **薄 IC 显形**:横截面薄 → IC 样本少时诚实标注 / 显「—」,绝不伪造 IC。
4. **方向是假设**:6 因子方向以实测 IC 验真,不预设结论。

## 8. 测试(TDD,先红后绿)

- `_merge_fund_flow`:① 字段正确合并到 (datetime,code);② 文件缺失 → 全 NaN 不崩;③ 票/日不在覆盖 → NaN;④ 当日精确合并不 ffill(D 缺则 D 为 NaN,即便 D-1 有值);⑤ panel 索引保持。
- DSL:6 因子表达式各自 `validate_expr` 通过 + `compile_factor` 在含资金面列的小面板上求出有限值。
- 字段可寻址:含 fund_flow 列的 panel,`main_net_pct` 等经动态注入可在 eval 命名空间求值。
- 诚实:无资金面列的旧面板上,引用资金面字段的因子 → NaN / 友好失败,既有因子**字节不变**(回归守护)。

## 9. 验证(真数据)

- 跑 regen(或局部)在 csi300 上算 6 因子 IC;**至少跑通**、产出真值;薄样本据实显形(不强求显著)。
- 浏览器:选股因子库出现「资金面」族 6 因子,带真 IC 或诚实「—」。
- 旧路径回归:不引用资金面字段的既有因子 IC / 选股结果**不变**(merge 默认对旧因子是 no-op 加列)。

## 10. 后续增量(范围外,单独立项)

- 龙虎榜 `stock_fund_flow_daily` → 上榜事件研究(复用 `/workflow/event`)+ 游资稀疏因子
- xdxr → 除权前后效应事件 + 高送转(送转股)因子
- 融资融券 `margin_trading` → 两融余额/净买因子(**需每日采集累积**,现仅 2 天)
- 资金面接选股九视角 V8 + 落子 decide 注入

## 11. 风险与坑

- **横截面薄**:现在截面 IC 样本少 → 因子能定义、个股值能算,但 IC 验证弱;需采集器每日跑积累几周。**不因薄而造数**。
- **GateGuard**:每个文件首改先报 facts。
- **engine 改动须重启 9999**:改 `panel.py`/`expr.py`(引擎)后,杀 9999 监听 PID 等看门狗拉新代码。
- **测试用引擎 fork 路径**:跑 pytest 须确保 `sys.path` 指仓内 `engine/`(`tests/conftest.py` 已 prepend),否则验真因子数对不上。
- **regen 性能**:IC 全量算 O(n);小范围 csi300 守时。
- **方向假设**:6 因子方向未经验证,IC 出来前不宣称有效。
