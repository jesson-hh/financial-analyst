# 帷幄工坊闭环 + 引擎因子研究线 — 设计

> 日期:2026-06-22
> 目标:让帷幄(console agent)补齐 v4 模型工坊闭环(列/训/用/**删/设为默认**),并放行引擎 alpha-zoo 因子研究线(7 个工具),向「帷幄能调用 guanlan 全部主能力」再推进一步。
> 不做:真覆写生产 prod 的 promote(风险高,单独立项);guanlan 原生 MCP(单独立项);前端模型 picker 与「默认变体」联动(可选后续)。

---

## 0. 背景(现状审计结论)

- 帷幄白名单 `CONSOLE_ALLOWED` 现可调 **46/71** 工具(28 `ww_*` + 18 引擎只读研究工具)。
- 工坊侧:有 `ww_model_list`/`ww_model_train`,**缺删除、缺「上线」**。后端 `/screen/model/delete` 存在;**`/screen/model/promote` 不存在**,且「上线=覆写只读 prod」风险高 → 本设计用「设为默认变体」指针替代,不碰 prod。
- 引擎 alpha-zoo 研究线(442 学术因子)在 guanlan **已验证存活**(`alpha_list` 返回 442 条、`alpha_show` 正常),但 7 个工具未进帷幄白名单。

---

## 1. Part A — 工坊「删除 + 设为默认」

### A1. `ww_model_delete`(新 ww_ 工具)
- 包现成 `POST /screen/model/delete`(入参 `{id}`)。
- `confirm=True`(销毁性);`cost="instant"`;`reachable=["/screen/model/delete"]`。
- 行为:删指定变体;`id="prod"` → 诚实拒绝(`model_registry.delete_variant("prod")` 本就抛 `ValueError`,工具捕获后回 `ok:False` + 原因,不崩)。
- 返回:删除结果 + 剩余变体列表(复用 `list_variants`)。
- **联动**:若被删变体正是当前默认变体 → 同时清除默认指针(回落 prod),避免悬空指针。

### A2. 默认变体指针 + `ww_model_set_default`(新轻量功能,不碰只读 prod)

**存储层(`guanlan_v2/screen/model_registry.py`)**
- `get_default_model() -> Optional[str]`:读 `MODELS_DIR/_default.json`(`{"id": "m_xxx"}`);文件不存在/损坏/指向已删变体 → 返回 `None`(= 用 prod,诚实降级)。
- `set_default_model(model_id: Optional[str]) -> None`:`model_id` 为变体 id → 校验该变体存在(`variant_ranking_path(id).exists()`),写指针;`model_id in (None, "", "prod")` → 删除指针文件(回落官方 prod)。校验失败抛 `ValueError`(诚实失败)。

**端点层(`guanlan_v2/screen/api.py`)**
- `POST /screen/model/default`(入参 `{id}`):调 `set_default_model`;返回 `{ok, default: <id|None>}`。
- `GET /screen/models`:每条变体标注 `is_default: bool`;另回顶层 `default_model: <id|None>`。

**解析层(`/screen/run` 开头,单一改动点)**
- 现状:`ScreenIn.model` 缺省 `"prod"`。
- 改:在 run handler 顶部 `model = _resolve_model(body.model)`,其中
  `_resolve_model(m)`:`m` 为空/`"prod"` → `get_default_model() or "prod"`;`m` 为显式变体 id → 原样返回(不变)。
- **不变性保证**:没设默认指针时 `get_default_model()` 返回 `None` → 解析回 `"prod"` → **零行为变化**。
- **诚实**:响应已有 `model` 字段照实回报实际所用模型;`provenance`/响应体不伪装成 prod。
- **prod 文件全程只读不动**;清除指针即刻回滚。

**`ww_model_set_default`(新 ww_ 工具)**
- 入参 `{id}`(变体 id;传 `"prod"`/省略 = 清除回官方)。
- `confirm=True`(改平台默认);`cost="instant"`;`reachable=["/screen/model/default"]`。
- 描述点明:设为默认后,选股页/`ww_screen_run` 不指定模型时缺省用该变体;随时可设回 prod。
- `ww_model_list` 输出顺带标出「当前默认 = X」。

---

## 2. Part B — 引擎因子研究线(7 工具进白名单)

把以下 7 个引擎工具加入 `_ALLOWED_ENGINE_TOOLS`(均只读或自带 `confirm_required=True`,放行即保留其确认门):

| 工具 | 性质 | 用途 |
|---|---|---|
| `alpha_list` | 只读 | 列 442 注册因子(可按 alpha101/gtja191/qlib158 过滤)|
| `alpha_show` | 只读 | 看单个因子公式+论文出处 |
| `alpha_compare` | confirm·分钟 | 并排对比 2-8 个因子 RankIC/ICIR/健康分类 |
| `alpha_bench` | confirm·分钟 | 全 442 因子跑分,出最强 top-N |
| `event_report` | confirm·分钟 | 事件研究(触发后 1/5/10/20 日收益)|
| `alpha_forge` | confirm·分钟 | 自然语言想法→因子表达式+快测 IC |
| `factor_report` | confirm·分钟 | 单因子完整评测(IC衰减/十分位/多空净值)|

- **实现期逐个真机验证**:每个用小样跑一次;**跑不通(缺 universe/面板数据)的不接、在交付说明里诚实列出剔除项**。
- 提示词点明:这是引擎 **alpha-zoo 研究线**(学术因子库/事件研究/炼因子),与 guanlan 自有 `ww_factor_analyze`/`ww_backtest`/`ww_factor_compose`(跑 guanlan 面板)是两套并行体系,各管各的场景,避免帷幄混用。
- **`alpha_forge` 的 `save=` 写引擎自有 user-factor 库**(非 guanlan factorlib),与 `ww_factorlib_save` 不互通 → 提示词标注此分流,默认 `save=false`。

---

## 3. Part C — 接线「四处同步」铁律

帷幄工具改动必须四处同步(否则守护测试拦):
1. **`WW_TOOL_TABLE`**(`console/tools.py`)+2:`ww_model_delete`、`ww_model_set_default`。
2. **`_ALLOWED_ENGINE_TOOLS`**(`console/tools.py`)+ 验证存活的引擎工具(目标 7,按真机存活数)。
3. **`_SYSTEM_PROMPT`**(`console/api.py`)具名新工具(纪律:每个 `ww_` 都在提示词;引擎研究线作为一组介绍)。
4. **守护测试计数**:`ww_` 28→30;`CONSOLE_ALLOWED` 46→最多 55(按 Part B 存活数)。

---

## 4. 测试

**单元(`tests/test_console_tools.py` / `tests/test_screen_api.py` / `tests/test_model_registry.py`)**
- `set_default_model`/`get_default_model`:设/清/校验不存在 id 抛错/指向已删变体降级 None。
- `/screen/run` 默认解析:没设=prod(行为不变)、设了变体=用变体、清除=回 prod。
- `ww_model_delete`:删变体成功、拒删 prod、删默认变体时连带清指针。
- `ww_model_set_default`:设/清/校验。

**守护(`tests/test_console_tools.py` 等已有计数测试)**
- ww_ 计数、CONSOLE_ALLOWED 计数更新;每个 `ww_` 在 `_SYSTEM_PROMPT`;新引擎工具在白名单。

**真机(9999)**
- Part B 7 工具逐个跑(留存活、诚实剔除报告)。
- Part A 端到端:`ww_model_set_default(m_x)` → `ww_screen_run`(不传 model)返回变体排名 → `ww_model_set_default(prod)` → 回 prod 排名;`ww_model_delete` 删变体;删默认变体连带回落 prod。
- 全量 pytest 绿(含本会话已修的工坊 fixtures 由用户处理,不在本设计范围)。

---

## 5. 红线 / 不变性

- **prod v4 永不被覆写**;「设为默认」只是可逆指针,清除即回官方。
- 没设默认指针时 `/screen/run` 行为**逐字节不变**(回归守护)。
- 响应 `model` 字段始终照实回报实际所用模型(诚实,不伪装 prod)。
- 引擎研究线工具跑不通的**不接**(不暴露会报错的工具),交付明列剔除项。
- 帷幄工具改动「四处同步」,守护测试为闸。
