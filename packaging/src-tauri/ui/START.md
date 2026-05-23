# 觀瀾 — 一键可跑 (已接真后端 + 修好模型 bug)

这是从 design 的 canvas 抽出来的**真正接好后端**的那套 (不是过期的
export/ mock). 我已修好模型切换的 2 个 bug.

## 跑 (连真后端)

```powershell
# 1. 起后端 (fastapi/uvicorn 已装进 .venv)
G:\financial-analyst\.venv\Scripts\financial-analyst.exe serve --port 9999
#    浏览器开 http://127.0.0.1:9999/health 应返回 {"ok":true,"version":"1.9.4",...}

# 2. 开前端后端开关: 编辑 index.html 第 64 行, 取消注释:
#    window.GUANLAN_BACKEND = 'http://127.0.0.1:9999';

# 3. 起前端
cd G:\stocks\fa_ui_ready
python -m http.server 5173
#    浏览器开 http://localhost:5173
```

不开第 2 步的开关 = 跑本地 mock (6 只股票演示), 不依赖后端.

## 文件

| 文件 | 说明 |
|---|---|
| index.html | 入口 (含后端开关) |
| app.jsx | 主应用 (design 接好的, 6 处端点都接了) |
| agent-adapter.jsx | SSE 适配器 (我修了 model 漏传) |
| shared.jsx | 共用组件 |
| tokens.css | 设计 tokens |

## 测试时盯这些 (校对已确认接对的)

- 输入"看下茅台怎么样" → 真 agent → 工具链 + 速览卡 + §N 引用
- 追问"它同行呢" → 多轮上下文 (后端按 session 复用)
- "茅台跌破1200提醒我" → 写后端 → 盯盘面板出现 → 盘中触发弹真 toast
- /mode safe → 每个工具调用前弹 y/n
- 状态栏切模型 → 下一轮换该模型 (本次修复的 bug)
- 自选股墙价格每 4 秒刷新 (腾讯行情)
- "跑茅台研报" → 抽屉真全文 (5-8 分钟)

## 真数据前提

- LLM: `.env` 里 `DASHSCOPE_API_KEY` (已有)
- 实时行情/资金流: 腾讯行情免 cookie ✓; 雪球/同花顺 F10 需 Chrome cookie
- 同花顺扩展工具 (问财/资金流): `opencli plugin install file://G:\financial-analyst\opencli-plugin-ths-extra`

## 注意

这套是从 design canvas 顶层抽的. 如果你后续让 design 继续改 UI, 记得把
`agent-adapter.jsx` 的 model fix (body 里 `model: model || null`) 同步过去,
否则 design 重新导出会覆盖.
