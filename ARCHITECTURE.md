# 观澜 V2 架构

## 设计哲学:Approach B —— 重组头部,复用引擎

观澜 V1 的能力(行情、资金流、新闻、研报、盯盘、因子评测)已沉淀在 `financial_analyst` 引擎里,经过验证、连着真数据。V2 的目标**不是重写这些**,而是:

1. 把**前端按「研究闭环」的五模块重新组织**(V1 是平铺单页 + 散落原型);
2. 用一个**薄壳后端**把引擎(`build_app()`)和多模块前端缝在一起。

所以 **V2 = 引擎(复用)+ 头部(重组:UI 组织 + 壳)**。

> **2026-06-04 变更**:引擎由「import 外部 `G:/fa-watch-wt/src`」改为「**fork 进仓库 `engine/`**」——guanlan-v2 自包含,后端 import 的是仓库内 `engine/financial_analyst`,配置用仓库内 `config/`,**只有数据仍外部**(经 `get_data_paths` → `G:/stocks`)。下文凡「import 引擎」即指从仓库内 `engine/` import;与上游 fa-watch-wt 已脱钩(修复需手动 backport)。

## 三层

```
┌─────────────────────────────────────────────┐
│ 前端  ui/<module>/*.html + *.jsx (无构建)      │  浏览器内 Babel 编译
│   └─ 共享 _shared/ (tokens/nav/bus/shared)     │
├─────────────────────────────────────────────┤
│ 后端薄壳  guanlan_v2/server.py                 │  FastAPI: import build_app + StaticFiles
├─────────────────────────────────────────────┤
│ 引擎  financial_analyst (仓库内 engine/, 已 fork) │  buddy SSE 后端 + 所有工具; 数据→G:/stocks
└─────────────────────────────────────────────┘
```

## 前端:无构建多页

- **无打包**:没有 webpack/vite/node_modules。每页是独立 HTML,引 React 18 UMD + `@babel/standalone`,`<script type="text/babel">` 在**浏览器内即时编译** JSX。
- **多页 + 全页导航**:不是 SPA。模块之间靠 `_shared/guanlan-nav.js` 注入的顶栏做**整页跳转**(`../<module>/<page>.html`)。
- **改完刷新即生效**:无构建步骤。**代价**:浏览器会缓存 jsx,改完必须 bump HTML 里的 `?v=` 查询串(见 dev_guide)。
- **每页一个根组件**:HTML 末尾 `ReactDOM.createRoot(...).render(<XApp/>)`。

## 共享层 _shared/

| 文件 | 职责 |
|------|------|
| `tokens.css` | 设计 tokens(CSS 变量:宣纸/月夜配色、字体、灰阶)。每页必引。 |
| `tokens-styles.css` | 附加全局样式。 |
| `shared.jsx` | 跨页共用 React 组件(Sparkline 等)。 |
| `guanlan-nav.js` | 全局导航条。注入 `#gl-nav`,5 模块 tab,按当前页 basename 高亮,href 为 `../<module>/<page>`。 |
| `guanlan-bus.js` | 档案库总线 `window.GL`(见下)。 |

## 档案库总线 guanlan-bus.js

五模块共享的**唯一事实源**,localStorage 真持久化(`guanlan:store:v1`)+ 跨标签同步 + 发布订阅 + 带上下文 handoff。

- **5 类物料**:`research`(研报/素材)、`factor`(因子)、`card`(经验卡)、`seat`(席位)、`decision`(落子决策)。
- **CRUD**:`GL.all(type) / get(id) / byRef(id) / put(a) / patch(id,fields) / link(from,to) / remove(id)`。
- **物料带 `refs`**:互相引用(card 引 research+factor,seat 引 card+factor),构成研究闭环的图。
- **订阅**:`GL.on(fn)` → state 变化回调(跨标签同步)。
- **带上下文跳转**:`GL.handoff(ch,payload) / take(ch) / peek(ch) / go(href,ch,payload)`。一个模块把物料"递"给另一个模块。
- **统计**:`GL.stats()` → 各类物料计数(导航条右侧"共享档案库 N 件"读它)。
- 初次加载 `seed()` 一张初始物料图谱(4 研报 → 4 因子 → 6 卡 → 4 席位),供设计稿渲染。

## 后端薄壳 guanlan_v2/server.py

- `_ensure_engine_importable()`:把 `GUANLAN_FA_SRC`(**默认 = 仓库内 `engine/`**,引擎已 fork 进来)**prepend 到 sys.path,在 import 之前**,压过任何 editable 安装的旧 `financial_analyst`。`create_app()` 另把 `FA_CONFIG_DIR` setdefault 到仓库内 `config/`(deepseek `llm.yaml` + `loaders.yaml`→stocks),使**配置也自包含**(否则引擎 `find_config` 会解析到外部 workspace 配置)。设 `GUANLAN_FA_SRC=G:/fa-watch-wt/src` 可指回上游 A/B。
- `create_app()`:`build_app()`(引擎全部真实端点)→ `mount("/ui", StaticFiles(ui, html=True))` → `/` 重定向到 `/ui/`。
- 启动时 stderr 打印 `engine source:`,可核对引擎来源。
- **不在壳里加业务端点**:要加端点加到引擎(现在是仓库内 `engine/financial_analyst/buddy/server.py`),壳只负责缝合 + 服务静态前端。

详见 [guanlan_v2/README.md](guanlan_v2/README.md)。

## 请求流

```
浏览器 GET /                → 302 /ui/ → index.html → 跳 graph/观澜 · 研究图谱.html
页面 jsx fetch(window.GUANLAN_BACKEND + '/run' | '/factor/*' | '/watch/*' | '/quotes' ...)
       → 同源 9999 → 引擎工具 → 真数据(stock_data 经 get_data_paths;实时走腾讯)
纯前端模块(graph)→ 只读写 window.GL(localStorage),不连后端
cards(经验卡)→ guanlan 自有 /cards/*(右栏知识库读真卡 + 沉淀真持久化;GL 仍存跨模块);seats 见其模块说明
```

## 视觉系统(沿用设计稿)

- **基底**:宣纸暖白 `#f1ead9`(白天)/ 夜墨(月夜)。
- **强调**:朱砂红(涨)`#b94a3d` · 黛绿(跌)`#4a6b5c` · 印章红 `#a8392d` · 墨金 `#8a6f3f`。
- **字**:Noto Serif SC(标题)+ Noto Sans SC(UI)+ JetBrains Mono(数字)。
- **品牌符号**:印章方框「觀」。
- 交互母题:多步工具链=纵向"研究纸带"+朱砂印章序号;引用=印章红脚注 §N;盯盘触发=朱砂警铃 toast。
