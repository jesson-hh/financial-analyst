# L2-b · Task 9 部署与排练账(2026-08-02,Steps 1-2 + 排练弹;Steps 3-5 待周一盘中)

> 计划:`docs/superpowers/plans/2026-07-31-orchestration-L2b-data-runtime.md` · 执行:控制器本人。
> 完整过程台账在 `.superpowers/sdd/progress-orchestration.md`(gitignored,本文件是入 git 的账目副本)。

## 前置:Task 9 裁决链(Tasks 0-8 全过、闸全绿之后)

真机第一脚踩到的结构性事实:**起点冻结的会话午夜 as_of 之下,盘中实时拉取永远过不了 PitGuard**(available_at=pulled_at > 午夜)。裁决(不动 D-C 闸、不搞墙钟边界、不搞逃逸时重采):

1. **盖戳语义**:`verified_snapshot` 增发**结算候选**——锚=venue 行情时间戳(腾讯协议字段 30),结算会话=委托日历上锚所在会话的**前一会话**,available_at=结算会话收盘 15:00+08(命名域规则,非伪造);实时候选被结算候选**取代**(PitGuard 全有全无,双发会毒化整批)。两态自动归位:盘中拉取锚=今天→昨收归昨日;周末拉取锚=周五→昨收归周四。
2. **新鲜度改按交易日计数**:`max_trading_sessions=1` + 委托日历(机制现成)。语义=只有「紧邻 as_of 会话的前一会话」的数据算 fresh;周一读周五行 fresh、读周四行 STALE。**任务域摘要有意识重冻:source_registry 701be202→bb81979a、routing b2897dd2→1f47a283**(1b 先例;重冻时生产端零 ContextSnapshot,零失效)。密封九期链(9e73ddf6/0c48db78/42af2460/40819483/2c4b4e0d)与 source_config 76c96f67、日历材料逐位未动。
3. **跨仓一键**:`G:\stocks` 腾讯 handler 补 `"quote_time": vals[30]`(非 git 仓,备份 `.bak-20260802-quotetime`;两仓皆无法钉它=静默回归风险,挂账)。真机实证周日信封 `20260731161450`(周末冻结在周五)。
4. 评审三轮(opus):4 Important + 7 Minor 全闭;新抓 **N1 注入洞**(vendor 日期串逐字进 `DataResult.warnings` 头部、可伪造结算徽章)→ `date.fromisoformat` 规范化重发 + **availability 键结**(徽章要求 available_at == 结算会话结算时点)双闸修死,四钉对轮一代码 4 红证明非同义反复。
5. **真供应商端到端证明**(控制器 scratchpad):真腾讯拉取→结算候选→真 Phase-3 dispatch→`OK / PIT passed / latest_available_at=2026-07-30T07:00Z`。

提交链:`dec8dc7c`(闸前清扫)→ `57e771fa`(结算候选)→ `8d7210f1`(日历归位+会话计数新鲜度重冻)→ `8f207d9c`(评审轮)→ `9aaad878`(N1 修死)。全树 5910 passed + 1 xfailed。

## Step 1 — 一列车部署点(06:10-06:15)

- 9998 预演(子进程环境带 `GUANLAN_PORT=9998`,代际链环境未触碰):bound / 14 命名空间 / registry_digests **3→5**(新增两个=L2-b 数据注册,启动零失败);预演进程即杀。
- 9999 杀于 06:10:08,代际链自愈拉起 L2-b 树:store_status 与预演逐位一致。**9999 首次同时服务 L1+L2-b。**
- 里程碑推送:main `b13986f→9aaad878` CAS 快进已推。

## Step 2 — Lane-0 真跑(06:15)

`lane0_driver propose → approve → run`(2 次 LLM 结算,8192 tokens)。**第一份携带真数字摘要的生产 ContextSnapshot**:`bootstrap-run-ctx-4502925d…`(session 2026-08-02,as_of 冻结 2026-08-01T16:00Z,deep_lane_sees=True);outcome degraded(regime incomplete=战役已知模型侧;factor/rotation completed)。徽章诚实:pit_inputs_partial(8 有 9 缺,缺列渲染 UNAVAILABLE 零填充)+ regime_missing。

控制器探针三项全过:
- (a) 已提交 `data_context.source_registry_digest == 配方 == bb81979af882` 逐位,source_config 76c96f67,mode=ONLINE;
- (b) manifest(lane0-capture-2026-08-02,content 4ea006e94374…)经 **resolver 的同一通道**(PRODUCTION_CAPTURE_NAMESPACE + manifest schema ref + 密封 schema registry digest + content digest)`find_ref` 命中,locator 与语义摘要双吻合;
- (c) 驱动器注记逐字点名配方(「每个摘要都是量出来的,没有占位符」)。

## 排练弹(15:19-15:23,用户当场要求真跑)

租约 `612a6f19`(1 次准入 / 6 LLM / 2h,键结密封 reduced 记录 2c4b4e0d+链摘要)→ 策略 `strat_mrgjipyt4s0` 临时 opt-in(跑完即还原)→ run `deep-9b8a1386afcb1dbf`(300308,231s,6 次 LLM 结算,租约消费):

- **pv.price_action / pv.microstructure:FAILED `aux_data_ungranted`** —— Task 6 的典型拒**首次上生产**(不是 bridge_execution_error);
- 主干五席 sentiment / bull-r1 / bear-r1 / research-mgr / **pm 全 COMPLETED**;
- **pm 真读达成计划 Step-4 数据判据**:`DataRequest` 参数逐字回显本 run 提交的 subject(300308/SZ/chinext + 会话午夜 as_of);一条 `cap.data.verified_snapshot` ToolCallRecord;`VerifiedSnapshotDataResult` **status=ok**、badges=`[SETTLED_PRIOR_SESSION]`、完整诚实警示(点名结算会话 2026-07-30、「0.00% 是构造使然非市场事实」)、**pit_audit passed**、latest_available_at 2026-07-30T07:00Z ≤ as_of;渲染块(untrusted 信封,1297 字节)见证于 pm PromptAssemblyRecord untrusted_blocks ordinal-1——判据措辞逐条落地。周日读数 ok 而非 STALE:周六午夜 as_of 使周四行恰为紧邻前会话(语义自洽)。
- pm 产出 Hold(对证据不足的裁断非兜底)但**行文未引用读数**;**trader INCOMPLETE**(PortfolioTargetProposal 解码 None,模型侧偶发)→ run 终态诚实 failed,无 proposal 无台账行,快线照常返回。
- 过程抓一个控制器自己的坑并立规:**驱动脚本必须从仓根跑**——`cd scratchpad` 使 `var/` 相对路径静默绑到空宇宙(首发即中,诚实拒,浪费 1 次快线 LLM)。

污染清点:seats_ledger 零 orchestrated 行零今日条目(已验);快线判断未落盘;策略文件逐字还原;run 行是只追加溯源账(手删=库损坏),对一切产品面不可见。

## 服务器深链开关(15:56,用户拍板「先拧」)

`GUANLAN_SEATS_DEEP=1` 入 `var/secrets.env`(`GUANLAN_SEATS_DEEP_PRESET=reduced`、`GUANLAN_SEATS_WATCH=1` 原已在);杀 9999→链 8s 自愈。证据(`var/server-9999.log`;注意:**复活路径的 stdout+stderr 合并追加在此,`server-9999.err.log` 只有手动启动写**):减证据横幅 + 「GUANLAN_SEATS_DEEP=1:编排深研判 decide_fn 已装配」,全程零装配失败行。watcher enabled、盯 [300750, 833509, 300308]、market_open=false。**自此:盘中 tick 对「deep_research opt-in + 有效租约」的策略自动升级深跑;无租约诚实退快线,绝不自批。**

## 没证明什么 / 剩什么(周一 08-03 盘中)

- 完整主干含 **trader 落 `PortfolioTargetProposal` + 台账 orchestrated 行**(本次模型侧偶发拦住)、pm 行文引用读数——收官 run 的判据;
- 流程:re-opt 策略(备份在 ARCHIVE_DIR)→ 新租约 → lane0 跑 08-03 会话 → 盘中 watcher 真升级 → Step 5 记账 → 终审全分支复审 → 终推;
- 挂账:2027 日历材料年底前必须提交(现为响亮悬崖);`DataResult.warnings/badges` 位于渲染可信框架内而类型系统不知情(封闭徽章词表=类级修法,归 L3/终审);G:\stocks `quote_time` 键两仓皆无钉。
