# ETF Analysis Layer (子项目 B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`).

**Goal:** 对照个股 `stock-deep-dive`,建 ETF 深度分析多 agent 流水线,`fa etf-report 510300` 出研报(.md/.json/.html),消费子项目 A 的 `ETFLoader`。

**Architecture:** 复用引擎脊柱(Orchestrator/DAGNode/SubAgent/AgentMemory/load_preset/report 写盘/introspector,资产无关)。新建 ETF 专属 agent 类(放 `agent/etf/`),个股 agent 作模板 mirror。新 `etf-deep-dive.yaml` + ETF memory + `run_etf_report_oneshot` + `fa etf-report`。

**Tech Stack:** Python;fa `agent/`、`swarm/loader`、`tui.py`、`cli.py`;Pydantic;LLMClient;pytest(mock ETFLoader+LLM)。

**环境(隔离 worktree):** 全在 `G:/fa-etf-wt`,分支 `etf-data-layer-wt`(A 之上,**别切分支**)。测试:`PYTHONPATH="G:/fa-etf-wt/src" "G:/financial-analyst/.venv/Scripts/python.exe" -m pytest <path> -v`。测试**扁平** `tests/`。commit 只 add 指定文件,**无 Co-Authored-By**。参考 spec:`docs/superpowers/specs/2026-05-31-etf-analysis-layer-design.md`。

**核心模式(所有 ETF agent 遵循,读 `agent/base.py` + 对应个股件确认):** SubAgent 子类:类级 `NAME`、`OUTPUT_SCHEMA`(pydantic BaseModel)、`_execute(self, inputs: dict) -> dict`;`run()` 用 OUTPUT_SCHEMA 校验。LLM agent:`messages=[{system: SYSTEM_PROMPT + memory}, {user: json.dumps(upstream)}]`,`LLMClient.for_agent(NAME).chat(..., response_format={"type":"json_object"})`。注册:`tui.py:_ensure_registered` 加 `registry.register(EtfXxx)`。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/financial_analyst/agent/etf/__init__.py` | ETF agent 子包 |
| `agent/etf/quote_fetcher.py` `metrics_fetcher.py` | T1 数据(调 ETFLoader) |
| `agent/etf/{holdings,technical,flow,valuation}_analyst.py` | T2 四维分析师 |
| `agent/etf/{bull_advocate,bear_advocate,risk_officer,report_writer}.py` | T3 |
| `config/swarm/etf-deep-dive.yaml` (+ `_resources/config/swarm/` 副本) | DAG preset |
| `memories/<etf-agent>/*.md` (+ `_resources/memories_seed/` 副本) | ETF 知识库 |
| `tui.py` (改:`_ensure_registered` 注册 + `run_etf_report_oneshot`) / `cli.py` (改:`etf-report` 命令) | 驱动+CLI |
| `tests/test_etf_agents_*.py` / `test_etf_preset.py` / `test_etf_report_e2e.py` | 测试 |

---

## Task 1: ETF 数据 agent (etf-quote-fetcher + etf-metrics-fetcher)

**先读模板:** `agent/base.py`(SubAgent ABC)、`agent/tier1/quote_fetcher.py`、`agent/tier1/factor_computer.py`(纯 Python 数据 agent + OUTPUT_SCHEMA + 拿 loader)。**纯 Python 无 LLM。**

**Files:** Create `agent/etf/__init__.py`, `agent/etf/quote_fetcher.py`, `agent/etf/metrics_fetcher.py`; Test `tests/test_etf_data_agents.py`. Modify `tui.py`(注册).

- [ ] **Step 1: 失败测试**(mock ETFLoader)

```python
# tests/test_etf_data_agents.py
import asyncio
from financial_analyst.agent.etf.quote_fetcher import EtfQuoteFetcher
from financial_analyst.agent.etf.metrics_fetcher import EtfMetricsFetcher

class _FakeLoader:
    def fetch_etf_quote(self,*a,**k):
        import pandas as pd
        return pd.DataFrame({"trade_date":["2026-05-29"],"open":[4.9],"high":[5.0],"low":[4.8],"close":[4.92],"vol":[100],"amount":[49000]})
    def fetch_etf_meta(self,c): return {"name":"300ETF","total_fee":0.2,"benchmark":"沪深300","index_code":"000300.SH","fund_type":"ETF"}
    def fetch_etf_premium_discount(self,c): return {"realtime_premium_discount_pct":-0.1}
    def fetch_etf_nav(self,c,*a,**k):
        import pandas as pd
        return pd.DataFrame({"nav_date":["2026-05-29"],"unit_nav":[4.91]})
    def fetch_etf_flow(self,c,*a,**k): return {"latest_share_change":-1260.0,"aum_latest":1.37e7,"aum_unit":"wan_yuan"}
    def fetch_tracking_error(self,c,*a,**k): return {"tracking_error_annualized":0.0022,"window":60}
    def fetch_etf_holdings(self,c,*a,**k): return {"end_date":"20260331","holdings":[{"symbol":"600519.SH","ratio":9.0}]}

def test_quote_fetcher():
    a = EtfQuoteFetcher(loader=_FakeLoader())
    out = asyncio.run(a._execute({"code":"SH510300","asof_date":"2026-05-29"}))
    assert out["name"]=="300ETF" and out["total_fee"]==0.2 and "close" in out

def test_metrics_fetcher():
    a = EtfMetricsFetcher(loader=_FakeLoader())
    out = asyncio.run(a._execute({"code":"SH510300","asof_date":"2026-05-29"}))
    assert out["premium_discount"]["realtime_premium_discount_pct"]==-0.1
    assert out["tracking_error"]["tracking_error_annualized"]==0.0022
    assert out["holdings"]["end_date"]=="20260331"
```
> Adapt to base.py: if `_execute` is sync (not async) or constructor differs, match it (drop asyncio.run if sync). Allow `loader=` kwarg (default `ETFLoader()`).

- [ ] **Step 2: 跑确认失败** → FAIL (module missing)
- [ ] **Step 3: 实现** — SubAgent 子类,`NAME="etf-quote-fetcher"`/`"etf-metrics-fetcher"`,`OUTPUT_SCHEMA` pydantic(quote: price/returns/ma/vol/volume_ratio + meta;metrics: premium_discount/nav/flow/tracking_error/holdings 嵌套)。`_execute` 调 `self.loader.<method>` 组装。无 LLM。构造器 `loader=None → ETFLoader()`。
- [ ] **Step 4: 注册** — `tui.py:_ensure_registered`(读现有 register 用法照搬)。
- [ ] **Step 5: 跑通** → PASS。回归 `pytest tests/ -k "etf and agent" -q`。
- [ ] **Step 6: commit** `git add agent/etf/__init__.py agent/etf/quote_fetcher.py agent/etf/metrics_fetcher.py tui.py tests/test_etf_data_agents.py && git commit -m "feat(etf-b): tier-1 etf data agents (quote + metrics)"`

---

## Task 2: ETF 四维分析师 (holdings/technical/flow/valuation)

**先读模板:** `agent/tier2/fundamental_analyst.py`(LLM 分析师:SYSTEM_PROMPT + OUTPUT_SCHEMA score[-2,2]+bull/bear points + _execute build messages + LLMClient.for_agent + AgentMemory)。4 个都 mirror。

**Files:** Create `agent/etf/{holdings,technical,flow,valuation}_analyst.py`; Test `tests/test_etf_analysts.py`. Modify `tui.py`(注册 4 个).

- [ ] **Step 1: 失败测试**(mock LLMClient 返回合规 JSON)

```python
# tests/test_etf_analysts.py
import asyncio, json
from financial_analyst.agent.etf.holdings_analyst import EtfHoldingsAnalyst

def test_holdings_analyst(monkeypatch):
    import financial_analyst.agent.etf.holdings_analyst as m
    payload = {"holdings_score":1,"bull_points":["集中度合理"],"bear_points":["单票偏高"],
               "top_holding_weight":9.0,"sector_concentration_hhi":0.12,"index_methodology_note":"宽基"}
    class _C:
        def chat(self,*a,**k): return json.dumps(payload)
    monkeypatch.setattr(m, "LLMClient", type("X",(),{"for_agent":staticmethod(lambda n:_C())}))
    a = EtfHoldingsAnalyst()
    out = asyncio.run(a._execute({"etf-metrics-fetcher":{"holdings":{"holdings":[{"symbol":"x","ratio":9}]}}}))
    assert -2 <= out["holdings_score"] <= 2 and "bull_points" in out
```
> CRITICAL: read how the stock analyst imports/calls LLMClient + whether _execute is async; align the monkeypatch target + asyncio to the real call site. One test per analyst (4), each asserting score∈[-2,2] + the dim-specific field.

- [ ] **Step 2: FAIL** → **Step 3: 实现 4 个** — `NAME="etf-{holdings,technical,flow,valuation}-analyst"`,SYSTEM_PROMPT 写该维评分规则(holdings:集中度/指数方法;technical:价格 MA/RSI/突破;flow:申赎 regime/AUM/流动性;valuation:折溢价/跟踪误差/费率拖累),OUTPUT_SCHEMA=`{<dim>_score:int[-2,2], bull_points:list[str], bear_points:list[str], <dim 特有字段>}`。`_execute` 读上游 dict + memory 调 LLMClient。
- [ ] **Step 4: 注册 4 个** (tui.py)。
- [ ] **Step 5: PASS** + 回归。
- [ ] **Step 6: commit** `git add agent/etf/holdings_analyst.py agent/etf/technical_analyst.py agent/etf/flow_analyst.py agent/etf/valuation_analyst.py tui.py tests/test_etf_analysts.py && git commit -m "feat(etf-b): tier-2 etf analysts (holdings/technical/flow/valuation)"`

---

## Task 3: ETF 多空辩论 (bull + bear)

**先读模板:** `agent/tier3/bull_advocate.py` + `bear_advocate.py`(thesis_bullets + [V#]/[F#] 锚 + ≥2 条强制 + target_price + bear memory_mode=retrieval)。

**Files:** Create `agent/etf/bull_advocate.py` `bear_advocate.py`; Test `tests/test_etf_debate.py`. Modify `tui.py`.

- [ ] **Step 1-2: 失败测试**(mock LLM 返回 thesis_bullets;断言 ≥2 条 + 每条带 [V#]/[F#] + target_price 字段)。
- [ ] **Step 3: 实现** — `etf-bull-advocate`(V锚 V1主题顺风/V2净流入/V3折价/V4方法论/V5低费/V6流动性,SYSTEM_PROMPT 列锚),`etf-bear-advocate`(F锚 F1拥挤/F2跟踪漂移/F3高费/F4集中/F5溢价回归/F6清盘/F7杠杆衰减,memory_mode=retrieval)。OUTPUT_SCHEMA mirror 个股(thesis_bullets:list[str] + target_price_high/base 或 low/downside_pct)。≥2 条 + retry/placeholder 照搬。
- [ ] **Step 4-6: 注册 + PASS + commit** `git add agent/etf/bull_advocate.py agent/etf/bear_advocate.py tui.py tests/test_etf_debate.py && git commit -m "feat(etf-b): tier-3 etf debate (bull V-anchors + bear F-anchors)"`

---

## Task 4: ETF CRO + report-writer

**先读模板:** `agent/tier3/risk_officer.py`(risk_score[-2,0] + veto_flags + 硬否决) + `report_writer.py`(rating sum + action/target/stop/position + pydantic+sanity-fix + 写 .md/.json + markdown_body) + `_resources/memories_seed/report-writer/report_template.md`。

**Files:** Create `agent/etf/risk_officer.py` `report_writer.py`; Test `tests/test_etf_writer.py`. Modify `tui.py`.

- [ ] **Step 1-2: 失败测试** — CRO:mock LLM,断言 risk_score∈[-2,0] + veto_flags。writer:mock LLM 返回 markdown_body+5维dims+action,`_execute(out_dir=tmp)`,断言写出 `<tmp>/<code>_<asof>.md`+.json + `rating_overall==sum(dims)` + veto→position_pct==0。
- [ ] **Step 3: 实现** — `etf-risk-officer`(CRO,risk_score[-2,0] only,硬否决:持续溢价/低流动/跟踪爆裂/AUM清盘线/杠杆长持;veto→position 0;borrows_memory:[etf-bear-advocate])。`etf-report-writer`(OUTPUT_SCHEMA:rating_overall[-10,10]、rating_dimensions{holdings,technical,flow,valuation,risk}、action∈{buy,hold,sell,avoid,accumulate}、target_price>0、stop_loss>=0、position_pct[0,0.10]、markdown_body、summary_json;`_execute` 汇总上游→LLM→python sanity-fix(rating!=sum→覆盖、veto/rating<=0→position 0、action 一致)→写 .md+.json,8段模板)。复用个股 writer sanity 逻辑。
- [ ] **Step 4-6: 注册 + PASS + commit** `git add agent/etf/risk_officer.py agent/etf/report_writer.py tui.py tests/test_etf_writer.py && git commit -m "feat(etf-b): tier-3 etf CRO + report-writer (5-dim rating, .md/.json)"`

---

## Task 5: etf-deep-dive.yaml preset + introspector + 加载测试

**先读:** `config/swarm/stock-deep-dive.yaml`、`swarm/loader.py`、`agent/tier3/introspector.py`(通用则复用,否则建 `agent/etf/introspector.py` mirror)。

**Files:** Create `config/swarm/etf-deep-dive.yaml` (+ `src/financial_analyst/_resources/config/swarm/etf-deep-dive.yaml`);(如需)`agent/etf/introspector.py`;Test `tests/test_etf_preset.py`.

- [ ] **Step 1: 失败测试** — `load_preset("etf-deep-dive", ...)` 返回 DAGNode 列表,含全部 ETF agent name,deps 无环,report-writer 依赖 4 analysts+bull+bear+risk。
- [ ] **Step 2-3: 写 yaml**(按 spec DAG 表:variables code/asof_date;每 agent name+deps+input_keys;bear/CRO memory_mode:retrieval;CRO borrows_memory:[etf-bear-advocate])+ bundled 副本。introspector:先试复用个股(deps 指 etf-report-writer);prompt 若写死个股则 mirror etf 版。
- [ ] **Step 4-6: PASS + commit** `git add config/swarm/etf-deep-dive.yaml src/financial_analyst/_resources/config/swarm/etf-deep-dive.yaml tests/test_etf_preset.py [agent/etf/introspector.py] tui.py && git commit -m "feat(etf-b): etf-deep-dive swarm preset + introspector + load test"`

---

## Task 6: ETF memory(知识库 markdown)

**先读:** `agent/memory.py`(加载机制)+ 个股 `_resources/memories_seed/<agent>/` 结构。

**Files:** Create under BOTH `memories/<etf-agent>/` AND `src/financial_analyst/_resources/memories_seed/<etf-agent>/`:
- `etf-report-writer/report_template.md`(8段模板 + 5维评级规则 + AUM-tier 类比市值-tier:巨型 ETF 自下而上 alpha 归零)+ `etf_rating_system.md`
- `etf-bull-advocate/v_anchors.md`(V1-V6)· `etf-bear-advocate/f_anchors.md`(F1-F7)
- `etf-valuation-analyst/premium_discount_playbook.md` · `etf-flow-analyst/flow_regime_signals.md`
- `etf-risk-officer/veto_rules.md`(否决阈值:溢价/流动性/跟踪误差/AUM清盘线/杠杆)

- [ ] **Step 1: 写 memory md**(内容实打实,非占位)。
- [ ] **Step 2: 测试** — 起一个 ETF agent,`memory.load_all()`(或对应方法)非空 + 含关键串(rating_system 含 "AUM")。
- [ ] **Step 3: commit** `git add memories/ src/financial_analyst/_resources/memories_seed/ tests/test_etf_memory.py && git commit -m "feat(etf-b): etf agent memory (rating system / anchors / playbooks)"`

---

## Task 7: CLI `fa etf-report` + run_etf_report_oneshot

**先读:** `cli.py` 的 `report` 命令(~line 89)+ `tui.py:run_report_oneshot`(~line 635)+ `render_report`。

**Files:** Modify `tui.py`(加 `run_etf_report_oneshot`)、`cli.py`(加 `etf-report` 命令);Test `tests/test_etf_cli_report.py`.

- [ ] **Step 1-2: 失败测试** — CliRunner invoke `etf-report SH510300 --out-dir <tmp>`,mock `run_etf_report_oneshot`,断言 exit 0 + 调用。
- [ ] **Step 3: 实现** — `run_etf_report_oneshot(code, asof, out_dir, trace)` mirror run_report_oneshot(_ensure_registered → MemoryIndex → `load_preset("etf-deep-dive",...)` → Orchestrator.run({code,asof_date,out_dir}) → render_report)。`cli.py` `@app.command("etf-report")` mirror `report`(单 code 或 -f file,--asof/--out-dir/--trace)。
- [ ] **Step 4-6: PASS + commit** `git add tui.py cli.py tests/test_etf_cli_report.py && git commit -m "feat(etf-b): fa etf-report cli + run_etf_report_oneshot"`

---

## Task 8: 端到端测试(mock ETFLoader + LLM 跑全 DAG)

**Files:** Test `tests/test_etf_report_e2e.py`.

- [ ] **Step 1-2: 失败→实现测试** — mock ETFLoader(fake 全维数据)+ mock LLMClient(每 agent 按 NAME 返回合规 JSON),`run_etf_report_oneshot("SH510300", asof, tmp_out)`,断言:生成 `<tmp>/SH510300_<asof>.md`+.json;json rating_overall==sum(5 dims);action 合法。
- [ ] **Step 3: 跑全 ETF 测试** `PYTHONPATH=... pytest tests/ -k etf -q` → 全绿(忽略已知无关 xdxr/tick/watchlist StringDtype 预存失败)。
- [ ] **Step 4: commit** `git add tests/test_etf_report_e2e.py && git commit -m "test(etf-b): end-to-end etf report pipeline (mock loader+llm)"`

---

## Task 9: 真数据冒烟(我亲跑,需 LLM key)
- [ ] `fa etf-report SH510300`(真 LLM + A 数据,worktree env:PYTHONPATH + FA_QLIB_ETF_URI/FA_PARQUET_ROOT 指 G:/stocks + LLM key)。验:出 `SH510300_<asof>.md/.json/.html`,8段齐、5维评级合理、多空带锚、CRO 否决逻辑生效。

---

## 收尾
- 全量 `pytest tests/ -k etf` 绿。final code-review subagent 通审 B。
- A+B 一起待合并(worktree etf-data-layer-wt → main,量化窗口空闲时)。
- ⚠ 不改个股 stock-deep-dive;commit 无 Co-Authored-By。
