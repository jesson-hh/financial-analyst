# CPCV 验证节点(③)设计

> ②①③ 三件套的最后一块。把 ① 的 CPCV + Deflated Sharpe 验证接进 AI 工作流 DAG —— 让用户在工作流里对模型(尤其在工作流里训练/入库的变体)一键跑验证,结果以诚实结果卡浮出。①(引擎 + `/screen/model/validate` 端点)已就绪;**③ 是纯前端**:新增一个节点类型 + 一个结果分支,复用 ① 后端与 ② 的模型选择器。

## 目标
- 在 AI 工作流里新增「CPCV 验证节点」:选一个 registry 模型(prod 或变体)→ 跑 CPCV(快验/严格)→ 在结果抽屉里看 DSR / 夏普分布 / IC 分布 / 路径数。
- 闭环:② 在工作流里训练 + 入库模型;③ 紧接着在同一画布里验证它。

## 范围
- **改**:`ui/factor/workflow.jsx`(新节点类型 `validate` + 执行器 + `ResultsDrawer` 新分支 + 模型选择器/上游预填);host `ui/factor/观澜 · AI 工作流.html` 的 `workflow.jsx?v=` cache-buster bump。
- **不改**:任何后端(复用既有 `/screen/model/validate` + `/status`);`v4.py`;② 的 model 节点 / promote。
- **红线**:fill-not-rebuild(只加节点 + 结果分支,不重构 workflow.jsx);复用现有 `ctx.post`/`_get`/`ModelLibPanel`;诚实缺席(`ready=false` 显 note,绝不编数);① 后端与 `v4.py` 零改。

## 架构(契合现有节点模型)

### 节点注册(`SPECS`)
新增类型 `validate`:
```
validate: { title: 'CPCV 验证', cat: 'fa',
  inputs:  [{ id: 'model', label: '模型', dt: 'series' }],   // 可选;接 model 节点输出
  outputs: [{ id: 'report', label: '验证', dt: 'validation' }],
  params: [
    { id: 'model_id',  label: '已选模型', type: 'text', value: '' },
    { id: 'model_name',label: '模型名',  type: 'text', value: '' },
    { id: 'tier',   label: '档位', type: 'select', value: 'strict',
      options: [{v:'strict',t:'严格(全历史 retrain-CPCV)'},{v:'quick',t:'快验(读冻结快照·秒级)'}] },
    { id: 'n_groups', label: '组数 N', type: 'num', value: 6 },
    { id: 'k',        label: '测试组 k', type: 'num', value: 2 },
    { id: 'purge',    label: 'purge 日', type: 'num', value: 5 },
    { id: 'embargo',  label: 'embargo 日', type: 'num', value: 5 },
  ]
}
```
- `dt: 'validation'` 加入 `TERMINAL_DT` → 节点输出会浮出到 `ResultsDrawer`。
- 放在 CATALOG 合理分组(分析/评估族,贴近 `analysis`)。

### 执行器(`NODE_EXEC.validate`)
镜像 L1 的 `runValidate` 逻辑,用现有 `ctx.post`/`_get`:
1. `id = (inputs.model && inputs.model.model_id) || String(params.model_id||'').trim()`;为空 → `throw '验证节点:未选模型 —— 接一个 model 节点或在节点里选'`。
2. **quick**:`POST /screen/model/validate {id, tier:'quick'}` → `result`;返回 `{ report: { __dt:'validation', tier:'quick', model_id:id, model_name, ...result } }`。
3. **strict**:`POST /screen/model/validate {id, tier:'strict', n_groups,k,purge,embargo}`;若 `!ok` → `throw (reason||'启动失败')`;轮询 `GET /screen/model/validate/status` 每 ~4s,至 `!running && phase==='done'`;取 `state.result`(后端 `load_cpcv_summary` 回灌);返回 `{ report:{ __dt:'validation', tier:'strict', model_id:id, model_name, ...(state.result||{}), error: state.ok?undefined:state.error } }`。
4. 全程不抛未捕获异常即正常浮出;捕获到端点失败按上面 throw,`runGraph` 对失败节点优雅降级不崩整图。

### 模型选择器 + 上游预填
- 节点 body 复用 model 节点的 `ModelLibPanel` 选 `model_id`/`model_name`(独立使用)。
- 若该节点的 `model` 入口连了上游 model 节点:节点 body 显示「来自上游:<model_name>」,执行时以 `inputs.model.model_id` 为准(picker 作 fallback/override)。

## 结果渲染(`ResultsDrawer` 新分支 `result.__dt==='validation'` 或带 cpcv 标志)
一张 CPCV 验证卡:
- 顶部:模型名/id + 档位徽章(严格/快验)。
- `ready===true`:**DSR**(突出·0–1)、夏普分布(median / p05 / p95 / std)、IC 分布(median/...)、`n_paths`、`n_trials`、`asof`、`kind`、`note`。
- `ready===false`:显 `note`(如「证据不足:已实现 OOS 仅 N 天<10」)+ 诚实徽章,**不显任何假数**。
- 复用既有 `RCard` 卡片样式,不新建视觉语言。

## 执行 / 并发
- strict 执行器 `await` 轮询(~分钟),阻塞该节点直至完成 —— 验证一般是叶/终端节点,DAG 等待可接受;quick 即时返回。
- 不引入新的全局状态;复用后端 `_VALIDATE_STATE` 单飞(已有并发守卫:第二个 strict 在跑会被端点挡)。

## 数据流 / 契约(已建,③ 只消费)
- `POST /screen/model/validate` body:`{ id, tier: 'quick'|'strict', n_groups, k, purge, embargo }`。quick → `{ ok, result }`;strict → `{ ok, started, state }`(或 `{ ok:false, reason }`)。
- `GET /screen/model/validate/status` → `{ ok, state:{ running, phase, ..., result? } }`(idle 时附 `load_cpcv_summary`)。
- 上游 model 节点 output `dt:'series'` 带 `.model_id` → 验证节点入口读取。

## 测试与验证
- 前端无单测;真机验证 = 浏览器。受并发限制(9999 服并发会话的 main 树·无 ① 代码),**live-browser 点测与 ① 的 L1 一起延后到合并后**(届时 9999 服 ① 代码再点快验/严格验证)。本期 ship 以代码评审 + JSX well-formed(节点/执行器/结果分支语法正确、复用真 helper)为界。
- 可选提前验证:临时从 worktree 起一个服务到空闲端口,浏览器点测(用户按需)。
- 守护:不破坏现有 workflow 相关测试(若有);不引后端测试(无后端改动)。

## 红线
fill-not-rebuild;复用 `ctx.post`/`_get`/`ModelLibPanel`/`RCard`;诚实缺席(`ready=false` 显 note);① 后端 + `v4.py` 零改;改后 bump `?v`。

## 挂账(③ 之外)
- 「自动验刚 promote 的变体」无 edge 直传(promote 经 panel 非图边)→ 本期靠 model 节点预填或手选;未来可让 promote/ML 节点把 `variant_id` 作 edge 输出,验证节点直接吃。
- TopBar / 选股工坊的 CPCV 迷你直方图(① 挂账)同议。
- 跨变体 PBO(CSCV)仍挂(① 挂账)。
