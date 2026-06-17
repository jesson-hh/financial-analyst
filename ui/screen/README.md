# screen — 九视角选股(A1)

本模块单页:`观澜 · 选股.html`(选股 2.0,因子混合 α + v4 模型 + 九视角)。

## 页面:观澜 · 选股

| 项 | 值 |
|----|-----|
| 页面 | 观澜 · 选股.html(`screen-app.jsx?v=20260612a`) |
| 入口组件 | `ScreenApp`(screen-app.jsx) |
| 数据层 | `screen-data.jsx`(因子目录 `XG_FBYID`、handoff/take 通道) |
| 后端 | `guanlan_v2/screen/api.py`:POST /screen/run(v4 + 因子混合 α);GET /screen/factors(全目录 95 因子) |

## 更新日志

**2026-06-13 · 导航摘除注记**:本页已从导航摘除;通过帷幄 `ww_show_page` 工具(口头调出右栏视图)或直链(`/ui/screen/观澜 · 选股.html`)访问;`?embed=1` 嵌入卫生一期已就绪(见下方融合批)。

**2026-06-12 · 帷幄融合批(`screen-app.jsx?v=20260612a`)**:
- **WW 旗**:页头身份区(`WW_EMBED`)在 `?embed=1` 时隐藏,被帷幄嵌入时不出现重复顶栏。`WW_LEGACY = ?legacy=1` 找回全部默认隐藏的 agent 控件。
- **「一句话调约束」「LLM 选因子」全局隐藏**:顶栏 agent chip + 左栏 LLM 框默认不渲染(`!WW_LEGACY`);`?legacy=1` 找回(spec §3.7;帷幄 `ww_screen_run` 工具替代)。
- **`take('screen')` 接完整 cfg**:帷幄通过 `GL.handoff('screen', {cfg:{factors,pool,blend,topN}})` 传入整套选股配置;页面 mount 时 `GL.take('screen')` 收 `{cfg}` 后写入 state 并调 `refresh()` 真重算。注意:cfg 写入 state 不自动触发重算,必须等 `refresh()` 显式调用(依赖 tick);刷新恢复时 iframe 重载会按 agent 传入的 cfg 重算,出与汇报一致的结果。
