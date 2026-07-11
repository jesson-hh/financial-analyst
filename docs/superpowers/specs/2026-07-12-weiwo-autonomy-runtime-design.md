# 帷幄智能体化一期设计:思考预算层 + autonomy 运行时 + 盘后自主复盘官

日期:2026-07-12 · 状态:待用户审阅

前置对话结论(已获用户逐项拍板):
- **路线 = 纯 B 内脑**:全国产模型(DeepSeek/Kimi),零外部 API 依赖;帷幄本体成长,不外挂大脑。
- **首里程碑 = 盘后自主复盘官**:每日盘后零人值守跑一圈,查成绩单→复盘落子→巡检数据→写晨报。
- **核心原则 = 结构补智能**:确定性 Python 运行时负重(队列/账本/门禁/文件交接),LLM 只做有界决策;
  国产模型便宜,用 N 采样 + 批判 agent 的扇出换单脑深度。
- **架构分层已明确**(见 §7 演进爬梯):工作空间=job 目录(非 worktree 沙箱)、记忆三层写入权不对称、
  调度台=最小核无大屏、workflow 创建权 v1 不给 LLM。

---

## 0. 背景:两个用户痛点

1. **帷幄不配叫智能体**:单脑单上下文顺序调工具;所有"自主回路"是写死的 Python 状态机,帷幄只能按按钮;
   无任务系统(goal 池挂账已久)、无子 agent、无议程。
2. **LLM 思考时间太短、秒出结果**:2026-07-12 六 agent 全仓审计(4 域接缝 + 挂账收集 + 完整性批判,
   179 次工具调用)+ 控制器亲手复核,结论=三层结构性封死思维链 + 缓存加速器,见 §1。

## 1. 审计结论:「思考短」的机制(全部 file:line 实证)

| 层 | 事实 | 证据 |
|---|---|---|
| 客户端 | `chat()` kwargs 仅 model/messages/temperature/tools/response_format;全文件零 max_tokens/stream/reasoning/thinking——调用方想给思考预算也无参数可传 | engine/financial_analyst/llm/client.py:227-264 |
| 配置 | 生产几乎全落非推理 SKU deepseek-chat;deepseek-reasoner 在册(config/llm.yaml:11)但唯一活路=落子 deep 档硬编码;agent_overrides 仅 industry_extract→kimi-k2.6 | config/llm.yaml:1-2,30-31;seats/api.py:831-835 |
| 调用形态 | 除帷幄主循环(15 轮工具循环,无 tool_calls 即收工)与研究批判环(≤5 轮但每轮只许一句话 diagnosis)外,全仓一次性单发 + response_format=json_object + 「只输出 JSON」;温度 0.1-0.3 | screen/llm.py:75-101;buddy/agent.py:199;research/api.py:108 |
| 缓存加速器 | 大量"LLM 判读秒出"是零调用回读:情绪 store 当日 (date,code) 命中即不打模型;macro 翻译永久缓存;市场温度 llm 块=纯文件读;backtest decision 缓存 | datafeed/sentiment.py;macro/translate.py:33-64;screen/market_temp.py:134-146;backtest/decision.py:356-360 |
| 超时天花板 | deepseek intl_clash httpx 120s;screen 缝 wait_for 45-120s;domestic 600s 仅 kimi 享有 | client.py:58-71 |
| 唯一反例 | industry kimi k2.6 研报抽取:双层 600s、实测 170s/約8k tokens——全仓唯一"真在想"的缝 | industry/llmx.py:212-230 |

**配置解析链修正(批判环坐实,推翻旧认知)**:`guanlan_v2/server.py:120-121` 在 create_app() 里
`os.environ.setdefault("FA_CONFIG_DIR", 仓内 config/)`,且 `_config.py:57-59` 把 FA_CONFIG_DIR 排在
workspace 指针之前 → **9999 进程内引擎缝实际吃仓内 G:/guanlan-v2/config/llm.yaml**;
pinned G:/financial-analyst/config/llm.yaml 只对进程外 CLI 生效(两份 default 恰好同为 deepseek-chat,
SKU 结论不受影响,但"生产换模型须改 pinned yaml"的旧操作口诀对 9999 运行时是错的)。

**覆盖面诚实声明**:9999 挂的是整个引擎 buddy server(server.py:161-163),研报管线 14-agent 约 20+
调用点、cards/refine、backtest/decision、watch/agent、factor forge/compose 等未逐缝审计;抽查表明
机制同构(deepseek-chat 单发+json_object),本设计的预算层对它们同样适用,但升档只动 §3.3 白名单。

**大盘判读停摆(控制器亲手实证)**:var/sentiment/market-202607.jsonl 判读**每天在写**(ts=2026-07-11),
但 `as_of` 停在 **2026-06-13 09:31**——LLM 每天在认真研判一个月前的旧闻。修法进复盘官职责(§5.4)。

## 2. 红线(不变式,全单适用)

1. 复盘官/autonomy 全链**只读 + 写报告**:绝不写 picks/正式信号/blend/seats 决策;绝不改模型/配方。
2. 长期记忆写入唯一通道 = 蒸馏 + matured 门 + 人审 confirm(现有轨道);子 agent 对帷幄记忆**只读**。
3. 诚实显形:失败段标"该段未完成"绝不编造;报告中每个数字带出处(工具名+日期);批判不过→段降级显形。
4. opt-in 默认关:日跑开关 `GUANLAN_REVIEW_DAILY=1` 只认 var/secrets.env(setx 对看门狗代际无效)。
5. UI 只填充不重建:console 页加晨报卡,不动现有布局。
6. 升档诚实:rerank 换 reasoner = 换 A/B 处理组定义;rerank_ab 档案与成绩单标注模型代次(§3.3)。

## 3. 交付单元一:LLM 思考预算层(底座硬化)

### 3.1 client.py 扩展(engine fork 内,向后兼容)
- `chat()` 增可选 `max_tokens: int | None = None`,非 None 时下传 create kwargs;所有现有调用不变。
- 座席级 httpx 超时覆盖:`agent_overrides` 支持 `timeout` 字段,for_agent 解析后按座席构造/复用
  对应超时的 httpx client(deepseek-reasoner 思考 1-3 分钟,120s intl_clash 天花板必须可放宽)。
- **reasoner 兼容性探针(实施首任务)**:真机验证 deepseek-reasoner 对 response_format=json_object 与
  tools 的支持;不支持则 deep 档走「系统提示内嵌 JSON 形状 + `_extract_json` 正则抢救」路径
  (引擎已有先例 buddy/server.py _comments_sentiment)。探针结论写进计划,不允许拍脑袋。

### 3.2 配置:思考档位(座席 = 模型 + 预算的命名组合)
config/llm.yaml `agent_overrides` 扩展为可带预算字段(缺省字段=现行为):
```yaml
agent_overrides:
  industry_extract: {provider: kimi, model: kimi-k2.6}          # 现存,extract 档
  rerank:           {provider: deepseek, model: deepseek-reasoner, max_tokens: 8192, timeout: 300}
  review_officer:   {provider: deepseek, model: deepseek-reasoner, max_tokens: 8192, timeout: 300}
  review_section:   {provider: deepseek, model: deepseek-chat}   # 复盘官段 agent,fast 档
```
三档约定:**fast**(deepseek-chat,填表型)/ **deep**(deepseek-reasoner + max_tokens + 长超时,判断密集型)
/ **extract**(kimi-k2.6,长文抽取)。守护测试钉住 schema(未知字段报错防笔误)。

### 3.3 首批升档白名单(本单只动这些缝,其余一律保持 fast)
- **行业重排 rerank**(rerank.py:46-50 经 screen/llm.py 底座):50 票全排列判断值得思维链。
  连带:rescore 归档与 rerank_ab pair 载荷加 `model` 字段(代次标注);ww_rerank_perf 显示模型代次;
  成绩单跨代次不混合归因(展示按代次分组)。
- **复盘官汇总段 + 批判段 + 蒸馏教训起草**(单元二新座席)。
- 明确不升:情绪 tag(三分类枚举)、macro 翻译、phrase 解析、pick_factors——填表型任务升档纯浪费。

### 3.4 帷幄长轮 token 预算闸(无人值守安全门)
- console 主 agent turn 累计 completion tokens 软预算:env `CONSOLE_TURN_TOKEN_BUDGET`(默认 0=关,
  不改现有交互行为;secrets.env 开)。超 80% 下轮注入「预算将尽,收敛作答」;超 100% 停止工具循环、
  以「预算耗尽」诚实收尾(绝不静默截断)。
- autonomy job 的调用数/token 预算由运行时账本负责(§5.2),两道闸独立。

### 3.5 单元一验收
- 单测:kwargs 传递(打桩)、座席解析含预算字段、schema 守护、代次字段落档。
- 真机:reasoner 探针记录;rerank deep 档跑一次真重排,归档含 model 字段;耗时/输出对照 chat 档留档。

## 4. 交付单元二:autonomy 运行时(骨架四件套)

```
guanlan_v2/autonomy/
  jobs.py           # job 池+账本:var/jobs/jobs.jsonl 事件流 + var/jobs/<job_id>/ 工作目录
  runtime.py        # 状态机 queued→running→gated→done/failed;预算帽;超时;断点续跑;单飞锁
  subagent.py       # 子 agent 派工:简报文件+工具白名单子集+LLM 循环+产物落文件
  playbooks.py      # playbook 注册表(v1 仅 review_officer 一个,写死 Python 编排,LLM 零编排权)
  review_officer.py # 盘后复盘官编排(§5)
  api.py            # 薄壳:GET /autonomy/jobs、GET /autonomy/report/latest、POST /autonomy/run
```

- **工具 = console tools.py impl 函数进程内直调**(白名单子集),绝不 HTTP 自调(协程内同步自 HTTP
  堵 loop 会招看门狗杀的既有红线);长调用走 asyncio.to_thread。
- **工作空间**:job 目录即短期记忆;段间文件交接(汇总 agent 只读段产物文件,不读原始上下文)。
- **记忆三层**:短期=job 目录 / 情景=jobs.jsonl+日报历史 / 长期=帷幄 keyed 记忆(子 agent 只读)。
- **预算帽**:每 job LLM 调用数上限(复盘官默认 12)+ 每段超时(默认 300s)+ 全 job 超时(默认 30min);
  超帽→job 标 failed 诚实显形,绝不静默续跑。
- **断点续跑**:重启后扫描 jobs.jsonl,running 状态的 job 标 interrupted;段产物已落盘的段跳过重跑。

## 5. 交付单元二续:盘后自主复盘官 playbook v1

### 5.1 段结构(段 agent=fast 档;汇总/批判/蒸馏草稿=deep 档)
| 段 | 干什么 | 工具子集(console impl) |
|---|---|---|
| A A/B 成绩单 | 读 rerank_ab 对,汇总 Δ/成熟度/模型代次;**出现 matured 对→deep 档起草「行业·」教训草稿进报告**,标"待人审·未入记忆" | rerank_perf_impl |
| B 落子复盘 | 台账/watcher 近况,决策 vs 后果对照,watcher 健康与预算消耗 | seats 台账读、picks 读 |
| C 数据+调度巡检 | /data/health、/screen/health 摘要;断供龄期;regen/rerank/DL 调度是否如期 | data_health、screen health |
| D 综合晨报 | 合并三段+明日待办(如"蒸馏已解锁,一键确认");deep 档 | 只读 A/B/C 产物文件 |
| E 批判 | 对 D 挑刺:每个数字有无出处、有无编造、失败段是否如实标注;不过→对应段标降级 | 只读产物文件 |

### 5.2 三项新职责(挂账并单,均为复盘官 job 内的确定性步骤)
1. **大盘 LLM 判读日更 + as_of 停摆根修**:复盘官盘后触发一次市场级 news_sentiment 强刷(经现有
   screen/news.py 缝);连带修 as_of 源头——查 news_search 路的大盘快讯截止时间为何冻在 06-13
   (实施时定位:喂给 judge_sentiment 的 market 列表来源),修数据源而非改字段。
2. **蒸馏收尾自动化**:matured 门放行时草稿自动生成(§5.1 段A);人审 confirm 仍是唯一入记忆通道。
3. **macro 温度计快照搭车**:复盘官 job 末尾顺手调 macro 快照采集一次(纯数据落盘,零 LLM)。

### 5.3 触发与开关
- `GUANLAN_REVIEW_DAILY=1`(secrets.env)+ 挂 regen 调度 tick:当日 regen+rerank 均落定后入队,
  每数据日至多一次;失败重试≤1。
- `POST /autonomy/run {playbook:"review_officer"}` 手动触发(单飞锁,与日跑互斥)。
- `CONSOLE_REVIEW_MODE=monitor` 同时开启(secrets.env;自学回路阶段1 已交付验真、现默认 off
  console/api.py:344-347;先影子观测攒证据,enforce 另议)。

### 5.4 产出与 UI
- 日报:`var/reports/daily/YYYY-MM-DD.md` + 同名 .json(结构化:各段状态/数字出处/降级标记/待办)。
- console 页晨报卡:最新日报渲染 + 历史列表 + "确认蒸馏"按钮(直通现有 confirm 门)。只加卡。
- 帷幄工具 `ww_review_report`(读最新/指定日日报);工具四处同步(specs+白名单+_SYSTEM_PROMPT+守护计数)。

### 5.5 rerank_ab 档案留存保护(捆绑小修)
seats/api.py rerank_ab 分支读取改为**按 kind 过滤的全文件流式扫描**,摆脱 read_picks 500 行尾窗
(现档案 783 行量级,顺读毫秒级;防日跑把 A/B 证据挤出窗口)。calibration 默认路径一字不动。

## 6. 交付单元三:落子 decide 持仓感知(捆绑独立任务)

- 现状:decide 链每 bar 独立研判无持仓上下文,买后无人喊卖、口头止损不执行(clock 只机械封顶);
  order 链已有持仓感知先例。当前落子 LLM 判断质量头号残余缺口(luozi 审计 07-11 挂账)。
- 修法:_decide_impl 的 prompt 增加持仓块(hold_entry:入场价/入场时间/浮盈亏/已持有 bar 数),
  fast/deep 两档同步;测试钉住"有持仓时 prompt 含持仓块、无持仓时不含"。
- 边界:只喂上下文,不改决策 schema、不动 clock 机械止损、不动信号混合。

## 7. 架构与演进(爬梯,写死边界防散焦)

| 里程碑 | 长出来的架构 |
|---|---|
| **1(本 spec)** | job 池+账本+运行时+子 agent 派工+文件交接=承重墙全量;固定 playbook;思考预算层 |
| 2 多 playbook | 调度仲裁(优先级/并发)、情景记忆深度回读、夜间自主研究程序(议程=从教训/拒稿反推实验) |
| 3 Planner 座席 | workflow 创建权下放(schema 内组合 playbook/节点积木,P4 执行器为底座),永不白纸编排 |
| 除非证伪不做 | per-agent 私有长期记忆、向量库、worktree 沙箱(直到 agent 要改文件)、调度大屏 UI |

## 8. 测试与验收

- 单测:job 状态机/断点续跑/预算帽拦截/playbook 编排(LLM+工具全打桩)/报告组装/批判降级路径/
  段产物文件交接/开关默认关/工具计数守护;单元一清单见 §3.5;全量回归绿。
- 真机 e2e(控制器亲手,9998 隔离;杀 9999 只为触发看门狗自愈):
  1. 手动 POST 触发复盘官全链:五段产物+晨报落盘+console 卡显形+数字对照真档案逐位核。
  2. reasoner 探针+rerank deep 档真跑一次,归档 model 字段坐实。
  3. 大盘判读强刷后 as_of 走到当日。
  4. 隔日验证调度自触发;交易日顺带验收 watcher 首个盘中 tick 与复盘向导 JudgeCard 真 LLM(既有欠账)。
- **验收标准:连续两晚零人值守出报,报告数字全可溯源,失败段全部诚实显形。**

## 9. 明确不做 / 独立小单(防散焦,证据见 2026-07-12 审计)

- **独立小单(不进本单)**:变体 universe 股池滚动;picks/A-B 成绩单选股页入口;calibration _closes
  NaN 过滤(seats/api.py:1026 族);落子死代码清扫;混合权重 w 分 regime 门控;研报 bear-advocate
  幻觉市值 prompt 修。
- **不做**:变体 LLM 决策层(07-11 已修,勿重复立项);落子进攻触发(6fb35d6 已解,残余=本 spec §6);
  glmcp 写门细粒度(默认锁死非本单路径);零星展示型 TODO(screen/api.py:1568、fundflow/pulse.py:42)。

## 10. 实施切分建议(writing-plans 输入)

三个 SDD 单元按依赖串行:**单元一(思考预算层)→ 单元二(autonomy+复盘官,依赖一的座席)→
单元三(decide 持仓感知,独立可并)**。分支从 main 新开;真机 e2e 控制器亲手;合 main 惯例;推远端须再问。
