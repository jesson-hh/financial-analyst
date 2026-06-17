# _shared — 共享层

所有模块页面共用的视觉、导航、数据总线、组件。每个模块页面在自己的 jsx 之前引这些。

## 文件

### tokens.css
设计 tokens,定义 CSS 变量:配色(`--paper` 宣纸 / `--ink` 浓墨 / `--yin` 印章红 / 朱砂红涨 / 黛绿跌 / 墨金)、字体(`--serif` Noto Serif SC / `--sans` Noto Sans SC / `--mono` JetBrains Mono)、灰阶。**每页必引**。

### tokens-styles.css
附加全局样式(滚动条、hover 等),补充 tokens.css。

### shared.jsx
跨页共用 React 组件(如 Sparkline 迷你走势图)。需要的页面引。

### guanlan-nav.js
全局模块导航条:
- 注入 `#gl-nav` 顶栏(印章「觀」品牌 + 5 模块 tab + 右侧"共享档案库 N 件")。
- `MODULES` 数组:5 个入口,`file` 为 `../<module>/<page>.html`(重组后跨文件夹相对路径)。
- 高亮当前页:`here === m.file.split('/').pop()`(按文件名 basename 匹配)。
- 用 flex 列布局把页面内容下压,兼容内部 `height:100vh`。

### guanlan-bus.js
档案库总线 `window.GL`(= `window.GuanlanBus`)。**五模块的唯一事实源**,localStorage 持久化(`guanlan:store:v1`)。
- 5 类物料:research / factor / card / seat / decision。
- API:`all(type) get(id) byRef(id) put(a) patch(id,f) link(from,to) remove(id) on(fn) handoff(ch,p) take(ch) peek(ch) go(href,ch,p) stats() reset()`。
- 物料带 `refs` 互引(card→research+factor,seat→card+factor)。
- 跨标签同步(storage 事件)、发布订阅、带上下文 handoff。
- 初次 `seed()` 一张图谱(4 研报 / 4 因子 / 6 卡 / 4 席位)。

## 引用方式
模块页面里:`<link href="../_shared/tokens.css">`、`<script src="../_shared/guanlan-bus.js">`、`<script src="../_shared/guanlan-nav.js">`、`<script type="text/babel" src="../_shared/shared.jsx">`。

**2026-06-12 · 帷幄融合批(`guanlan-nav.js`)**:
- **`?embed=1` 守卫**:各模块页检测到 `embed=1` 时 `guanlan-nav.js` 跳过注入 `#gl-nav` 导航条,帷幄右栏 iframe 内不出现重复顶栏(spec §3.4「嵌入卫生」)。
- **`MODULES` 首位加帷幄**:`MODULES` 数组首位新增帷幄入口(`../console/观澜 · 帷幄.html`),印章「帷」,独立打开或 `?legacy=1` 模式均可通过导航条跳回。

**2026-06-13 · 导航收敛两门面(`guanlan-nav.js`)**:
- **`MODULES` 收敛**:`MODULES` 数组缩减为两个门面——**帷幄**(home 入口)和**席位·落子**;经验卡/工作流/选股/图谱/对话 五页从顶栏摘除。
- **代码/直链全保留**:五页代码不删,直链(`/ui/<module>/观澜 · X.html`)仍可访问;URL 加 `?legacy=1` 通过各页自身导航找回;帷幄顶栏「分页 ▾」提供快速跳转入口。
- **帷幄替代路径**:五页通过帷幄 `ww_show_page` 工具(口头调出右栏视图)或直链访问——界面定位降级为「agent 召之即来的工作台视图」。

**2026-06-10**:`guanlan-bus.js` seed 物料统一带 `demo:true` + 老 localStorage 按 seed id 迁移补标(审计 M3,渲染端据此打「示例」徽章);五个引用页升 `guanlan-bus.js?v=2`。

**2026-06-11 · P2-C 后端持久桥(`guanlan-bus.js?v=3`,六引用页全升)**:strategy/research/decision 三类**非 demo**真物料镜像到后端影子库 `/archive/*`(guanlan_v2/archive,落 var/archive/<id>.json)——此前清缓存即永久丢。桥 = 包装 put/patch/remove fire-and-forget 上推 + 首拍(setTimeout 0,等内联脚本设好 GUANLAN_BACKEND)拉 list 合并本地缺失 id(**本地优先**绝不覆盖)+ 存量回填;file:// 无后端全静默,行为同旧版。`stats()` 顺带补 strategy 计数。验真:图谱一开,4 件真物料(2策略+1研报+1决策)自动落 var/archive。
