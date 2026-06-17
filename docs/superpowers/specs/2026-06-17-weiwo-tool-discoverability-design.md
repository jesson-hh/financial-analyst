# 帷幄工具可发现性补丁(#3 + #4)— 设计

**日期**:2026-06-17
**承接**:2026-06-17 帷幄中枢审查的工具发现侧两条挂账(记忆侧 #1/#2 已另案修复)。

## 目标

消除审计确认的两条「工具可发现性」裂缝:**#3** 系统提示词与工具表漂移(`ww_f10`/`ww_screen_factors` 未具名、无守护);**#4** 引擎工具结果里指向 `news_collect` 的悬空引用(console 调不到、撞模块门拿到误导信息)。两条都只改 `_SYSTEM_PROMPT`(`guanlan_v2/console/api.py:26-47`)+ 加守护测试,**不动 engine、不增删工具(守护计数 26/44 不变)**。

## 背景(审计确认)

- **#3**:`_SYSTEM_PROMPT` 具名了 24 个 ww_,但 `ww_f10`、`ww_screen_factors` 未具名;7 个引擎研究工具(`stock_brief`/`financials`/`news_query`/`quote_lookup`/`realtime_quote`/`wisdom_search`/`quant_reports`)仅以「行情/财务/新闻/经验检索等查询工具」泛指。无测试断言「每个 ww_ 工具都在提示词」→ 漂移无人看守(`ww_f10` 2026-06-16 入表却没同步进提示词)。缓解:schema 描述带触发语、全部 44 schema 喂 LLM,故是可维护性/可发现性缺口,非硬失败。
- **#4**:`news_collect` 在 engine `tools.py` 有 4 处文案指向(`news_query` 空结果 `:485`、staleness note `:463`、lhb `:784`、`stock_brief` 输出 `:1336`),其中多为 runtime 输出、会进 LLM 上下文。但 `news_collect` 不在 `CONSOLE_ALLOWED`(console 用 `ww_news_collect`)。LLM 照做调 `news_collect` → 撞模块门(`agent.py:455-466`)拿到固定误导语「该能力在『量化』模块」。

## 方案(用户已选)

### 改动 1(#3):提示词具名 + 守护测试
- 在 `_SYSTEM_PROMPT` 自省段(`api.py:34` 一带)补具名:
  - `ww_f10`(F10 基本面:估值/总股本/公告/龙虎榜两融/券商目标价)
  - `ww_screen_factors`(列因子库 id + IC,写选股 factors 前查)
- 把 `api.py:30` 的泛指句「以及行情/财务/新闻/经验检索等查询工具」点名高价值引擎工具:`stock_brief`(一键多维速览)、`financials`(财务基本面)、`news_query`(本地新闻库历史查询)。其余引擎工具(quote_lookup/realtime_quote/wisdom_search/quant_reports)仍泛指(schema 描述足够)。
- **守护测试**:断言 `WW_TOOL_TABLE` 里**每个 ww_ 工具名都出现在 `_SYSTEM_PROMPT`**。守护只钉 ww_(我们自己拥有、可控的);引擎工具不强制具名。

### 改动 2(#4):新闻路由纪律(不动 engine)
- `_SYSTEM_PROMPT` 加一条纪律(纪律 12):「任何工具结果提示『调 `news_collect` 刷新』时,实际改用 `ww_news_collect`(需确认);查本地历史新闻库用 `news_query`(只读);实时新闻情绪/快讯用 `ww_news_search`。`news_collect` 这个裸名字你调不到,别直接调。」一处覆盖 engine 里全部 4 个悬空点。
- **守护测试**:断言 `_SYSTEM_PROMPT` 同时含 `ww_news_collect` 与对裸 `news_collect` 的警示路由(防该纪律被误删)。

## 测试策略

- 单元:
  - `test_system_prompt_names_all_ww_tools`:遍历 `WW_TOOL_TABLE`,断言每个 name 子串在 `api._SYSTEM_PROMPT`(#3 守护)。
  - `test_system_prompt_routes_news_collect`:断言 `_SYSTEM_PROMPT` 含 `ww_news_collect` 且含对裸 `news_collect` 的路由/警示(#4 守护)。
  - 全量 523 保持绿;守护计数 26/44 不变(不增删工具)。
- 真机(独立验证 + 清理零残留):
  - #3:全新会话问「贵州茅台 600519 的总股本/有什么公告/券商目标价」→ 应选 `ww_f10`(现已具名)。读 events.jsonl 取证。
  - #4:新闻类提问 → 选 `ww_news_search`,不去调裸 `news_collect`(对比审计期 T2c 的过度取数)。
  - 杀 9999 重启加载新码;验后删测试会话、还原现场。

## 改动文件

| 文件 | 改动 |
|---|---|
| `guanlan_v2/console/api.py` | `_SYSTEM_PROMPT`:具名 ww_f10/ww_screen_factors + 点名 stock_brief/financials/news_query;加纪律 12 新闻路由 |
| `tests/test_console_api.py` | 2 个守护测试(ww_ 全具名、news_collect 路由) |

## 红线 / 非目标

- **不动 engine**(engine 里的 news_collect 文案保持原样,靠 console 提示词纪律覆盖)。
- **不增删工具**:守护计数 26/44 不变;提示词只补不删既有纪律。
- 非目标:提示词工具清单自动生成(本期手动具名 + 守护即可);engine 模块门误导信息文案优化(engine 侧,独立);news 三工具彻底去重(超范围)。
