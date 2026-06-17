# graph — 研究图谱(首页 / 中枢)

| 项 | 值 |
|----|----|
| 页面 | 观澜 · 研究图谱.html |
| 入口组件 | `GraphApp`(graph.jsx) |
| 后端 | 无(纯读 `window.GL` 档案库) |
| 闭环位置 | **总览**——把 research/factor/card/seat/decision 的关系画成图 |

## 职责
V2 的**首页与中枢**(`/`、`/ui/` 都重定向到这里)。把档案库里五类物料按 `refs` 关系渲染成研究图谱,一眼看清"哪篇研报炼出了哪个因子、验证成哪张卡、装配进哪个席位"。

## 数据
只读 `GL.all(type)` / `GL.byRef(id)` / `GL.stats()`;无后端调用。当前由 `guanlan-bus.js` 的 seed 图谱驱动(4 研报→4 因子→6 卡→4 席位)。

## 交互
点物料 → `GL.handoff` 带上下文跳到对应模块(chat/factor/cards/seats)。

## 状态
设计稿渲染(rootKids>0 已验证)。真实物料随各模块产出写入 GL 后自动出现在图上。

**2026-06-10 · 示例/真物料显形(graph.jsx `?v=2` + guanlan-bus.js `?v=2`,审计 M3)**:bus `seed()` 的 18 件设计稿示例物料统一打 `demo:true`(+ 按已知 seed id 的 localStorage 迁移补标;用户真物料 `card_user_*`/`strat_*` 不受影响);支柱 chip 与溯源链节点对 demo 项渲染「示例」徽章 —— 真假一眼可分。浏览器验真:18 个徽章在位、factorlib 真因子(`lib_turnover_*`)无徽章、零报错。

**2026-06-11 · 互通批(P1⑧,`graph.jsx ?v=3`)**:本页此前**所有跳转 404**——PILLARS.href 与 TYPE_HREF 全是裸文件名,相对 /ui/graph/ 解析而目标页在兄弟目录(首页点哪儿都 404,handoff payload 写了但页面死胡同)。已全改 `../<module>/` 相对路径(对照 guanlan-nav.js);`focusPayload` card 分支补 `focusCardName`(对齐 validation 按名匹配器,原 focusCard:id 键不被认)、factor 分支补 `expr` 键(对齐 workflow 接收)。验真:四 href HEAD 全 200;点卡物料 → cards 页自动聚焦「缩量企稳反转」、handoff 消费;seat/decision → 落子(P1⑨ 已接收)。

**2026-06-13 · 帷幄融合批(`graph.jsx?v=4`)**:
- **WW_EMBED 旗**:`?embed=1` 时顶栏隐藏,由帷幄顶栏统一接管,嵌入右栏不出现重复顶栏。
- **帷幄注册**:本页已注册进帷幄 `WW_PAGES`(channel `null`,仅展示不接收 handoff payload);`ww_show_page` 工具可口头调出。
- 本页已从导航摘除;直链(`/ui/graph/观澜 · 研究图谱.html`)仍可用,代码保留;顶栏「分页 ▾」仍可跳回图谱。
