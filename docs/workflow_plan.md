# 因子研究节点工作流 · 实施计划

> 状态:**待审**(2026-06-04)。逐步实施、每步控制端验真。
> 取向:把「AI 工作流」装饰性节点图,重组成**可执行的因子研究 DAG**;基础块全变真代码;画布复用;增量 + mock 占位。
> 与上版关系:取代早期「直接接量化工作台面板」取向(其设计稿 `factor_wiring_design.md` 与量化工作台页均已于 2026-06-04 删除)。改走可执行节点 DAG + 自建结果抽屉,不复用旧整页。

---

## 1. 目标与范围

**目标**:`观澜 · AI 工作流.html`(`WorkflowApp` / [workflow.jsx](../ui/factor/workflow.jsx))的节点能**真的跑**——你把基础代码块拖上画布、连线、调参、点运行,沿边传数据、逐节点调真计算、出真结果。

**纳入**:数据 / 因子 / 模型 / 回测 / 特征 / 分析 这套**基础块(mock 里已有提示)**全部变真代码;**因子库**(从 stocks 合规迁移)。

**排除(你指明)**:炼因子(forge)、Agent 回测(`/backtest/run`)、实时盯盘。

**原则**:逐步改,未改的块用 mock 占位;单/多因子评测**靠连对工作流自然得出**,不做专门面板。

---

## 2. 现状(据实)

`run()`([workflow.jsx:300-308](../ui/factor/workflow.jsx))只是按 x 把节点逐个点亮(running→done),**零 fetch、零端点、数据不沿边传**。节点模型(`source/formula/python/feature/xgb/lgbm/svm/rf/pca/spearman/iccalc/mf/analysis/backtest`,[CATALOG:33-39](../ui/factor/workflow.jsx))是理想 ML 管线,**与引擎真端点不对齐**。HTML 无 backend 注入、workflow.jsx 零数据层。

→ 画布/拖拽/连线/调参/撤销/导入导出**都现成可用**;缺的是「让节点真的算」。

---

## 3. 节点清单 × 引擎现状(本计划的核心)

诚实区分:哪些块有现成引擎端点(接上即真),哪些块**引擎还没有、需新增引擎端点**(在 `fa-watch-wt` 加,合规;待你提供算法信息)。

| 组 | 代码块 | 引擎现状 | 实现路径 |
|---|---|---|---|
| 数据 | 数据源/池 | `resolve_universe_codes` 有 | 前端参数 → 喂下游 universe;无需端点 |
| 因子 | 因子代码(formula/python) | expr DSL + `/factor/report` 现成 | 前端 exec → `/factor/report` |
| 因子 | 因子分析(analysis) | `/factor/report` 现成 | 同上(评测节点) |
| 因子 | iccalc(IC 计算) | 含在 `/factor/report` 的 ic 块 | 并入评测,或拆细(待定) |
| 因子 | mf(多因子) | `/factor/compose` 现成 | 前端 exec → `/factor/compose` |
| 模型 | lgbm | `/factor/compose` method=`lgbm`/`linear` 有 | 并入「多因子组合」的 method 参数 |
| 模型 | xgb / svm / rf | ❌ 引擎无独立端点 | **加引擎端点**(待你信息) |
| 因子 | pca / spearman | ❌ 引擎无端点 | **加引擎端点**(待你信息) |
| 特征 | feature(特征工程) | ❌ 引擎无端点 | **加引擎端点**(待你信息) |
| 回测 | 向量化回测 | ⚠️ `/factor/report` 已含十分位多空回测;TopN 组合回测❌ | 先复用 report 的 portfolio,或**加引擎端点**(待你信息) |
| 库 | 因子库 | `/factor/list` 现成(alpha101/gtja191/qlib158 + user) | stocks 因子**合规迁移**进引擎 zoo → `/factor/list` 服务(见 §5) |

→ **「因子 + lgbm 合成 + 评测」这条链现在就能接真**;「特征/模型/pca/向量化回测」需引擎新端点,等你给算法信息逐个加。

---

## 4. 机制:节点三件套 + 真 DAG 执行器

每种基础块 = 我预写的三件套:

```
节点类型 = {
  spec   : 端口(输入/输出, 带类型 dt) + 参数        // 已有
  exec   : async (上游输入, 参数) → 输出             // ★调真端点/真计算 = "把代码块写好"
  render : 真结果面板(复用量化工作台的 FactorReportView 等, 有目的性复用)
}
```

- **连线 = 带类型的数据管道**:边连「输出端口→输入端口」(类型校验已有);运行时下游 `exec` 从上游 `outputs` 取数。
- **真 DAG 执行器**(替换点灯 `run()`):拓扑排序 → 逐节点 `await exec(上游输出, 参数)` → 存输出、节点态 running/done/error → 出真结果;错误逐节点诚实显示。
- **参数↔代码**:因子节点参数(如窗口 N)模板化进表达式 `delta(close, {N})`,调参实时更新文本,运行时把最终表达式发引擎。
- **数据层 + backend 注入**:workflow.jsx 加薄 `q/getJSON/postJSON`(复用量化工作台模式);HTML 加同源注入(照 chat 约定)。

---

## 5. 因子库:stocks → 引擎 合规迁移(硬规则相关)

你说「stock 里有很多因子,直接复制迁移过来」。但你自己的硬规则是「**禁止把 stocks 复制进 guanlan-v2**」。合规做法(读 stocks 仅作参考、写只写引擎):

1. **读** stocks 的因子定义(表达式/元数据)——仅参考,不拷文件进 guanlan-v2。
2. **译**成引擎 expr DSL / `AlphaSpec` 格式。
3. **注册**成引擎 zoo 新因子家族(`fa-watch-wt` 的 `factors/zoo/<新家族>/alphas.py`,仿 alpha101/gtja191/qlib158)——这是「要新后端能力→加引擎」,合规。
4. `/factor/list` 自动服务 → 「因子库」节点浏览+搜索 → 拖进工作流当输入。

→ **不拷进 guanlan-v2 薄壳、不改 stocks、因子能力落引擎**。需先定位 stocks 因子位置(待你指/我勘察)。**这条要你确认:是按合规迁移(注册进引擎),还是你另有意思?**

---

## 6. 里程碑(增量,未建块 mock 占位)

- **M0 前端基座**:workflow HTML 同源 backend 注入 + workflow.jsx 数据层 + `?v=` bump。
- **M1 执行框架**:三件套框架 + 真 DAG 执行器(替换点灯)。
- **M2 接现成端点块**:数据源、因子代码→`/factor/report`、多因子→`/factor/compose`、分析。**这批立即出真数据**(控制端 `preview_eval`/`preview_network` 验真)。
- **M3 因子库**:stocks 因子合规迁移进引擎 zoo + 因子库节点(搜索/拖入)。
- **M4+ 需加引擎端点块**:特征 / xgb·svm·rf / pca·spearman / 向量化回测——按你给的算法信息,逐个**加引擎端点 + 前端节点**;未建前 mock 占位。

每个 M 独立可交付 + 控制端验真;不用截图(动画 timeout)。

---

## 7. 硬规则合规

- ✅ 架构 import 引擎、数据只经 `get_data_paths`。
- ✅ 不复制 stocks/引擎到 guanlan-v2(因子库走引擎注册,§5)。
- ✅ 要新后端能力 → 加引擎 `fa-watch-wt`(特征/模型/pca/回测端点、stocks 因子家族),不加薄壳。
- ✅ 不改 stocks;不 push;不合 main;不并行写 bin/日历。
- ✅ 改 jsx bump `?v=`;控制端独立验真;完成更新 [ui/factor/README.md](../ui/factor/README.md)。

---

## 8. 需要你提供的信息(每块一份)

为把「特征/模型/pca/spearman/向量化回测」变真代码,每块请给:

| 字段 | 说明 |
|---|---|
| 输入 / 输出 | 吃什么数据类型 → 吐什么(对齐端口 dt) |
| 参数 | 可调项 + 默认 + 范围(对齐节点 params) |
| 计算逻辑 | 算法/公式(或指向 stocks/引擎里的现成实现) |
| 数据来源 | 经 `get_data_paths` 的哪份(日线/5min/财务/新闻…) |
| 是否已有实现 | stocks/引擎里有没有可参考的代码(有目的性复用) |

因子库同理:stocks 因子在哪、什么格式、要迁哪些。

---

## 9. 待你拍板

1. **因子库迁移路径**:合规(读 stocks → 注册进引擎 zoo → `/factor/list`)对吗?还是你另有所指?
2. **引擎端点**:特征/模型/pca/向量化回测要动引擎(`fa-watch-wt`,合规但是真后端开发),确认走这条?
3. **起步**:先搭 M0+M1+M2(现成端点那条链跑通)给你看,还是先等你给齐基础块信息再统一开工?
