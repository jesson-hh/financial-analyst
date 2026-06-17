# 因子库总账 (factor_library) — P1

> 状态:**P1 已落地、控制端验真通过**(2026-06-04)。后端 `guanlan_v2/factorlib/` 已建:base 18 + mined 1 = **19 因子**(Qlib-DSL→zoo-DSL 译写)注册进引擎运行期 zoo,`/factor/list` registered 442→**461**(新增 `library`/`library_mined` 族),`/factorlib/list` 已暴露(count=19)。实际端点:`/factorlib/list`、`/factorlib/registered`、`/factorlib/validate`。
> 取向:因子库**落仓内** `guanlan_v2/factorlib/`(cards/seats 在仓后端先例),**借引擎 primitive 求值**、**注册进引擎运行期 zoo registry**,数据全经 `get_data_paths`。**不改 `engine/` 副本、不碰 fa-watch-wt、不改 G:/stocks**。

---

## 1. 位置与结构

```
guanlan_v2/factorlib/            ← 新建·因子库模块 (仓内, 挂薄壳; cards/seats 先例)
├── __init__.py                  # 包标记 + 导出 build_factorlib_router / register_library_factors
├── qlib_to_zoo.py               # Qlib-DSL → 引擎 zoo-DSL 确定性译写器 (迁移核心)
├── store.py                     # LibraryFactorStore: base/mined 持久化 + 运行期注册 (复用引擎 UserFactorStore 范式)
├── api.py                       # build_factorlib_router() → server.py include_router → /factorlib/*
├── base/                        # 基础因子: 从 G:/stocks 迁移 (Qlib 串 → 译 → zoo expr → 注册)
│   └── *.(py|json)
├── mined/                       # 自挖因子: 手写 / forge 入库, 持久化
│   └── *.(py|json)
└── README.md                    # 迁移台账 (来源文件 → 因子名 → 译写状态)
```

- **为何在仓内**:引擎已 fork 进仓内 `engine/`(默认加载、保持纯净不改),`fa-watch-wt` 运行时不加载(待删);凡新后端能力一律落 `guanlan_v2/<module>/`(cards/seats 先例)。因子**注册/服务**在 `factorlib/`,**求值**借 import 引擎 `financial_analyst.factors`(expr / compile_factor / AlphaSpec)。
- **base vs mined**:`base/` = 从 stocks 迁来的既有因子;`mined/` = 我们自己挖/手写/forge 沉淀的因子。二者一起进引擎运行期 zoo registry → 一起出现在 `/factor/list`。

## 2. 数据口径(铁律)

- 数据根**全经引擎** `financial_analyst.data.paths.get_data_paths()`(本机 = `config/loaders.yaml` → G:/stocks qlib bin)。**零硬编码路径、零复制 stock_data**。
- panel 字段齐备(`engine/.../factors/zoo/expr.py` L36-41 映射,已核验):
  - 价量:`close open high low volume vwap amount returns`。
  - 行业:`industry`。
  - 基本面:`pe_ttm pb ps_ttm dv_ttm total_mv circ_mv turnover_rate`。
  - `vwap`/`amount` 缺失时 panel 自动合成(`panel.py` L89-92)。
- 频率:与引擎一致(日线 bin 为主)。universe 经 `config/universes/*.txt`(`csi300_active / csi_fast / csi300_2024h2 / etf / sample30`)。

## 3. 迁移来源台账(stocks → factorlib/base)

**来源**(只读参考,不拷文件、不改 stocks):`G:/stocks/results/factor_mining/*.txt`(挖掘产出,如 `rolling_top30_factors.txt`、`round_*_factors.txt`、`v2_round_*_factors.txt`)、`G:/stocks/configs/*.txt`。每行格式 `表达式|因子名`。

**关键事实(决定迁移方式,已核验):stocks 因子是 Qlib-DSL,与引擎 zoo-DSL 不兼容,迁移=译写,不是复制。**

| 维度 | Qlib-DSL(stocks 源) | 引擎 zoo-DSL(`expr.py` ns,权威白名单) |
|---|---|---|
| 字段 | `$close $open $high $low $volume`、`$turnover_rate $amount` | `close open high low volume`、`turnover_rate amount`(无 `$`) |
| 时序 | `Ref(x,n) Std(x,n) Mean(x,n) Sum(x,n) Corr(x,y,n)` | `delay(x,n) stddev(x,n) ts_mean(x,n) ts_sum(x,n) correlation(x,y,n)` |
| 标量 | `Abs Log Sign`、`If(c,a,b)`(三目) | `abs_ log sign`、`filter_where`(无三目) |

实证(`rolling_top30_factors.txt` 第 2 行):
```
Ref($close,20)/$close-1-Std($close/Ref($close,1)-1,20)*10-Std($turnover_rate,20)/(Mean($turnover_rate,20)+1e-8)|quality_v2
```
译为 zoo-DSL(示意):`delay(close,20)/close-1-stddev(close/delay(close,1)-1,20)*10-stddev(turnover_rate,20)/(ts_mean(turnover_rate,20)+1e-8)`。

**迁移流程**(`qlib_to_zoo.py` + `store.py`):
1. **读** stocks `*.txt`(仅参考)。
2. **译** Qlib 串 → zoo 串(确定性正则/AST,`qlib_to_zoo.py`)。
3. **编译** `compile_factor(zoo_expr)`,**注册** `register(AlphaSpec(...))` 进引擎运行期 registry。
4. **译不动的诚实失败、记台账**(如 `$pe_ttm` 之类 Qlib 专名、`If` 三目)——**跳过并在 `base/README.md` 登记原因**,不静默吞。

> 台账落 `guanlan_v2/factorlib/README.md`:`来源文件 · 原 Qlib 串 · 因子名 · 译写状态(ok / skipped+原因)`。

## 4. 注册语义(引擎 registry,已核验)

- `register(AlphaSpec)` 是**普通函数(非装饰器)**;**同名 + 不同 compute 对象 → `raise ValueError`**。故重注册前必须先 `unregister(name)`。
- 复用引擎 **`financial_analyst.factors.forge.UserFactorStore`** 范式(`engine/.../factors/forge/store.py`):`register_one()`(L75-83)= `unregister(name)` → `register(AlphaSpec(compute=compile_factor(expr)))`;`register_all()`(L85-93)批量重建。**`factorlib/store.py` 照此实现,勿重造。**
- import 顶包 `financial_analyst.factors.zoo` 触发内置三族(alpha101 / gtja191 / qlib158)自动注册(`FA_ZOO_LAZY=1` 可延迟)。
- **注册时机**:server 启动时(`create_app()` 内,cards/seats 之后)调 `register_library_factors()` 把 base/mined 注入运行期 registry。

## 5. 与 `/factor/list` 的关系

- `/factor/list`、`/factor/save` 由**引擎** buddy server 提供(`engine/financial_analyst/buddy/server.py`),**不是** guanlan `server.py`。guanlan `server.py` 是薄壳:`build_app()`(引擎全端点)+ `include_router(cards/seats[/factorlib])`。
- `/factor/list` 返回 `{registered, user}`;一旦 base/mined 注册进运行期 registry,**自动出现在 `registered`(或经 store 归 `user`)**,无需改引擎。
- `/factor/report`、`/factor/bench`、`/factor/compose` 同样**自动可对新注册因子求值**(它们读同一 registry)。
- `factorlib/api.py` 的 `/factorlib/*`(如 `/factorlib/migrate`、`/factorlib/catalog`)是**管理/迁移**接口(在仓内、不改引擎);因子的**浏览/评测**仍走既有 `/factor/*`,前端因子库节点查 `/factor/list`。

## 6. 配方:如何加一个因子

**A. 手写一个 mined 因子(zoo-DSL)**
1. 在 `guanlan_v2/factorlib/mined/` 新增定义(因子名 + zoo expr,如 `my_mom20 = delta(close,20)/delay(close,20)`)。
2. `store.py` 启动时 `compile_factor` + `register_one`(同名先 `unregister`)。
3. 重启 :9999 → `/factor/list` 含它 → 前端因子库节点可搜/拖入 → 公式输入或多因子节点引用。

**B. 从 stocks 迁一个 base 因子(Qlib-DSL)**
1. 在 stocks `*.txt` 找到 `Qlib串|名`(只读)。
2. `qlib_to_zoo.translate(qlib_str)` → zoo 串(译不动→记台账跳过)。
3. 同 A 的 2–3 步注册、上线。
4. 在 `factorlib/README.md` 台账登记来源与译写状态。

**C. forge 沉淀(NL→DSL)** :经既有 `/factor/forge` 出 expr → `/factor/save` 落库 → 归 `mined/`(复用引擎 UserFactorStore 持久化)。

> 改 `factorlib/*` 后端 → **必须重启 :9999**(薄壳约定)。改前端 `workflow.jsx` → bump HTML 的 `?v=`(当前 `?v=4`)。

## 7. 硬规则合规(本模块)

- ✅ 落 `guanlan_v2/factorlib/`;**不改 `engine/`、不碰 fa-watch-wt、不改 G:/stocks**(只读参考)。
- ✅ 数据只经 `get_data_paths`;零硬编码、零复制 stock_data。
- ✅ 注册走引擎**运行期** registry(import 注入),不改 engine 源码。
- ✅ 译不动的因子诚实失败 + 记台账,不静默吞。
- ✅ 前端既有组件零改:只扩 `workflow.jsx` 的 `SPECS`/`CATALOG`/`NODE_EXEC`;HTML 仅 bump `?v=`(非组件改动)。

## 8. 相关文档

- 总计划:[workflow_buildout_plan.md](workflow_buildout_plan.md)(§2 因子库、§5 分期 P1)。
- 数据脐带:[data_contract.md](data_contract.md)(因子库条目)。
- 节点 × 端点:[module_map.md](module_map.md)(因子库 / 数据源节点)。
- 加节点配方:[node_recipe.md](node_recipe.md)(SPECS/CATALOG/NODE_EXEC 三件套)。
- 前端状态:[../ui/factor/README.md](../ui/factor/README.md)。
