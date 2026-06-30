# guanlan MCP server

把帷幄的 `ww_*` 工具(去 2 个 console-UI-only)+ 7 个引擎 alpha-zoo 研究工具暴露成 MCP 工具(35 个),
供外部 MCP 客户端(别的 Claude / IDE 插件 / agent)驱动 guanlan。

## 两种传输(任选)
- **HTTP**:随 9999 后端一起跑,挂在 `http://127.0.0.1:9999/gl-mcp`。
- **stdio**:`python -m guanlan_v2.mcp`(本地客户端启动它)。

`example.mcp.json` 是两种的客户端配置样例。

## 与引擎 MCP 并存
9999 上 `/mcp` 是引擎自带 MCP(20 个引擎研究/dream 工具);本 server 是 `/gl-mcp`(35 个 guanlan 工具)。两者并存、各管各的。

## 写操作默认锁
写/销毁类工具(`ww_model_train/delete/set_default`、`ww_factorlib_save`、`ww_cards_save`、
`ww_seats_decide/bind`、`ww_update_data`、`ww_news_collect`、`ww_report_run`、`ww_etf_report_run`、
`alpha_forge`)默认**调不动**——外部客户端无帷幄确认弹窗,故需在 9999 启动环境设
`GUANLAN_MCP_WRITE=1` 后重启才放行。只读工具与 `ww_memory_write` 不受锁。
`list_tools` 始终列出全部并标注 `readOnlyHint`/`destructiveHint`。

## 无真下单
guanlan 是研究平台,无券商真实下单 → MCP 不暴露下单工具(诚实)。
