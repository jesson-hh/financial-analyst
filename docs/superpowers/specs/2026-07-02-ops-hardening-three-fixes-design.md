# 挂账三修:看门狗短命化 · DL 新鲜度显形 · MCP 研报真执行(设计文档)

**日期**:2026-07-02
**状态**:设计已确认,待写实施计划
**来源**:双端链路检查(本日)+ 帷幄自主闭环大审计挂账真 bug([[weiwo-autonomy-audit-2026-07-02]] b/a 两条)。用户选定:短命周期检查 / 容忍窗+显形 / 真执行。P0 四期方案**另行立项**(独立 brainstorm),不在本 spec。

---

## 0. 范围与不打架红线

**做(三个有界修复,互相独立)**
1. 看门狗换短命周期检查(根治常驻冻死)
2. DL 预测新鲜度容忍窗 + staleness 显形(修静默回落纯 LGB)
3. glmcp 研报 background 信封真执行(修假成功红线)

**改动落点**(全部不在并行会话未提交 WIP 清单内):
`scripts/check_9999.ps1`(新)、`scripts/register_check_9999.ps1`(新)、`scripts/watchdog_9999.ps1`(仅头部 deprecated 注记)、`guanlan_v2/strategy/compute/dl_ensemble.py`、`ui/screen/screen-app.jsx`、`guanlan_v2/glmcp/server.py`、新测试文件。
**红线**:不碰 `console/api.py`、`console/tools.py`、`screen/api.py`、`screen/catalog.py`、`cpcv.py`、`model_workflow.py` 及并行 WIP 中的测试文件;每次提交前 `git branch --show-current` 确认 main。

**不做(范围外)**
- 「无可用因子」误导文案修复(在 screen/api.py,并行占用 → 挂账等其收工)
- P0 四期(picks 落盘 + 6 薄工具)= 独立立项
- nssm/服务化(用户选短命周期检查)

---

## 1. 修1 · 看门狗 → 短命周期检查

### 问题
常驻 PowerShell 循环看门狗在本机必冻死(计划任务拉起的常驻进程冻结),冻死后还持有全局 mutex 挡住新实例 → 9999 死了无人拉,双端全断(本日两次现场)。

### 设计
- **新 `scripts/check_9999.ps1`:单趟执行 ~10 秒即退,无常驻、无 mutex 长持。**
  每趟逻辑(移植旧 watchdog 的判定,去掉 while 循环):
  1. 读 `var/check_9999.state`(JSON:`{fails: n}`,短命进程的跨趟记忆;缺省 fails=0)
  2. 查 9999 监听:
     - **无监听** → 杀残留监听进程 → 等端口释放(≤30s,10048 守卫)→ 以旧 watchdog 同款命令行拉起 server(`cmd /S /C "python server.py >> var/server-9999.log 2>&1"`,`G:\financial-analyst\.venv` 解释器)→ 写 state fails=0 → 日志 → 退出(不等 boot 完成——下一趟自然复查)
     - **有监听 + HTTP 健康**(`GET /workflow/list` 200,5s 超时,Proxy=null)→ fails=0 → 退出
     - **有监听 + HTTP 死(卡死)** → fails+1 落 state;**fails ≥ 3(≈连续 3 分钟卡死)→ 强杀监听 PID + 等端口释放 + 拉起 + fails=0**
  3. 全程日志追加 `var/watchdog-9999.log`(沿用·带 [check] 前缀区分旧行)+ 5MB 轮转
  4. 单趟互斥:named mutex 只在本趟持有(秒级),拿不到(上一趟未退)直接退出——冻死的单趟被计划任务 ExecutionTimeLimit 强杀,mutex 随进程释放 → 自愈
- **计划任务注册**:新任务 `guanlan-v2-9999-check`,每 1 分钟重复、无限期、**`ExecutionTimeLimit=2 分钟`**(冻死的单趟被调度器强杀);开机边界由每分钟重复天然覆盖。注册脚本 `scripts/register_check_9999.ps1`(镜像旧 register 脚本形制)。
- **退役旧常驻**:`schtasks /delete` 旧任务 `guanlan-v2-9999-watchdog` + 杀现存 watchdog 实例;`watchdog_9999.ps1` 头部加 `[DEPRECATED 2026-07-02]` 注记(保留作历史/回退)。

### 自愈闭环论证
短跑检查自身不冻(生命周期 10s + 调度器 2min 强杀兜底);即便它拉起的 server 后来冻死,表现为「端口在 + HTTP 死」→ 连败计数 → 强重启。**MTTR ≤ 1 分钟(死)/ ≤3 分钟(卡)**,无任何单点常驻。

### 验证(真机)
1. 杀 9999 → ≤2 分钟内自动回来(日志见 [check] no-listener → start)
2. 伪造卡死:state 写 fails=2(+ 分支单验)→ 下一趟强杀重启路径走通
3. 连续观察 ≥10 分钟无误杀(健康时零动作)

---

## 2. 修2 · DL 新鲜度容忍窗 + 显形

### 问题
`_load_dl_for_date`(dl_ensemble.py:67-97)严格当日匹配:预测停更 → 源静默 inactive 退纯 LGB,只在 provenance reason 埋一句,徽章无从分辨「刻意纯 LGB」和「DL 断供」(审计挂账 bug b;本日现场:预测停 6-28、数据到 7-01)。

### 设计
- **`_load_dl_for_date(path, ld, score_col, max_stale_days=4)`**:当日无预测 → 在 `eval_date < ld` 且 `(ld − eval_date).days ≤ max_stale_days`(自然日,默认 4 ≈ 周末+1-2 交易日)内取**最近一期**截面;返回值扩为 `(s, df, cutoff, stale_days, fail)`(当日命中 `stale_days=0`);超窗 → fail=`"预测断供 N 日(>4),退出"`。
  - **PIT 安全**:旧预测=过去时点做出的预测,零前视;lookahead 判定沿用 cutoff 语义不变。
  - **当日命中路径行为不变**(回归守护:合成数据下 stale_days=0 时混合输出与旧实现 allclose 1e-12)。
- **`apply_dl_ensemble`**:`stale_days` 透传进每源 info(`sources[].stale_days`);active 且 stale>0 的源 reason 带「旧 N 日」。ICIR 自适应权重逻辑不变。
- **徽章**(ui/screen/screen-app.jsx:564-583 多源路径,文件不在并行 WIP):
  - 活跃源 `stale_days>0` → 显 `lstm(0.33·旧2日)`,tooltip 带天数
  - 存在因断供 inactive 的源且无任何活跃 DL → 主文案 `v4 · 纯 LGB ⚠DL断供`,tooltip 列各源断供天数
  - bump 选股页 HTML `?v`
- **可调**:`max_stale_days` 作 `DLSource` 可选字段(默认 4),`default_dl_sources()` 不显式传 → 全局默认。

### 测试
单测(合成 parquet):①窗内取最近一期 + stale_days 对;②超窗 inactive + reason 断供;③当日命中 stale_days=0 且与旧实现输出等价;④apply_dl_ensemble sources[] 透传 stale_days。真机:regen 后 provenance 出现 stale_days 字段(当日刚刷新 → 0)。

---

## 3. 修3 · glmcp 研报 background 信封真执行

### 问题
`ww_report_run`/`ww_etf_report_run` 的 impl 返回 `{"content": 受理文案, "background": {...}}` 信封;console 事件循环(console/api.py:749 `_spawn_bg`)才真执行。glmcp `dispatch_tool`(glmcp/server.py:45-59)直调 impl 只取 content → **返回「已受理」但啥也没跑 = 假成功红线**(审计挂账 bug a)。

### 设计
- `dispatch_tool` 结果后处理:`isinstance(result, dict) and result.get("background")` → 调新增 `_spawn_background_detached(bg: dict) -> str`:
  - `kind == "report"` → detached 子进程跑 **console 同款 CLI**:`financial-analyst report <code> [--asof <asof>]`,`cwd=仓根`,stdout/stderr 追加 `var/mcp_bg_<jobid>.log`,`creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`(不随 MCP 客户端/stdio 会话退出而死;产物由 CLI 落 reports store,与 console 路径同款)
  - `kind == "etf_report"` → detached python 子进程调 console `_run_etf_report_bg` 的**同一引擎函数**(plan 期逐字核对 console/api.py:581+ 的实际调用并镜像;绝不改 console 文件)
  - 其它 kind → 诚实返「该后台任务类型 MCP 通道暂不支持」
  - Popen 成功 → 返回**诚实受理凭证**:`已真启动后台研报(job <id> · <code> · 预计 5-8 分钟 · 产物落 reports store · 日志 var/mcp_bg_<id>.log)`——受理凭证,**绝不谎称完成**
  - Popen 失败 → 诚实报错(不吞)
- 写门(`GUANLAN_MCP_WRITE`)语义不动:原有 gated 判定在前,不变。
- 无 background 信封的工具:行为逐字节不变。

### 测试
新文件 `tests/test_glmcp_background.py`(monkeypatch `subprocess.Popen` + 假 impl 返信封):①凭证文本含 job id/日志路径且无「完成」字样;②Popen 收到正确 cmd/cwd/flags;③无信封路径原样;④Popen 抛错 → 诚实错误文本。真机:MCP 调一次 `ww_report_run`(小票)→ 验 reports store 真出产物 + 日志有内容。

---

## 4. 验证汇总
- 修1:杀 server ≤2 分钟自愈;伪造卡死强重启;10 分钟无误杀。
- 修2:4 单测绿 + 当日等价守护 + regen 后 provenance 含 stale_days。
- 修3:4 单测绿 + 真机 MCP 研报真出产物。
- 全量回归:test_dl_ensemble / test_screen_api / test_guanlan_mcp(**只跑不改** —— 它在并行 WIP;若断言与新行为冲突,冲突点写进新测试文件并报告用户,不动 WIP 文件)。

## 5. 风险与坑
- **计划任务**:重复间隔下限 1 分钟;`ExecutionTimeLimit` 必设(否则冻死单趟永不释放 mutex)。
- **误杀窗口**:server 冷启动 ~10-30s 端口未起,下一趟重复拉起 → 被 10048 守卫/单趟 mutex 挡住,安全(旧 watchdog Stop-Listeners 空跑 no-op 同款)。
- **测试打架**:`tests/test_guanlan_mcp.py` 在并行 WIP → 修3 新测试放**新文件** `tests/test_glmcp_background.py`,零碰撞。
- **stale 权重语义**:容忍窗不衰减权重(YAGNI,显形已足);要衰减后续在 DLSource 扩展。
- **etf_report 引擎函数签名**:以 console/api.py 实际调用为准(读不改),plan 期核对。
- **gat 第三源**:后来他人注册的 gat 源无预测文件 → 修2 后它会显「断供」——这是**诚实显形而非误报**(它确实从未供数);若徽章嫌吵,tooltip 承载即可,plan 期定主文案只对「曾供过数的源」亮 ⚠。
