# 研报管线加强(证据包+交互+运行时)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研报 17 节点 DAG 接上平台十大数据面(证据包),辩护人拿真数字交锋(bear 反驳 bull、deep 档),introspector 升数字溯源门,glmcp 崩溃根修。

**Architecture:** guanlan_v2 侧起报前构建证据包 JSON→env `FA_EVIDENCE_PACK` 传子进程;引擎零 LLM 节点 evidence-loader 读包,下游 soft 消费段级降级;DAG yaml 仓内 `config/swarm/` 落主本(FA_CONFIG_DIR 赢过 pinned workspace)。

**Spec:** docs/superpowers/specs/2026-07-12-report-evidence-pack-design.md(da48bf1)

## Global Constraints

- 红线:研报纯展示绝不进信号;引擎不 import guanlan_v2.*;无包退回现状(段级诚实降级,绝不编造);数字只许引用【证据】。
- ETF 研报线零回归(全量测试守护);advocates 内容重试(≤2)与瞬时重试机制不动。
- 提交:逐文件 add;尾注 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 后端/引擎生效须重启 9999——Task 8 控制器统一;子任务不碰生产进程。
- **yaml 遮蔽陷阱**:find_config 链=explicit→$FA_CONFIG_DIR→workspace(pinned G:\financial-analyst)→…→引擎 _resources。9999 进程及其子进程有 FA_CONFIG_DIR=仓内 config/(server.py:120 setdefault+T1 显式注入)。**所有 swarm yaml 改动落仓内 `config/swarm/stock-deep-dive.yaml`(新建,先字节拷现行版再改),引擎 `_resources/config/swarm/` 同步同内容**(bundled 兜底一致);守护测试钉 find_config 解析到仓内。

## 测绘事实(2026-07-12 三轮 recon,实施依据,不得推翻)

**引擎侧**:SubAgent=NAME/OUTPUT_SCHEMA 类属性+`async _execute(inputs)->dict`(inputs 键=上游节点名+code/asof_date/out_dir,值=model_dump 后 dict),base.run 包 pydantic 校验(base.py:26-66);零 LLM 样本=agent/market/overseas_market_scanner.py(DI collector 模式);注册=tui.py:188-274 import+tuple 列表(幂等),loader.py:92 build+DAGNode{deps,input_keys,soft_deps};orchestrator._ready(:24-32)按 deps∈done 动态分波→**bear 的 deps 加 bull-advocate 即天然晚一波,零 orchestrator 改动**;env 三层先例=mainline_classifier.py:21-24,63-65(ctor>env>默认,缺文件 raise=ok:False 显形);7 个 prompt 注入点见 recon 表(report_writer「确定性块」模式 :283-309 优先仿);introspector 现只吃 8 个下游结论(:85-93),无 md 抽数 util——溯源检查走结构化字段比对+LLM 列举,不做 md 正则。

**datafeed 侧(evidence.py 十 section 直调)**:①quote_live=live_book.read_orderbook/read_ticks/read_quote_failover(:29/:69/:96;probe 子进程级,每调秒级+全局≥1s 节流,section 限 3 调);②fundflow=fundflow.pulse.read_live(kind)(:216,SWR)+**个股级有源**:live_client.probe("eastmoney_fund_flow", code=, limit=5)(NEED_CODE 在册,guanlan 侧未验过——实施带探针,字段名以实测为准,best-effort try/except);③board_eco=market_tape.read_tape()(:193,永不阻塞,warming 诚实);④sentiment=sentiment.read_summary(code, day)(:164);⑤kuaixun=datafeed.kuaixun.fetch_kuaixun(200)+按票过滤(news_marks.py:172-180 先例:norm∈codes 或名字命中标题/摘要);⑥chain=rescore.industry_scores([code])(:92,ai_chain 外票诚实 None)+industry.aggregate.segment_detail(seg) 的 opinions;⑦quant=strategy.ranking.load_v4_ranking()+v4_pct_map()(:54/:84)+DL 逐票读三 parquet(var/v4_fincast_pred/dl_pred_lstm/dl_pred_gat,列 eval_date/instrument/pred_ret_5d,取 instrument==code 最新)+rerank=自扫 var/rescore_runs.jsonl 新→旧找首个 rerank.ok 且含票的 run(read_latest 只看末行不够);⑧mainline=ranking.mainline_status_map()+name_industry_map()(:125/:104,lru_cache 注意);⑨macro=screen.market_temp.build_market_temp()(:61,便宜安全;**别调 build_pulse 现拉**);⑩holding=seats.api._ledger_events/_ledger_replay 模块级直导(:336/:351),upl 用 quote_live 价自算或 null。evidence.py 放 guanlan_v2/reports/ 无撞名;var/reports/evidence/ 不存在可建(勿碰 var/reports/daily/=复盘官)。

**运行时**:console spawn=console/api.py:278-306(PYTHONPATH 在 :289,_ENGINE_DIR :190);glmcp spawn=glmcp/server.py:45-82(env 无 PYTHONPATH=崩因);报告落 out/{code}_{asof}.md+.json(report_writer:399-402),sanity(:326-397)不回写 json;progress={out}/{code}_progress.json(tui.py:704-723)。

---

### Task 1: glmcp/console spawn env 对齐 + config 路由探针(TDD)

**Files:** Modify `guanlan_v2/glmcp/server.py`(:45-82 spawn env)、`guanlan_v2/console/api.py`(:278-306 补 FA_CONFIG_DIR 显式);Test `tests/test_report_spawn_env.py`(新建)

- glmcp `_spawn_background_detached` env 改为(与 console 逐项对齐):
```python
        repo = Path(__file__).resolve().parents[2]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
               "PYTHONPATH": str(repo / "engine") + os.pathsep + os.environ.get("PYTHONPATH", ""),
               "FA_CONFIG_DIR": os.environ.get("FA_CONFIG_DIR", str(repo / "config"))}
```
- console `_call_buddy_report` env 补一行 `env.setdefault("FA_CONFIG_DIR", str(_REPO_DIR / "config"))`(_REPO_DIR 按 _ENGINE_DIR 同法派生)。
- 测试:①两处 spawn env 构造函数化/或直接断言构造出的 env dict 含两键且指向仓内;②**config 路由探针测试**:`subprocess.run([sys.executable,"-c","from financial_analyst._config import find_config; print(find_config('llm.yaml'))"], env=修后env)` 断言输出路径 == 仓内 config/llm.yaml(引擎在 PYTHONPATH)。
- 提交:`fix(report): glmcp spawn 补 PYTHONPATH/FA_CONFIG_DIR——pinned旧引擎崩溃根修+config路由钉死`

### Task 2: 证据包生产器 guanlan_v2/reports/evidence.py(TDD)

**Files:** Create `guanlan_v2/reports/evidence.py`;Test `tests/test_report_evidence.py`(新建)

- `build_evidence_pack(code: str, out_dir: Path | None = None) -> Dict`:返回 `{ok, path, sections_ok: [名], errors: {名: 原因}}`;落 `var/reports/evidence/{norm_code}_{YYYYMMDDHHMM}.json`(spec §2 schema)。十 section 各自独立 try/except(单 section 失败→该键 null+errors 记原因,包永远产出);全部经模块级薄函数(`_sec_quote_live/_sec_fundflow/...`)便于打桩;耗时预算:quote_live ≤3 次 probe、kuaixun 1 次、其余文件读/SWR——同步函数,调用方自行 to_thread。
- 个股资金流探针(实施步骤):真机跑一次 `live_client.probe("eastmoney_fund_flow", code="SH603986", limit=5)` 把实际字段名记进实现注释与报告;失败=该子块 null。
- rerank 检索:逐行倒扫 RUNS_PATH,首个 `rerank.ok` 且 rows 含票的 run 取 {rank_before, rank_after, stance, run_id, ts};扫描上限 200 行。
- 测试:十 section 全打桩(每 section 桩其薄函数);断言 schema 键全集/单 section 抛错→null+errors/文件真落盘/norm code;零网络(conftest 已有各隔离)。
- 提交:`feat(report): 证据包生产器——十数据面section级降级落盘`

### Task 3: 引擎 evidence-loader 节点 + yaml 主本迁仓内(TDD)

**Files:** Create `engine/financial_analyst/agent/tier1/evidence_loader.py`、`config/swarm/stock-deep-dive.yaml`(仓内新建=现行 pinned/内置版字节拷+本任务改动);Modify `engine/financial_analyst/tui.py`(:198-233 import+:235-274 注册表加一条)、`engine/financial_analyst/_resources/config/swarm/stock-deep-dive.yaml`(与仓内同步同内容);Test `tests/test_evidence_loader.py`(新建)

- EvidenceLoader 照 overseas_market_scanner 结构:`NAME="evidence-loader"`,`Output(BaseModel)`={ok: bool, generated_at: str = "", sections: dict = {}, errors: dict = {}, note: str = ""};`__init__(memory_root, pack_path=None)` 三层(ctor>env FA_EVIDENCE_PACK>None);`_execute`:无路径/文件不存在 → `raise FileNotFoundError("FA_EVIDENCE_PACK 未设置或文件不存在——平台证据缺失,下游段将降级")`(ok:False 显形);JSON 坏 → raise;成功返回 pack 原样(sections/errors 透传)。
- yaml 改动(本任务只加节点,消费接线在 T5/T7):`agents:` 加 `{name: evidence-loader, deps: [], input_keys: []}`。
- 测试:tmp pack 文件→节点 run ok:True sections 透传;无 env→ok:False error 含"平台证据缺失";坏 JSON→ok:False;**yaml 主本守护**:设 FA_CONFIG_DIR=仓内后 `find_config("swarm/stock-deep-dive.yaml"…按 loader 实际调用形态)` 解析到仓内副本、且仓内与 _resources 副本内容一致(防漂移);load_preset 全 17+1 节点 build 成功无环(registry 先 _ensure_registered)。
- 提交:`feat(engine): evidence-loader 零LLM节点+swarm yaml 主本迁仓内(FA_CONFIG_DIR 赢)`

### Task 4: 起报接缝——构包+env 注入+mainline 新鲜+scanner 捷径(TDD)

**Files:** Modify `guanlan_v2/console/api.py`(_call_buddy_report)、`guanlan_v2/glmcp/server.py`(report 分支)、`guanlan_v2/server.py`(FA_MAINLINE_PANEL setdefault)、`engine/financial_analyst/market/market_scanner.py`;Test `tests/test_report_seams.py`(新建)

- console:spawn 前 `pack = await asyncio.to_thread(build_evidence_pack, code)`(import 函数内);ok 时 env["FA_EVIDENCE_PACK"]=pack["path"];构包失败 try/except 只记日志不挡报(诚实=引擎侧 loader 显形降级)。glmcp 同款(其 spawn 上下文同步则直调)。
- server.py create_app:`os.environ.setdefault("FA_MAINLINE_PANEL", str(仓内 strategy/vendor/artifacts/monthly_mainlines_panel.parquet))`(MARKET_STATUS_PATH 同款先例 :126;子进程继承→引擎 mainline-classifier 吃新鲜面板,34 天陈旧根治)。
- market-scanner 捷径:`_execute` 开头读 env FA_EVIDENCE_PACK(mainline 三层同款),pack 存在且 board_eco/sections 可用→用其聚合(zt/zb/晋级率/行业 rank)直接产出 Output 对应字段、跳过串行扫并在输出 note 标"平台证据路径";否则退回现状(max_scan 5000→1500 收敛+日志)。
- 测试:console/glmcp 接缝(桩 build_evidence_pack 记调用+env 断言);server setdefault;scanner 两路径(桩 pack 文件→跳扫;无 pack→退回,max_scan 断言)。
- 提交:`feat(report): 起报构证据包+FA_MAINLINE_PANEL新鲜面板+market-scanner证据捷径`

### Task 5: 证据块+数字锚七处注入 + report-writer 徽章/持仓视角 + bear F4 修(TDD)

**Files:** Modify 引擎 7 文件(fundamental/technical/whale_analyst、bull/bear_advocate、risk_officer、report_writer)、仓内+_resources 两份 yaml(下游 deps/soft_deps/input_keys 加 evidence-loader;bull/bear/risk 的 input_keys 加 quote-fetcher);Test `tests/test_report_prompt_evidence.py`(新建,桩 LLM 捕 prompt)

- 七 agent 统一模式(report-writer「确定性块」仿):`ev = inputs.get("evidence-loader") or {}`;`if ev.get("sections"): block = "\n\n# 平台证据 (确定性 — 引用须带出处, 数字禁编造)\n" + json.dumps(按消费矩阵裁剪的 sections, ensure_ascii=False)`;SYSTEM_PROMPT 追加 2-3 句(有该块必须引用并标出处/证据没有的数字写"证据未及")。消费矩阵按 spec §2(fundamental←chain/mainline/quant;technical←quote_live/fundflow/board_eco;whale←fundflow/board_eco;advocates/risk←**数值摘要行**=市值/PE/ret60(来自 quote-fetcher inputs)+主力净流/北向/情绪 tag(来自 ev);report-writer←全部)。
- **数字锚**:bull/bear/risk 的 yaml input_keys 加 quote-fetcher(deps 不加=不改波次,quote 必然已 done——它是全体 tier2 的硬依赖,input_keys 引用安全);三文件 upstream 拼装加数值摘要行。bear F4 模板(bear_advocate.py:25)改为引用真实市值变量文案("当前市值 {mv_yi} 亿——若 <200亿 且 PE>100 且 ret60>50% 则援引 F4")。
- report-writer:①每大段落尾 `〔证据 as_of: {对应 section as_of}〕`(ev 缺→"〔平台证据缺失,本段基于引擎自采数据〕");②holding 段:ev.sections.holding.held → 渲染「# 持仓视角 (确定性)」小节(成本/数量/浮盈亏+提示词要求给持有者视角结论)。
- 测试:桩 LLM(_CapLLM 同款)逐 agent 断言:有 ev→prompt 含「平台证据」与对应 section 键;无 ev→prompt 不含且与旧逐字节等价(捕获对照);bear prompt 含真市值数字;report-writer 徽章/持仓段。
- 提交:`feat(report): 平台证据块+数字锚七处注入——幻觉市值断根/徽章/持仓视角`

### Task 6: bear 反驳波 + deep 档座席(TDD)

**Files:** Modify 两份 yaml(bear deps+input_keys 加 bull-advocate)、`engine/.../bear_advocate.py`(upstream 加 bull 输出+SYSTEM_PROMPT 反驳指令)、`config/llm.yaml`(agent_overrides);Test `tests/test_report_debate.py`(新建)

- **座席名探针(实施第一步)**:Grep 各 agent `LLMClient.for_agent(` 实际传名(推测=self.NAME,如 "bear-advocate"),overrides 键以实测为准;`config/llm.yaml` 加四座席 `{provider: deepseek, model: deepseek-reasoner, max_tokens: 8192, timeout: 300}`(bull/bear/risk-officer/report-writer;test_llm_config_guard schema 白名单已容)。
- bear:`bull_out = inputs.get("bull-advocate") or {}` 进 upstream dict;SYSTEM_PROMPT 加"你能看到 bull 论点,必须逐条针对性反驳或指出漏洞,不许回避"。
- 测试:yaml 解析后 bear.deps 含 bull(无环仍过 load_preset);桩 LLM 断言 bear prompt 含 bull thesis 文本;llm.yaml 守护测试更新(deep 档四座席存在断言,照 test_llm_config_guard 现有模式)。
- 提交:`feat(report): bear晚一波逐条反驳bull+辩护人/风控/写手升reasoner deep档`

### Task 7: introspector 数字溯源门 + sanity 回写 + 依赖卫生(TDD)

**Files:** Modify `engine/.../introspector.py`、`engine/.../report_writer.py`(:399-402 回写)、两份 yaml(introspector deps/input_keys 加 evidence-loader+quote-fetcher;risk-officer 的 news-reader/f10-reader 硬依赖降 soft_deps)、`engine/.../risk_officer.py`(三条死 HARD RULE:quote 数值现已进 inputs——改为引用真值,factor-computer 两条删除并在 prompt 注明"该规则依赖的节点已退役");Test `tests/test_report_introspector.py`(新建)

- introspector:ctx 加 evidence/quote;Output 加 `provenance_violations: List[str] = []`;确定性检查(纯函数 `_check_provenance(summary_json, quote, ev) -> List[str]`:target_price/stop_loss 相对现价倍数合理带出处、position_pct 与 veto 一致——沿用 sanity 语义)+LLM 列举报告正文未溯源数字断言;violations 非空 → 对已落盘 md **追加**尾节「## ⚠ 未溯源数字(introspector 校验)」+逐条(append-only 不重写);_pending_introspections 落盘红线保留。
- report_writer:sanity 修正后的 parsed 回写 .json(⑥根修——json.dumps 用修正后 dict)。
- 测试:_check_provenance 用例矩阵;md 追加(tmp out_dir);sanity 回写(json 与 return 一致);yaml soft 化断言。
- 提交:`feat(report): introspector数字溯源门+sanity回写json+死规则/硬依赖卫生`

### Task 8: 全量回归 + 真机对照(控制器亲手)

- [ ] 全量 pytest 全绿(ETF 线零回归);杀 9999 自愈。
- [ ] 备份 out/ 现存 SH603986 旧报告;console 路径出一份改后报告(持仓票):对照 spec §6 六验收点(as_of 徽章/bear 逐条反驳/市值与 pack 逐位一致/未溯源清单/持仓视角/deep 档耗时记录)。
- [ ] glmcp 路径真跑一份不崩(崩溃根修坐实);进度 json 含 evidence 摘要。
- [ ] 台账收官;终审;合 main(推远端须再问)。
