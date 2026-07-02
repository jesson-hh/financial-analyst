# P3 研究回路可视化 + 人审转正 · 设计 spec(2026-07-02)

> 背景:帷幄自主闭环四期第四期(收官)。P0(picks 落盘,`6aac973`)、P1(收益回流,`2b805e8`)、P2(自主研究回路,`b9c969e`)已合 main。
> P3 目标:研究回路的历史在落子界面可视化;draft 因子人审转正面(选股页 UI + 帷幄确认门工具);两个诚实性小洞顺手修。
> 用户已拍板:轮次卡两模式通吃;给帷幄转正工具(过确认门);**这期不做定时器**;两小洞都修。

## 0. 范围与红线

- **后端零新端点**:全部数据源已在(`GET /research/runs`、`GET /research/rounds`、`GET /factorlib/list`、`POST /factorlib/promote`);后端唯一改动=console 两工具+提示词。
- **不做定时器**:`guanlan_v2/research/api.py` docstring 的「零 env 开关、零定时器」承诺**原样保持**;回路仍只能人/帷幄显式发起。定时自主开跑(job runner)另立项。
- **UI 只填充不重建(用户红线)**:零新页面、零新弹窗、现有布局逐字保留;所有 UI 改动=落子右栏加一个默认折叠区块、选股页因子面板加一个折叠组、工作流页三处小修。
- 诚实红线:draft 徽章绝不冒充正式;转正即时性如实(「下次选股目录刷新后上货架」);状态四态(done/error/running/interrupted)全渲染;规则兜底轮前缀直显;后端恒 HTTP 200 诚实失败,前端判 `j.ok` 且 fetch 全 try/catch 降级 null/[]。
- 采纳红线:转正=确认动作(UI 原生 confirm / 帷幄 confirm 门);帷幄只能提请,批准权在用户。

## 1. 落子右栏「研究回路」卡(核心件)

**组件** `ResearchLoopCard`,加进 `ui/seats/luozi-panels.jsx`(与 RunPicker 并排;仓例:跨页无 import 机制,AILoopModal 视觉元素照抄一份是既定做法——先例 toast 四页各抄)。

**挂载**:`ui/seats/luozi-app.jsx` 右栏容器(:742-773)内、`OrderWatchPanel`(:750)之前,**两模式通吃**(研究历史是全局事,不分实盘/复盘)。

**形态**:
- 默认**折叠成一行头**:外壳照 RunPicker(`flexShrink:0, borderBottom:'1px solid var(--line)'`,panels:1028);头行=serif 12.5/600「研究回路 ✦」+ mono 9「N 次研究」+ 展开箭头(▸/▾)。不挤现有区块。
- 展开后:run 列表 `maxHeight≈300, overflowY:auto`。
- **run 行**:mono 时间戳 + goal 截断(~16 字)+ 状态章(done=✓ var(--dai) / error=✗ var(--zhu) / running=⟳ var(--jin) / interrupted=⚠ var(--ink-3),四态全渲染)+ 最佳 RankIC(`+x.xxxx` mono)+ 入库徽章(promoted.status:`draft·待人审` var(--jin) 实线 / `多因子未入库` 虚线 var(--line) / `入库失败` var(--zhu);无 promoted 不显)。
- **选中 run 行内嵌展开逐轮流水**(照 RunPicker 选中展开范式,朱左线+浅朱底):每轮=「第 {k+1} 轮 · {stage:propose→初始/improve→改进}」+ 过门标(gate.passed→✅ / 未过→· / failed→❌)+ RankIC ±x.xxxx + 样本外中文(VL 映射照 AILoopModal:robust稳健/degraded衰减/overfit疑似过拟合/insufficient期数不足/na不适用;VC 配色 robust=rgb(74,107,92)/degraded=#b8860b/overfit=var(--zhu))+ diag 截断(rule 兜底轮 diag 自带「(规则兜底·非 LLM) 」前缀,直显即诚实)+ failed 轮错误摘要。
- **「上画布」按钮**(run 行尾,仅 `workflow_saved.ok` 时显):跳工作流页深链 `location.href='../factor/观澜 · AI 工作流.html?load=<workflow_saved.id>'`,**透传 embed/ws**(先例 validation.jsx:475-476,防帷幄 iframe 内跌回独立态);绝不自动运行。
- 最佳轮高亮(全轮 max rank_ic)照 AILoopModal:边框 var(--zhu-soft)+底 rgba(168,57,45,0.04)。

**数据**:`ui/seats/luozi-data.jsx` 加 `lzResearchRuns(limit)` / `lzResearchRounds(runId)`(照 runsList/runDecisions 范式 :1250-1271:`API=window.GUANLAN_BACKEND||''`,失败回 null/[] 诚实降级);**rounds 拉回后剔掉 graph 字段再入 state**(每行带完整 DAG 很重,console 工具同款处理);文件尾导出块挂 `window.lzResearchRuns/lzResearchRounds`。

**刷新**:展开时拉取;展开状态下 60s 轮询(有 running run 时可感知进度);卡内部自持 state(不挂 mode/code 切换的重置 effect,:395-400 不碰)。

**注意**:rounds 接口**新在前**,渲染前反序成时间正序;轮次序号用 `r.k+1` 不用数组下标;因子名读 `(r.metrics||{}).factor` 或 `r.exprs.join(' + ')`(后端轮次行无顶层 factor)。

**缓存**:改 jsx 后 Edit bump `观澜 · 落子.html` 对应 `<script ?v=>`(luozi-data 与 luozi-panels 与 luozi-app 三个)。

## 2. 工作流页三处小填充(ui/factor/workflow.jsx)

1. **`?load=<wid>` 深链**:mount effect 读 URL 参 `load`,有则 `loadEntry({id: wid})`(loadEntry:965 已有 `/workflow/get` 兜底,零新逻辑);与既有 `?q=`/`?embed=`/`?ws=` 参并存。落子卡「上画布」的落点。
2. **修「0 节点」化妆缺陷**:HistoryModal 列表行(:1472-1473)读 `w.nodes` 改为 `(w.graph&&w.graph.nodes)||w.nodes`(服务端条目 nodes 嵌在 graph 下;载入功能本不受影响,纯显示修正)。
3. **draft 徽章**:FactorLibModal「全部因子」数据链(:1646-1652)并拉 `GET /factorlib/list` 把 `status` 合进行形(按 name 匹配);行渲染(:1720)用既有 `badge()` helper(:1669)对 `f.status==='draft'` 加 `badge('draft·待审','var(--zhu)')`。只显形,不放转正按钮(转正在选股页+帷幄)。

改完 bump 工作流页 html 的 `?v=`。

## 3. 选股页「待审 draft」区(ui/screen/screen-app.jsx)

`FactorLibrary` 组件(:733-806)内、fams.map(:802)之后、ic_note(:803)之前,插折叠组 **「待审 draft(研究回路)」**:

- 数据:组件内 fetch `API+'/factorlib/list?validate=false'` 过滤 `f.status==='draft'`(**必须另拉——XG_FACTORS←/screen/factors 链路永远拿不到 draft**,后端 catalog.py:74-75 单点过滤);需给 FactorLibrary 传 `API` prop(挂载点 :838 上层已持有)。
- 行:name + expr 截断 + ic(有则显,无则「—」诚实)+ 行尾**「转正」按钮** → `window.confirm('转正上架「<name>」?转正后进入选股因子目录。')`(先例 :167 delVariant)→ `POST /factorlib/promote {name}` → 判 `j.ok` → 成功后重拉 draft 列表 + `await window.xgLoadCatalog(API)`(触发后端 refresh_factor_defs,转正因子**立即上货架可勾选**)。
- 空态:无 draft 时整组不渲染(零噪音)。
- status 判断用 `f.status==='draft'`(正式因子**无 status 键**,别用 `!f.status` 反推);注意与 luozi-data.jsx:1130 的 GL status:'draft'(不可编译义)撞词不同义,互不相干。

改完 bump 选股页 html 的 `?v=`。

## 4. 帷幄两工具(计数 42→44 / 67→69 / 46→48)

**`ww_factor_drafts`**(confirm=False,cost="instant"):
- impl:`_self_get('/factorlib/list?validate=false')` → 过滤 `f.get('status')=='draft'` → content 列 name/expr/ic/description;空=「无待审 draft。研究回路达标产物会自动出现在这里。」
- reachable=["/factorlib/list"]

**`ww_factor_promote`**(confirm=True——帷幄只能提请,确认弹窗用户点头才执行;cost="seconds"):
- impl 照 factorlib_save_impl 骨架(:1267-1297):name strip 校验早退 → `_self_post('/factorlib/promote', {name})` → **判 `r.get('ok')`**(not_found 走 reason 非异常;promote 幂等,ok:true 不断言「刚由 draft 转正」)→ 成功 content 诚实:「已转正「{name}」,下次选股目录刷新后上货架(选股页/ww_screen_factors 可见)。」
- reachable=["/factorlib/promote"]

**四处同步**(锚点已核):WW_TOOL_TABLE 插在 ww_research_runs(:2036)之后、ww_capabilities(:2037)之前;tests/test_console_tools.py :613(42→44)/:619-620(67→69、42→44)/:1084(42→44)/:1086(67→69)+ expected 集(:1099-1134)追加 `/factorlib/promote` 与 `/factorlib/list`;tests/test_guanlan_mcp.py :13/:71/:100(46→48,:13 注释算术账 39→41);glmcp/README.md :4 与 :13 两处 46→48 + :26-28 写锁点名清单补 ww_factor_promote(confirm=True 自动 gated 归 GUANLAN_MCP_WRITE,tooltable 纯派生零代码改动)。

**提示词**(console/api.py):能力段 P2 句(:39)后补一句「另有(P3):列待审 draft 因子 ww_factor_drafts(只读)、draft 转正上货架 ww_factor_promote(需用户确认)」;纪律 14(:55)保留首句路由,末句改为「draft 因子转正(上选股货架)须经用户明确同意:先 ww_factor_drafts 列出待审 draft 给用户看,用户点头后用 ww_factor_promote(需确认)转正;绝不擅自转正、未转正前绝不宣称 draft 已可用于选股。」

**文案一致性顺手改**:tools.py :2014(ww_research_loop description)与 :851(_research_run_line verdict)两处「人审 POST /factorlib/promote 转正」改指「人审 ww_factor_promote/选股页待审区转正」,避免与新纪律自相矛盾。

## 5. 测试

**后端(pytest)**:两工具 impl 测试(fake _self_get/_self_post,打桩签名铁律 `lambda path, timeout=30:` / `lambda path, payload, timeout=120:`):drafts 列表/空态;promote 成功/not_found/缺 name;计数守护 44/69/48 全同步。

**前端(真机,独立端口不碰生产 9999)**:起 9998 测试 server,浏览器逐项核:
- 落子页:研究回路卡折叠头显形→展开列 run(含 P2 e2e 留下的真档案)→选中展开逐轮流水(过门标/样本外中文/诊断)→四态渲染(档案里有 done;interrupted 可临时注入档案行验证)→「上画布」深链跳工作流页且图铺上画布(?load= 生效)
- 选股页:造一个临时 draft(POST /factorlib/save status=draft)→待审区显形→点转正(confirm)→待审区消失+因子目录出现该因子→还原(删测试因子 JSON)
- 工作流页:历史列表节点数不再是 0;因子弹窗 draft 徽章显形
- 帷幄工具:进程内调 ww_factor_drafts/ww_factor_promote impl 冒烟
- 全部 jsx 改动的 `?v=` bump 核对;测后还原现场

## 6. 展望锚点(未立项)

- 定时自主开跑 job runner(goal 池文件 var/research_goals.jsonl + regen 式 opt-in 定时器,19 点错峰)——用户裁定这期不做
- 帷幄 cockpit 深链 run_id / SSE 实时轮次推送(现全轮询够用)
