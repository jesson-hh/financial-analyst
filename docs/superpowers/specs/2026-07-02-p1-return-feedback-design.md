# P1 收益回流 — 设计

日期:2026-07-02
关联:帷幄自主闭环审计(memory: weiwo-autonomy-audit-2026-07-02);四期路线图 P1;前置 P0 已合 main(picks 档案在位,`var/screen_picks.jsonl` + `GET /screen/picks`)
基线:main@6aac973(守护计数现值:WW_TOOL_TABLE 39 / CONSOLE_ALLOWED 64 / MCP 43;用户多会话并行活跃,实现时以守护测试现值为锚)
后续:P2 自主研究回路 / P3 落子可视化+job runner——本 spec 不含

## 背景与目标

P0 给了闭环"跟踪对象"(picks 档案)与帷幄读取面;但"D 日选的股后来怎么样"仍无计算件,收益证据(vintage/快照/factor_ic)积累靠人手点 regen,训练→验证→上架三件套无判据门。P1 四件事:

1. **全A等权基准产物**:regen 顺算,给收益对比一把公平尺子
2. **`GET /seats/basket_perf`**:篮子前向持有收益 vs 基准(闭环第 3 环计算件)
3. **`ww_picks_perf`**:帷幄"查成绩单"工具
4. **两个 opt-in 开关(默认关=合并零行为变化)**:regen 每日定时 + promote 阈值门

## 非目标(YAGNI)

- 不做"成绩→自动改因子/权重/默认模型"(P2;本期一切采纳仍人工)
- 不做落子界面可视化(P3)、不做服务器 job runner(P3)
- 不改交易信号/选股算法/前端(零前端);不重写用户工坊的 promote 主流程(门是包裹)

## §1 全A等权基准产物

- **产物**:`ARTIFACTS_DIR/eqw_market_ret.parquet`,列 `date`(YYYY-MM-DD)、`ret`(float,当日全市场 close/prev_close−1 截面均值)、`n`(int,当日参与均值的样本数)
- **生产**:`regen_all` 在 breadth 步骤后顺算(新纯函数模块 `guanlan_v2/strategy/compute/eqw_market.py`,复用 breadth 同源全市场面板/loader 路径,实现计划时以 breadth.py 现状为准钉死取数点);**PIT:当日未结算 bar 不落**;全量重算幂等覆盖
- **读取**:模块内 `load_eqw_ret()` mtime 缓存(同 factor_ic 模式);产物缺失回 None(消费方显形)

## §2 GET /seats/basket_perf

- **入参**:`codes`(逗号分隔,≤40 只,数字核归一)、`start`(选股日 YYYY-MM-DD)、`horizon`(int 默认 5,钳 1..60)
- **口径(对齐置信校准/vintage,注明随响应下发)**:逐票 start(或其后首根交易日)**收盘进 → +horizon 根收盘出**,等权,不含成本;取数经 `get_default_loader().fetch_quote` + `_drop_unsettled`
- **未成熟诚实**:出场 bar 未到 → 该票 `matured:false`,`ret` 给到最新可算段(entry→最新收盘)并如实标注;篮子层给 `matured_n` 计数,消费方能区分
- **基准**:同窗口全A等权累计收益 = ∏(1+ret_d)−1(§1 产物按各票 entry→exit 窗口对齐,篮子层等权平均);产物缺失/窗口不覆盖 → `bench_ret:null` + note 显形,绝不编造
- **响应**:`{ok, n, matured_n, horizon, avg_ret, bench_ret, excess(两者皆有才给,否则null), per_code:[{code,entry_date,entry,exit_date,exit,ret,matured}], warnings, note(口径一句话)}`;单票取数失败该票剔除并入 `warnings`;全失败 `ok:false, reason`(恒 HTTP 200)
- **落点**:计算抽纯函数模块 `guanlan_v2/seats/basket_perf.py`,`guanlan_v2/seats/api.py` 挂薄端点

## §3 ww_picks_perf 工具

- **行为**:读 picks 档案 snapshot 行(默认最新一条;可选 `date` 选某天、`horizon` 透传),取其 picks codes + 档案 date 调 `/seats/basket_perf`,摘要:"6-30 正式选股 20 只 · 5日等权 +2.1% vs 全A等权 +0.4% · 超额 +1.7pp · 成熟 18/20";无 snapshot 档案时诚实提示"暂无正式选股档案(ww_screen_run 传 snapshot=true 落档)"
- **注册**:只读、无确认门、`reachable: ["/screen/picks", "/seats/basket_perf"]`;raw 带篮子摘要+per_code(≤40 行本就有界)
- **四处同步**:WW_TOOL_TABLE 39→**40**;CONSOLE_ALLOWED 64→**65**;MCP 43→**44**;`_SYSTEM_PROMPT` 具名(接在闭环读取面一行)+纪律 13 补"复盘选股成绩用 ww_picks_perf";守护计数测试+期望端点集(+`/seats/basket_perf`+`/screen/picks` 两项)

## §4 regen 每日定时(opt-in,默认关)

- **开关**:env `GUANLAN_REGEN_DAILY=1` 才启动;缺省完全不起线程(合并零行为变化)
- **实现**:仿 market scheduler 模式(daemon 线程 + 每日触发窗),每日 **18:00 后**当天未自动跑过则调既有 regen 启动函数(复用单飞锁;已在跑则本日让过);触发记 `last_auto_ts`
- **显形**:`GET /screen/health` 附 `regen_scheduler: {enabled: bool, last_auto_ts: str|null}`;注释/README 诚实写明"定时器随 9999 进程存亡,非 24/7 保证"
- **落点**:`guanlan_v2/screen/api.py`(或薄模块)+ `guanlan_v2/server.py` 启动处 env-gate 调用

## §5 promote 阈值门(opt-in,默认关)

- **开关**:env `GUANLAN_PROMOTE_MIN_OOS_IC`(float,如 `0.01`)设了才启用;缺省 train_promote 行为逐字不变
- **门逻辑**:`train_promote` 算完留出 oos_ic 后,`oos_ic is None or oos_ic < 门槛` → 变体照存但 `meta["status"]="draft"` + `meta["gate"]={"min_oos_ic": 门槛, "oos_ic": 实值, "passed": false}`;达标 → `meta["gate"]["passed"]=true`(不加 status)
- **消费侧**:`/screen/models` 默认过滤 `status=="draft"`(query `include_draft=1` 可见,draft 行带 status);`/screen/model/default` 对 draft 变体拒绝(`ok:false, reason` 诚实说明);`ww_model_list` 对 draft 标 ⚠(include_draft 时);`ww_model_promote` 摘要诚实报"oos_ic 0.004 < 门槛 0.01,已落 draft 区(不能设默认)"
- **红线**:门只拦"不合格自动进正式货架";**采纳(设默认)永远人工确认**;不动 model_workflow 重训主流程,门是尾部包裹

## §6 测试与验收

- **单测**:eqw 纯函数(均值/样本数/未结算剔除/幂等)+ load 缓存;basket_perf 纯函数(成熟/未成熟/首根顺延/单票失败剔除+warnings/基准缺失 null/excess 逻辑);ww_picks_perf(monkeypatch _self_get/_self_post,无档案分支);scheduler 触发判定(注入 fake clock,不真 sleep;当天已跑不重复;env 缺省不起线程);promote 门(env 未设零变化/不达标 draft+gate meta/达标 passed;models 过滤+include_draft;set_default 拒 draft)
- **守护**:计数 40/65/44;期望端点集 +2
- **全量回归**:0 failed(基线 737)
- **真机 e2e**:eqw 产物在位(真跑 regen 或现有产物补算)→ 对 P0 落的 e2e picks 记录跑真 basket_perf(bench 有数或诚实 null)→ ww_picks_perf 真调 → /screen/health 见 regen_scheduler 块(enabled:false)→ 门:tmp MODELS_DIR + 临时 env 验 draft 落区 + set_default 拒绝(不污染真变体)
- **红线(全程)**:基准缺失显形 null 不编造;未成熟不冒充已实现;两开关默认关(合并即零行为变化);绝不自动采纳;失败恒 HTTP 200 + ok:false 显形

## 流程

main 开分支 `p1-return-feedback`,subagent-driven(逐任务两段评审+opus 终审),完成后合并选项交用户。改 regen/model_workflow 时注意用户并行会话可能又动同文件——实现前 git status 对表、以守护测试现值为计数锚。
