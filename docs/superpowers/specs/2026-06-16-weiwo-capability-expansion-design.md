# 帷幄能力扩展设计(A/B/C 三阶段)

- 日期:2026-06-16
- 状态:已与用户对齐设计,待 spec 审阅 → writing-plans
- 关联审计:本会话 11-agent 工具覆盖审计(`var/console` 与本仓代码实测;结论见会话记录)

## 1. 背景与问题

针对「帷幄(观澜 console agent)」做了全系统工具覆盖审计,三条结论:

1. **大量后端功能没有工具入口**。帷幄当前能调 24 个工具(17 个 `ww_*` + 7 个白名单 buddy 研究工具),但 guanlan 自有 67 个 HTTP 端点里只直达约 8 个;引擎原生约 40 个工具里只放行 7 个研究工具,其余 30+ 个被 `CONSOLE_ALLOWED` 白名单挡掉。被挡的真独有能力包括:问财自然语言选股 `iwencai_search`、资金流 `ths_fund_flow`/`fund_flow_change`、大盘 `market_status`、雷达/简报 `mainline_radar`/`overseas_radar`/`morning_brief`、批量行情 `quote_batch`、产业链 `chain_for`、行业 `industry_show`、数据抓取 `update_data`/`news_collect`、ETF 研报 `run_etf_report` 等。

2. **能理解、能真测临时因子,但建不成持久的真实因子**(判定 partial)。`ww_factor_analyze`/`ww_backtest` 走 guanlan `/factor/report2`+`/backtest/vector`,经引擎 `compile_factor` 在真面板上算真 RankIC(已对抗核验 confirmed,非假料)。但系统里能持久化真因子的 `POST /factorlib/save`(校验+落盘 `mined/`+运行期注册进 zoo registry,且 `/screen/run` 读该库)**没有任何 `ww_` 工具触达**;`/workflow/compose`(多因子合成)、`/feature/build`(物化真 X/y)同样调不到。后果:帷幄分析出一条好因子,无法入库、不跨会话复用、用不进选股。

3. **没有自省/自学机制**(判定 no)。工具集硬编码在 `register_console_tools`,系统提示词把工具清单写死;能「记住」偏好/沉淀经验卡(memory.md / notes.md / wisdom),但没有「枚举自己有哪些工具 / 后端有哪些能力 / 遇到不会的把缺口记回去」的回路。`list_tools()` 是死代码(未注册为可调用工具)。

## 2. 目标 / 非目标

**目标**:用同一套接线机制,分三阶段补齐上述三个缺口。

- A:接通已实现但无入口的能力(因子入库 + 一批引擎研究/数据工具)。
- B:补齐因子炼制工作流(合成、特征物化、字段词表、ETF 研报),形成「写 DSL→查字段→测 IC→合成→入库→选股」闭环。
- C:加两个自省 meta 工具 + 失败写回记忆的纪律,让帷幄「看见自己有什么」并诚实降级。

**非目标(本 spec 不做)**:

- 真正的「扫描整个代码库自动学会新技能 / 运行期动态注册工具 / MCP·插件发现」——架构性大改,独立立项。
- 个人账户类工具(雪球自选 `watchlist_show`、蛋卷基金 `fund_snapshot`/`fund_holdings`)——暴露真实账户,本期不开放。
- 盯盘价格阈值预警 `alert_add`/`alert_list`/`alert_remove`——本期不开放(与 `ww_seats_bind` 的 agent 式盯盘语义不同,留待单独讨论)。
- `ww_alpha_forge`——见 §4 决策②,刻意不做。
- 不修改 `engine/` 下任何文件。

## 3. 关键机制(贯穿三阶段)

帷幄工具门控是「注册 + 单一白名单」两步链(已实测):

1. 9999 进程起 `BuddyAgent` 时 `register_console_tools()`(`guanlan_v2/console/tools.py:640`)把 `ww_*` 工具的 `Tool` 字面量幂等追加进引擎共享 `buddy.TOOL_REGISTRY`。
2. 每轮 `run_turn(..., allowed_tools=CONSOLE_ALLOWED)`(`console/api.py:508`)。引擎侧 `CONSOLE_ALLOWED` 产生两道门:`_tool_schemas(allowed)`(`engine/.../buddy/agent.py:267`,白名单外 LLM 看不到)+ 执行兜底门(`agent.py:451`,硬调也拦)。
3. `profile`/`FACTOR_TOOL_NAMES` 那套门控只服务已退役的 `/run` chat 页,**对帷幄无效**,且刻意剔除 `ww_*`——帷幄只走 `CONSOLE_ALLOWED`。

**开放新功能给帷幄的标准操作**:

- 新 `ww_` 工具:`register_console_tools` 的 `specs` 里加一条 `(name, desc, schema, _wrap(impl), cost, confirm)` + 把名字加进 `CONSOLE_ALLOWED`(两处缺一不可,只加白名单不注册会 `Unknown tool`)。
- 放行已注册的引擎工具:只把名字加进 `CONSOLE_ALLOWED` 一处即可(它已在 `TOOL_REGISTRY`)。
- 两种情况都应同步更新 `_SYSTEM_PROMPT` 工具清单/纪律,否则 LLM 不知该工具存在;并更新守护测试。

**确认门**(已与用户对齐):写持久状态 / 外部抓取的工具 `confirm_required=True`;只读取数工具免确认。确认机制是 `Tool.confirm_required` 静态属性,执行前经 `confirm_callback` 弹窗。引擎研究工具的原生 `confirm_required` 实测如下——`run_report`/`run_etf_report`/`alpha_bench`/`factor_test`/`alpha_compare`/`alpha_forge`/`factor_report`/`event_report`/`factor_compose`/`wisdom_review` 为 True;`update_data`/`news_collect`/`iwencai_search`/各类 fund flow/`market_status`/雷达/简报为 False。

**因子双库(决定 B 的落点)**:引擎工具(`alpha_forge save`、`factor_compose`)写引擎 `UserFactorStore`;guanlan `/factorlib/save` 写 `mined/` 并注册进 zoo registry,且 **`/screen/run` 只读 guanlan 这条库**。为让「建的因子能流进选股」,本期一律走 **guanlan 因子库/工作流这一条 lane**(与已有 `ww_factor_analyze`/`ww_backtest` 同源)。

## 4. 已对齐的判断决策

- **① 走 guanlan 因子 lane**:`ww_factor_compose`→guanlan 合成端点、`ww_feature_build`→`/feature/build`;不走引擎 `factor_compose`(写错库,`/screen/run` 看不到)。
- **② 不做 `ww_alpha_forge`**:`alpha_forge` 把自然语言炼成 DSL 并存进*引擎*库,而帷幄本身就是会写 zoo DSL 的 LLM。coherent 流是「Weiwo 写 DSL → `ww_factor_analyze` 测 → `ww_factorlib_save` 入 guanlan 库」。加 `ww_alpha_forge` 既冗余又写错库。
- **③ 用 `ww_factor_fields` 而非扩 `FACTOR_SEMANTICS`**:当前失败模式是 Weiwo 猜错字段名 → `validate_expr` 失败。`FACTOR_VOCAB`(`engine/.../factors/zoo/expr.py:11`)已含字段中文名/方向/频率/口径 + 算子全集;`ww_factor_fields` 直接把它(+ `_FIELD_NAMES`/`_OP_NAMES` + 几条范例)回给 Weiwo,治本于「理解并搭真实因子」。扩 `FACTOR_SEMANTICS` 只惠及 seats 决策路径,非因子组合,本期不做。

## 5. Phase A — 接通已有能力

### A1. `ww_factorlib_save`(新 ww_ 工具,confirm)

- 落点:guanlan `POST /factorlib/save`(`guanlan_v2/factorlib/api.py:119`,`SaveIn`: `name/expr/family/description/source/is_qlib/meta`)。
- impl `factorlib_save_impl(name, expr, family="library_mined", description="", source="帷幄 · ww_factorlib_save", is_qlib=False)` → `_self_post("/factorlib/save", ...)`。
- 诚实口径:透传后端 `ok` 与 `registered`(落盘成功即 `ok:True`,运行期注册是否生效看 `registered`);重名/非法 expr → 后端 `ok:False` + reason,原样回。
- artifact:`page="factor"`(可顺带弹工作流界面)。
- `confirm_required=True`(写持久状态)。

### A2. 直接加 `CONSOLE_ALLOWED` 的只读引擎工具(无需包装)

`iwencai_search`、`ths_fund_flow`、`fund_flow_change`、`ths_concept_board`、`market_status`、`mainline_radar`、`overseas_radar`、`morning_brief`、`quote_batch`、`chain_for`、`industry_show`。均已注册、原生 `confirm_required=False`、只读,白名单一行放行即可。

### A3. `ww_update_data` / `ww_news_collect`(薄 ww_ 包装,confirm)

引擎 `update_data`/`news_collect` 原生 `confirm_required=False`,而用户要求外部抓取需确认。为不改 `engine/`,各包一层薄 ww_ 工具:`confirm_required=True`,impl 经 `_buddy_tools_mod().get_tool("update_data").run(**args)` 代理执行并透传 `ToolResult`。schema 对齐引擎工具入参(实现期照抄引擎 `input_schema`)。引擎原 `update_data`/`news_collect` 不进白名单(只暴露 ww_ 包装版)。

## 6. Phase B — 因子炼制工作流

### B1. `ww_factor_compose`(新 ww_ 工具,免确认)

- 落点:guanlan 多因子合成端点(`FactorComposeIn`: `members[]`/`method∈{equal,ic,icir}`/`universe`/`oos_frac`/...;具体路由 `/factor/compose` vs `/workflow/compose` 实现期照代码核定)。
- impl 收 `members`(zoo 表达式或注册名列表)+ `method` → 自调端点 → 摘要回 OOS RankIC/ICIR + 各腿权重。
- 免确认(只评测不入库)。artifact `page="factor"`。

### B2. `ww_feature_build`(新 ww_ 工具,免确认)

- 落点:guanlan `POST /feature/build`(`FeatureBuildIn`: `features[]`/`label`/`fwd_days`/`universe`/`codes`/`oos_frac`/...)。
- impl 收特征表达式列表 + 标签(缺省前向收益) → 自调端点 → 摘要回 n_dates/n_codes/coverage/逐特征 IC。
- 免确认。

### B3. `ww_factor_fields`(新 ww_ 工具,免确认)

- 纯进程内:返回 `FACTOR_VOCAB` 文本 + `_FIELD_NAMES`/`_OP_NAMES`(分价量/基本面/技术/财务/参照 + 算子)+ 3-5 条 canonical 范例(如 `rank(-delta(close,20))`、`-stddev(returns,20)`、`rank(roe)`、`regbeta(returns,idx_ret,60)`)。
- 目的:Weiwo 写 DSL 前查合法字段/算子,治「猜错字段名 → validate 失败」。诚实口径:这是 DSL 词表,不是完整方向语义。

### B4. `ww_etf_report_run`(新 ww_ 工具,confirm)

- 落点:引擎 `run_etf_report`(对标 `ww_report_run`)。impl 返回 background 信封,由 console api 后台跑道执行(复用 `ww_report_run` 的后台跑道模式);或薄包装代理引擎工具(实现期取与 `ww_report_run` 一致的方式)。
- `confirm_required=True`(5-8 分钟重任务)。

### 闭环

`ww_factor_fields`(查字段)→ Weiwo 写 zoo DSL → `ww_factor_analyze`(测 IC)→ `ww_factor_compose`(合成,可选)→ `ww_factorlib_save`(入 guanlan 库 + 注册)→ `ww_screen_run`(按 id 用进选股)。

## 7. Phase C — 自省 / 自学治本

### C1. `ww_capabilities`(新 ww_ 工具,免确认)

- 纯进程内:取 `TOOL_REGISTRY` ∩ `CONSOLE_ALLOWED`,列每个工具的 name/description/confirm_required/cost_hint。
- 目的:Weiwo 运行期能枚举「自己当前能调哪些工具」。

### C2. `ww_endpoints`(新 ww_ 工具,免确认)

- 落点:`GET /openapi.json`(`_self_get`)→ 汇总 path+method+summary。
- **诚实标注可达性**:对每个端点标「我可经某 ww_ 工具直接调 / 仅界面可达我调不到」(用一张 ww_→端点的静态映射判定)。目的是回答「观澜平台能做什么」+ 诚实降级(「平台有 X 功能但我目前调不到,需在界面用」),**不冒充能调**。
- 输出做精简/分组(端点上百,只回 path+method+一句话+可达标记),必要时按前缀分组。

### C3. 失败 → 记忆纪律(提示词,无新工具)

`_SYSTEM_PROMPT` 加纪律:①不确定自己能做什么 → 先 `ww_capabilities`/`ww_endpoints` 自查;②遇到平台没有的能力 / 工具失败 → `ww_memory_write` 把缺口记下(session 或 global),供后续人工补能力。仍守纪律 2(失败诚实报,不装成功)。

## 8. 横切关注点

- **接线**:新 ww_ 工具走 `register_console_tools` specs + `CONSOLE_ALLOWED` 两处;纯白名单项只改 `CONSOLE_ALLOWED`;同步 `_SYSTEM_PROMPT`。
- **不动 engine/**:全部落 `guanlan_v2/console/`;薄包装经 `get_tool(name).run()` 代理,只在 ww_ 层加确认门与 artifact。
- **诚实**(守红线):`ww_factorlib_save` 回真 `registered`;`ww_endpoints` 标可达性不冒充;失败 `ok:False` 不装成功;`ww_factor_fields` 标明是词表非完整语义。
- **artifact/页面**:因子类工具 `page="factor"`,新闻/数据类 `channel="console"` 无页面跳转,避免误弹界面。

## 9. 测试与验证

- 每个新 impl 纯逻辑单测(参数归一、错误分支、摘要渲染),monkeypatch `_self_get`/`_self_post`/`get_tool`。
- `CONSOLE_ALLOWED` 计数/成员守护测试更新;`test_engine_profile_excludes_ww` 仍须绿(profile 路径不外露 ww_)。
- 薄包装代理:测 `get_tool` 被以正确参数调用、`confirm_required=True`、`ToolResult` 透传。
- `ww_capabilities`/`ww_endpoints`:测交集/可达标注正确。
- 真机 9999 deepseek 端到端验各阶段一条主链:A=「把 `rank(-delta(close,20))` 存进因子库」→ 看 `registered=True` 且 `ww_screen_factors` 能查到;B=「合成动量+反转两因子并看 OOS」+「这条 DSL 字段对不对」;C=「你现在能调哪些工具 / 观澜平台能做什么」。
- 改后端须按 PID 杀 9999 监听者等端口释放、看门狗自动拉新代码(运维坑见项目记忆)。

## 10. 分阶段交付

A → B → C 顺序实现,每阶段独立:实现 → 两段评审(python-reviewer + 整合审查)→ pytest 全绿 → 真机端到端验证 → 再进下一阶段。一份 spec、一份分阶段计划(writing-plans 产出)。

## 11. 实现期待核定项(非阻塞)

- guanlan 多因子合成具体路由(`/factor/compose` vs `/workflow/compose`)与确切入参字段。
- `ww_etf_report_run` 取「后台跑道信封」还是「薄包装代理」——与 `ww_report_run` 现行实现保持一致。
- `ww_update_data`/`ww_news_collect` 的 schema 是否需裁剪引擎全量入参(防 Weiwo 误触 `all` 全市场重拉)。
- `ww_endpoints` 的 ww_→端点可达性映射表的维护方式(静态常量,随新增工具同步)。

## 12. 备注

- 当前工作目录不是 git 仓库(`Is a git repository: false`),故本 spec 不做 `git commit`,仅落盘 `docs/superpowers/specs/`。
