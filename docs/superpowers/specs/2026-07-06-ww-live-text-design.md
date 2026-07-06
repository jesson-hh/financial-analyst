# ww_live_text:stocks 实时文本源 13 端点接进帷幄(薄壳子进程)设计

- 日期:2026-07-06
- 缘起:stocks 侧同日新增只读 live facade(`G:\stocks\src\data\live_text_sources.py` + `scripts/probe_live_text_sources.py`,13 端点)。审查坐实:个股新闻/快讯两路与 `ww_news_live` 同源同端点(观澜零改动);**实时公告/互动易/研报元数据/涨停题材热度=帷幄 agent 够不着的增量**(帷幄无 Bash,probe CLI 不可达)。用户拍板:「加 ww_live_text 薄壳 把13个端点都接进帷幄」。
- 红线:准确不幻觉;失败诚实降级(空+note,不编造);**零重造**(端点归 stocks 所有,观澜只做子进程薄壳);只读(probe `write_enabled:false`,不落盘)。

## 1. 一句话

`ww_live_text(source, code?, date?, limit?)` = 子进程起**后端同解释器**跑 stocks 的 `probe_live_text_sources.py --json`(cwd=G:\stocks),把 13 个实时文本/题材/互动端点 + catalog 自省开放给帷幄 agent,rows 透传(剥 `raw`、截长文),秒级返回。

## 2. 已定决策

1. **子进程薄壳,不 import stocks 代码**:`sys.path` 注入 `G:\stocks` 有 `src` 包名撞名风险;probe CLI 是 stocks 官方 agent 入口(其 doc 明示)。解释器用 `sys.executable`(生产=pinned venv pandas 3.0.3,**已真机验证**能跑 probe:catalog import 链 OK + stock_news 真网拉 2 条)。
2. **14 个 source 白名单**(13 端点 + `catalog` 自省):`catalog / stock_news / global_news / research / industry_research / cninfo_announcements / concept_blocks / ths_hot_reason / em_zt_pool / ths_limit_up_pool / cninfo_irm / ths_hot_list / em_hot_rank / em_hot_concept`。
3. **参数复用语义照搬 probe CLI**:`code` 对 `ths_hot_list` 是榜期(hour/day…,缺省 hour)、对 `industry_research` 是行业代码(缺省 `*`)、其余为 6 位股票代码(SZ000630/000630 均收,壳内取 6 位);`date` 对 `em_zt_pool`/`ths_limit_up_pool` 是 YYYYMMDD(**缺省补当日**,否则 API 静默空)、对 `ths_hot_reason` 缺省交 facade 自默认(YYYY-MM-DD)。
4. **上下文卫生**:rows 剥掉 `raw` 键(体积大且信息已归一);`text`/`summary`/`content` 类长字段截 400 字加省略号(截断非编造)。limit 夹 [1,50]。
5. **降级 vs 拒绝**:caller 错误(source 不在白名单/该 source 必填 code 而缺/date·limit 形态非法)→ `ok:False` 明说;外部失败(probe 缺席/超时 90s/非零退出/JSON 脏)→ `ok:True, rows:[], note` 诚实记因,恒不编造(与 ww_news_live 同约)。
5b. **(评审对抗核实后增补,Critical 修)content 自带全部 rows**:console 通道 LLM 只见 `_wrap` 产出的 `ToolResult.content`,无 content 键时兜底 `json.dumps(...)[:400]` 会把结果截成断裂 JSON(幻觉面)。故 impl 组装 content=头行+逐行 JSON(全量,行内已剥 raw+字段截 400);`ww_news_live` 同构存量缺陷随本批一并修(content=头行+逐条标题)。信封级测试穿真 `_wrap` 锁死。
5c. **(评审真机坐实,Important 修)date 归一**:涨停两池非空 date 剥非数字须恰 8 位否则 `ok:False`(上游对 ISO 格式静默返空,会被 0 行 note 伪装成正常);`ths_hot_reason` 的 date 归一为 YYYY-MM-DD(其值进上游 URL 路径,归一顺带封死注入面)。argv 用 `--opt=value` 形态防 `-` 开头值被 argparse 吞;catalog 不受 limit 截(承诺列全部端点)。
6. **四点同步**:本 spec + `CONSOLE_ALLOWED`(WW_TOOL_TABLE 注册)+ `_SYSTEM_PROMPT`(roster 行 31 追加 + 规则 7 扩句)+ 守护计数 **49/74/53 → 50/75/54**(test_console_tools 613/619/620/1084/1086 五处 + test_guanlan_mcp 13/71/100 三处;MCP 表自 WW_TOOL_TABLE 派生自动 +1)。

## 3. 接口(公有契约)

- 工具:`ww_live_text(source, code="", date="", limit=20)`,cost seconds,confirm False,reachable []。
- 返回:`{ok, source, code, date, rows[≤limit], n, note, pulled_at}`;`rows` 形状=probe 原样(LiveTextItem dict 或各端点专有 dict),仅剥 `raw`+截长文;`catalog` 返能力目录。
- 子进程:`[sys.executable, G:\stocks\scripts\probe_live_text_sources.py, --source, S, --code, C, --date, D, --limit, N, --json]`,cwd=`G:\stocks`,`PYTHONIOENCODING=utf-8`(GBK 坑),timeout 90s(东财串行限流 1s/请求 + cninfo orgId 首拉全表)。

## 4. 诚实降级表

| 情形 | 行为 |
|---|---|
| source 不在白名单 | `ok:False`,列出合法值 |
| 必填 code 缺(stock_news/cninfo_announcements/concept_blocks/cninfo_irm/em_hot_concept/research) | `ok:False` |
| probe 脚本不存在(G:\stocks 缺席) | `ok:True, rows:[], note:"stocks probe 不可用"` |
| 子进程超时/非零退出/stdout 非 JSON | `ok:True, rows:[], note` 带 stderr 尾 |
| 端点返回空(非交易日/无数据) | `ok:True, rows:[], note:"该源当前无数据"` |

## 5. 测试(注入桩,离线)

1. 注册面:`ww_live_text ∈ CONSOLE_ALLOWED`,schema 四参,confirm False。
2. happy:桩 subprocess.run 返带 `raw`+长 `text` 的 JSON → rows 剥 raw、text 截 400、n 对。
3. 校验:未知 source → ok:False;stock_news 缺 code → ok:False。
4. 降级三态:TimeoutExpired / returncode≠0 / stdout 脏 → 恒 ok:True + rows [] + note 记因。
5. 守护计数 50/75/54 全绿。
6. 真机验收(有网,生产 venv):≥2 端点真拉(公告/互动易)。

## 6. 验收

- 帷幄 agent 调 `ww_live_text(source="cninfo_irm", code="000630")` 秒级返真互动易问答,且 **content 经真 `_wrap` 全量可见**(信封级真机验证:5 条问答 1520 字无截断);
- `mode=pit` 零改动(既有测试为证);`ww_news_live` 仅补 content(同构缺陷修,items/freshness 契约不变);全量 pytest 绿;9999 重启吃新工具。

## 7. 评审结论(wf_ceac205a-a06,四镜头+每发现 3 反驳者)

5 条成立全修(3 Critical 同根=content 缺失截 400/1 Important date 归一/1 Important ww_news_live 同构)、0 条误报、8 Minor 全处理(2 条 stocks 侧挂账:orgId 全表重拉、facade 限流跨进程失效——见记忆)。

## 已知限制(随 stocks 侧)

东财风控须串行低频(壳不并发、不重试轰炸);涨停池/热点归因非交易日空;互动易未回复 `answer` 空属正常;研报只有元数据+PDF URL;北交所票 `em_hot_concept` 前缀映射缺陷在 stocks 侧(已在记忆挂账)。
