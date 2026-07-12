# 研报管线加强:证据包接流 + agent 交互加强 + 运行时修(设计)

日期:2026-07-12 · 状态:待用户审阅 · 范围:A+B+C 全量(用户已拍板)

## 0. 背景与诊断(2026-07-12 测绘,全 file:line 坐实)

研报管线 = 引擎 fork 的 17 节点波次 DAG(engine/financial_analyst/agent/orchestrator.py:55-91,
config/swarm/stock-deep-dive.yaml 15-95):波1 九个 fetcher/scanner 并行 → 波2 三 analyst →
波3 bull/bear advocate → 波4 risk-officer → 波5 report-writer → 波6 introspector。三层病根:

1. **数据断流(结构性)**:17 个 agent 对 guanlan_v2 全部新数据面零引用零例外(grep 坐实)。
   根因=进程边界(子进程导不到 guanlan_v2.*,news_pulse.py:5 docstring 自证),引擎内平行重造了取数。
   实证代价:mainline-classifier 读 G:/stocks 的 34 天旧 panel(06-08),而仓内 regen 产物 07-11 新鲜;
   news.sqlite 陈旧 9 天;47 源实时/资金流/龙虎榜北向/五档/统一情绪/产业链板全部闲置。
2. **交互结构缺陷**:
   - bear_advocate 幻觉市值真凶=它只吃 analyst 分类输出(FundamentalOutput 仅 mv_tier 无数值,
     fundamental_analyst.py:20-26),F4 失败模板(bear_advocate.py:25)硬编码"Sub-200亿"逼模型背模板编数。
   - risk_officer 三条 HARD RULE 死代码(risk_officer.py:28,34,37 引用 quote/factor-computer,
     但 deps 里没有,factor-computer 节点 06-04 已删)。
   - bull/bear 互不见对方;introspector 报告写完才跑不拦任何东西;17 agent 全 deepseek-chat 单发。
   - market-scanner 全市场 5000 码串行 for(market_scanner.py:118,历史实测 387s)。
   - risk-officer 对 news-reader/f10-reader 硬依赖(yaml:69-70)与其它 context 的 soft 化不一致。
3. **运行时病**:glmcp 后台 spawn 无 PYTHONPATH(glmcp/server.py:45-82)→吃 pinned 旧引擎→缺
   news-sentiment 注册→KeyError 起跑即崩(100%);console 路径设了 PYTHONPATH(console/api.py:289)正常。
   report_writer sanity 修正只进 md 附注不回写 .json(report_writer.py:326-402)。

## 1. 红线(不变式)

- 研报纯展示:绝不进 picks/信号/blend/seats;证据包只读平台数据,零写副作用。
- 诚实显形:证据包缺失/某数据面拉取失败 → 对应段降级并标注,绝不编造;报告每段带数据 as_of。
- 引擎自包含:引擎不 import guanlan_v2.*(进程边界不破);证据包经文件+env 传递,无包也能跑(退回现状)。
- 数字纪律:advocates/risk-officer 只许引用【证据】里的数字;introspector 校验不过 → 段标降级。
- ETF 研报线本期不动(共享 agent 的改动须不破坏 ETF 线,回归测试守护)。

## 2. 架构:证据包(Evidence Pack)

**传递缝**:guanlan_v2 侧起研报前构建 `var/reports/evidence/{code}_{yyyymmddHHMM}.json`,
路径经 env `FA_EVIDENCE_PACK` 传给子进程(console _call_buddy_report 与 glmcp spawn 两处都注入);
引擎新增零 LLM 节点 **evidence-loader**(波1):读 env 路径→JSON 校验→结构化输出;下游以
**soft_dep** 消费(包缺失=该节点 ok:False,下游段降级不塌报)。

**pack schema(生产器 guanlan_v2/reports/evidence.py,全部复用现成 datafeed/模块直调,零新拉取逻辑)**:
```json
{
  "code": "SH603986", "generated_at": "...", "sections": {
    "quote_live":   {"as_of":..., "price":..., "pct":..., "orderbook_summary":..., "ticks_summary":...},   // seats/live_book
    "fundflow":     {"as_of":..., "stock_main_net":..., "sector":..., "sector_rank":..., "market_main":...}, // fundflow+market_tape
    "board_eco":    {"as_of":..., "zt/zb/晋级率":..., "lhb":[...], "north_net":...},                          // market_tape
    "sentiment":    {"as_of":..., "tag":..., "read":..., "market_read":..., "market_tilt":...},              // datafeed/sentiment 当日
    "kuaixun":      {"as_of":..., "items":[{time,title}...≤8 相关条]},                                        // datafeed/kuaixun+过滤
    "chain":        {"seg":..., "quadrant":..., "therm":..., "industry_views":[...≤3 行业观点]},              // industry board
    "quant":        {"v4_rank":..., "v4_pct":..., "dl":{lgb,fc,lstm,gat}, "rerank":{before,after,stance}},   // screen 榜+rerank 档案
    "mainline":     {"as_of":..., "top":[...], "stock_hit":...},                                              // 仓内新鲜 panel
    "macro":        {"as_of":..., "astock_temp":..., "global_temp":..., "market_temp_stance":...},           // macro pulse+market_temp
    "holding":      {"held":bool, "avg_cost":..., "qty":..., "upl":...} | null                                // 台账(持仓视角)
  }
}
```
每 section 独立 try/except:失败=该 section null+errors 列表记原因(包永远能产出,内容诚实缺斤少两)。

**消费矩阵**:fundamental←chain/mainline/quant;technical←quote_live/fundflow/board_eco;
whale←fundflow/board_eco(北向/龙虎榜/主力);news-sentiment←kuaixun/sentiment(补充自拉);
advocates/risk-officer←**数值摘要行**(市值/PE/60日涨幅/主力净流/北向/情绪 tag——数字锚);
report-writer←全部+holding(持有该票时报告加「持仓视角」小节)+逐段 as_of 徽章。

## 3. 单元A:数据接流

1. `guanlan_v2/reports/evidence.py`:`build_evidence_pack(code) -> {ok, path, errors}`(上述 schema;
   全部进程内直调 datafeed/fundflow/live_book/industry/screen/macro 现成函数;section 级降级)。
2. 接缝注入:console/api.py `_call_buddy_report` 起子进程前 `build_evidence_pack`,env 加
   `FA_EVIDENCE_PACK=<path>`;glmcp spawn 同步注入(其 PYTHONPATH 修在单元C,同一 diff 顺手)。
3. 引擎 `tier1/evidence_loader.py`(零 LLM):读 env→校验→输出 sections;挂进 stock-deep-dive.yaml
   波1,下游按消费矩阵加 soft_deps + prompt 注入(各 agent 的 user prompt 加【平台证据·as_of】段)。
4. mainline-classifier 数据源指向仓内新鲜产物(env `FA_MAINLINE_PANEL` 由 9999 进程 setdefault 到
   guanlan_v2/strategy/vendor/artifacts/monthly_mainlines_panel.parquet;子进程继承)。
5. market-scanner 改造:优先吃 evidence pack 的 board_eco/market_tape 聚合(秒回),无包才退回串行扫
   (退回路径加 max_scan 下调与日志显形)。

## 4. 单元B:agent 交互加强

1. **数字锚根修**:advocates/risk-officer 的 upstream 注入 quote-fetcher 数值摘要+pack 数值行
   (市值 mv_yi/PE/ret60/主力净流/北向/情绪);系统提示加红线「只许引用【证据】中的数字,
   证据没有的数字一律写'证据未及'」;bear F4 模板改为引用真实市值变量。
2. **对抗交锋(+1 轮)**:DAG 改 bull(波3)→bear(波3.5,inputs 加 bull 输出,提示词=「逐条反驳
   bull 论点,不许回避」)→risk-officer(波4,升裁判:吃双方论点+证据数值,输出裁决与风险等级);
   保持 DAG 无环,总 LLM 调用 +0(bear 本来就要跑,只是晚一波+多吃一份输入)。
3. **思考档位**:config/llm.yaml agent_overrides 加 `bull-advocate/bear-advocate/risk-officer/
   report-writer → deepseek-reasoner + max_tokens 8192 + timeout 300`(单元一座席基建现成);
   其余节点保持 fast。**前置探针**:实测 9999 spawn 的子进程 find_config 命中哪份 llm.yaml
   (FA_CONFIG_DIR 继承与否),两 spawn 点 env 显式注入 FA_CONFIG_DIR=仓内 config 钉死(belt+suspenders)。
4. **introspector 升数字校验门**:从"事后感想"改为校验器——抽取报告中全部数字断言,逐一对照
   evidence pack+quote 数值;查无出处的数字 → 报告尾部「⚠ 未溯源数字」清单+对应段落标降级
   (不重写报告,诚实显形;复盘官批判环同款)。
5. **DAG 卫生**:risk-officer 三条死 HARD RULE 改为引用真输入(quote 数值已随数字锚进来)或删除;
   news-reader/f10-reader 对 risk-officer 降为 soft_deps;sanity 修正回写 .json(⑥根修)。

## 5. 单元C:运行时与显形

1. glmcp spawn 补 `PYTHONPATH=<repo>/engine`+`FA_CONFIG_DIR`+`FA_EVIDENCE_PACK`(与 console 路径
   逐项对齐;崩溃根修)。
2. 报告 md:每大段落尾部加 `〔证据 as_of: …〕` 徽章;缺证据段标「平台证据缺失,本段基于引擎自采数据」。
3. 进度 json 加 evidence_pack 摘要(哪些 section ok/null),前端抽屉可见。

## 6. 测试与验收

- 单测:pack 生产器 section 级降级/schema 守护;evidence-loader 无包降级;DAG yaml 校验(新节点/
  依赖无环);advocates 数字锚 prompt 注入(桩 LLM 捕 prompt 断言含真数值);introspector 校验器
  纯函数(数字抽取+出处匹配);sanity 回写;glmcp spawn env 断言。全量回归绿(ETF 线测试必须全绿)。
- 真机 e2e(控制器亲手):同一票(持仓票 SH603986 优先)出一份改后报告,对照改前存档:
  ①每段 as_of 徽章在;②bear 反驳段逐条引用 bull 论点;③市值等数字与 pack 逐位一致(幻觉根治验证);
  ④introspector 未溯源清单空或如实列出;⑤glmcp 路径真跑一份不崩;⑥「持仓视角」小节出现(台账持有时)。
- 验收标准:**改后报告每个数字可溯源到证据包或引擎自采数据,两条触发路径行为一致。**

## 7. 明确不做

- ETF 研报线升级(共享改动回归守护即可);研报结论进信号(红线);多轮自由辩论(1 轮反驳封顶,
  防成本失控);向量检索类"资料库"(YAGNI);报告 UI 重做(抽屉纯文本渲染现状保留)。

## 8. 实施切分建议(writing-plans 输入)

三单元一条分支串行:C1(glmcp 修+探针,小)→ A(pack+loader+接缝,大)→ B(交互,大)→
控制器真机对照验收。真机 e2e 亲手;合 main 惯例;推远端须再问。
