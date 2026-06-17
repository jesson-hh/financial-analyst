# 观澜 V2 开发指南

## 起服务

```bash
# 方式 A:直接跑(推荐)
G:/financial-analyst/.venv/Scripts/python.exe G:/guanlan-v2/guanlan_v2/server.py
# → http://127.0.0.1:9999/

# 方式 B:Claude_Preview(launch.json 已配 guanlan-v2,端口 9999)
```

环境变量:
- `GUANLAN_FA_SRC` — 引擎源目录(默认 `G:/fa-watch-wt/src`)。**决定用哪套引擎**。
- `GUANLAN_HOST` / `GUANLAN_PORT` — 监听地址/端口(默认 127.0.0.1 / 9999)。

启动 stderr 会打印 `[guanlan_v2] engine source: <path>` —— 核对它指向 fa-watch-wt(否则被旧 editable 安装压过,会缺 recipe/watch 端点)。

## 加一个新页面

1. 在对应模块文件夹建 `观澜 · X.html`。
2. `<head>` 引共享层:`<link rel="stylesheet" href="../_shared/tokens.css">`。
3. `<body>` 末尾按需引:`../_shared/guanlan-bus.js`、`../_shared/guanlan-nav.js`、`../_shared/shared.jsx`、本模块 `x.jsx`。
4. `ReactDOM.createRoot(document.getElementById('root')).render(<XApp/>)`。
5. 若要进全局导航:在 `_shared/guanlan-nav.js` 的 `MODULES` 加一项 `{label, file:'../<module>/观澜 · X.html'}`。
6. **所有本地 src/href 用相对路径**:同模块裸名,跨模块 `../<module>/<file>`,共享 `../_shared/<file>`。

## 加后端能力

- **加到引擎**(`G:/fa-watch-wt/src/financial_analyst/buddy/server.py`),不要加到 V2 薄壳。V2 `build_app()` 自动拿到。
- 前端 fetch:`window.GUANLAN_BACKEND + '/your/endpoint'`(`GUANLAN_BACKEND` 由页面注入,默认同源 `window.location.origin`)。

## 缓存陷阱(必读)

无构建 → 浏览器缓存 jsx。改完 jsx,普通 F5 可能还是老版。两种解法:
- 改 HTML 里 jsx 引用的 `?v=` 查询串(`src="x.jsx?v=2"`)。
- 或硬刷新(Ctrl+Shift+R)。

## 调试

- **截图会超时**:这些页面有无限 CSS 动画(`@keyframes`),renderer 一直忙,`preview_screenshot` 会 timeout。**用 `preview_eval`** 读 DOM 状态(`#root` 子节点数、`fetch` 端点状态、iframe 渲染)。
- 验证渲染:`document.getElementById('root').childElementCount > 0`。
- 验证后端:在页面里 `fetch(location.origin+'/ui/<module>/<page>')` 看 200。

## 中文文件名

- 页面名含中文 + 空格 + `·`。浏览器自动 URL 编码,Starlette StaticFiles 解码,正常工作。
- 命令行/脚本里操作这些文件:让 Python 从 `os.listdir` 读盘,别把中文经 stdin 管道给 python(Windows 会乱码)。

## 验证纪律

- 子代理的"测试通过"自述**不可信**,控制端必须独立复核(读文件 / preview_eval / 跑命令)。这条在 V2 之前的工作里抓出过引擎身份 bug 和空白页谎报。
