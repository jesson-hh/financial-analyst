# ui — 观澜 V2 前端(无构建,按模块组织)

纯 HTML + JSX,浏览器内 Babel 编译,无打包。按「研究闭环」五模块组织。

## 结构

| 路径 | 内容 |
|------|------|
| `index.html` | 入口,重定向到 `graph/观澜 · 研究图谱.html`。 |
| `_shared/` | 共享层:`tokens.css`/`tokens-styles.css`(视觉)、`shared.jsx`(共用组件)、`guanlan-nav.js`(全局导航)、`guanlan-bus.js`(档案库总线)。见 [_shared/README.md](_shared/README.md)。 |
| `graph/` | 研究图谱(首页/中枢)。见 [graph/README.md](graph/README.md)。 |
| `chat/` | 对话·研报。见 [chat/README.md](chat/README.md)。 |
| `factor/` | 因子·工作流(2 页)。见 [factor/README.md](factor/README.md)。 |
| `cards/` | 经验卡。见 [cards/README.md](cards/README.md)。 |
| `seats/` | 席位·落子。见 [seats/README.md](seats/README.md)。 |
| `_archive/` | 旧设计稿/变体,不接线、不进导航。见 [_archive/README.md](_archive/README.md)。 |

## 无构建机制

- 每页 HTML 引 React 18 UMD + `@babel/standalone`,`<script type="text/babel" src="x.jsx">` 浏览器内编译。
- 末尾 `ReactDOM.createRoot(...).render(<XApp/>)`。
- 模块间靠 `_shared/guanlan-nav.js` 整页跳转(非 SPA)。

## 引用规矩

- 共享层:`../_shared/<file>`。同模块:裸文件名。跨模块:`../<module>/<file>`。
- 后端:`window.GUANLAN_BACKEND`(页面注入,默认同源)+ 端点路径。

## 改前端注意

- 改 jsx 必须 bump HTML 的 `?v=`(缓存)。
- 关键控件常显(不要只 hover 出现)。
- 详见 [../docs/dev_guide.md](../docs/dev_guide.md)。
