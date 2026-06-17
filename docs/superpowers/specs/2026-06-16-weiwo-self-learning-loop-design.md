# 帷幄自学回路设计(借鉴 Hermes,封闭后端安全版)

- 日期:2026-06-16
- 状态:已与用户对齐重组方向,待 spec 审阅 → writing-plans
- 关联:本会话「帷幄能力扩展 A/B/C」交付后([[weiwo-capability-expansion]]) + 对 NousResearch/hermes-agent 生态(hermes-agent / hermes-agent-self-evolution / learn-hermes-agent / portable tool-maker / camel)的研究综合

## 1. 背景

用户希望帷幄具备「自己扫描系统、运行期动态注册工具、自学新技能」的能力。研究 Hermes agent 后**转向**:Hermes 的"自升级"价值不在运行期造工具(那是开放世界 OS-agent 特性),而在 **turn 结束后 fork 一个受限沙箱复盘 agent 把经验写回记忆/技能** 的"自学回路",且其安全哲学是 **"agent 敢大胆是因为容器够稳能回滚"**(白名单沙箱 + 量化门 + 备份 + 人审,而非靠 agent 自律)。

观澜帷幄是**封闭已知后端的薄封装层 + 金融实盘平台**,因此:

- **维持三条非目标**:不做代码库自动扫描学技能、不做真·运行期热注册工具、不做 MCP/插件发现。Hermes 印证这些是开放世界特性,照搬只增风险。
- **采纳四个低风险重组**(本 spec 范围):阶段0 注册表数据化、阶段1 自学回路(受限后台复盘)、阶段2 帷幄记忆有界化;统一安全协议贯穿。

**现实警示(Hermes 教训)**:Hermes self-evolution repo 营销远超落地——reward 宣传 LLM-judge 实跑词重叠、pytest 门/PR/回滚多处"写了没接线"。**本设计每条安全门/触发条件都必须有守护测试实测验证"确实在跑/在拦",不接线不算完成。**

> 术语澄清:本 spec 的"记忆治理"指**帷幄自己的运行期记忆 `var/console/memory.md`**(`ww_memory_write` 写、阶段1 复盘会自动写),不是 Claude-Code 的项目 `MEMORY.md`(另一套系统)。

## 2. 目标 / 非目标

**目标**:
- 阶段0:把硬编码工具 `specs` 数据化成单一声明式注册表,四处(`CONSOLE_ALLOWED`/守护计数/`ww_capabilities`/`ww_endpoints` 可达性)自动派生。
- 阶段1:turn 后台受限沙箱复盘 agent,把"踩坑/能力缺口/可复用结论"写回三类**可人审/不碰交易信号**的通道,带 opt-in→monitor→enforce 三态 + fail-closed。
- 阶段2:`var/console/memory.md` 有界化(软上限 + replace 收敛写回序 + 离线 Curator 合并)。

**非目标(本 spec 不做)**:
- 真·运行期热注册/注销工具、对话内生效的能力扩张。
- 代码生成(写新 Python impl)——portable fork 的 Tool Maker(同进程裸跑 LLM 代码、查重只查名)是明确反面教材。
- 声明式 api-wrapper 注册(给端点 URL 生成薄包装)——最克制的运行期造工具,仍留作远期独立立项。
- MCP/插件发现、agentskills.io 技能标准、DSPy/GEPA 框架依赖。
- 代码库自动扫描学技能。
- 离线 GEPA 式 prompt/α 优化管线——独立开发期项目,本 spec 不含(见 §8 远期)。

## 3. 阶段0 — 注册表数据化(纯重构,前置)

**现状**:`register_console_tools()`(`guanlan_v2/console/tools.py`)的 `specs` 是硬编码元组列表;`CONSOLE_ALLOWED` 是独立 set 字面量;`ww_capabilities`/`ww_endpoints` 的 `_WW_REACHABLE_ENDPOINTS` 又是另一处;守护测试断言计数。新增工具须四处手工同步(spec §11 反复强调的坑)。

**重构**:引入单一声明式注册表——每条工具一个声明对象:
```
{ name, description, input_schema, impl, cost, confirm,
  is_ww: bool,                  # ww_ 自有 vs 放行的引擎工具
  reachable_endpoint: str|None  # 该工具触达的后端路径(供 ww_endpoints 可达性派生)}
```
派生:
- `register_console_tools()` 遍历该表注册 `is_ww` 的工具(经 `_wrap(impl)`)。
- `CONSOLE_ALLOWED` = 表中所有 name 的集合(ww_ + 放行引擎工具)。
- `_WW_REACHABLE_ENDPOINTS` = 表中非空 `reachable_endpoint` 的集合。
- `ww_capabilities` 读 `TOOL_REGISTRY ∩ CONSOLE_ALLOWED`(不变)。

**约束**:纯重构,**零新能力、零行为变化**;`register_console_tools()` 返回的 ww_ 计数、`CONSOLE_ALLOWED` 成员、守护测试计数(26 ww_ / 44 总 / 26 explicit_ww)**必须逐一不变**。守护测试 `test_engine_profile_excludes_ww_but_console_whitelist_resolves` 仍绿。新增一个测试断言"四处派生一致"(从注册表派生出的 `CONSOLE_ALLOWED` == 旧硬编码集合)。

## 4. 阶段1 — 自学回路(受限后台复盘)

### 4.1 触发
`_run_turn` 主对话跑完(`final_response` 且未被打断)后,统计本轮事件:`tool_calls`(本轮 tool_call 数)与 `had_failure`(任一 tool_result `ok=False` 或出现"能力缺口"信号,如 agent 调了白名单外工具被拦)。**触发条件 = `tool_calls >= 5` 或 `had_failure`**(阈值常量可调)。仅在自学开关非 `off` 时触发。

### 4.2 受限复盘 agent(沙箱)
经现有后台跑道(`_spawn_bg` 新增 `kind="review"` → `_run_review_bg`,镜像 `_run_etf_report_bg` 结构):
- fork 一个 `BuddyAgent`,**`allowed_tools` 白名单只给 `{"ww_memory_write", "ww_cards_save"}`**(对标 Hermes review fork 的 memory+skills 两工具;现有 `run_turn(allowed_tools=...)` 双门硬保证它**调不了任何第三个工具**)。
- 喂入:本轮对话快照(user_msg + assistant 文本 + tool_call/tool_result 摘要)+ 复盘 system prompt。
- 复盘 prompt 要点:只抽"用户纠正了风格/流程、出现非平凡技巧、遇到平台没有的能力/工具反复失败"四类信号;产物只用两个白名单工具写**三类通道**(见 4.3);宁缺毋滥,无可沉淀就不写。
- 运行预算小(`max_iterations` 低,如 8);daemon/executor;输出不混入主对话流。

### 4.3 写入边界(安全核心)
复盘 agent **只能写三类**,全部可人审、**绝不碰交易信号文本**:
1. **session notes**(`ww_memory_write` scope=session)——本会话任务笔记。
2. **global 缺口记忆**(`ww_memory_write` scope=global)——"平台缺某能力 / 某工具反复失败"。
3. **draft 经验卡**(`ww_cards_save` status=draft)——走已有 draft→approved **人审**门才生效。

**红线(硬保证,非靠自觉)**:
- 白名单只有 2 个工具 → 物理上**写不了** creed / 因子方法论 / α / 落子下单 / 影子组合 / `ww_factorlib_save`(这些工具不在复盘白名单内,allowed_tools 门硬拦)。
- `ww_cards_save` 复用 `claim_audit.unsourced_percents`(已落地)——未注明出处的数字断言 advisory。
- 复盘 agent 不被对话里混入的 news/f10/网页等外部料当指令驱动(prompt 明示 + 产物仅 draft/notes 本就低危)。

### 4.4 三态开关 + fail-closed
配置项 `review_mode ∈ {off, monitor, enforce}`(默认 `off`,会话级或全局):
- `off`:不触发复盘。
- `monitor`:**跑复盘但拦截其写操作**——把它"想写什么"作为事件 emit 给用户看,**不真落盘**(影子观测)。
- `enforce`:真写(notes/缺口/draft 卡)。
- **fail-closed**:复盘任何异常(LLM 失败/超时)静默吞掉,**绝不影响主对话**;`monitor`/`off` 下即便复盘逻辑出错也零副作用。

`monitor` 拦截实现:给复盘 fork 的两个白名单工具包一层"dry-run"——`monitor` 模式下 `ww_memory_write`/`ww_cards_save` 的 impl 不落盘只回"将写入 X"(经一个 review-mode ContextVar 控制),emit 成 `review_proposal` 事件。

### 4.5 审计
每次复盘产 `review` 事件:触发原因(tool_calls/had_failure)、mode、产物摘要(写了/将写哪几条)。落 events.jsonl。

## 5. 阶段2 — 帷幄记忆有界化

`var/console/memory.md`(global)与会话 notes 现状是自由 append(阶段1 会让它增长更快)。治理:
- **写回偏好序**(`ww_memory_write` 或一个新的归一层):先尝试 **replace 收敛**同主题既有条目 > 否则 append;**禁超长单条**(上限如 280 字);禁会话级命名/一次性内容进 global。
- **软上限**:global memory.md 超过阈值(如 8KB)时,写入前触发/提示合并。
- **离线 Curator 合并器**:一个可手动/周期触发的离线步骤(对标 `consolidate-memory` 思路),把碎片同类条目合并、过期条目**归档不删**(`var/console/memory.archive.md`),可恢复。纯离线、产物可人审。

## 6. 统一安全协议(贯穿,尤其阶段1)

1. **opt-in**:`review_mode` 默认 `off`,不改既有行为。
2. **monitor→enforce**:先影子观测(只 emit 不落盘)验证无误,再开 enforce。
3. **fail-closed**:自学失败=不写;自改失败=不生效。
4. **能力收口**:复盘 fork 工具白名单 = 2 个(Hermes 式),allowed_tools 双门硬拦。
5. **capability 污点意识**:敏感写工具(落子/影子/seats_bind/factorlib_save)本就不在复盘白名单;主路径它们的确认门已是 advisory+人 y/n。
6. **自改生效唯一通道**(git-less 等价 PR):守护测试全绿 + 两段评审(python-reviewer + 整合审查)+ 真机端到端 + 杀 9999 重启。
7. **红线复用**:`claim_audit` / `audit_flags` / PIT,不开新假料通道。
8. **门必须实测**:每条门有守护测试证明在拦(防 Hermes "写了没接线")。

## 7. 测试与验证

- **阶段0**:派生一致性测试(注册表派生的 `CONSOLE_ALLOWED` == 重构前集合)+ 守护计数 26/44/26 不变 + 全量 pytest 绿。
- **阶段1**:
  - 触发条件单测(tool_calls≥5 触发、<5 且无失败不触发、had_failure 触发)。
  - **复盘 agent 白名单只含 2 工具**——构造它尝试调第三个工具,断言被 allowed_tools 门硬拦(这是最关键的安全门,必须实测)。
  - `monitor` 模式:断言**不落盘**只 emit `review_proposal`;`enforce`:断言真写 draft 卡 + 缺口记忆。
  - fail-closed:复盘 LLM 抛错 → 主对话结果不受影响、无副作用。
  - 写入复用 claim_audit(draft 卡含未注明数字 → advisory)。
  - 红线:断言 creed/α/落子等工具不在复盘白名单。
- **阶段2**:replace 收敛(同主题二次写 → 替换非追加)、超长拒、上限触发、Curator 合并后归档可恢复。
- **真机**(deepseek,杀 9999 重启):制造一轮 5+ 工具调用 + 一次工具失败的对话 → `monitor` 下看 `review_proposal` 事件(不落盘)→ 切 `enforce` 重跑 → 确认 draft 卡/缺口记忆被写、且**没碰任何交易信号文本**。

## 8. 分阶段交付 + 远期

阶段0 → 阶段1 → 阶段2 顺序实现,每阶段:实现 → 两段评审 → pytest 全绿 → 真机验证 → 再下一阶段。一份 spec、一份分阶段计划(writing-plans 产出)。

**远期(独立立项,本 spec 不含)**:离线 GEPA 式 prompt/α 优化管线(优化 seats creed / 因子方法论 prompt / 选股 α;reward 用**确定性硬指标**=回测净年化/RankIC/calibration 命中率,不用 LLM judge/词重叠;train/val/holdout 严格分离;开发期跑,绝不在实盘对话内);声明式 api-wrapper 注册;真·运行期热注册。

## 9. 实现期待核定项(非阻塞)

- 复盘触发阈值(tool_calls≥5?)与"能力缺口"信号的精确判定(白名单外调用被拦 / 工具 ok=False / agent 文本含"做不到")。
- `review_mode` 配置落点(全局 env / 每会话 meta / 控制台开关)与默认值(确认默认 off)。
- `monitor` dry-run 拦截的实现位置(复盘专用 ContextVar vs 包装工具)。
- 阶段2 memory 软上限阈值与 Curator 触发方式(手动工具 / 周期 scheduler)。
- 复盘 system prompt 的具体措辞(借 Hermes 复盘 prompt 的"四信号 + 宁缺毋滥",但产物限三类)。

## 10. 备注

当前 cwd 非 git 仓库(`Is a git repository: false`),不做 `git commit`,仅落盘 `docs/superpowers/`。
