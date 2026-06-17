# 加一个节点 · 配方

> 文件: [`ui/factor/workflow.jsx`](../ui/factor/workflow.jsx) · 入口组件 `WorkflowApp` · 由 `观澜 · AI 工作流.html` 经 `<script src="workflow.jsx?v=N">` 加载。
> 改完 workflow.jsx 必须 **bump HTML 里的 `?v=N`**(否则浏览器吃旧缓存)。

## 一句话

**加一个节点 = 注册 `SPECS[type]` + 挂进 `CATALOG` + 写 `NODE_EXEC[type]`(+ 可选 `render`)**。

四件事各管一层,互不耦合:

| 件 | 位置(模块作用域) | 管什么 | 必填? |
|---|---|---|---|
| `SPECS[type]` | `const SPECS = { … }` | 节点的**形状**: 标题、分类、输入/输出端口(id+label+dt)、参数(id+控件) | 必填 |
| `CATALOG` | `const CATALOG = [ … ]` | 节点出现在**左栏目录**的哪一组 | 必填(否则拖不进画布) |
| `NODE_EXEC[type]` | `const NODE_EXEC = { … }` | 节点的**执行逻辑**: `async (inputs, params, ctx) => output` | 必填(缺省=透传空,不报错但不产出) |
| `render`(可选) | `Node` 组件内 `rows.map` | 自定义**参数控件外观**;不写则用默认 step/select/text | 可选 |

执行器(`topoOrder` + `runGraph`)与结果抽屉(`ResultsDrawer`)**不用动** —— 它们按 `SPECS` 的端口 / dt 通用驱动。

---

## 三层合同(务必对齐)

### 1. `SPECS[type]` 的端口 id = 数据沿边流动的键

```js
mytype: {
  title: '我的节点', cat: 'fe',                      // cat ∈ io|fe|ml|mf|fa|bt (决定配色, 见 CAT)
  inputs:  [{ id: 'x', label: '输入X', dt: 'series' }],
  outputs: [{ id: 'y', label: '输出Y', dt: 'factor' }],
  params:  [{ id: 'k', label: '参数K', type: 'step', value: 5, step: 1 }],
}
```

- `inputs[].id` / `outputs[].id` 就是 `NODE_EXEC` 里 `inputs.<id>` / 返回对象 `{ <id>: … }` 的键 —— **必须逐字一致**。
- `dt`(数据类型)用于**连线校验**(`startWire` 拒绝 `dt` 不匹配的连接)与**终端判定**(`runGraph` 把 `dt ∈ {report, ic, result}` 且非占位的载荷送结果抽屉)。
- `params[].type` ∈ `step`(数字±)/ `select`(下拉)/ `text`(文本);`step` 可带 `step`/`dec`。

### 2. `NODE_EXEC[type]` 的三个入参(executor 注入)

```js
mytype: async (inputs, params, ctx) => {
  // inputs : { [本节点输入端口id]: 上游同名输出端口载荷 }。未连的端口键缺省 → 自行容缺。
  // params : node.params (字段同 SPECS[type].params[].id)
  // ctx    : { universe, node, post, allExprs }
  //          ctx.universe  — 全局 universe (source→_universeOf, 无 source→'csi_fast'); 端点节点优先取它
  //          ctx.post      — 即 _post(path, payload): POST 引擎, 已通 /factor/report、/factor/compose
  //          ctx.node      — 当前节点对象 (id/type/x/y), 一般用不到
  //          ctx.allExprs  — deriveCall 全图表达式集合 (mf 兜底用)
  return { y: { __dt: 'factor', /* …载荷… */ } };   // 键 = SPECS.outputs[].id
};
```

**载荷形状约定**(沿边传的「类型化数据」,对齐 dt):

| dt | 载荷 |
|---|---|
| `series` | `{ __dt:'series', expr?, universe? }`(数据源给 universe;公式/Python 给 expr,不求值) |
| `fe` / `model` / `factor`(占位) | `{ __dt:'<dt>', __pending:'<type>未接,待后续期', …透传上游 }` |
| `ic` / `report`(真报告) | 引擎返回对象 + `{ _label, _universe, _warnings }`(`mf` 另加 `_compose:true`) |

**终端规则**:`dt ∈ {report, ic, result}` 且载荷**非 `__pending`** 且含 `ic|portfolio|composite|_compose` → 自动送结果抽屉。占位(`__pending`)永远不当结果,杜绝谎报。

**报错规则**:exec 内 `throw new Error('中文原因')` → `runGraph` 记首错、继续跑其余节点;收尾若无任何真结果 → 错误喂结果抽屉红色态。

### 3. `CATALOG` 挂目录

```js
const CATALOG = [
  …
  { g: '02 · 特征工程', items: ['feature', 'mytype'] },   // 把 type 加进某组 items
];
```

---

## 完整示例: 加一个「因子裁剪(Winsorize)」节点

需求: 收一个公式因子表达式,调引擎 `/factor/report` 出 IC 报告,作为终端节点直接进结果抽屉。(逻辑借道已通的 `/factor/report`,演示三层如何咬合。)

**① `SPECS` 加一项**(放进 `const SPECS = { … }`):

```js
winsor: {
  title: '因子裁剪', cat: 'mf',
  inputs:  [{ id: 'src', label: '因子', dt: 'series' }],   // 接公式输入的 series
  outputs: [{ id: 'rep', label: '报告', dt: 'report' }],   // dt=report → 终端, 进抽屉
  params:  [{ id: 'q', label: '分位阈', type: 'step', value: 0.01, step: 0.01, dec: 2 }],
},
```

**② `CATALOG` 挂进「04 · 因子相关」**:

```js
{ g: '04 · 因子相关', items: ['pca', 'spearman', 'iccalc', 'mf', 'analysis', 'winsor'] },
```

**③ `NODE_EXEC` 写执行器**(放进 `const NODE_EXEC = { … }`):

```js
winsor: async (inputs, params, ctx) => {
  const f = inputs.src;
  const expr = (f && f.expr) ? f.expr : null;
  if (!expr) throw new Error('因子裁剪: 上游需「公式输入」直连 (无表达式)');
  // 真调引擎 (params.q 待引擎支持裁剪参数后透传; 当前先借 report 验证管线咬合)
  const r = await ctx.post('/factor/report', { expr_or_name: expr, universe: ctx.universe });
  if (r && r.status && r.status !== 'ok') throw new Error(r.status + (r.error ? ' · ' + r.error : ''));
  return { rep: Object.assign({}, r, { _label: expr, _universe: ctx.universe, _warnings: r.warnings || [] }) };
},
```

**④ render(可选)**: 本例参数是普通 `step`,默认控件已够 —— **不用动 `Node`**。

完成。拖「公式输入」→「因子裁剪」连线、点运行:执行器按拓扑序点亮两节点,把 `formula` 的 `{__dt:'series', expr}` 沿边喂进 `winsor` 的 `inputs.src`,`winsor` 调 `/factor/report` 返回 `dt='report'` 真报告 → 自动进结果抽屉,KPI / IC 序列 / 净值全填真数据。

> 别忘了: 改完 `workflow.jsx` → 把 `观澜 · AI 工作流.html` 的 `workflow.jsx?v=N` **加一**。
