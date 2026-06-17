# 五层选股体系 vendor 进仓 · 设计 spec

- 日期:2026-06-05
- 缘起:用户的「五层漏斗选股体系」(L1 量化粗筛 → L2 主线雷达 → L3 情绪/涨停 → L4 九视角 → L5 五维评级,5500→池→≤5)目前活在仓外 `G:/fa-watch-wt/research/strategy/`,选股模块若依赖它会陈旧/漂移。
- 已对齐决策(用户):**把 canonical strategy 复制进 guanlan-v2 自己的文件夹,在仓内迭代** + **未暴露层 vendor 进仓 + 版本戳 + 契约测试**。
- 状态:**待用户过目 spec → 再进实现**(代码一行未动)

---

## 0. 背景与现状

- 「选股」模块(`ui/screen` + `guanlan_v2/screen`)当前 L1 是玩具因子(`-delta(close,5)`),离用户真 L1(v4_ranking 23 因子 + 市值分层 + LGB + FinCast 自适应)差很远。
- 五层 canonical 完整源 = **`G:/fa-watch-wt/research/strategy/`**(已核实);`G:/stocks/strategy/` 是另一份(内容未逐一核);`G:/financial-analyst` 只有 .md 记忆,无脚本。
- 我们 `engine/` fork **只 vendored 了 L2/L3**(`agent/mainline/mainline_classifier.py`、`factors/sentiment/{board_scorer,volume_regime}.py`),并暴露 **`mainline_radar` 工具**;L1/L4/L5 不在 fork。
- 红线:仓内薄壳、UI 只填充不重建、不改 engine/、数据经引擎 `get_data_paths`、不改 G:/stocks、不 push/合 main。

## 1. 决策记录(用户已确认)

1. **来源策略**:canonical 收进 guanlan-v2,**仓内即事实源**,之后在仓内迭代(根治"用 strategy 旧版"陈旧)。
2. **未暴露层接法**:vendor 进仓 + **版本戳**(记录复制自哪、何时、源 hash)+ **契约测试**(依赖的函数签名/输出形,变了就红)。
3. 沿用 cards 先例(当初 fork wisdom 自己改)的「fork-并自有」路线;漂移成本是已知代价。

## 2. 已核实的源-漂移地图

| 层 | canonical 源 | 在 `engine/` fork? | 已暴露引擎工具? | 本 spec 处置 |
|---|---|---|---|---|
| **L1** v4_ranking(市值分层排名)| fa-watch/research/strategy/v4_ranking.py | ❌ | ❌ CLI only | **vendor 进仓**(核心目标)|
| L1 report_v2(单股研报)| 同上 report_v2.py(93.8KB)| ❌ | ✅ ≈ `run_report` 工具 | **不 vendor**,选股不需要;单股研报复用 run_report |
| **L2** 主线雷达 | 同上 mainline/ | ✅ vendored | ✅ `mainline_radar` | **用引擎工具**,不再搬 |
| **L3** board/vol | 同上 sentiment/ | ✅ vendored | ⚠️ 内嵌 stock_brief | 一期用引擎(stock_brief),需独立查询再加薄端点 |
| **L4** 九视角 | analyst_playbook.md | ❌(只 memory 种子)| ❌ | **vendor .md**(知识,供 agent/rating 引用)|
| **L5** rating/pitfalls | rating_system.md / pitfalls.md | ⚠️ 只 memory 种子 | ⚠️ run_report tier3 散落 | **vendor .md** + 评级逻辑后续相位量化 |

## 3. 硬现实(诚实,决定工作量,**不糊**)

`v4_ranking.py`(442 行)实测:
- **全部逻辑在 `def main()`**——只 `print` + 写 parquet,**无可调函数返回排名**。→ 必须重构 `main()` → `rank_universe(date, universe) -> DataFrame`。
- **每跑现训 34 因子 LightGBM**(秒~分钟级),**不能每请求训**。→ 必须缓存:跑成「今日排名产物」,`/screen/run` 读缓存。
- **绑死 G:/stocks 工作区**:`sys.path.insert(0,'G:/stocks')` + `from config import PROVIDER_URI_MAP,PARQUET_DIR,_HOME`,并读多个 parquet:
  - `tushare_stock_basic.parquet`(股本/名称/行业)
  - `strategy/research/market_breadth_resid.parquet`(R27 残差因子)
  - **FinCast 预测 parquet**(= Flow Matching W11 产物;**源码缺**,只有预测文件 → 黑盒输入,在外部工作流再生 = 真陈旧源头)
  - `spot_2026-04-03.parquet`(**日期硬编码**)
- `mainline_radar_v1.py` 同样硬编码 `PANEL_PATH='G:/stocks/strategy/mainline/monthly_mainlines_panel.parquet'`(预计算月度面板)——但 L2 走引擎工具,本 spec 不直接 vendor 此脚本。

> 含义:vendor v4_ranking 不是 copy,是**重构 main()→可调 + 解开 ~5 个产物耦合 + 缓存训练结果 + FinCast 黑盒输入怎么办**。这是真实代价,§10 待拍板。

## 4. Vendor 范围(搬什么 / 不搬什么)

**搬(进 `guanlan_v2/strategy/`)**:
- `v4_ranking.py`(L1,重构成可调)+ 它内联的 23/34 因子计算(自包含,无本地 `from factors import`,只依赖 qlib/lightgbm/产物)
- `analyst_playbook.md`、`rating_system.md`、`pitfalls.md`(L4/L5 知识)
- 版本戳 `_PROVENANCE.md`(源路径 + 复制日期 + 各文件 size/hash + 上游 git 状态如可得)

**不搬**:
- `report_v2.py`(单股研报 ≈ 引擎 `run_report`)
- `reports/`(493)、`chain_kb/`(88)、`stocks/`(189)、`research/`(85)、`log.md`(283KB)等产物/历史
- L2/L3 脚本(用引擎已 vendored 版 + `mainline_radar`/`stock_brief`)
- G:/stocks 的数据 parquet(数据留外部,经解耦后的数据访问层读)

## 5. 落点结构 + 版本戳 + 契约测试

```
guanlan_v2/strategy/                  # 新增包(vendored 五层 · 仓内自有事实源)
  __init__.py
  _PROVENANCE.md                      # 版本戳:复制自 fa-watch/research/strategy@<date>,文件 hash 表
  ranking.py                          # = v4_ranking.py 重构;rank_universe(...) -> DataFrame(可调,无 print/CLI)
  paths.py                            # 数据访问解耦层:G:/stocks 硬编码 → 引擎 get_data_paths / 配置
  knowledge/
    analyst_playbook.md  rating_system.md  pitfalls.md
tests/
  test_strategy_ranking.py            # 契约测试:rank_universe 返回形(列 code/name/mv/score/...)、市值分层上限不被破坏
  test_strategy_provenance.py         # 版本戳存在 + 列出的文件 hash 与盘上一致(漂移哨兵)
guanlan_v2/screen/api.py              # 改:/screen/run 的 L1 从玩具因子 → 调 strategy.ranking(缓存)
```
- **版本戳**:`_PROVENANCE.md` 记录每个 vendored 文件的源路径 + 复制时 size/sha256;`test_strategy_provenance` 校验(本仓副本未被意外篡改 + 可对比上游)。
- **契约测试**:固定 `rank_universe` 的**输入签名 + 输出列契约**;将来在仓内迭代或对比上游时,形变即红。

## 6. 路径/产物解耦策略(Phase 0 核心活)

把 `v4_ranking` 的 G:/stocks 硬编码集中到 `strategy/paths.py`:
- qlib provider / PARQUET_DIR / _HOME → 经引擎 `get_data_paths` 或 guanlan `config/` 解析(对齐全仓数据契约,零散硬编码)。
- `spot_<date>.parquet` 硬编码日期 → 取最新可用。
- **FinCast 预测 parquet**:一期**可选降级**——`v4_ranking` 本就有「FinCast 不存在 → 退化纯 LGB」分支;先走纯 LGB(50% LGB + 50% 自适应,不含 FC),把 FM 作为 §10 待定项。
- `market_breadth_resid` / `mainline panel` 等预计算产物:一期决定**复制快照进仓(带版本戳)** 还是**只读引用 stocks**(§10)。

## 7. /screen/run 怎么消费 L1(相位后)

- 新增 `strategy.ranking.rank_universe(date, universe)` 产出「今日排名」(缓存 parquet,带日期),`/screen/run` 读缓存:候选行的 `score`/`pct` 来自真 v4 评级(市值分层),替换玩具因子。
- L2/L3 叠加:`/screen/run` 或前端调引擎 `mainline_radar` + `stock_brief`(board/vol),把状态/情绪作只读徽章挂到候选行(漂移走引擎稳定接口)。
- UI 组件树/布局不动(只填充)。

## 8. 相位

- **Phase 0 — vendor + 可跑 + 哨兵**:建 `guanlan_v2/strategy/`,搬 v4_ranking + 3 个 .md + 版本戳;重构 `main()→rank_universe()`;解开 G:/stocks 硬编码到 `paths.py`(纯 LGB 降级,先不接 FinCast);契约测试 + provenance 测试。**不动 /screen/run**,先证「仓内能独立跑出排名」。
- **Phase A — L1 进选股**:`/screen/run` 的 L1 改读 strategy 排名缓存;候选叠 L2 `mainline_radar` + L3 board/vol 只读徽章。出可信「池子」。
- **Phase B — L5 评级 + 护盾**:rating_system 五维 + 市值分层量化成 `/rating`;pitfalls 排除 + 3 护盾(v4.1/4.2/4.3)量化成后端 gate;选股页加「收敛 ≤5 + 5 档」。
- **Phase C — L4 + 仲裁 + FM**:九视角接 agent prompt(显式挂 V#)+ V# 优先级仲裁;FinCast/FM 视源码补/代理。

## 9. 漂移治理(让陈旧可发现,不静默)

1. **单一事实源**:vendored 后 guanlan 即源;**绝不**再在别处复制第 N 份。
2. **版本戳 + provenance 测试**:记录复制自哪/何时/hash;可一键 diff 上游看是否落后。
3. **契约测试**:`rank_universe` 输入签名 + 输出列契约固定。
4. **引擎接口走稳定面**:L2/L3 调 `mainline_radar`/`stock_brief`(chat 也用,变了会一起炸 → 立刻发现),不裸 import 引擎内部、不解析 CLI stdout。
5. **`/screen/health`**:核对依赖的引擎工具 + 排名缓存新鲜度;不兼容/过期**响亮失败**。
6. **同步流程**:上游 strategy 升级时,人工 diff → 更新 vendored 副本 + bump 版本戳 → 契约测试守关。

## 10. ⓥ 待用户拍板

1. **落点文件夹名**:`guanlan_v2/strategy/`(top-level,跨模块复用)vs `guanlan_v2/screen/strategy/`(挂选股下)?(建议 top-level)
2. **预计算产物处置**:`market_breadth_resid` / FinCast 预测 / mainline panel —— 复制快照进仓(自包含但要同步)vs 只读引用 G:/stocks(最新但是外部依赖)?
3. **FinCast / Flow Matching**:源码缺。一期**纯 LGB 降级**(可接受?)还是要先去 fa-watch/financial-analyst 找 FM export 脚本 / 用现成预测 parquet 作黑盒输入?
4. **计算模型**:v4_ranking 训 LGB 慢 → 做成**定时/手动跑一次出缓存**(选股读缓存)是否可接受(非盘中实时)?
5. **依赖**:`lightgbm` 是否已在引擎 venv?(qlib 已有)需要则装。

## 11. 数据契约合规 + 验证

- strategy 是 guanlan 自有应用层;数据仍只经解耦后的数据访问层(引擎 get_data_paths)碰真 qlib/parquet;不改 G:/stocks、不改 engine/、不 push、不合 main。
- 控制端验证(Phase 0):`pytest tests/test_strategy_*` 绿;命令行在仓内跑出 Top20 排名(证明脱离 G:/stocks CLI 仍可跑)。
- Phase A 验证:控制端开选股页 → `/screen/run` 候选 `score` 来自 v4 排名缓存(对比:玩具因子排序应不同),L2/L3 徽章显示。

---

> 备注:本仓非 git 仓库,spec 仅落盘,未 commit。实现前需用户在 §10 五个 ⓥ 项给方向(尤其 ②③④ 决定工作量与可行性)。
