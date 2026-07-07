# guanlan MCP server

把帷幄的 `ww_*` 工具(去 3 个仅 console 语境可用的,见下)+ 7 个引擎 alpha-zoo 研究工具
暴露成 MCP 工具(数量随 WW_TOOL_TABLE 派生,现 **57 个**,守护断言在 tests/test_guanlan_mcp.py),供外部 MCP 客户端(别的 Claude / IDE 插件 / agent)驱动 guanlan。

## 两种传输(任选)
- **HTTP**:随 9999 后端一起跑,挂在 `http://127.0.0.1:9999/gl-mcp`。
- **stdio**:`python -m guanlan_v2.glmcp`(本地客户端启动它)。

`example.mcp.json` 是两种的客户端配置样例。

## 与引擎 MCP 并存
9999 上 `/mcp` 是引擎自带 MCP(20 个引擎研究/dream 工具);本 server 是 `/gl-mcp`(guanlan 工具,现 54 个)。两者并存、各管各的。

## 排除的 3 个工具(为什么 MCP 里没有)
- `ww_plan_update` / `ww_show_page`:console-UI-only(改会话计划/往 console 右栏弹页面),MCP 无意义。
- `ww_seats_bind`:产 seat_bind 信封靠**前端 window.GL 落地**建盯盘 agent;MCP 语境无页面
  = 调了也不会发生任何事(空转假成功)→ 诚实排除。要建盯盘请经帷幄 console。

## 研报类长任务(MCP 通道真执行)
`ww_report_run` / `ww_etf_report_run` 在 console 里由事件循环起后台任务;MCP 通道没有该跑道,
由 `dispatch_tool` 检测 background 信封 → **detached 子进程真跑**(不随 MCP 客户端退出而死),
返回带 job id 与日志路径的受理凭证(`var/mcp_bg_<job>.log`)。启动失败会显形报错,绝不假成功。

## 写操作默认锁
写/销毁类工具(`ww_model_train/promote/validate/delete/set_default`、`ww_factorlib_save`、`ww_cards_save`、
`ww_seats_decide`、`ww_update_data`、`ww_news_collect`、`ww_report_run`、`ww_etf_report_run`、`ww_regen`、
`alpha_forge`、`ww_factor_promote`)默认**调不动**——外部客户端无帷幄确认弹窗,故需在 9999 启动环境设
`GUANLAN_MCP_WRITE=1` 后重启才放行。只读工具与 `ww_memory_write` 不受锁。
`list_tools` 始终列出全部并标注 `readOnlyHint`/`destructiveHint`。

## 无真下单
guanlan 是研究平台,无券商真实下单 → MCP 不暴露下单工具(诚实)。
