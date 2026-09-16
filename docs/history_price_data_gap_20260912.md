# 历史策略回放价源与复权缺口（只读核查，2026-09-12）

## 结论

当前历史规则回放的选股与现金账本已能执行，但原始行情并不满足“连续、同口径、完整公司行动处理”的比较前提。不能把已经跑出的收益差异当作六种策略的有效优劣排名。

本地确实存在累计复权字段 `factor.day.bin`，但本次 800 只股票中，799 只最后有效值只到 **2026-03-13**，另 1 只到 **2026-02-27**。现行日线增量通道不抓取 `adj_factor` 或 `pre_close`。周更 ZIP 中的 `pre_close` 样例可到 2026-05-29，不能解决 6 月至 9 月 11 日的完整覆盖。

因此，当前最小安全修复是：**在历史回放专用层加价源质量门，遇到未知复权/内部 OHLC 矛盾就明确阻断绩效比较与旧结果排名**。不能以末值前填、补 1、删除股票或改规则代替修复。补齐且核验价源以后，再在专用回放副本上复权并按原先冻结的规则重跑；不改全局 loader 或原始数据。

## 核查范围与方法

- 只读本地源代码、Qlib 二进制、现成 ZIP/Parquet；未联网拉取、未改任何原始数据、未修改全局 loader。
- 实际代码集合取自本次冻结面板，不使用可能包含历史大并集的 `instruments/csi800.txt`。
- 冻结面板：[panel.parquet](G:/guanlan-v2/.data/workflows/_history/rw_general/hs_e6157a0e7ff626c15af61efe3a48dd69cc5d1534026ce1775c7085176577fad0/panel.parquet)。800 只，710,785 个 `(datetime,instrument)` 行，2023-01-03 至 2026-09-11。
- 代码集合摘要：`55198d5ffcc7013f5f7f9d4655838650fa8d4a7cbb99966937ae1fbb4c3b49dc`。
- 二进制读法：首个 little-endian float32 为全局日历起始位置，余下 float32 为值；按 `cn_data/calendars/day.txt` 对齐，再严格重索引到冻结面板，不填缺值。
- 日期范围与覆盖数为实际读取结果；公司行动字段的完整业务语义若未证实，下面明确保留不确定性。

## 已确认的数据链

| 环节 | 源码与实际行为 | 对回放的影响 |
|---|---|---|
| 历史 ZIP 导入 | [import_daily_from_zip.py:40](G:/stocks/scripts/import_daily_from_zip.py:40) 将 `adj_factor` 原样映射为 `factor` | `factor` 是独立保存字段，不代表 OHLC 已复权 |
| 误导性开关 | [import_daily_from_zip.py:44](G:/stocks/scripts/import_daily_from_zip.py:44) 注释和 `USE_ADJ=True` 声称前复权；[第118行](G:/stocks/scripts/import_daily_from_zip.py:118) 只是额外读取 `adj_factor`；[第129行](G:/stocks/scripts/import_daily_from_zip.py:129) 仍直接取 `row[src_field]`，[第153行](G:/stocks/scripts/import_daily_from_zip.py:153) 直接写入，没有乘除复权因子 | 不能仅凭开关名称或注释认定历史 OHLC 已复权 |
| 当前增量入口 | [incremental_update_tushare.py:88](G:/stocks/scripts/incremental_update_tushare.py:88) 的字段只有 open/high/low/close/vol/amount；[第563行](G:/stocks/scripts/incremental_update_tushare.py:563) 仅按字段映射构造请求；[第581行](G:/stocks/scripts/incremental_update_tushare.py:581) 发出查询 | 这条日线更新路径没有获取/持续落盘 `adj_factor` 或 `pre_close` |
| 原始行情读取 | [qlib_binary.py:44](G:/financial-analyst/src/financial_analyst/data/loaders/qlib_binary.py:44) 的 quote 映射只含 OHLCV/amount；[第254行](G:/financial-analyst/src/financial_analyst/data/loaders/qlib_binary.py:254) 原样构造返回 | `fetch_quote` 不应用 `factor`，不会自行修复除权跳变 |
| 另一报价更新器 | [pytdx_kline.py:9](G:/financial-analyst/src/financial_analyst/data/updaters/pytdx_kline.py:9) 明示 pytdx bars 不复权，第一版不计算复权 | 不能用它补出统一复权口径 |

以上是现存代码行为与读到的字段一致性的证据，不等同于证明每一历史文件都只由这一个脚本生成；原始 ZIP 导入日志/供应商版本并未在本次全面核验。

## 本次 800 池的逐年覆盖

文件位置：`G:/stocks/stock_data/cn_data/features/<code_lower>/<field>.day.bin`。

| 字段 | 文件存在数 / 800 | 能否作为完整复权依据 |
|---|---:|---|
| `factor` | 800 | 否，有效日期覆盖中断 |
| `adj_factor` | 0 | 不存在这个二进制字段 |
| `pre_close` | 0 | 不存在这个二进制字段 |
| `adjclose` | 800 | 文件存在不能代表有有效值；另一路面板检查发现样例为空，本次未把它当成可用替代 |

有限且正值的 `factor` 与冻结面板逐行对齐：

| 年份 | 有效 factor 行 | 缺失/不可用行 | 面板行合计 |
|---|---:|---:|---:|
| 2023 | 189,442 | 85 | 189,527 |
| 2024 | 191,883 | 140 | 192,023 |
| 2025 | 193,805 | 230 | 194,035 |
| 2026（截至9月11日） | 35,126 | 100,074 | 135,200 |
| 合计 | 610,256 | 100,529 | 710,785 |

所有已读取的有限 factor 值中没有非正值。有效覆盖最晚日期分布为 `2026-03-13: 799`、`2026-02-27: 1`。这不是把不存在报价的日子当作缺因子，而是相对于已经进入本次冻结面板的行统计。

## factor 的含义与可验证的复权桥

`factor.day.bin` 来源映射为原始 `adj_factor`；宁德时代的已知除权附近，因子相邻比值与“前一实际收盘 / 当日 pre_close”一致，支持其作为累计价格调整倍率的解释：

| 日期 | 原始开盘 | 当日 pre_close | factor | factor 相邻比 | 前一原始收盘 / 当日 pre_close |
|---|---:|---:|---:|---:|---:|
| 2023-04-25 | 398.00 | 396.88 | 1.005499959 | 1.000000000 | 约1.000000012 |
| 2023-04-26 | 213.00 | 212.99 | 1.821799994 | 1.811834931 | 1.811822123 |
| 2023-04-27 | 224.00 | 224.50 | 1.821799994 | 1.000000000 | 1.000000000 |

差异符合 float32 和原始因子精度量级；这里只验证了这个事件样例，不能据此豁免全池逐日覆盖、来源一致性和其他事件核验。

如果取得同口径、逐日完整且已证实为除权参考价的 `pre_close_t`，可以验证递推桥：

`F_t / F_(t-1) = raw_close_(t-1) / pre_close_t`。

但不能把**普通前收盘字段**一概理解成除权参考价。必须用源字段定义、`change = close - pre_close`、`pct_chg` 一致性和真实除权样例交叉验证；有停牌、跨空档、重复日、换源时不能盲目 `shift`。本地现有日线增量文件不足以完成这条桥到 9 月 11 日。

## 其他本地候选来源为何不能直接拼接

### 周更 CSV ZIP

- [2026.zip](G:/stocks/股票历史数据（每周更新）/全A日K/2026.zip) 有按股票的 CSV，包含 `pre_close/change/pct_chg`，宁德时代样例有 95 行，2026-01-05 至 2026-05-29。该 ZIP 文件更新时间为 5 月 30 日；本次没有声称全 800 只均完整覆盖到该日期。
- [后复权因子 ZIP](G:/stocks/股票历史数据（每周更新）/复权因子/复权因子_后复权.zip) 有 5,849 个文件。宁德时代样例 1,932 行，2018-06-11 至 2026-05-29；5月28日值 `1.890059661277906`，5月29日值 `1.8882783018867924`。
- [前复权因子 ZIP](G:/stocks/股票历史数据（每周更新）/复权因子/复权因子_前复权.zip) 有 5,836 个文件。宁德时代样例首日值竟为 `-0.0187845303867403`，末日为 `1.0`。

这些“因子”不能仅凭名称认作与 Qlib 中正值累计 `adj_factor` 等价。非事件日变化、负值以及前后复权标定方式都需要先弄清；可能是调整价格与原价之比或其他算法产物。本次没有将它们用于任何修复或收益重算。

### 除权除息事件表

- [xdxr.parquet](G:/stocks/stock_data/parquet/xdxr.parquet)：192,348 行、6,262 个代码，event_date 从1990-03-01到2026-09-18，包含本次结束日之后的事件。
- [gbbq.parquet](G:/stocks/stock_data/parquet/xdxr/gbbq.parquet)：181,977 行、6,182 个代码，字段使用复用语义，如 `hongli_panqianliutong`、`songgu_qianzongguben`。
- [divid_factors_latest.parquet](G:/stocks/stock_data/parquet/tdx_tq/divid_factors_latest.parquet)：只有贵州茅台1只、2行，不能覆盖全池。
- [xdxr.py:6](G:/financial-analyst/src/financial_analyst/data/updaters/xdxr.py:6) 明示逐代码完整覆盖替换，不是历史供应商版本存档；[字段定义](G:/financial-analyst/src/financial_analyst/data/updaters/xdxr.py:46) 与[原样数值映射](G:/financial-analyst/src/financial_analyst/data/updaters/xdxr.py:113) 也没有形成经核验的逐日累计因子。

事件表可能是后续修复依据，但必须先验证类别、单位（例如每股/每十股）、生效日、发布时点和是否包含未来事件。现有表没有为每个历史时点保存已知版本，不能冒充严格 PIT。不能因表中存在事件就无条件前填一个旧 factor。

## 独立面板审计的相关阻断（非本次重复计算）

另一只读审计任务已向主任务报告：原始 OHLC 存在993行内部矛盾；136个大开盘断点中113个有可比较因子，111个经因子校正回到正常区间，23个因子缺失；原始基线有1,258个持仓日缺 factor。此处引用协调结果，不将它们冒充本次重新计算的统计。

重要区别：**统一复权乘数无法修复 `high < low` 或收盘不在高低范围中的单字段矛盾**。因此即便补齐 factor，也仍需通过基本 OHLC 质量门，不能立即恢复策略排名。

## 最小修复要求（待主任务实施）

1. 先增历史专用 `history_price_quality.py`，只在新历史回放路线调用。检查严格日历/索引、正有限 OHLC、`low <= open/close <= high`、复权覆盖与来源标识；附实际问题代码/日期/数量。问题不会通过删股票、删日期或改变候选规则被隐藏。
2. 质量门未通过时，API 明确产出 `data_quality_blocked` 或同等语义，不输出“已验证优胜策略”；旧历史报告也须展示阻断标识，避免遗留收益继续被视作可比较。
3. 补齐经核验的累计因子或逐日除权参考 `pre_close`，形成**回放专用、不可变、可哈希审计的副本**；不修改原 bin，不更改全局 loader。缺值不补1、不跨未知事件前填。
4. 使用固定的历史已知基准 `F_base` 归一化，例如 `adjusted_price_t = raw_price_t * F_t / F_base`，同一天 OHLC 使用同一倍率。不得用未来结束日才知道的标定变化改写既有前缀。量额保留明确真实单位，不把合成复权价格的研究份额冒充可成交原始股数。
5. 除权因子法对应合成连续价格/再投资近似，不是完整分红到账、红利税和配股缴款账本；仍需明确研究近似与非严格供应商版本 PIT 的限制。
6. 在相同股票池、日期、手续费和已冻结六套规则上重跑。原始版留档标记不可比较；修正版不通过事后调参来制造更好的收益。

## 必要测试

- 2拆1/送转事件：原始开盘机械折半不能使持仓净值折半；同日 OHLC 倍率一致。
- 已知现金分红事件：明确测试所采用的连续价格/再投资口径，不与实际现金到账混淆。
- 缺 factor、无来源、0/负/无穷 factor、事件日期晚于回放日：全部明确阻断，绝不自动补值。
- `pre_close` 桥在停牌空档、重复日期、跨年、上市首日和换源边界：缺证据即失败，不跳过数据行。
- 原始 high/low/open/close 矛盾：即使 factor 可用仍阻断。
- 前缀不变性：增加或修改未来行情/未来除权事件，不改变历史截止日前的选择、执行与净值。
- 覆盖检查的分母固定为冻结输入面板/代码集合，不能在发现缺值后缩小股票池。
- 质量门生效时，新接口与旧报告 UI 不展示有效排名；正常已有因子向量回测、诊断图和原始 loader 行为保持不变。

## 当前交付边界

本文件只记录只读发现和修复要求。原始数据、全局 loader、线上代码均未因本核查发生变化。第二次发布应另设严格文件清单，待主任务确定 `history_price_quality.py`、`strategy_replay.py` 与历史旧结果 UI 防误读补丁后再 staging；本次未自行开始第二次 staging 或部署。
