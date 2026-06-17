# F10 富化 Batch 2(估值根治 + 身份注入 + F10深挖)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 接上轮 F10 富化已用的数据;把 `G:\stocks` 里"有但没用"的几块接进研报:A 用 daily_basic 真 PE/PB/MV 根治估值(含 PE)、B 注入本票身份(名称/行业/地区)、C 深挖 F10(股东/主力/龙虎榜异动)。

**Architecture:** 沿用上轮:`data/f10_corpus.py`(确定性解析+PIT)+ `quote_fetcher` 兜底链 + 薄消费方。本轮加 1 个新小模块 `data/stock_basic.py`,扩 `f10_corpus` 3 个解析器,改 `quote_fetcher` 窗口与身份字段,`report_writer`/`f10_reader` 浮出新事实。

**Tech Stack:** Python 3.13, pytest, pandas。**非 git 仓**:"Checkpoint"=pytest 绿,不 commit。引擎在 `engine/financial_analyst/`,测试 `tests/`(conftest 已挂 sys.path)。

**红线(全程):** 数字/日期全确定性抽取,逐字喂 LLM;缺料→None/空/"无",绝不编造;PIT 把 `date>asof` 行裁掉、季度/报告期按披露滞后取可见最近期(复用 `f10_corpus._visible_date`)。解析器**必须跳过空行 + box 分隔线**(无 `｜` 的行 `continue` 不结表)——见现有 `_parse_broker`/`_parse_lhb` 写法,照抄该 idiom。

**真数据 anchors(写测试用,均来自 000630 实测):**
- daily_basic 2026-06-09:`pe_ttm=31.87, pb=2.28, total_mv=8380900(万元=838.09亿), circ_mv=6964400, turnover_rate=3.99`。
- tushare_stock_basic:`ts_code=000630.SZ, name=铜陵有色, area=安徽, industry=铜, market=主板, list_date=19961120`。
- 股东研究(截至 2026-03-31):A股户数 86.6667万;控股股东=铜陵有色金属集团控股有限公司(45.58%);实控人=安徽省国有资产监督管理委员会;top1 集团 38.4575亿/占A股34.51%/其他/未变;top2 香港中央结算 4.5631亿/4.10%。
- 主力追踪(更新 2026-04-15):机构持股汇总最新**完整**期=2025-12-31(机构数量587、累计持仓比例13.29%、基金持仓比例13.29%);2026-03-31 列标"更新中/未完"须跳过。户数趋势最新 2025-09-30=26.9241万户。
- 龙虎榜单 §3异动:`【交易日期】2026-02-03 ... 振幅:15.55% 成交量:14.2239亿股 成交金额:102.3364亿元`。

---

## Task 1(A):quote_fetcher daily_basic 窗口加宽

**Files:** Modify `engine/financial_analyst/agent/tier1/quote_fetcher.py`; Test `tests/test_quote_fetcher_dailybasic.py`

- [ ] **Step 1: 失败测试** —— 假 loader:daily_basic 只在"asof 前 ~7 天"那行有数据(5天窗口外、20天窗口内),断言用上真 pe/pb/mv,且不走 F10(`f10_valuation` is None)。

```python
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path: sys.path.insert(0, str(_ENGINE))
import pandas as pd
from financial_analyst.agent.tier1 import quote_fetcher as qf

class FakeLoader:
    def fetch_quote(self, code, start, end):
        idx = pd.date_range("2026-03-20", periods=80, freq="D")
        return pd.DataFrame({"close":[6.25]*80, "vol":[1e6]*80}, index=idx)
    def fetch_daily_basic(self, code, start, end):
        s = pd.Timestamp(start); e = pd.Timestamp(end)
        if s <= pd.Timestamp("2026-06-09") <= e:
            return pd.DataFrame([{ "pe_ttm":31.87,"pb":2.28,"ps_ttm":None,"dv_ttm":None,
                                   "total_mv":8380900.0,"circ_mv":6964400.0,"turnover_rate":3.99}],
                                index=[pd.Timestamp("2026-06-09")])
        return pd.DataFrame()

def test_widened_window_recovers_real_pe(tmp_path):
    agent = qf.QuoteFetcher(memory_root=tmp_path, loader=FakeLoader())
    out = asyncio.run(agent._execute({"code":"SZ000630","asof_date":"2026-06-15"}))
    assert out["pe"] == 31.87 and out["pb"] == 2.28
    assert round(out["mv_yi"],2) == 838.09        # total_mv/10000
    assert out.get("f10_valuation") is None        # 没走 F10 兜底
```

- [ ] **Step 2: 跑,确认失败** — `python -m pytest tests/test_quote_fetcher_dailybasic.py -q`(现 5 天窗口→空→走 F10 或 None,断言失败)
- [ ] **Step 3: 改** — `quote_fetcher.py` 把 daily_basic 回看窗口从 `timedelta(days=5)` 改为 `timedelta(days=20)`(覆盖 ~4 交易日滞后 + 周末)。其余不动(已 `row=db.iloc[-1]` 取最近≤asof 行;F10 兜底仍只在 `mv_yi is None` 触发)。
- [ ] **Step 4: 跑,确认通过**;Checkpoint `python -m pytest tests/test_quote_fetcher_dailybasic.py tests/test_quote_fetcher_f10.py -q` 全绿(F10 兜底测试仍绿:它的 fake loader daily_basic 返空)。

---

## Task 2(B):本票身份注入

**Files:** Create `engine/financial_analyst/data/stock_basic.py`; Modify `quote_fetcher.py`; Test `tests/test_stock_basic.py`

- [ ] **Step 1: 失败测试**

```python
import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path: sys.path.insert(0, str(_ENGINE))
from financial_analyst.data import stock_basic as sb

def test_get_basic_real():
    b = sb.get_basic("SZ000630")           # 也接受 000630 / 000630.SZ
    assert b["name"] == "铜陵有色" and b["industry"] == "铜" and b["area"] == "安徽"
    assert b["market"] == "主板" and b["list_date"] == "19961120"

def test_get_basic_missing_is_none():
    assert sb.get_basic("SZ999999") is None
```

- [ ] **Step 2: 跑,确认失败**(no module)
- [ ] **Step 3: 实现 `stock_basic.py`** —— 读 `parquet/tushare_stock_basic.parquet`(`ts_code` 形如 `000630.SZ`);`get_basic(code)` 把 `SZ000630`/`000630` 归一成 `000630.SZ` 查;命中返 `{name,area,industry,market,list_date(str)}`,无→None;路径常量 `BASIC_PATH=os.environ.get("GL_STOCK_BASIC", r"G:\stocks\stock_data\parquet\tushare_stock_basic.parquet")`,读不到→None(诚实降级,不抛)。
- [ ] **Step 4: 注入 quote_fetcher** —— `QuoteOutput` 加 `name/industry/area/market/list_date: Optional[str]=None`;`_execute` 末尾 `try: b=stock_basic.get_basic(code); except: b=None`,有则填这 5 个字段(供所有下游 agent 在 prompt 里看到本票身份)。
- [ ] **Step 5: 跑,确认通过**;Checkpoint 三测全绿(stock_basic + quote_fetcher_f10 + dailybasic)。

---

## Task 3(C1):f10_corpus `_parse_holders`(股东研究)

**Files:** Modify `engine/financial_analyst/data/f10_corpus.py`;新增 fixture `tests/fixtures/f10/sz000630/股东研究_20260521.txt`;Test 加进 `tests/test_f10_corpus.py`

结构(`｜`分隔):①`【1.控股股东与实际控制人】` 两行:控股股东｜名称(比例)、实际控制人｜名称。②`【4.股东变化】` 抬头自由行 `截至日期：YYYY-MM-DD 十大流通股东情况 A股户数:86.6667万 ...`;表 `股东名称｜持股数(股)｜占流通股比(%)｜股东性质｜增减情况(股)`,**长名字会折行**(续行 col0 有名字尾、其余空)→只在"持股数单元非空"时算一行,续行跳过。

- [ ] **Step 1: 建 fixture**(从真文件裁:控股股东两行 + 截至日期自由行 + top3 流通股东含一条折行名,UTF-8,保留 `｜`)。
- [ ] **Step 2: 失败测试**

```python
def test_parse_holders():
    h = fc._parse_holders(_fixt("股东研究"), asof=None)
    assert h["report_date"] == "2026-03-31"
    assert h["a_share_holders"] == 866667.0          # 86.6667万 户
    assert "铜陵有色金属集团" in h["controlling_holder"]
    assert h["top_holders"][0]["name"].startswith("铜陵有色金属集团")
    assert h["top_holders"][0]["pct"] == 34.51
    assert h["top_holders"][1]["name"].startswith("香港中央结算")

def test_parse_holders_pit_too_early():
    assert fc._parse_holders(_fixt("股东研究"), asof="2024-01-01") is None
```

- [ ] **Step 3: 实现 `_parse_holders(text, asof)->dict|None`** —— 抽 控股股东/实控人(`_cells` 取第2格)、截至日期(`_find_date` 自由行)、A股户数(regex `A股户数:([\d.]+)万`→×1e4)、top_holders(表内 `｜`行:name=cells[0]、shares=`_num`、`占流通股比` 形如 `34.51 占A股`→取数字 pct;持股数空→续行跳过)。PIT:`_visible_date(report_date)<=asof` 否则 None(asof=None→取)。照抄 `_parse_broker` 的"跳空行/分隔、首格非日期不算"idiom。
- [ ] **Step 4: 接线** —— `_CAT_PARSERS["股东研究"]="holders"`;`load_facts` 里 `elif kind=="holders": facts.holders=_parse_holders(txt, asof)`(holders 字段从 None 占位变真填)。
- [ ] **Step 5: 跑通**;Checkpoint `pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 4(C2):f10_corpus `_parse_main_capital`(主力追踪)

**Files:** Modify `f10_corpus.py`;fixture `tests/fixtures/f10/sz000630/主力追踪_20260415.txt`;Test 进 `test_f10_corpus.py`

结构:①`【1.机构持股汇总】` 期列表表(`报告日期｜2026-03-31｜2025-12-31｜...`,行=机构数量/累计持有/累计持仓比例/基金持股/基金持仓比例;**最新列可能含"未完/更新中"→该期视为不完整跳过**)。②`【2.股东户数变化】` 行表(`截止日期｜股东户数｜变动户数｜变动幅度%｜股价｜户均流通股｜较上期变化%`)。

- [ ] **Step 1: 建 fixture**(机构持股汇总:表头期 + 机构数量行 + 累计持仓比例行 + 基金持仓比例行,其中最新列写 `1(更新中)`/`13.29%` 等;户数变化:2-3 行。UTF-8 保留 `｜`)。
- [ ] **Step 2: 失败测试**

```python
def test_parse_main_capital():
    m = fc._parse_main_capital(_fixt("主力追踪"), asof=None)
    assert m["report_period"] == "2025-12-31"          # 跳过 2026-03-31 未完
    assert m["inst_count"] == 587
    assert m["inst_holding_pct"] == 13.29
    assert m["holder_count_trend"][0]["count"] == 269241.0   # 26.9241万户(最新期)

def test_parse_main_capital_pit():
    m = fc._parse_main_capital(_fixt("主力追踪"), asof="2025-11-01")
    assert m["report_period"] <= "2025-09-30"          # 12-31 可见日 2026-04-30 > asof 被裁
```

- [ ] **Step 3: 实现 `_parse_main_capital(text, asof)->dict|None`** —— §1 解析期列表(同 `_parse_valuation` 思路:抓 `报告日期` 行得期;每行 label 对齐期值;含 `未完/更新中` 的单元→该期标不完整);选 `_visible_date(p)<=asof` 且**完整**的最近期(asof=None→最近完整)。inst_count/inst_holding_pct/fund_holding_pct 取该期。§2 户数趋势:逐行 `{date, count(万→×1e4), change_pct}`,PIT 裁 `date>asof`,按日期倒序。
- [ ] **Step 4: 接线** —— `F10Facts` 加字段 `main_capital: Optional[Dict]=None`;`_CAT_PARSERS["主力追踪"]="main_capital"`;`load_facts` `elif kind=="main_capital": facts.main_capital=_parse_main_capital(txt, asof)`。
- [ ] **Step 5: 跑通**;Checkpoint 全绿。

---

## Task 5(C3):f10_corpus lhb 扩面(§3异动 + 尝试 §2/§4)

**Files:** Modify `f10_corpus.py`(扩 `_parse_lhb`);更新 fixture `龙虎榜单_20260601.txt` 增 §3 段;Test 进 `test_f10_corpus.py`

§3 结构(自由行):`【交易日期】2026-02-03 日振幅达15%,振幅:15.55%  成交量:14.2239亿股 成交金额:102.3364亿元`。

- [ ] **Step 1: fixture** —— 在现有 `龙虎榜单` fixture 末尾加 `【3.涨跌幅异动】` + 2 条 `【交易日期】...振幅:..%  成交量:..亿股 成交金额:..亿元`(含一条 `date>asof` 的供 PIT 测)。
- [ ] **Step 2: 失败测试**

```python
def test_parse_lhb_abnormal():
    a = fc._parse_lhb(_fixt("龙虎榜单"), asof=None)["abnormal"]
    assert a[0]["date"] and a[0]["amplitude_pct"] == 15.55
    assert a[0]["amount"] == 102.3364e8        # 亿→元
def test_parse_lhb_abnormal_pit():
    a = fc._parse_lhb(_fixt("龙虎榜单"), asof="2026-02-10")["abnormal"]
    assert all(x["date"] <= "2026-02-10" for x in a)
```

- [ ] **Step 3: 实现** —— 在 `_parse_lhb` 里加 §3 解析:逐行 regex `【交易日期】(20\d\d-\d\d-\d\d).*?振幅:([\d.]+)%.*?成交量:([\d.]+)亿股.*?成交金额:([\d.]+)亿元` → `{date, amplitude_pct, volume(亿股×1e8), amount(亿×1e8)}`,PIT 裁 `date>asof`,填 `lhb["abnormal"]`。**§2资金流向/§4大宗交易**:打开真文件看结构,能干净解析就填 `moneyflow`/`block_trades`(同 idiom),**结构吃不准就保持空列表 + 在 provenance 记一句"§2/§4 未解析"——绝不瞎填**。
- [ ] **Step 4: 跑通**;Checkpoint `pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 6:消费方浮出新事实(holders/main_capital/abnormal)

**Files:** Modify `engine/financial_analyst/agent/tier1/f10_reader.py` 与 `engine/financial_analyst/agent/tier3/report_writer.py`;Test `tests/test_report_writer_ownership.py`

- [ ] **Step 1: 失败测试**(report_writer 纯函数渲染股东/主力,逐字真值,空则"无")

```python
from financial_analyst.agent.tier3 import report_writer as rw
def test_render_ownership_section():
    facts = {"holders":{"report_date":"2026-03-31","controlling_holder":"铜陵有色金属集团控股有限公司(45.58%)",
                        "a_share_holders":866667.0,"top_holders":[{"name":"铜陵有色金属集团控股有限公司","pct":34.51}]},
             "main_capital":{"report_period":"2025-12-31","inst_holding_pct":13.29}}
    s = rw.render_ownership_section(facts)
    assert "铜陵有色金属集团" in s and "45.58" in s and "13.29" in s and "86" in s
def test_render_ownership_empty():
    assert "无" in rw.render_ownership_section({"holders":None,"main_capital":None})
```

- [ ] **Step 2: 跑失败** → **Step 3: 实现** `render_ownership_section(facts)->str`(确定性:控股股东/实控人/A股户数/top3 流通股东 + 机构持仓比例;空→"股东与主力:无")并接进 `report_writer._execute`(同 `render_broker_section` 注入法,facts 取自同一 `load_facts(code, pit_asof).to_dict()`);`f10_reader` 把 holders 摘要 + main_capital + abnormal 加进喂 LLM 的确定性 facts 块(同 events/margin 写法)。
- [ ] **Step 4: 跑通**;Checkpoint `pytest tests/ -q -k "f10 or report_writer or quote or stock_basic or news"` 全绿。

---

## Task 7:验收

- [ ] **Step 1:** `python -m pytest tests/ -q` 全绿(含旧 450)。
- [ ] **Step 2:** 重跑真研报(engine fork 上 sys.path 最前 + **SZ 前缀码**):
`python -c "import sys,asyncio;sys.path.insert(0,'engine');from pathlib import Path;from financial_analyst.tui import run_report_oneshot;print(asyncio.run(run_report_oneshot('SZ000630',None,Path('out'),False)))"`
验证 `out/SZ000630_<date>.md`:**PE 不再 null(≈31.87)**、出现行业=铜/本票身份、股东(控股股东/户数/top)与主力(机构持仓/户数趋势)浮现、§3异动可见;抽样数字与 F10/daily_basic 逐字一致(无幻觉)。
- [ ] **Step 3:** 更新记忆 `stock-report-pipeline-defects`(batch2 已接:估值根治/身份/股东主力/异动;挂账 D 量化预测、§2资金流向/§4大宗若未解)。

---

## Self-Review
- 覆盖:A→T1;B→T2;C(holders/main_capital/lhb异动)→T3/T4/T5;浮出→T6;验收→T7。
- 占位符:§2资金流向/§4大宗 明确允许"结构吃不准→honest 空 + provenance 记录",非空泛 TODO。
- 类型一致:`F10Facts` 新增 `main_capital`,`holders` 由 None→dict;`load_facts(code,asof)` 不变;quote `pe/pb/mv_yi` 真值优先、`f10_valuation` 仅兜底;新 `_parse_holders/_parse_main_capital` 与 `_visible_date` 复用一致。
- 红线:全确定性抽取 + 跳空行/分隔 + PIT 裁 future + 缺料诚实空。
