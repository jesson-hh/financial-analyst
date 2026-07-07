# datafeed 统一实时客户端 + stocks 探针补源(a-stock-data 对齐)设计

- 日期:2026-07-07
- 缘起:数据端体检(2026-07-06-data-seams-audit.md)T1/T2 主题 + 用户拍板「继续;实时客户端对照 a-stock-data 找遗漏并补充」。
- 红线:准确不幻觉;诚实降级(planned/error 显形,绝不伪造);零重造(端点归 stocks);PIT 回测链零改动;东财防封(串行限流,不并发轰炸)。

## 1. a-stock-data 对照结论(DF-1)

| 类别 | 清单 | 处置 |
|---|---|---|
| 已实现未暴露 | em_zb_pool(炸板)/em_dt_pool(跌停)/em_yzt_pool(昨涨停) | **注册即得**(函数已在 live_text_sources.py) |
| planned 8 源上游有现成实现 | fund_flow(120日)/lhb(全市场日表)/unlock/margin/block_trade/holder_change/dividend/tencent_quote | **全部补实现**(移植 vendored SKILL.md 参考实现) |
| 未登记高价值 | ths_hsgt_realtime(北向分钟)/eastmoney_industry_comparison(行业排名)/eastmoney_fund_flow_minute(分钟资金流)/eastmoney_lhb_stock(个股席位) | **新增实现** |
| 缓办(登记 planned) | ths_eps_forecast(与估值层重复,stocks 原评估=单独议)/iwencai_search(需 API key) | 登记 planned 显形,不实现 |
| 不接 | 期权层(sina options)/mootdx·百度K线(与腾讯主路径重复)/新浪三表(与 tushare 沉淀重复)/cls_telegraph(上游下线)/limit_up_sentiment(纯算术,观澜 astock 自有) | 记录于 stocks log,不登记 |

注册表 21 → **30**(28 可用 + 2 planned:eps/iwencai;catalog 不计)。

## 2. stocks 侧补源(DF-2,G:\stocks 非 git 仓:文件级验证+log.md)

- `src/data/live_text_sources.py` 新增 handler(移植 `third_party/a_stock_data/SKILL.md` 参考实现,统一走 `em_get` 串行限流/requests session/异常上抛由 probe 信封收):`eastmoney_datacenter` 助手 + `em_fund_flow_daily(code,page_size)`/`em_fund_flow_minute(code)`/`em_lhb_daily(date)`/`em_lhb_stock(code,look_back)`/`em_unlock(code,forward_days)`/`em_margin(code,page_size)`/`em_block_trade(code,page_size)`/`em_holder_change(code,page_size)`/`em_dividend(code,page_size)`/`tencent_quote(codes)`/`ths_hsgt_realtime()`/`em_industry_comparison(top)`。
- `src/data/live_sources.py`:8 个 planned 填 handler;新增注册 em_zb_pool/em_dt_pool/em_yzt_pool/eastmoney_fund_flow_minute/eastmoney_lhb_stock/ths_hsgt_realtime(alias northbound)/eastmoney_industry_comparison(alias industry_rank)+2 planned(ths_eps_forecast/iwencai_search);`_call_handler` 补必要 kind(codes_quote/no_args 等);adapter 别名同步。
- 测试:`scripts/test_live_text_sources.py` 扩(桩 http 逐 handler + 注册表状态断言);真机探针抽验 ≥4 新源。
- 旧 CLI `probe_live_text_sources.py` 不动(遗留冻结,新源仅经新门面)。

## 3. 观澜统一实时客户端(DF-3)

`guanlan_v2/datafeed/live_client.py`(新模块,中台件①):
- `probe(source, code="", date="", limit=20)`:子进程 `sys.executable` 跑 **`G:\stocks\scripts\probe_live_sources.py`(新 30 源 CLI)**,cwd=stocks 根,PYTHONIOENCODING=utf-8,timeout 90s;**模块级 threading.Lock + 跨调用最小间隔 1.0s**(补跨进程节流缺口);信封透传 `{ok, source_id, status(ok|planned|error), error, items, fetched_ts, pulled_at, note}`;items 剥 raw+顶层长串截 400(沿 ww_live_text 约)。
- `catalog(max_age_s=3600)`:probe catalog 结果模块级缓存;失败回静态 fallback 表(源 id+别名,与 stocks 注册表同步手抄一份,漂移由测试守护)。
- date/code 归一沿 live_text_impl 现约(涨停类 date 8 位否则拒;六位码提取)。
- **挂点改造**:①`console/tools.py::live_text_impl` 改调 client(工具契约 `{ok,source,code,date,rows,n,note,content,pulled_at}` 不变;`_LIVE_TEXT_SOURCES` 枚举扩到新源全集;planned 源返 ok:True+空+note="该源已登记未实现(planned)");②`macro/astock.py` 默认 live_fn 换 client 包装,**内部调用 limit 放开至 300**(修涨停 ≥50 饱和,agent 面 ww_live_text 仍夹 50);③`seats/news_marks.py` 不动(后续可选挂点,本批非目标)。
- 四点同步:无新工具(ww_live_text 原名扩容),计数 **51/76/55 不变**;`_SYSTEM_PROMPT` 工具描述句更新源清单;spec=本文。

## 4. 快赢(DF-4)

`GUANLAN_REGEN_DAILY=1` 写入 `var/secrets.env`(看门狗代际环境唯一可靠注入点),9999 重启后验 scheduler 真启(日志/首跑)。

## 5. 测试与验收

- 观澜:client 桩测(信封/节流/catalog 缓存/planned 透传)+ live_text_impl 回归(既有 7 测 + 枚举更新)+ astock 注入测;全量 pytest 绿。
- 真机:①新源探针 ≥4 个真返(资金流/龙虎榜/北向/炸板池);②`ww_live_text(source=eastmoney_fund_flow, code=000630)` 经 _wrap 全量 content;③macro 打板温度 zt_count 可 >50;④9999 重启后 regen scheduler 启动日志。
- 评审:对抗评审 workflow(红线/移植保真/节流/测试)后修。

## 6. 评审结论(wf_4cc6559d-830,三镜头;验证阶段因 Fable5 用量上限中断→改由 Opus 逐条自判)

5 条成立(0 反驳,验证未跑故按 file:line 逐条核实)+ 若干 Minor,全部处置:
- **Critical**:test_macro_astock 截断测(_ZT_LIMIT 50→300 未更新致红)→ 已修 + 加 64 家真值回归测。
- **Important①**(stocks 侧):炸板/跌停/昨涨停三池透传原始密钥行(p×1000)→ 委托 stocks 照 em_zt_pool 同款归一。
- **Important②**(stocks 侧):盘中分钟序列(fund_flow_minute/北向)头部截断=返回早盘而非最新 → 委托 stocks 改「最新在前」交付。
- **Important③**:注释宣称的「真机对账测」实不存在 → 已补 `test_static_sources_reconcile_with_stocks_registry`(读 stocks 注册表对账 source_id 30/30,缺席 skip)+ 改正注释。
- **Minor**:probe/catalog 对非 dict JSON 崩 → 加 isinstance 守卫(诚实降级);tencent 前缀/多码被 6 位提取毁 → tencent_realtime_quote 入 CODE_PASSTHROUGH(SH000001/逗号多码原样透传);no_args 丢 http(stocks 侧)→ 委托修;regen 开关 off-path → secrets.env 注释写明「删行+重启」。
- **存档不修**:concept_blocks 经统一信封逐板展开丢 total/concept_tags(统一 envelope 设计使然,属 stocks 契约;记忆挂账)。
