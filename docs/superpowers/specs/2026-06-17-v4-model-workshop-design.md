# v4 模型工坊 — 设计 spec

**日期**:2026-06-17
**状态**:设计已与用户对齐,待写实现计划

## 1. 背景与目标

用户已造了很多因子并入库(`factorlib` → `FACTOR_DEFS` 目录,即 `/screen/factors` / `ww_screen_factors` 列出的那套)。需求:

> 自己筛选因子 → 训练 v4 模型 → 用训练好的模型选股 → 训练多组变体做对比测试。

现状阻碍:当前 v4 训练([`guanlan_v2/strategy/compute/v4.py:218 build_v4`](../../../guanlan_v2/strategy/compute/v4.py))用**固定的 ~38 个内部工程特征**(`build_feature_panel` 产出,`mf = 除 label/pe/pb/total_mv/ps 外全列`)训 LightGBM。用户造的库因子(zoo 表达式)**完全不参与 v4 训练**。

本功能让用户从「v4 基础特征 + 我的库因子」的统一列表里勾选任意子集,训练**命名变体**,在选股页用变体选股,按**留出 OOS IC** 对比多组变体。**生产 v4 全程不动**。

## 2. 架构与边界(三个独立单元)

### 2.1 训练器 `guanlan_v2/strategy/compute/model_train.py`(新)
- **职责**:输入 = `{name, factor_ids:[库因子id], base_features:[基础特征名](前端送勾选的显式列表), universe}`;输出 = 一份与生产 v4 **同 schema 的 7 列排名产物** + 一份 `meta.json`。
- **实现关系(关键)**:`model_train` = **参数化的 `build_v4`**。先把 `build_v4` 重构出两个参数:`feature_cols`(显式特征列表,缺省 = 现状的 `mf`)+ `extra_factor_panel`(可选,注入已求值的库因子列,缺省 None)。`build_v4` 默认调用**字节级零行为变化**(生产 regen 不受影响);`model_train` 用这两个参数喂选中的特征集 + 库因子列。变体因此跑的是**完整 build_v4 管线**(LGB+五维+自适应),产出完整 7 列 → 对选股侧 drop-in。
- **复用**(不重造):
  - 基础特征面板:`build_feature_panel`([v4.py:130](../../../guanlan_v2/strategy/compute/v4.py))。
  - 库因子求值:`compile_factor(expr)(panel)` + `load_panel_cached`([factor_ic.py:36,71](../../../guanlan_v2/screen/factor_ic.py) 同款),把选中库因子逐个求值成列,按 `code×datetime` join 到面板。
  - LGB 训练:照搬 [v4.py:252](../../../guanlan_v2/strategy/compute/v4.py) 的 `lgb.train(LGB_PARAMS, ...)`,但 `mf` = 勾选的列集合(基础 ∪ 库因子)。
  - rank-IC:照搬 [v4.py:283](../../../guanlan_v2/strategy/compute/v4.py) 的 `g["_score"].rank().corr(g["label"].rank())`,但跑在**留出集**上(见 §5)。
  - 五维/自适应/导出 7 列:沿用 build_v4 后半段(变体只换 LGB 学的特征,其余 v4 框架不变 → 产物对选股侧是 drop-in)。
- **依赖**:engine `financial_analyst.data.*` / `financial_analyst.factors.zoo.*` / `lightgbm`;读 qlib binary(`G:/stocks/stock_data/cn_data`)。
- **不依赖**:在线请求路径(同 build_v4,只在离线训练子进程跑)。

### 2.2 变体注册表(filesystem)
- 目录:`guanlan_v2/strategy/vendor/artifacts/models/<variant_id>/`
  - `v4_ranking.parquet` — 7 列(`code/lgb_score/lgb_pct/lgb_rank/v4_total/v4_layer/date`),与 `v4_ranking_latest.parquet` 同 schema。
  - `meta.json` — 见 §4 数据结构。
- 生产 v4 = 保留 id `prod`,读老路径 `V4_RANKING_PARQUET`([paths.py:14](../../../guanlan_v2/strategy/paths.py)),**不进 models/ 目录、不被本功能写**。

### 2.3 选股集成
- `load_v4_ranking(model_id=None)` 参数化([ranking.py:35](../../../guanlan_v2/strategy/ranking.py)):`None`/`"prod"` → 老路径;否则 → `models/<id>/v4_ranking.parquet`(缺失 → 诚实报错,前端回落 prod)。
- `ScreenIn` 加 `model: str = "prod"`;`_screen_via_v4` 用它选排名源。`ranking_date()` 同步参数化(变体的日期取自其产物)。
- 前端:选股页顶栏「评级池」变成**模型下拉**(prod + 变体);`xgBuildBackend` 把 `model` 透进 cfg。

## 3. 因子选择交互(核心交互逻辑)

入口:选股页顶栏一个「**⚙ 模型工坊**」按钮 → 拉出抽屉(覆盖选股页,不跳页,同 regen 进度条范式)。抽屉内容:

1. **统一可训练因子清单**(一个列表,两组分节):
   - 〈v4 基础特征〉~38 个工程因子(动量 `rev_20`/量价/波动/换手/`log_mv`/估值/breadth 残差…)——**默认全勾**。
   - 〈我的因子库〉`FACTOR_DEFS` 里 `supported`(有 expr)的因子,含用户造的——**默认不勾**。
   - 每项可单独勾/取消;基础特征**可以全部取消**(支持"纯库因子从零训")。
   - **硬约束**:总选中数 ≥ 1(为 0 时「训练」按钮禁用 + 提示)。
2. **变体命名**输入 + 「🔨 训练」按钮。
3. **已训变体列表**:每行 = 名字 · 因子数 · **留出 OOS IC** · 训练日;点行 = 设为当前选股模型;行尾「×」删除(带确认)。生产 v4 置顶、不可删。

## 4. 数据结构 `meta.json`

```json
{
  "id": "m_<时间戳36>",
  "name": "纯估值实验",
  "factor_ids": ["c_28f035", "c_264952"],
  "base_features": ["rev_20", "vol_dry", "..."],
  "n_features": 12,
  "universe": "all",
  "oos_ic": 0.041,
  "oos_icir": 0.83,
  "n_holdout": 20,
  "asof": "2026-06-17",
  "created": "2026-06-17T14:30:05",
  "train_rows": 1850000,
  "error": null
}
```
- `oos_ic`=留出集逐日 rank-IC 均值;`oos_icir`=均值/标准差;`n_holdout`=留出有效天数。
- 训练失败 → `error` 记原因、`oos_ic=null`,变体仍登记(诚实显形,不假装成功)。

## 5. 训练流程(异步,照搬 regen 范式)

`POST /screen/model/train {name, factor_ids, base_features, universe}` → 后台子进程(单飞锁、原子写、进度可轮询,交互与「拉取最新数据」一致):

1. `load_panel_cached(loader, codes, start, end, freq="day")` 建面板;`end = _latest_trade_date`(复用 regen 同款,保证 close+daily_basic 共同覆盖日)。
2. 拼特征列:勾选的基础特征(来自 `build_feature_panel`)∪ `compile_factor(选中库因子)`,按 `code×date` 对齐。
3. label = 未来 5 日收益(同 factor_ic 的 `fwd` / build_v4 的 `label`)。
4. **留出切分**:`train = [start ... ld-5-K]`,`holdout = 最近 K 个有标签日`(默认 K=20,模型不训这段)。
5. 训 LGB(复用 build_v4 训练块)→ 在 holdout 上算逐日截面 rank-IC(≥50 名)→ `oos_ic/oos_icir`。
6. 跑 build_v4 后半段产出 7 列产物 → 原子写 `models/<id>/v4_ranking.parquet` + `meta.json`。
7. 进度完成 → 工坊列表刷新。

`GET /screen/model/status` 返回 `{running, phase, step/total, elapsed, ok, error, variant_id}`(同 regen status 形)。
`GET /screen/models` 返回变体列表(读各 meta.json,按 oos_ic 降序)。
`POST /screen/model/delete {id}` 删变体目录(`prod` 拒删)。

**性能**:训练在全 A 上(drop-in,排名覆盖全市场),耗时 ≈ build_v4 的 LGB 部分(几分钟);不重算 breadth/mainline。`universe=csi300` 快速实验模式列为挂账(见 §9)。

## 6. 数据流

```
[工坊抽屉] 勾因子+命名 --POST /screen/model/train-->
[子进程] build_feature_panel(基础) + compile_factor(库因子) → 面板
        → 留出切分 → LGB train → holdout OOS IC
        → 7列产物 + meta.json 落 models/<id>/
[工坊列表] GET /screen/models(读 meta,按 OOS IC 排)
[选股] 顶栏选模型 → /screen/run {model:<id>} → load_v4_ranking(id)
       → 该变体排名 → α混合/约束/九视角照旧 → 候选清单
```

## 7. 对比口径(OOS IC 的诚实三档)

| 口径 | 来源 | 诚实度 | 本功能用途 |
|---|---|---|---|
| 回看 IC | build_v4 已有(样本内,偏乐观) | 低 | 不用来对比变体 |
| **留出 OOS IC** | 新增留出切分(复用 IC 公式) | 真·样本外(时间留出) | **变体对比主口径**,训完即得 |
| 向前 vintage OOS | model_vintage 已有(等未来真实现) | 最硬 | 挂账,v1 不依赖 |

工坊列表与 meta 标注"留出验证 OOS,非未来实盘",不冒充 vintage。

## 8. 错误处理

- 训练失败(因子求值全 NaN / LGB 异常 / 数据缺):子进程捕获,`meta.error` 记因、`oos_ic=null`,status `ok=false`,前端工坊行显「训练失败:<因>」——不写半成品产物(原子写,失败不 replace)。
- 选股选了已删/损坏变体:`load_v4_ranking` 抛 → `_screen_via_v4` 捕获 → 诚实回落 prod + 前端提示「变体不可用,已用生产 v4」。
- 单飞锁:训练进行中再点训练 → 拒绝 + 提示(同 regen 锁)。
- 至少选 1 因子:前端禁用按钮 + 后端二次校验(空 → 400)。

## 9. 范围

**v1 做**:统一因子选择(基础默认勾·可全取消·≥1)、训练命名变体、变体列表+留出 OOS IC、选股页选模型选股、删变体。

**挂账(不做)**:
1. 一键「采用为生产 v4」promote 按钮(先手动切着用,确认稳了再加)。
2. 帷幄 agent 工具(`ww_model_train`/`ww_model_list`/选模型选股)。
3. 向前 vintage OOS(等天数累积)。
4. 变体间排名 diff 对比视图。
5. `universe=csi300` 快速实验模式(全 A 训练较慢时再加)。

## 10. 红线 / 口径

- **绝不碰生产 v4**:`v4_ranking_latest.parquet` 本功能只读不写;变体一律落 `models/<id>/`。
- **不重搭评估**:IC 算法复用 build_v4 现成公式,只加"留出集"切分。
- **诚实**:训练失败显形不假装;OOS IC 标"留出验证"不冒充实盘 vintage;变体不可用回落 prod 并提示。
- **变体是 drop-in**:产物 7 列同 schema,选股侧 α/约束/九视角逻辑零改动,只换排名源。

## 11. 测试

- `model_train`:小宇宙(几只票×短窗)训一个变体 → 产物 7 列 schema 正确、meta 字段齐、oos_ic 是留出集算的(构造 holdout 与 train 不重叠,断言用到的日期不在 train 内)。
- 留出切分:断言 train 最大日期 ≤ ld-5-K,holdout 日期都 > train 最大日期。
- `load_v4_ranking(model_id)`:prod 走老路径;变体走 models/<id>;缺失抛。
- `/screen/run {model:<id>}`:用变体产物排名(与 prod 不同 id 排名集可不同);坏 id → 回落 prod。
- 错误路径:空 factor_ids → 400;训练失败 → meta.error 记录、无半成品产物。
- 前端守护:基础特征可全取消、≥1 校验、模型下拉透传 model 入 cfg。
