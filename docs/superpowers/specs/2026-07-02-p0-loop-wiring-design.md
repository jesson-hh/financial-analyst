# P0 闭环接线 + 诚实收尾 — 设计

日期:2026-07-02
关联:帷幄自主闭环审计(memory: weiwo-autonomy-audit-2026-07-02);四期路线图 P0
基线:main@faf102b(守护计数现值:WW_TOOL_TABLE 32 / CONSOLE_ALLOWED 57 / MCP 37)
后续:P1 收益回流(basket_perf+定时 regen+promote 判据门)/ P2 自主研究回路 / P3 落子可视化——本 spec 均不含

## 背景与目标

审计结论:闭环后半环(跟踪收益→迭代)缺的不是接线而是数据模型——选股结果从未落盘,收益跟踪没有对象;帷幄读不到台账/校准/回测历史/模型体检,也触发不了 regen。P0 三件事:

1. **picks 落盘**:给每次选股建档案(闭环的"跟踪对象",P1 地基)
2. **帷幄 7 个薄工具**:读取面(ledger/calibration/runs/health/tsic/critique)+ regen 触发
3. **MCP 诚实收尾**:收编盘上已有的研报真后台执行修复 + 排除 ww_seats_bind 空转

## 非目标(YAGNI)

- 不做收益计算(P1 `GET /seats/basket_perf`)、不做定时调度、不做研究回路 orchestrator(P2)、不做落子"研究回路"面板(P3)
- 不改选股算法/v4 模型/交易信号;零前端改动
- 不加任何自动采纳逻辑(regen 过确认门;一切迭代建议仍人审)

## §1 picks 落盘

**新模块 `guanlan_v2/screen/picks.py`**(纯函数,为 P1 的 read 接缝):

- `PICKS_PATH = var/screen_picks.jsonl`(模块常量,测试 monkeypatch)
- `append_pick(record: dict) -> bool`:append-only 一行一 JSON(utf-8,ensure_ascii=False);任何异常吞掉回 `False`(不抛,不阻断选股)
- `read_picks(snapshot_only: bool = False, limit: int = 50) -> list[dict]`:读尾部 limit 条,脏行跳过(诚实容错),`snapshot_only=True` 只回 `snapshot:true` 的

**`ScreenIn` 加字段**:`snapshot: bool = False`、`note: Optional[str] = None`。

**`/screen/run` 接线**:主路径(v4 与因子混合路径)成功构造响应后调 `append_pick`,记录 schema(示例为合成值):

```json
{"ts": "2026-07-02T15:30:00", "date": "2026-07-01", "snapshot": false, "note": null,
 "model": "prod", "pool": "all", "alpha": 1.0,
 "factors": [{"id": "c_28f035", "w": 1.0}], "topN": 20, "n_universe": 5027,
 "picks": [{"code": "SZ300750", "name": "宁德时代", "score": 0.92, "rank": 1}],
 "constraints": {"liqMin": 5, "mlStatus": "all", "industryNeutral": false, "indCap": null,
                  "exclST": true, "exclHalt": true, "exclLimit": false, "exclNew": false}}
```

- `picks` 取最终响应的 topN 行(与 UI 所见同口径);`model` 为解析后实际生效的 model_id(默认变体解析之后)
- **玩具回退路径(fallback)不落盘**(非生产口径,避免污染跟踪对象)
- 响应加 `picks_recorded: bool` 显形(落盘失败=false,绝不静默)

**新端点** `GET /screen/picks?snapshot_only=<0|1>&limit=<N>` → `{items: [...], path: "..."}`(只读)。

**`ww_screen_run`** schema/impl 透传 `snapshot`/`note` 两参数,摘要里回报 `picks_recorded`。

## §2 帷幄 7 个薄工具

全部为现有 HTTP 端点包薄壳(`_self_get`/`_self_post` 既有模式),无新算法:

| 工具 | reachable | confirm | 参数 | 输出摘要要点 |
|---|---|---|---|---|
| `ww_ledger_state` | GET /seats/ledger/state | 否 | — | 持仓 N 只/已实现盈亏/胜率;equity=null 时注明"缺价" |
| `ww_calibration` | GET /seats/calibration | 否 | horizon(默认5) | 逐档 range/n/hit_rate 全表;n<5 档注明样本不足 |
| `ww_seats_runs` | GET /seats/runs | 否 | limit(默认10) | run 头:run_id/code/strategy/ts/日期窗/决策计数 |
| `ww_model_health` | GET /screen/health | 否 | — | IC 趋势/vintage OOS 段(ready=false 诚实注明)/alert |
| `ww_factor_tsic` | POST /factor/tsic | 否 | expr、code、universe?、freq? | 时序 IC 值+n+口径一句话("逐{freq}bar Spearman") |
| `ww_workflow_critique` | POST /workflow/critique | 否 | goal、graph、metrics | diagnosis+改进图节点数;**必注明"指标为调用方自报,后端不复算(P2 加强)"** |
| `ww_regen` | POST /screen/regen + GET /screen/regen/status | **是** | end?、wait(默认true) | 照抄 ww_model_validate 轮询形态:15s×40 次(~10min 上限);超时诚实报"仍在跑,可稍后 ww_model_health 验新" |

**四处同步铁律**:WW_TOOL_TABLE 32→**39**;CONSOLE_ALLOWED 57→**64**(派生自动);`_SYSTEM_PROMPT` 新工具具名介绍+加一句纪律("研究/复盘先查 ww_ledger_state/ww_calibration 核真实成绩");守护计数测试(test_console_tools.py)同步。MCP 表自动获得这 7 个(ww_regen 为 gated)。

## §3 MCP 诚实收尾

**收编盘上未提交的 `_spawn_background_detached`**(glmcp/server.py,已实现:dispatch_tool 检测 background 信封 → detached 子进程真跑研报/ETF 研报 + 受理凭证 + 启动失败显形)。P0 补:

- 单测(monkeypatch subprocess.Popen):report 分支命令构造 / etf_report 分支 / 未知 kind 拒绝文案 / Popen 抛错→错误显形(绝不假成功)
- 随本分支提交(该代码当前未提交、未带测试)

**`_EXCLUDED` 2→3**:`{ww_plan_update, ww_show_page}` + **`ww_seats_bind`**(seat_bind 信封靠前端 window.GL 落地,MCP 语境=空转假成功,同 ww_show_page 处理)。

**计数**:MCP = 39 − 3 + 7 alpha-zoo = **43**;`test_guanlan_mcp.py` 断言同步(现值 37→43);README 更新计数并补两段说明:研报类经 MCP=detached 真跑(与 console 后台跑道并行的通道);ww_seats_bind 为何排除。

`ww_report_run`/`ww_etf_report_run` **留在** MCP 表(confirm→gated,写门管控)。

## §4 测试与验收

- **单测**:picks 模块(append/read/snapshot 过滤/脏行容错/失败回 False);/screen/run 集成(tmp 目录 monkeypatch PICKS_PATH,picks_recorded true/false 两路,fallback 不落盘);7 工具 impl(monkeypatch _self_*,含 regen wait 轮询、critique 自报注明);_spawn_background_detached 四分支;MCP 排除断言+计数 43
- **守护**:test_console_tools 39/64;test_guanlan_mcp 43
- **全量回归**:全绿(基线 676)
- **真机 e2e**:重启 9999 → 帷幄真调 ww_ledger_state/ww_calibration/ww_model_health(有真数)→ 跑一次 /screen/run 后 GET /screen/picks 见记录(note="e2e")→ MCP tools/list=43 且 ww_seats_bind 缺席 → ww_regen 仅验 status 端点连通(不真跑 5min regen)→ 研报 MCP 通道不做 e2e(5-8min,单测覆盖分支)
- e2e 产物:picks 的 e2e 记录 append-only 无害留档(snapshot:false 不入跟踪)

## 红线(全程)

落盘失败显形不阻断;无任何假成功;critique 摘要必注明指标自报;regen 过确认门;绝不自动采纳;不碰交易信号。

## 流程

main@faf102b 开分支 `p0-loop-wiring`,subagent-driven(逐任务两段评审+终审),完成后合并选项交用户。盘上未提交的 glmcp/server.py 改动=§3 收编对象,随分支提交。

## 展望锚点(非本期,仅为对齐方向)

P2/P3 的演化回路与盯盘 agent 的四条隔离缝(设计时须守):①研究研判全带 run_id(校准/历史视图自动隔离);②算力错峰(回路盘后/夜间跑);③经验单向阀(回路→draft→人审→approved→盯盘消费);④绝不原地改绑定策略(演化产物=新版本/建议,应用须人点头)。历史新闻覆盖限制:回放中新闻维度早于采集起点只能诚实空。
