# GuanLan UI — embedded for Tauri packaging

观澜 (GuanLan) 前端的副本, 给 Tauri build 用. **原始 source 仍在
`G:/stocks/fa_ui_ready/`**, 这里是为了 Tauri 独立打包需要.

## 同步策略

任何 `G:/stocks/fa_ui_ready/` 的改动**必须手工同步**过来:

```bash
# 一键同步
cp G:/stocks/fa_ui_ready/{index.html,app.jsx,agent-adapter.jsx,shared.jsx,tokens.css} \
   G:/financial-analyst/packaging/src-tauri/ui/
```

或者用 git submodule (P5 考虑).

## 文件

- `index.html` — 入口, 含 `window.GUANLAN_BACKEND` 后端开关 (默认 http://127.0.0.1:9999)
- `app.jsx` — 主应用 (设计接好的 6 端点全接, ~30K LOC)
- `agent-adapter.jsx` — SSE 适配器
- `shared.jsx` — 共用组件
- `tokens.css` — 设计 tokens

## Tauri 配置接线

`tauri.conf.json::build.frontendDist = "../ui"` → Tauri build 时直接打包这个目录.

`devUrl = "http://localhost:5173"` + `beforeDevCommand = "cd ../ui && python -m http.server 5173"`
→ `cargo tauri dev` 时自动起 http 服务器 + 加载.

## 后端连接

`index.html` 第 65 行 `window.GUANLAN_BACKEND = 'http://127.0.0.1:9999'` —
Tauri 启动时通过 sidecar 配置 (`tauri.conf.json::bundle.externalBin`) 自动起
`financial-analyst.exe serve --port 9999`, 前端无需改动.
