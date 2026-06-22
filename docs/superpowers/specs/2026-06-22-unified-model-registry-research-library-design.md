# 统一模型注册表 + 研究库双向通道 设计(②)

- **日期**:2026-06-22
- **状态**:设计已与用户逐节确认 + 边界澄清,待用户复审 spec → 转 writing-plans
- **关联**:`[[v4-model-workshop]]`、`[[rl-for-stock-selection-research]]`;**同一拆分的三块之②**:
  - ① CPCV/DSR 验证引擎 → `2026-06-22-cpcv-deflated-sharpe-validation-design.md`(已写,顺序后移:待 ② 之后)
  - **② 本文档**:统一注册表 + 研究库双向通道(**先做**)
  - ③ CPCV 做成工作流验证节点(待 ①② 之后)
- **架构总决策**:**统一到「排名产物」契约**——模型在哪训、什么算法都不重要,只要能出每日横截面排名就进同一 registry。

---

## 1. 背景与动机

现状:系统里有**两套互不相通的模型框架**:

| | 工作流 ML 节点 | 模型工坊(选股页) |
|---|---|---|
| 入口 | DAG `xgb/lgbm/svm/rf/nn/lstm` 节点 → `POST /model/<kind>` | `POST /screen/model/train` → `train_variant` |
| 产物 | **临时小规模 OOS 报告**(csi_fast 100 码 + 1y 窗 + 只测试行有预测),不落库 | **持久 v4 变体** → `models/<id>/{v4_ranking.parquet, meta.json}` |
| 可见性 | 只在工作流画布节点载荷 | 工坊列表 + TopBar picker + `/screen` 选股 |
| 互通 | ❌ 工作流训的模型工坊看不见 | ❌ 工坊变体工作流看不见 |

用户诉求:模型工坊 ⇄ 工作流 打通成**双向通道**——工作流训好的模型出现在工作流"研究库"且工坊能看见能用;工坊变体也能进工作流。核心顾虑:"工作流不只是工坊的后台,还有别的东西,怎么解决?"

**解法**:`/screen` 选股真正消费的是一个**排名产物** `(code, score, date)`(就是 `v4_ranking.parquet` 形状)。把它定为模型的通用契约 → **任何模型只要产出排名就进同一 `model_registry`(带 provenance)** → 单一真相源,工作流与工坊都只是它的客户端。工作流是通用 DAG(超集),工坊是精装模型视图,二者共享 registry 但各保留额外能力。

---

## 2. 目标与非目标

**目标**
- 泛化 `model_registry` 到"排名产物"契约:收任意来源/算法的模型 + provenance(`source/kind/recipe/retrainable`)。
- 工作流模型「存入模型库」→ **按生产规模异步重训**(全市场+全窗口)→ 生成 registry 排名 → 工坊/TopBar/`/screen` 可见可选股。
- 工坊变体(及任何 registry 模型)→ 出现在工作流"研究库",可拖成 `model` 节点进 DAG。
- "因子库" 扩成"研究库"(因子 tab + 模型 tab,带 provenance 徽章)。

**非目标(YAGNI / 分期)**
- 首期只覆盖**树模型**:v4 + lightgbm + xgboost + rf。**lstm/mlp/svm 暂不纳入**(svm 全市场 O(n²) 不可行、lstm/mlp 需 GPU 且慢——待 MVP-B 深度模型线)。
- CPCV 严格档的 `retrain_core(kind, recipe, mask)` 分派器**在 ① 建**(② 只保证 recipe 存好、`retrainable` 标对)。
- 把验证当硬闸门卡上线(③/以后)。
- GL 总线同步模型(模型是重产物,走后端列表为真相源,不进 localStorage)。

---

## 3. 已锁定的设计决策

| # | 决策 | 选定 |
|---|---|---|
| 1 | 统一方式 | **排名产物契约**(任何能出横截面排名的模型都进同一 registry) |
| 2 | 研究库形态 | **扩展现有「因子库」**→ 研究库(因子 tab + 模型 tab) |
| 3 | 存入规模 | 「存入模型库」= **按生产规模异步重训**(全市场 `all` + 全窗口)生成 registry 排名 |
| 4 | 类型范围 | **树模型先行**:v4 + lightgbm + xgboost + rf;其余分期 |
| 5 | 同步机制 | **后端列表为真相源**(`GET /models`,像 factorlib;保存后 refresh),不走 GL 总线 |
| 6 | 两通道两契约 | **因子库不要求排名**(因子=配料,当训练特征);**模型库要求横截面排名**(模型=可选股资产) |

---

## 4. 两通道两契约(关键概念边界)

A 股选股本质 = **横截面排名**(每日全市场排序挑前 N)。据此分两条通道,**不可混**:

| | 因子(配料) | 模型(可选股资产) |
|---|---|---|
| 通道 | **因子库 factorlib** | **模型库 model_registry** |
| 要排名吗 | **不需要** | **必须**(横截面 `(code,score,date)`) |
| 工坊怎么用 | 当**训练特征**勾入 | 当**可选股模型**直接选股 |
| 时序因子? | ✅ 只要能按 (code,date) 全市场求值即合格特征 | 无排名 → 进不了选股通道 |

**诚实边界**:
- "多因子 → 模型学排名"(旗舰场景)→ 有排名 → 走模型库 → 工坊可选股。
- "纯时序因子、无排名"→ 走因子库 → 工坊当特征用(看得见,身份是配料)。
- 纯个股/大盘级、无法跨市场截面化的时序信号 → 只能当因子/信号(进公式、进落子逐股研判),既非选股模型、严格说也非横截面特征;研究库可见但不冒充选股模型。
- "声称是模型却给不出横截面排名"的产物 → 不挂"可选股模型"牌子,只作研究产物被引用。

---

## 5. 架构总览

```
   工作流 (DAG·超集)                         模型工坊 (选股页·精装模型视图)
   ┌────────────────────┐                   ┌──────────────────┐
   │ 多因子→特征→树模型   │                   │ 训 v4 变体        │
   │ 训练节点(学排名)     │                   │ /screen/model/train│
   │  ↓「存入模型库」      │                   │  ↓ save_variant    │
   │ POST /model/promote  │                   └────────┬─────────┘
   │ (配方→生产规模重训)   │                            │
   └────────┬────────────┘                             │
            │ save_variant(source=workflow)            │ save_variant(source=workshop)
            ▼                                          ▼
   ┌──────────────────────────────────────────────────────────┐
   │  model_registry  (单一真相源 · 排名产物契约)               │
   │  models/<id>/{ranking.parquet, meta:{source,kind,recipe}} │
   └───────────────┬───────────────────────────┬──────────────┘
        GET /models │ (列出全部·带 provenance)    │
            ▼       ▼                           ▼
   ┌──────────────────┐       ┌──────────────────┐   ┌─────────────┐
   │ 工作流「研究库」    │       │ 工坊变体列表/TopBar│   │ /screen 选股 │
   │ 因子 tab + 模型 tab│       │ picker(source徽章)│   │ 读 ranking   │
   │ 模型可拖成 model 节点│      └──────────────────┘   └─────────────┘
   └──────────────────┘
```

---

## 6. 组件与接口

| 组件 | 文件 | 职责 | 改动 |
|---|---|---|---|
| 注册表泛化 | `guanlan_v2/screen/model_registry.py` | meta 加 `source/kind/recipe/retrainable`;ranking(沿用 `v4_ranking.parquet` 文件名保后兼容)为通用契约;`list_variants` 返回富 meta + prod 缺字段默认值兜底 | 加字段,prod 零改 |
| 工作流生产训练器 | 新 `guanlan_v2/strategy/compute/model_workflow.py` | 把 `workflow.api._materialize_xy`+`_build_model` 升到生产规模(全市场 `all` + 全窗口)→ 训树模型(lgbm/xgb/rf)→ 出**全截面每日排名** | **新建(② 主要工作量)** |
| 存入模型库端点 | `guanlan_v2/workflow/api.py` | `POST /model/promote`(收节点配方→异步子进程生产重训→`save_variant(source=workflow)`)+ `GET /model/promote/status` | 新端点(镜像 `/screen/model/train` 异步:线程+子进程+状态机) |
| 模型列表(共享) | `guanlan_v2/screen/api.py` `GET /models` | 已存在;扩为研究库共享源(工作流也调),回 provenance 字段 | 复用/微扩 |
| 工作流研究库 | `ui/factor/workflow.jsx` | 「因子库」模态扩成「研究库」(因子 tab + 模型 tab,provenance 徽章);新 `model` 节点(引用 registry 模型→输出其排名/分 series,可接回测/③验证节点) | 填充现有 UI + 1 新节点 |
| 工坊侧 | `ui/screen/screen-app.jsx` | 变体列表显示 `source` 徽章(本工坊/来自工作流) | 微调 |

**核心接口:**
```
POST /model/promote {name, kind, recipe:{features,label,universe,params,...}}
  → 异步:全市场 all + 全窗口重训 → ranking.parquet → save_variant(source=workflow,kind,recipe,retrainable=True)
GET  /model/promote/status → {phase,label,done,variant_id,reason?}
GET  /models → {ok, variants:[{id,name,source,kind,oos_ic,retrainable,asof,...}]}  # 工作流+工坊共读
```

---

## 7. 数据流(两个方向)

**工作流 → 工坊(promote):**
1. 工作流搭 DAG:多因子节点 → 特征工程 → lgbm/xgb/rf 训练节点学排名(秒级小规模验想法)。
2. 满意 → 模型节点点「存入模型库」→ `POST /model/promote`(带配方:特征 fe-spec + kind + 超参 + 名字)。
3. 后台异步:全市场 `all` + 全窗口重训 → 出全截面每日排名 → `save_variant(source=workflow,kind,recipe)`。
4. 完成 → 出现在工坊变体列表 + TopBar picker + 研究库模型 tab + 可在 `/screen` 选股(带"来自工作流"徽章)。

**工坊 → 工作流(model 节点):**
1. 工坊 v4 变体(及任何 registry 模型)出现在工作流研究库"模型 tab"。
2. 拖成 `model` 节点进 DAG → 输出其排名/分 series → 接「向量化回测」「③ 验证节点」等下游。

---

## 8. 注册表 schema(排名产物契约 + provenance)

```
models/<id>/
  v4_ranking.parquet   # 排名契约(文件名沿用 v4_ranking,所有模型都写它,保 loader/prod 后兼容;
                       #   下文「ranking」即指此文件):
                       #   必含 code, date, lgb_pct(标准化分位分,/screen 选股读它、跨模型可比);
                       #   可选 lgb_score / v4_total 等附加列
  meta.json:
    id, name, created, asof
    source: "workshop" | "workflow"
    kind:   "v4-lgb" | "lightgbm" | "xgboost" | "rf"     # 首期树模型
    recipe: {...}        # 重训配方:v4=base_features/factor_ids/universe/holdout;
                         #          workflow=features(fe-spec)/label/params/universe/window/combine
    retrainable: true    # 有配方+对应训练器 → CPCV 严格档可用(① 用)
    oos_ic, oos_icir, n_features, unsupported_factors
```

- **契约 = 排名文件 `v4_ranking.parquet`**:必含 `code/date/lgb_pct`(标准化分位)。任何 source/kind 只要产出它即可被选股、被验证;workflow 树模型重训后须把预测分归一成 `lgb_pct` 分位,与 v4 同口径可比。
- **recipe 是关键**:① 的 CPCV 严格档据此"按 kind 调对应训练器重训"。

---

## 9. 错误处理与红线

- **prod 不可动**:`prod`(生产 v4)只读老路径、不可删(现有保护保留);泛化 meta 对 prod 用默认值兜底(`source=workshop`,`kind=v4-lgb`)。
- **promote 异步失败显形**:生产重训失败 → status 带 reason,不留半成品 registry 条目(原子写 + 失败清理)。
- **入库契约校验**:`save_variant` 前校验 ranking 形状(`code/score/date` 齐、截面够厚 ≥ 阈值),不合格拒绝入库(诚实失败,不冒充可选股模型)。
- **诚实 provenance**:研究库/工坊明确标 source(本工坊/来自工作流)、kind;不混淆。
- **不碰交易信号算法**:② 只搬运/统一模型产物,不改 `/screen` 选股逻辑本身。
- **看门狗**:promote 生产重训是分钟级子进程,沿用 9999 看门狗。

---

## 10. 测试计划(TDD)

单测(`tests/`,引擎 fork 路径):
1. **registry 泛化**:存/读带 `source/kind/recipe/retrainable` 的 meta;旧 v4 prod meta 缺字段 → 默认值兜底不崩。
2. **ranking 契约校验**:形状不合格(缺列/截面太薄)→ 拒绝入库。
3. **promote 端到端**:合成小配方 → 生产训练器出全截面 ranking → `save_variant(source=workflow)` → `/models` 列出。
4. **工作流生产训练器**:树模型(lgbm/xgb/rf)全窗口训 → 出每日排名形状正确(code/score/date)。
5. **research 库列表**:`/models` 工作流+工坊读到同一份;provenance 字段齐。
6. **prod 保护**:prod 不可删、泛化后仍可被 `/screen` 读。
7. **两通道边界**:无排名产物 → 不进 model_registry(走 factorlib 当因子);有排名 → 进 registry。

---

## 11. 验收标准

1. 工作流"多因子→树模型学排名"的 DAG → 点「存入模型库」→ 异步生产重训 → 出现在工坊 + TopBar + `/screen` 可选股(带来源徽章)。
2. 工坊 v4 变体出现在工作流研究库模型 tab,可拖成 model 节点。
3. `model_registry` meta 带 `source/kind/recipe/retrainable`;prod 兜底不崩、仍可选股。
4. 全部单测(第 10 节)绿。
5. 红线核验:prod 只读不可删、入库契约校验、诚实 provenance、不碰 `/screen` 选股算法、失败显形。

---

## 12. 与 ①③ 的衔接

- **② → ①**:② 把 recipe 存进 meta、标 `retrainable` → ① 的 CPCV 严格档据此"按 kind 重训"验证**任意 registry 模型**(不止 v4)。① spec 需据此小改"验证对象 = 任意 registry 模型,两档边界 = `retrainable`"。
- **② → ③**:② 让模型成为 registry 一等公民 + 研究库可拖节点 → ③ 的"验证节点"可指向任意 registry 模型,接「运行测试」跑严格验证。
