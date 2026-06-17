# cards — 经验卡(A3)

| 项 | 值 |
|----|----|
| 页面 | 观澜 · 经验验证区.html |
| 入口组件 | `ValidationApp`(validation.jsx) |
| 后端 | guanlan 自有 `/cards/*`(`guanlan_v2/cards/`,挂在薄壳;**非**引擎 wisdom) |
| 闭环位置 | research + factor → **card(验证)** → seat |

## 职责
经验卡的**验证区**:把研报/因子炼成的"经验卡"做验证、打标,沉淀成可复用方法论。每张卡含:`title`、`cat`(类别:价量/资金/基本面/情绪/风格…)、`tags`、`verdict`(通过/存疑/驳回)、`conf`(置信 0-100)、`ic`、`insight`(洞察)、`expr`(因子表达式)、`src`(来源类型)、`refs`(来源研报+因子)、`status`(draft/approved/rejected)。

## 三桶本地库(`.data/wisdom`,markdown 落盘)
- **未验证(draft)** ← 左栏「素材库·待提炼」。已灌入 **12 条真·视频经验**(B站 UP 笔记,经引擎 `wisdom.migrate.parse_notes_markdown` 解析自 `G:/stocks/strategy/wisdom/bilibili_notes.md`;**源文件只引用不复制**,解析出的卡写进本地库)。`GET /cards/list?status=draft`(validation.jsx 的 `loadSources` → `cardToSource`)。
- **已验证·通过(approved)** ← 右栏「经验知识库」。`refreshKb` 调 `GET /cards/list`(后端 `status` 默认 approved),右栏「全部/通过/存疑」筛选在**前端按 `verdict`** 做;**不再用 `INITIAL_KB` mock**。
- **存疑(rejected 目录)** ← 验证后「存疑/留观」的卡;**「驳回」暂并入此桶**(当前只有 draft/approved/rejected 三态,非四态)。
- 落盘:`GUANLAN_WISDOM_ROOT`(默认 `guanlan-v2/.data/wisdom/{draft,approved,rejected}/EV-*.md`)。经验卡是 guanlan 自有应用数据,**不是 stock_data**,不经 get_data_paths。

## 流转(原 → 炼 → 验 → 用)
- **原**:点左栏未验证卡 →「采集原文」展示 4 段式(经验/适用条件/操作建议/反例·边界)+ 来源 BV。
- **炼**:与观澜对话修改 → `POST /cards/refine`(引擎大模型 **deepseek**,带基础 system prompt,真自然语言改写,实测 ~3s);引擎不可用(无 key/代理)时回退本地规则 `mockRevise`,不卡流程。**`expr` 受 DSL 词表约束**:`refine.py` 基础 prompt 内置因子表达式 DSL 白名单(价量/基本面字段 + `rank`/`ts_mean`/`correlation` 等算子),炼出的 `expr` 只能用清单内字段/算子(否则引擎编译报错),难量化的留空 —— 即「验」步白名单 grounding 的源头。
- **验**:**真·单因子回测** —— `POST /factor/report`(`expr_or_name=draft.expr`,`universe` 默认 `csi_fast`),取回 `ic.rank_ic_mean`/`ic.icir`/`portfolio` 等真指标。verdict 由前端 `verdictFromIC` 按 IC 阈值打:**通过**(`|ic|≥0.03 且 |icir|≥0.3`)/ **驳回**(`|ic|<0.015`)/ 余**存疑**;`expr` 经 §DSL 白名单 grounding,非法/算不出(`status≠ok`、compute_error、ic 为 null/NaN)→ **诚实驳回**(verdict=驳回 + note)。旧 `synthVal` 演示态仅在 `real=false` 时兜底显示(标注"数值非真实回测")。
- **用·沉淀**:`promote()` → `POST /cards` 真持久化(+ `GL.put` 跨模块闭环);写后刷新右栏。

## 后端 / 接线
- 代码:`guanlan_v2/cards/{card,store,api,ingest,refine}.py`(复制改造自引擎 `wisdom/`,改 UI 量化形状;`ingest` 复用引擎解析器;`refine` 接引擎 `LLMClient`→deepseek)。测试 `tests/test_cards_{card,store,api,ingest,refine}.py`(**42 passed**)。
- 端点:`GET /cards/list?status=approved|draft|rejected|all` · `GET /cards/{id}` · `POST /cards`(upsert,无 id→next_id)· `POST /cards/{id}/status` · `POST /cards/refine`(炼·大模型精炼,带基础 prompt)。
- 灌入(幂等):`from guanlan_v2.cards.ingest import ingest_notes_file; ingest_notes_file(CardStore())`;`GUANLAN_WISDOM_NOTES` 可覆盖源文件。
- 接线开关:HTML 注入 `window.GUANLAN_BACKEND`(http(s) 同源 → 9999 薄壳即真后端;file:// → 回退 mock);`validation.jsx?v=20260604e`。
- **两种卡形状(勿混)**:① 持久化 `Card`(title/cat/verdict/conf/ic/expr/insight/src/refs)——被 list/get/upsert/status 读写、落库;② 对话「草稿」(name/insight/conds/scenes/expr)——仅 `POST /cards/refine` 用,**不读写库**(只精炼前端草稿)。沉淀(用·`POST /cards`)时草稿才落成持久化 Card。

## 状态
- ✅ **右栏 KB + 沉淀接真**(MVP);✅ **左栏接真**:12 条真·视频经验入「未验证」桶,原→炼 可走。
- 控制端独立验证通过:`/cards/list?status=draft` 200 返 12 条、左栏渲染真标题、旧 4 条 mock 消失、点开见 4 段式真内容、提炼+对话面板可用、控制台无错。
- ✅ **验(第三步)= 真·单因子回测**(`POST /factor/report`,`csi_fast` 默认;`verdictFromIC` 按 IC 阈值打 verdict;非法 expr → 诚实驳回)。多因子/完整工作流仍走「因子·工作流」模块。
- ✅ **「炼」因子表达式 grounding 扩到「已验证 TA 指标库」**:`factorlib` 新增 `ta_*` 族(MACD/RSI/KDJ/BOLL/WR/BIAS/ROC/ATR,**20 条经 `/factor/report` 实测 ok**,`sma`=EMA 重建),范例由 `refine.py` 注入 prompt。实测炼对 MACD 写出 `cross(sma(close,13,2)-...)` 可编译式、对 OBV/CCI/SAR(真缺口)诚实留空。修正了原 `factor_dsl_kb.md §二` 误判 MACD "无法量化"。

## 开放项
- ✅ **「验」接真(单因子)**:已接引擎 `POST /factor/report`(`csi_fast` 默认),按 IC 阈值前端 `verdictFromIC` 打 verdict,非法 expr → compute_error 诚实驳回。沉淀按 verdict 路由(通过→approved / 存疑→rejected)。**完整工作流 / 多因子合成 / ML 验证**仍为后续 —— 经 `openWorkflow`(GL.handoff 携经验快照)跳转「因子·工作流」模块承接。
- ✅ **「炼」已接真大模型**(deepseek,`/cards/refine`,基础 system prompt)。可再进一步:接引擎 `wisdom/extractor` 做"原文→草稿卡"的服务端真提炼(初始炼出也走真模型)。
- **drift**:`guanlan_v2/cards/` 是引擎 `wisdom/` 的复制改造分叉(用户选定;UI 量化卡 ≠ 引擎定性卡,引擎已 vendored 进仓库),引擎后续改动不自动同步。
- 设计 spec:[`docs/superpowers/specs/2026-06-04-cards-backend-wiring-design.md`](../../docs/superpowers/specs/2026-06-04-cards-backend-wiring-design.md)。

**2026-06-10 · mock 清零快赢批(validation.jsx `?v=20260610a`,审计 M1/M2)**:
- 「数据验证」两张硬编示意图(IC 时序/十分位)加**真序列守卫** —— 仅 `/factor/report` 真返 `v.ic_ts`/`v.decile_rets` 才渲染(原真验证态也并排显假图,全仓唯一;后端补返序列字段后图自动恢复);KPI「周换手」真态不再显写死 38%(无真值显 —)。
- 素材库兜底显形:`loadSources` 三态 live/empty/error —— 后端失败/空桶时左栏顶部「示例数据」横幅(失败可重试),不再静默让演示卡冒充真素材;`/cards/refine` 失败回落 `mockRevise` 时回复明示「⚠ 大模型服务不可用,已降级为本地规则改写」。
- 浏览器验真:假图标签消失、live 态无横幅、零报错。喂真序列(后端补 `ic_ts`+`decile_returns`)留中期项。

**2026-06-10 · 验证图喂真序列(validation.jsx `?v=20260610c`,审计 N2,零引擎改动)**:
- `runWorkflowValidate` 端点 `/factor/report`(引擎,只有汇总值)→ **`/factor/report2`**(壳内可配报告,workflow 抽屉同源):`ic.rank_ic_series`(逐期 rank-IC)→ `v.ic_ts`、`quantile.group_ann_return` → `v.decile_rets`、`portfolio.turnover` → 真换手(原 KPI 写死 38%,标签「周换手」→「换手」)。M1 守卫据此**自动恢复两图渲染**。
- `ICBars` 内部归一化 + 条距按点数自适应(原裸像素高,真 IC 幅度会溢出)。
- 「回测参数」行真态改用 report2 真 meta(universe/区间/freq/分组/前瞻/样本股)—— 原静态芯片显「沪深300·2016-2025·15bps」与实跑(csi_fast·近1年·月频)不符,演示态才保留示例芯片。
- 浏览器端到端验真:真素材卡 → LLM 改写补表达式 `rank(-delta(close,5))` → 验证 → **两图复活且全真**(11 根 rank-IC 柱、十分位非单调)、KPI 真换手 67%、结论「存疑」由真 IC 判出;零报错。

**2026-06-11 · 互通批(P0②/P1⑧/P1⑩,`validation.jsx ?v=20260610d`)**:
- **P0② real 提升 bug**:`const real` 原声明 :882、首用 :856(babel env const→var 提升 → 恒 undefined)→ 真验证态永显静态演示参数芯片,与 N2 声称相反。声明已上移,真 meta 芯片才真正生效。
- **P1⑧ 修 404**:`openWorkflow` 与组合卡「据此搭建工作流」改 `../factor/` 相对路径(裸文件名相对 /ui/cards/ 是 404);顺删 legacy 裸键 `guanlan:handoff` 死写(全仓零读者)。
- **P1⑩ 关联**:`POST /cards/{id}/status` approve 落 `.data/wisdom/approved/` 后,引擎 `wisdom_search` 已可检索(server.py `FA_WISDOM_ROOT` 合流)——promote 的卡即刻可被对话/研报 agent 引用。验真:EV-001 批准后 `wisdom_search('安心持股')` 返回全文。

**2026-06-11 · P2-A promote 迁状态+去双 id(`validation.jsx?v=20260610e`)**:旧 promote 不带 id POST(后端 next_id 新建 → 双卡)、draft 原卡残留、GL 又造 `card_user_*` 第三套 id。重写:有后端 id(EV-NNN)→ 同 id upsert 更新字段 + `/cards/{id}/status` approve(set_status 真迁移 unlink 旧文件);GL 用后端同一 id;成功后 `loadSources()` 让升级卡从素材库消失;删 mock 时代 SRC_RS refs 表。后端语义验真:EV-013 联调卡 draft→approved 零残留、list 单条(已转 rejected 归档)。

**2026-06-13 · 帷幄融合批(`validation.jsx?v=20260613a`)**:
- **WW_EMBED 旗**:`?embed=1` 时页头 Header 与 grid embed 区域隐藏,由帷幄顶栏统一接管,嵌入右栏不出现重复顶栏。
- **WW_LEGACY 旗**:`?legacy=1` 找回 ChatRefine 对话精炼入口(全局隐藏,agent 入口收归帷幄后默认关闭)。
- **帷幄注册**:本页已注册进帷幄 `WW_PAGES`(channel `validation`);`ww_show_page` 工具可口头调出;`ww_cards_save` 工具(带确认门)可从帷幄对话回写经验卡。
- 本页已从导航摘除;直链(`/ui/cards/观澜 · 经验验证区.html`)仍可用,代码保留。
