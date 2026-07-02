# P2 自主研究回路 · 设计 spec(2026-07-02)

> 背景:帷幄自主闭环四期方案(见 2026-07-02 审计)第三期。P0(picks 落盘+7 读取工具,main `6aac973`)、P1(收益回流 basket_perf/ww_picks_perf/双 opt-in 门,main `2b805e8`)已交付。
> P2 目标:把现在困在前端 JS 的 aiLoop 闭环(提案→求值→批判→改进)搬到后端,做成可后台运行、逐轮落档、达标产物入 draft 抽屉的 research_loop 编排器。

## 0. 用户已拍板的设计决策

1. **求值引擎 = 小灶直调(A 方案)**:后端不复刻前端 24 节点图执行器;从 LLM 产图中提取因子表达式,按图形状直调既有模块函数三道菜(单因子 report2 / 多因子 compose / 回测 backtest_vector)。计算只有一套,与画布口径逐位一致(帷幄的 ww_factor_analyze/ww_backtest/ww_factor_compose 今天就是这么调的,见 console/tools.py:184-191 直调桥)。怪形状的图诚实跳过,绝不硬跑。
2. **达标入库 = factorlib 加 draft 状态**:达标因子自动存 factorlib 带 `status="draft"`;选股页目录过滤 draft;人审转正(`POST /factorlib/promote`)后才上货架。与 P1 模型 draft 门同范式。
3. **帷幄接入 = 两工具**:`ww_research_loop`(发起,confirm 确认门)+ `ww_research_runs`(查看)。计数 40→42 ww / 65→67 console / 44→46 MCP。
4. **教训沉淀 = 每 run 一条**:回路收工写一条 keyed 常驻记忆(≤280 字,失败也记),同研究目标收敛覆盖;逐轮细节全在轮次档案。
5. **存图桥 = 每 run 存最佳一张**:收工时把达标轮(或未达标时 RankIC 最佳轮)的图自动存进现有工作流库(`/workflow/save` 存储),用户在工作流页直接点开上画布。

## 1. 架构

新模块 `guanlan_v2/research/`(后端落点红线:新后端加 `guanlan_v2/<module>/` 路由挂薄壳):

- `store.py` — runs/rounds 双 jsonl 档案(照抄 `screen/picks.py` 三件套 + 落子 runs registry 双文件模式)
- `loop.py` — 编排器核心(提案/选菜/求值/过门/批判/入库/存图/教训,尽量纯函数可单测)
- `api.py` — `build_research_router()` + 单飞状态机(照抄 regen 范式)

宿主:回路跑在 9999 进程内 **daemon 线程**(与 regen/promote/model_train/validate 四个既有状态机同构),单飞锁一次只跑一个 run。LLM 走进程内 `LLMClient`(workflow api 既有纯函数),**全程无子进程**(无 GBK 坑面)。

挂载:`server.py` `create_app()` 内 `include_router(build_research_router())`(紧邻 screen/console 挂载区)。**命名空间前置检查**:确认引擎 buddy app 未占用 `/research/*` 路由(实现时 grep 引擎路由表核实;若被占则整体退到 `/workflow/research/*` 前缀)。

**零新开关、零定时器、零子进程**:回路只能由人/帷幄(过 confirm)显式发起,合并即零行为变化,不需要任何 env 门。定时自主开跑留 P3(job runner)。

## 2. 回路算法(确定性编排,LLM 只在提案/批判两个接缝)

入参:`{goal(必填非空), max_rounds=3(服务端钳 1..5), min_rank_ic=0.02(钳 0..0.2), universe="csi300_active"(须 ∈ workflow._UNIVERSE_OK), start?, end?, freq="month"}`。求值一律带 `oos_frac=0.3`(过门要求样本外判定,oos 必须开启)。

> 实现注记(写计划时核实的事实):generate/critique handler 是 `build_workflow_router()` 内闭包,且 engine LLM 客户端连接池绑事件循环(daemon 线程反复 `asyncio.run` 会踩 "Event loop is closed");故两个 LLM 接缝从 daemon 线程**同步 HTTP 自调**本进程端点(`workflow_critique_impl` 已验证的模式,LLM 调用落在 server 主循环上)——同一实现零复制,与「同源复用」意图一致。求值三道菜维持模块函数直调(`_factor_report2/_factor_compose/_backtest_vector` 均模块级 sync)。

每 run:

1. **提案(第 0 轮)**:进程内复用 `/workflow/generate` 的同源纯函数(`_llm_complete(SYSTEM_PROMPT)` → `_parse_graph` → `validate_graph` 权威校验+错误回灌重试≤2 → `_autowire_source`)。**LLM 失败或产图不合法 → run 诚实终止 `ok:false`,绝不静默降级关键词模板**(比前端 aiLoop 更严:前端降级 generateFromText 且不标注,是既存诚实性小洞,后端不继承)。
2. **选菜**:从图中收集 formula 节点 `params.expr` 与 factorlib 节点名 → exprs 列表;按形状选菜:
   - 图含 backtest 终端节点 → `backtest_vector`(features=exprs)
   - exprs ≥2 → `compose`(members=exprs)
   - exprs ==1 → `report2`(expr_or_name)
   - exprs ==0 或其他不可解形状 → 该轮 `failed=true, error="不支持的图形状(非配方模板)"`,诚实跳过求值
3. **求值(后端真算)**:直调模块函数(`_factor_report2`/`_factor_compose`/`_backtest_vector`,`JSONResponse` 经 `json.loads(resp.body)` 解包,复用/镜像 console/tools.py:174-191 的 `_resp_json` 桥)。提取六键指标(镜像前端 metricsOf 语义,compose 嵌套在 `composite` 块内需展开):
   `rank_ic`(headline_ic.rank_ic ?? ic.rank_ic_mean ?? metrics.rank_ic)/ `sharpe`(portfolio.sharpe)/ `ann_return` / `oos_verdict`(oos.verdict)/ `n_dates` / `factor`(exprs 摘要)。**消掉 critique「指标自报不复算」挂账(回路路径)**。
4. **过门**:`passed = rank_ic ≥ min_rank_ic 且 oos_verdict == "robust"`。达标 → 存 draft 入库(§4)→ run 提前收工(success)。
5. **批判改进**:未达标把**后端自算指标**喂给 critique 同源实现(`_CRITIQUE_SYS` LLM → validate → 失败落 `_rule_critique` 规则兜底);`source: llm|rule` 落进轮次行,rule 时 diag 前缀「(规则兜底·非 LLM) 」(对齐前端既有标注)。求值失败的轮:metrics 传空 dict,critique 仍可凭 goal+graph 重塑下一轮图。改进图进下一轮,直到达标或 max_rounds 用尽。

收工动作(无论达标与否):
- 存图桥:最佳轮(达标轮,否则 rank_ic 最佳的成功轮;全失败则跳过)的图 POST `/workflow/save`,name=`研究·{goal[:16]}·{run_id 后 6 位}`;落盘结果显形进终态行
- 教训:一条 keyed 常驻记忆(§5)
- 终态行落 runs 档案

## 3. 落盘与档案

双文件(照 picks.py 模板:模块级路径常量便于 monkeypatch;append 吞异常返 bool;read 新在前/坏行跳过/limit 钳制;utf-8 + ensure_ascii=False + default=str;ts=本地 isoformat 秒):

**`var/research_runs.jsonl`** — 起跑即落 run 头(诚实可见),收工追加终态行:
- run 头:`{run_id, kind:"start", goal, params:{max_rounds,min_rank_ic,universe,start,end,freq}, ts}`
- 终态行:`{run_id, kind:"end", ok, error, n_rounds, best_k, best_metrics, promoted:{name,status:"draft"}|null, workflow_saved:{id,name}|null, memory_written:bool, rounds_recorded:bool, ts}`
- run_id 后端生成:`"rr_" + uuid4().hex[:10]`
- 读取时状态推导:有终态行→done/error;无终态行且非当前在跑 run→`"interrupted"`(9999 重启即中断,诚实显形,无需启动扫描)

**`var/research_rounds.jsonl`** — 每轮一行(P3 可视化的数据源,形状承接前端 aiLoop 轮次记录):
`{run_id, k, ts, stage:"propose"|"improve", diag, critique_source:"llm"|"rule"|null, exprs:[str], dish:"report2"|"compose"|"backtest"|null, metrics:{rank_ic,sharpe,ann_return,oos_verdict,n_dates,factor}, gate:{passed,min_rank_ic}, failed:bool, error:str|null, graph:{nodes,edges}}`

端点(全部诚实失败恒 HTTP 200 `{ok:false,reason}`):
- `POST /research/loop/start` → `{ok,started,run_id,state}` | `{ok:false,reason:"already_running",state}` | `{ok:false,reason:"goal 不能为空"}`
- `GET /research/loop/status` → `{ok,state}`(state 含 running/phase/round_k/total_rounds/label/run_id/started_at/ended_at/ok/error/lines[-12:]/elapsed_sec;单飞锁只取一次绝不嵌套)
- `GET /research/runs?limit=20`(钳 1..100)→ `{ok,runs,n}` 新在前,含推导状态
- `GET /research/rounds?run_id=&limit=50`(钳 1..200)→ `{ok,rounds,n}` 新在前(含完整 graph)

## 4. factorlib draft 门(绝不自动采纳)

- `SaveIn` 加 `status: str = ""`(合法值 `""|"draft"`,非法值诚实拒);save 时非空 status 写进 mined JSON 条目
- draft 因子**照常注册**进 zoo registry(可按名复验,与 draft 模型"完整存在只是不上架"同语义)
- `screen/catalog.py` `_build()` factorlib 并入段:`status=="draft"` 跳过 → **选股页目录永远看不到 draft**
- `/factorlib/list` 下发 status 字段(显形 draft 标)
- **`POST /factorlib/promote {name}`(新端点,人审转正)**:在 mined JSON 里找到该条目,摘掉 status → `{ok,name}` | `{ok:false,reason:"not_found"}`;下次 `/screen/factors` 入口热刷新即上货架。P2 只给端点(curl/后续 UI 按钮);帷幄侧转正工具与「待审面板」留 P3
- 回路入库命名:`lib_rl_{run_id 后 6 位}_r{k}`(保证唯一,规避 save 重名双重拒绝);description=goal+一句诊断;meta 带六键指标快照
- 边界:达标轮若为多因子合成(compose,≥2 表达式),**不自动入库**(库以单表达式为单位,合成权重是数据驱动的非固定表达式);终态行 promoted 诚实标 `skipped_multi`,成分表达式全在轮次档案供人工取用

## 5. 帷幄两工具 + 教训记忆

**`ww_research_loop`**(confirm=True——花 LLM 钱+可能写 draft,发起必过确认门;cost="minutes"):
- 入参 `{goal 必填, max_rounds?, min_rank_ic?, universe?, wait?=true, timeout_seconds?=1800, poll_seconds?=15}`
- impl:`_self_post /research/loop/start` → wait 时轮询 status(照 model_promote_impl 三段式:post-start→poll→deadline 诚实超时「后端可能仍在跑,稍后 ww_research_runs 查」)→ 收工读 runs/rounds 拼成绩单 content:`「研究『{goal}』{n} 轮 · 最佳 RankIC={x}(第{k}轮) · {达标已入 draft:lib_rl_xxx | 未达标(诊断一句)} · 图已存工作流库」`
- reachable=["/research/loop/start","/research/loop/status","/research/runs"](按 impl 真实调用;仓规 reachable 取自 `_self_post/_self_get` 实调)

**`ww_research_runs`**(confirm=False,cost="instant"):
- 入参 `{run_id?, limit?}`;无 run_id 列近期 run(状态/轮数/最佳指标/draft 名),有 run_id 出逐轮详情(diag/指标/过门;graph 不进 content 防灌上下文,raw 瘦身)
- reachable=["/research/runs","/research/rounds"]

**四处同步铁律**:WW_TOOL_TABLE(40→42)/ console/api.py `_SYSTEM_PROMPT`(能力行+纪律:研究回路发起必过确认;复盘研究成绩用 ww_research_runs)/ tests/test_console_tools.py(42/67 + expected-endpoints 集 +4)/ tests/test_guanlan_mcp.py(44→46 三处)。MCP:两工具均收录(`_EXCLUDED` 不变);ww_research_loop confirm=True → 归 `GUANLAN_MCP_WRITE` 写门锁;glmcp/README 计数 46。

顺手小修:`workflow_critique_impl` 的挂账注记文案更新为「⚠ metrics 为调用方自报,后端不复算(后端自算口径走 ww_research_loop 研究回路)」——P2 交付后该注记保持诚实。

**教训记忆(每 run 一条)**:收工经 `memory_write_impl`(scope="global", key=`研究·{goal[:24] 消毒}`)写一条 ≤280 字:结论+达标与否+最佳因子+一句诊断。**失败也记**(「该目标下动量类全过拟合」也是经验)。同 key 收敛覆盖=同一研究目标最新认知。`memory_written` 显形进终态行。

## 6. 诚实红线清单(全期贯穿)

- 提案 LLM 失败 → run 诚实终止,绝不降级模板(严于前端现状)
- critique 规则兜底 → `critique_source:"rule"` 落档 + diag 前缀「(规则兜底·非 LLM) 」
- 指标全部后端真算;怪图诚实 skip 不硬跑
- draft 绝不自动上架;转正=人的动作;发起过 confirm 门
- 恒 HTTP 200 诚实失败;单飞;落盘失败显形(rounds_recorded/memory_written/workflow_saved 进终态行)
- 零新 env 开关、零定时器、零子进程;绝无自改代码/提示词
- 回路与盯盘隔离锚点不变(run_id 隔离/算力错峰/经验单向阀过人审/绝不原地改绑定策略)

## 7. 测试

**单测**(monkeypatch 模块级常量 + fake LLM/求值桥):store 往返/坏行/interrupted 推导;选菜(四种形状);六键提取(report2/compose/backtest 三种响应夹具);过门判定;draft 命名唯一;catalog draft 过滤;promote 端点(含 not_found);全回路假 LLM 干跑(达标提前收工/用尽轮数/提案失败终止/求值失败轮继续);教训写入(tmp memory 路径);两工具 impl(fake _self_post/_self_get,含 wait 超时);守护计数 42/67/46。

**真机 e2e**(测后还原现场):发起小 run(goal 如「找一个短周期反转因子」,universe=csi300_active 或 sample30,freq=month,max_rounds=2),验证:status 轮询真进度;rounds.jsonl 逐轮行(diag/指标/critique_source);工作流库出现「研究·…」条目并可 GET 读回;若达标 → /factorlib/list 见 draft 标且 /screen/factors **不见**它,promote 后可见;记忆落线;ww_research_loop 成绩单文案;ww_research_runs 列表/详情。还原:删测试 draft JSON/工作流库条目/记忆行。

## 8. 展望锚点(P3,未立项)

- 落子右栏「研究回路」轮次卡:照 AILoopModal 视觉(纯 `{running,goal,rounds,step}` 驱动,workflow.jsx:2008),数据源=本期 rounds.jsonl,经 GET 或 SSE 喂给它;「应用此工作流」onApply 契约(铺画布不自动跑)原样保留
- 帷幄侧转正工具 + 「待审 draft」面板
- 服务器 job runner(定时自主开跑,opt-in 默认关)
