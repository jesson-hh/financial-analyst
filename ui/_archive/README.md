# _archive — 旧设计稿 / 变体(不接线、不进导航)

设计探索期留下的旧页面与变体。**不属于五模块,不接后端,不进全局导航**,仅作参考。需要某个变体就把它迁回对应模块再接线。

## 清单
| 文件 | 说明 |
|------|------|
| 观澜 · 投研台.html | ObservatoryApp 的旧版投研台布局。 |
| 观澜 · 投研台重设计.html | 投研台重设计稿。 |
| 观澜 · 界面探索.html | 早期界面探索(`App`)。 |
| 观澜 · 量化研究.html | 量化研究专栏稿。**无 render 调用,打开是空白页**(已知不完整 stub)。 |
| design-canvas.jsx + .design-canvas.state.json | 设计画布工具。 |
| variant-pavilion / report / stream / stream-geek / stream-tech / stream-v2 .jsx | 6 个布局/流式变体。 |

## 注意
不要在这些文件上继续开发。真要用,迁回 graph/chat/factor/cards/seats 之一,补 render + 共享层引用 + nav 入口。
