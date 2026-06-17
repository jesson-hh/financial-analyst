# 帷幄 + 个股研报 F10 语料富化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `G:\stocks` 下的 TDX F10 本地语料,经一个确定性解析+PIT 层接入个股研报与帷幄,修复市值误判/消息面跑题/fetcher 死路,并新增券商目标价,全程确定性抽数字、回测不前视。

**Architecture:** 新增唯一共享模块 `data/f10_corpus.py`(读语料 + 确定性解析 + PIT,仿 `news_pulse.py`)。消费方(quote-fetcher / news-sentiment / f10-reader / report-writer / 帷幄 ww_f10)薄薄调它。PIT 与解析只此一处,消费方按构造即正确。

**Tech Stack:** Python 3.13, pytest, pandas(仅 ww_f10 跨股搜索用 parquet;读路径纯 glob+正则)。引擎在 `G:\guanlan-v2\engine\financial_analyst\`,测试在 `G:\guanlan-v2\tests\`(`tests/conftest.py` 已把 `engine` 入 sys.path)。

**仓库注意:本工作目录非 git 仓(`git: false`)。** 下方每个 "Checkpoint" = 跑指定测试全绿即可,**不执行 `git commit`**(无 git)。

**设计依据:** `docs/superpowers/specs/2026-06-16-weiwo-f10-report-enrichment-design.md`

**语料真实结构(来自 000630 实测,写解析器的依据):**
- 文件:`{root}\{code小写}\{category}_{yyyymmdd}.txt`,UTF-8。`root` 默认 `G:\stocks\news_data\tdx_f10`。
- 表格定宽,单元用全角竖线 `｜`(U+FF5C)分隔,行首尾也是 `｜`。数字带单位:`134.0947亿`/`646.993亿`/`177.86万`/`3.5900`/`83.59%`;缺失为 `-`;日期 `2026-05-29`。
- 关键行样例:
  - 最新提示:`｜总股本(股)            ｜    134.0947亿｜    127.9413亿｜...｜`、`｜每股净资产(元)        ｜  2.7954｜...｜`、`｜净资产收益率(%)       ｜  3.5900｜...｜`、表头 `｜★最新主要指标★...｜  2026-03-31｜  2025-12-31｜...｜`、自由行 `｜2026-03-31  ...营业总收入(元):646.993亿  同比增83.59%  ...｜`、`｜2026-03-31  ...净利润(元):13.3845亿  同比增19.12%  ...｜`
  - 研究报告(取含"目标价格"那张表):表头 `｜  发生日期  ｜    评级机构    ｜  本期  ｜  上期  ｜  报告日价格(元)  ｜  目标价格(元)  ｜`、数据 `｜ 2026-03-31 ｜  国泰海通  ｜  增持  ｜  -  ｜  5.81｜  6.80｜`
  - 公司大事:`｜   2026-05-29   ｜铜陵有色(000630)2025年年度权益分派实施公告  ｜`
  - 龙虎榜单:`｜  2026-05-29  ｜  23.292亿｜  5.188亿｜  1227.234万｜  69.73万｜  23.4147亿｜`(表头 `交易日期｜融资余额(元)｜融资买入额(元)｜融券余额(元)｜融券卖出量(股)｜融资融券余额(元)`)

---

## File Structure

| 文件 | 职责 |
|---|---|
| Create `engine/financial_analyst/data/f10_corpus.py` | 唯一:定位文件 + 确定性解析 5 类 + PIT + 组装 `F10Facts` |
| Create `tests/test_f10_corpus.py` | f10_corpus 全部单测 |
| Create `tests/fixtures/f10/sz000630/*.txt` | 4 个真实小样本(估值/研报/大事/龙虎榜) |
| Modify `engine/financial_analyst/agent/tier1/quote_fetcher.py` | db 空时 F10 估值兜底(灭②) |
| Modify `engine/financial_analyst/agent/tier1/news_sentiment.py` | F10 events 折进 by_code(灭①) |
| Modify `engine/financial_analyst/agent/tier1/f10_reader.py` | 短路改调 f10_corpus(灭③) |
| Modify `engine/financial_analyst/agent/tier3/report_writer.py` | 券商目标价段 + SYSTEM_PROMPT/契约修 |
| Modify 帷幄 console 后端(`ww_*` 工具注册处)+ 测试 | 新增 `ww_f10` 工具 |

---

## Task 1: f10_corpus — 基础解析助手 `_num` / `_cells` / `_find_date` / `_visible_date`

**Files:**
- Create: `engine/financial_analyst/data/f10_corpus.py`
- Test: `tests/test_f10_corpus.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_f10_corpus.py
import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.data import f10_corpus as fc


def test_num_handles_units_and_missing():
    assert fc._num("134.0947亿") == 13409470000.0
    assert fc._num("177.86万") == 1778600.0
    assert fc._num("2.7954") == 2.7954
    assert fc._num("83.59%") == 83.59
    assert fc._num("-") is None
    assert fc._num("") is None
    assert fc._num(None) is None


def test_cells_splits_fullwidth_bar():
    line = "｜总股本(股)            ｜    134.0947亿｜    127.9413亿｜"
    assert fc._cells(line) == ["总股本(股)", "134.0947亿", "127.9413亿"]


def test_find_date():
    assert fc._find_date("｜   2026-05-29   ｜公告｜") == "2026-05-29"
    assert fc._find_date("无日期") is None


def test_visible_date_reporting_lag():
    assert fc._visible_date("2026-03-31") == "2026-04-30"   # Q1
    assert fc._visible_date("2025-06-30") == "2025-08-31"   # H1
    assert fc._visible_date("2025-09-30") == "2025-10-31"   # Q3
    assert fc._visible_date("2025-12-31") == "2026-04-30"   # annual
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_corpus.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.data.f10_corpus'`

- [ ] **Step 3: 写最小实现**

```python
# engine/financial_analyst/data/f10_corpus.py
"""TDX F10 本地语料:确定性解析 + PIT。唯一读 G:\\stocks F10 的地方。

设计文档:docs/superpowers/specs/2026-06-16-weiwo-f10-report-enrichment-design.md
所有数字/日期走确定性抽取,LLM 不碰。诚实降级,绝不伪造。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# 跨仓默认路径(可经环境变量覆盖)
CORPUS_ROOT = Path(os.environ.get("GL_F10_ROOT", r"G:\stocks\news_data\tdx_f10"))
INDEX_PATH = Path(os.environ.get("GL_F10_INDEX", r"G:\stocks\stock_data\parquet\tdx_f10_index.parquet"))

_DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")
_NUM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(亿|万|%)?")


def _num(s: Optional[str]) -> Optional[float]:
    """'134.0947亿'->1.340947e10, '177.86万'->1778600, '3.59%'->3.59, '-'/''->None。"""
    s = (s or "").strip()
    if not s or s in {"-", "－", "—"}:
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "亿":
        v *= 1e8
    elif unit == "万":
        v *= 1e4
    return v  # % 直接返回百分数本身(如 3.59)


def _cells(line: str) -> List[str]:
    """按全角/半角竖线切单元,去首尾空单元。"""
    parts = re.split(r"[｜|]", line)
    out = [p.strip() for p in parts]
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def _find_date(s: str) -> Optional[str]:
    m = _DATE_RE.search(s or "")
    return m.group(1) if m else None


def _visible_date(period: str) -> str:
    """季报 报告期 -> 标准披露截止日(防回测看未披露财报)。"""
    y, m, d = period.split("-")
    key = (m, d)
    if key == ("03", "31"):
        return f"{y}-04-30"
    if key == ("06", "30"):
        return f"{y}-08-31"
    if key == ("09", "30"):
        return f"{y}-10-31"
    if key == ("12", "31"):
        return f"{int(y) + 1}-04-30"
    return period
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_corpus.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 2: 测试 fixtures(4 个真实小样本)

**Files:**
- Create: `tests/fixtures/f10/sz000630/最新提示_20260601.txt`
- Create: `tests/fixtures/f10/sz000630/研究报告_20260526.txt`
- Create: `tests/fixtures/f10/sz000630/公司大事_20260601.txt`
- Create: `tests/fixtures/f10/sz000630/龙虎榜单_20260601.txt`

- [ ] **Step 1: 写 `最新提示_20260601.txt`**(UTF-8)

```
☆最新提示☆ ◇000630 铜陵有色 更新日期：2026-06-01◇ 港澳资讯 灵通V9.0
【1.最新提醒】
｜★最新主要指标★      ｜    2026-03-31｜    2025-12-31｜    2025-09-30｜    2025-06-30｜    2025-03-31｜    2024-12-31｜
｜每股收益(元)          ｜        0.1000｜        0.1900｜        0.1400｜        0.1100｜        0.0900｜        0.2200｜
｜每股净资产(元)        ｜        2.7954｜        2.7580｜        2.6938｜        2.6304｜        2.7089｜        2.6667｜
｜净资产收益率(%)       ｜        3.5900｜        6.5900｜        5.1800｜        4.2000｜        3.2300｜        8.4300｜
｜总股本(股)            ｜    134.0947亿｜    134.0947亿｜    134.0947亿｜    127.9413亿｜    127.9413亿｜    127.9292亿｜
｜流通A股(股)           ｜    111.4299亿｜    111.4299亿｜    111.4301亿｜    105.2767亿｜    105.2768亿｜    105.2645亿｜
｜2026-03-31  每股资本公积(元):0.4914  营业总收入(元):646.993亿  同比增83.59%  每股经营现金流量(元):0.4945｜
｜2026-03-31  每股未分利润(元):1.0888  净利润(元):13.3845亿  同比增19.12%  经营活动现金净流量增长率:473.09%｜
```

- [ ] **Step 2: 写 `研究报告_20260526.txt`**(UTF-8)

```
☆研究报告☆ ◇000630 铜陵有色 更新日期：2026-05-26◇ 港澳资讯 灵通V9.0
【1.研报评级预测】
｜  发生日期  ｜    评级机构    ｜      本期      ｜      上期      ｜     报告日价格(元)     ｜      目标价格(元)      ｜
｜ 2026-04-22 ｜    国信证券    ｜      增持      ｜      增持      ｜                       -｜                       -｜
｜ 2026-04-20 ｜    华安证券    ｜      买入      ｜      买入      ｜                       -｜                       -｜
｜ 2026-03-31 ｜    国泰海通    ｜      增持      ｜       -        ｜                    5.81｜                    6.80｜
｜ 2025-08-19 ｜    国信证券    ｜      增持      ｜      增持      ｜                       -｜                       -｜
```

- [ ] **Step 3: 写 `公司大事_20260601.txt`**(UTF-8)

```
☆公司大事☆ ◇000630 铜陵有色 更新日期：2026-05-28◇ 港澳资讯 灵通V9.0
｜   2026-05-29   ｜铜陵有色(000630)2025年年度权益分派实施公告｜
｜   2026-05-21   ｜铜陵有色(000630)十一届一次董事会会议决议公告｜
｜   2026-05-14   ｜铜陵有色(000630)2025年度股东会决议的公告｜
｜   2026-05-09   ｜铜陵有色(000630)投资者关系管理信息20260508｜
```

- [ ] **Step 4: 写 `龙虎榜单_20260601.txt`**(UTF-8)

```
☆龙虎榜单☆ ◇000630 铜陵有色 更新日期：2026-06-01◇ 港澳资讯 灵通V9.0
【1.融资融券】
｜   交易日期   ｜   融资余额(元)   ｜  融资买入额(元)  ｜    融券余额(元)    ｜  融券卖出量(股)  ｜  融资融券余额(元)  ｜
｜  2026-05-29  ｜          23.292亿｜           5.188亿｜          1227.234万｜           69.73万｜           23.4147亿｜
｜  2026-05-28  ｜         23.6212亿｜          3.4683亿｜          899.1675万｜           35.29万｜           23.7111亿｜
｜  2026-05-22  ｜         25.3844亿｜          4.5214亿｜          844.7988万｜            66.2万｜           25.4689亿｜
```

- [ ] **Step 5: Checkpoint** — 4 文件存在且为 UTF-8:
Run: `python -c "import glob,pathlib; [print(p, pathlib.Path(p).read_text(encoding='utf-8')[:20]) for p in glob.glob(r'tests/fixtures/f10/sz000630/*.txt')]"`
Expected: 打印 4 个文件路径无报错。

---

## Task 3: valuation 解析 + 季报 PIT

**Files:**
- Modify: `engine/financial_analyst/data/f10_corpus.py`
- Test: `tests/test_f10_corpus.py`

- [ ] **Step 1: 写失败测试**

```python
def _fixt(cat):
    import glob
    p = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10" / "sz000630"
    hit = glob.glob(str(p / f"{cat}_*.txt"))[0]
    return pathlib.Path(hit).read_text(encoding="utf-8")


def test_parse_valuation_live_latest_period():
    v = fc._parse_valuation(_fixt("最新提示"), asof=None)
    assert v["report_period"] == "2026-03-31"
    assert v["total_shares"] == 13409470000.0
    assert v["bvps"] == 2.7954
    assert v["roe"] == 3.59
    assert v["revenue"] == 646.993e8
    assert v["revenue_yoy"] == 83.59
    assert v["net_profit"] == 13.3845e8


def test_parse_valuation_pit_picks_visible_quarter():
    # asof 2026-04-15:Q1(可见 2026-04-30)与 2025 年报(可见 2026-04-30)都不可见
    # -> 退到 2025-09-30(可见 2025-10-31 <= asof)
    v = fc._parse_valuation(_fixt("最新提示"), asof="2026-04-15")
    assert v["report_period"] == "2025-09-30"
    assert v["bvps"] == 2.6938


def test_parse_valuation_asof_too_early_returns_none():
    v = fc._parse_valuation(_fixt("最新提示"), asof="2024-01-01")
    assert v is None
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_corpus.py -k valuation -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_parse_valuation'`

- [ ] **Step 3: 实现 `_parse_valuation`**

```python
_VAL_LABELS = {
    "每股收益": "eps",
    "每股净资产": "bvps",
    "净资产收益率": "roe",
    "总股本": "total_shares",
    "流通A股": "float_shares",
}


def _parse_valuation(text: str, asof: Optional[str]) -> Optional[Dict[str, Any]]:
    lines = text.splitlines()
    periods: List[str] = []
    by_metric: Dict[str, Dict[str, Optional[float]]] = {}
    rev_by_period: Dict[str, Dict[str, Optional[float]]] = {}

    for ln in lines:
        if "最新主要指标" in ln:
            periods = [d for c in _cells(ln) if (d := _find_date(c))]
            continue
        cells = _cells(ln)
        if periods and cells:
            label = cells[0]
            key = next((v for k, v in _VAL_LABELS.items() if label.startswith(k)), None)
            if key:
                vals = [_num(c) for c in cells[1:1 + len(periods)]]
                by_metric[key] = dict(zip(periods, vals))
        d = _find_date(ln)
        if d and "营业总收入" in ln:
            rev = rev_by_period.setdefault(d, {})
            m = re.search(r"营业总收入\(元\):([\d.]+)亿", ln)
            y = re.search(r"营业总收入.*?同比增(-?[\d.]+)%", ln)
            if m:
                rev["revenue"] = float(m.group(1)) * 1e8
            if y:
                rev["revenue_yoy"] = float(y.group(1))
        if d and "净利润(元)" in ln:
            rev = rev_by_period.setdefault(d, {})
            m = re.search(r"净利润\(元\):([\d.]+)亿", ln)
            y = re.search(r"净利润.*?同比增(-?[\d.]+)%", ln)
            if m:
                rev["net_profit"] = float(m.group(1)) * 1e8
            if y:
                rev["net_profit_yoy"] = float(y.group(1))

    if not periods:
        return None

    def visible(p: str) -> bool:
        return asof is None or _visible_date(p) <= asof

    eligible = [p for p in periods if visible(p)]
    if not eligible:
        return None
    target = max(eligible)   # 'YYYY-MM-DD' 字符串可比

    out: Dict[str, Any] = {"report_period": target}
    for key, series in by_metric.items():
        out[key] = series.get(target)
    rev = rev_by_period.get(target, {})
    for k in ("revenue", "revenue_yoy", "net_profit", "net_profit_yoy"):
        out[k] = rev.get(k)
    return out
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_corpus.py -k valuation -q`
Expected: PASS (3 passed)。若 PIT 期与断言不符,核对 `_visible_date` 与 fixture 期列后微调。

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 4: events 解析(公司大事/业内点评)+ PIT

**Files:**
- Modify: `engine/financial_analyst/data/f10_corpus.py`
- Test: `tests/test_f10_corpus.py`

- [ ] **Step 1: 写失败测试**

```python
def test_parse_events_live_sorted_desc():
    evs = fc._parse_events(_fixt("公司大事"), category="公司大事", asof=None)
    assert evs[0]["date"] == "2026-05-29"
    assert "权益分派实施" in evs[0]["title"]
    assert evs[0]["category"] == "公司大事"
    assert len(evs) == 4


def test_parse_events_pit_drops_future():
    evs = fc._parse_events(_fixt("公司大事"), category="公司大事", asof="2026-05-15")
    dates = [e["date"] for e in evs]
    assert dates == ["2026-05-14", "2026-05-09"]   # 05-29/05-21 被裁
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_corpus.py -k events -q`
Expected: FAIL — no attribute `_parse_events`

- [ ] **Step 3: 实现 `_parse_events`**

```python
def _parse_events(text: str, category: str, asof: Optional[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in text.splitlines():
        cells = _cells(ln)
        if len(cells) < 2:
            continue
        d = _find_date(cells[0])
        if not d or _find_date(cells[1]):   # 第二格也是日期 -> 数据表行,非事件
            continue
        title = cells[1].strip()
        if not title or title in {"评级机构", "预测机构"}:
            continue
        if asof is not None and d > asof:
            continue
        out.append({"date": d, "title": title, "category": category})
    out.sort(key=lambda e: e["date"], reverse=True)
    return out
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_corpus.py -k events -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 5: broker 解析(研究报告:评级+目标价)+ PIT

**Files:**
- Modify: `engine/financial_analyst/data/f10_corpus.py`
- Test: `tests/test_f10_corpus.py`

- [ ] **Step 1: 写失败测试**

```python
def test_parse_broker_extracts_target_price():
    b = fc._parse_broker(_fixt("研究报告"), asof=None)
    r = [x for x in b["ratings"] if x["org"] == "国泰海通"][0]
    assert r["date"] == "2026-03-31"
    assert r["rating"] == "增持"
    assert r["report_price"] == 5.81
    assert r["target_price"] == 6.80
    assert len(b["ratings"]) == 4


def test_parse_broker_pit_filters_by_date():
    b = fc._parse_broker(_fixt("研究报告"), asof="2026-01-01")
    assert all(x["date"] <= "2026-01-01" for x in b["ratings"])
    assert any(x["org"] == "国信证券" and x["date"] == "2025-08-19" for x in b["ratings"])
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_corpus.py -k broker -q`
Expected: FAIL — no attribute `_parse_broker`

- [ ] **Step 3: 实现 `_parse_broker`**

```python
def _parse_broker(text: str, asof: Optional[str]) -> Dict[str, Any]:
    """只取含'目标价格'那张评级表。列:发生日期|评级机构|本期|上期|报告日价格|目标价格。"""
    ratings: List[Dict[str, Any]] = []
    in_table = False
    for ln in text.splitlines():
        cells = _cells(ln)
        if "发生日期" in ln and "目标价格" in ln:
            in_table = True
            continue
        if in_table:
            d = _find_date(cells[0]) if cells else None
            if not d or len(cells) < 6:
                in_table = False
                continue
            if asof is not None and d > asof:
                continue
            ratings.append({
                "date": d,
                "org": cells[1],
                "rating": None if cells[2] in {"-", ""} else cells[2],
                "prev": None if cells[3] in {"-", ""} else cells[3],
                "report_price": _num(cells[4]),
                "target_price": _num(cells[5]),
            })
    ratings.sort(key=lambda r: r["date"], reverse=True)
    return {"ratings": ratings}
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_corpus.py -k broker -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 6: lhb 解析(融资融券日表)+ PIT

**Files:**
- Modify: `engine/financial_analyst/data/f10_corpus.py`
- Test: `tests/test_f10_corpus.py`

- [ ] **Step 1: 写失败测试**

```python
def test_parse_lhb_margin_rows():
    m = fc._parse_lhb(_fixt("龙虎榜单"), asof=None)["margin"]
    assert m[0]["date"] == "2026-05-29"
    assert m[0]["margin_balance"] == 23.292e8        # 融资余额
    assert m[0]["margin_buy"] == 5.188e8             # 融资买入额
    assert len(m) == 3


def test_parse_lhb_pit():
    m = fc._parse_lhb(_fixt("龙虎榜单"), asof="2026-05-25")["margin"]
    assert [r["date"] for r in m] == ["2026-05-22"]
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_corpus.py -k lhb -q`
Expected: FAIL — no attribute `_parse_lhb`

- [ ] **Step 3: 实现 `_parse_lhb`**

```python
def _parse_lhb(text: str, asof: Optional[str]) -> Dict[str, Any]:
    """融资融券日表。列:交易日期|融资余额|融资买入额|融券余额|融券卖出量|融资融券余额。"""
    margin: List[Dict[str, Any]] = []
    in_table = False
    for ln in text.splitlines():
        cells = _cells(ln)
        if "交易日期" in ln and "融资余额" in ln:
            in_table = True
            continue
        if in_table:
            d = _find_date(cells[0]) if cells else None
            if not d or len(cells) < 6:
                in_table = False
                continue
            if asof is not None and d > asof:
                continue
            margin.append({
                "date": d,
                "margin_balance": _num(cells[1]),
                "margin_buy": _num(cells[2]),
                "short_balance": _num(cells[3]),
                "short_sell_vol": _num(cells[4]),
                "total_balance": _num(cells[5]),
            })
    margin.sort(key=lambda r: r["date"], reverse=True)
    return {"margin": margin, "moneyflow": [], "block_trades": [], "abnormal": []}
```

(注:moneyflow/block_trades/abnormal 本轮留空列表占位;事件主线由 events/margin 已覆盖,真表结构可在后续轮补。)

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_corpus.py -k lhb -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_f10_corpus.py -q` 全绿。

---

## Task 7: `locate` + `load_facts` 组装 + 诚实降级

**Files:**
- Modify: `engine/financial_analyst/data/f10_corpus.py`
- Test: `tests/test_f10_corpus.py`

- [ ] **Step 1: 写失败测试**

```python
def _fixt_root():
    return str(pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10")


def test_load_facts_live_assembles_all():
    f = fc.load_facts("SZ000630", asof=None, root=_fixt_root())
    d = f.to_dict()
    assert d["valuation"]["total_shares"] == 13409470000.0
    assert d["events"][0]["date"] == "2026-05-29"
    assert any(r["target_price"] == 6.80 for r in d["broker"]["ratings"])
    assert d["lhb"]["margin"][0]["date"] == "2026-05-29"
    assert d["snapshot_date"]
    assert d["asof"] is None
    assert d["honest_note"] == ""
    assert d["provenance"]


def test_load_facts_missing_stock_is_honest_empty():
    f = fc.load_facts("SZ999999", asof=None, root=_fixt_root())
    d = f.to_dict()
    assert d["valuation"] is None and d["events"] == []
    assert "无" in d["honest_note"]


def test_load_facts_code_normalization_and_lowercase_dir():
    assert fc.load_facts("000630", asof=None, root=_fixt_root()).to_dict()["events"]
    assert fc.load_facts("sz000630", asof=None, root=_fixt_root()).to_dict()["events"]
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_corpus.py -k load_facts -q`
Expected: FAIL — no attribute `load_facts`

- [ ] **Step 3: 实现 `locate` / `F10Facts` / `load_facts`**

```python
_CAT_PARSERS = {
    "最新提示": "valuation",
    "公司大事": "events",
    "业内点评": "events",
    "研究报告": "broker",
    "龙虎榜单": "lhb",
}


def _norm_code(code: str) -> str:
    s = code.strip().upper()
    if re.fullmatch(r"\d{6}", s):
        s = ("SH" if s[0] in "6859" else "SZ") + s
    return s


@dataclass
class F10Facts:
    code: str
    asof: Optional[str] = None
    snapshot_date: Optional[str] = None
    valuation: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    broker: Dict[str, Any] = field(default_factory=lambda: {"ratings": []})
    lhb: Dict[str, Any] = field(default_factory=lambda: {"margin": [], "moneyflow": [], "block_trades": [], "abnormal": []})
    holders: Optional[Dict[str, Any]] = None
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    honest_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def locate(code: str, *, root=None) -> Optional[Dict[str, Any]]:
    """glob {root}/{code小写}/*.txt -> {category: (path, snapshot_date)},每类取最新快照。"""
    base = Path(root) if root else CORPUS_ROOT
    cdir = base / _norm_code(code).lower()
    if not cdir.exists():
        return None
    found: Dict[str, tuple] = {}
    for p in cdir.glob("*.txt"):
        stem = p.stem
        if "_" not in stem:
            continue
        cat, date = stem.rsplit("_", 1)
        if not re.fullmatch(r"\d{8}", date):
            continue
        prev = found.get(cat)
        if prev is None or date > prev[1]:
            found[cat] = (str(p), date)
    return found or None


def load_facts(code: str, asof: Optional[str] = None, *, root=None) -> F10Facts:
    norm = _norm_code(code)
    facts = F10Facts(code=norm, asof=asof)
    snap = locate(code, root=root)
    if not snap:
        facts.honest_note = f"F10 无此股({norm})语料"
        return facts

    snap_dates = sorted({d for _, d in snap.values()}, reverse=True)
    facts.snapshot_date = snap_dates[0] if snap_dates else None

    for cat, (path, sdate) in snap.items():
        kind = _CAT_PARSERS.get(cat)
        if not kind:
            continue
        try:
            txt = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            facts.provenance.append({"category": cat, "snapshot_date": sdate, "error": str(exc)[:80]})
            continue
        try:
            if kind == "valuation":
                facts.valuation = _parse_valuation(txt, asof)
            elif kind == "events":
                facts.events.extend(_parse_events(txt, cat, asof))
            elif kind == "broker":
                facts.broker = _parse_broker(txt, asof)
            elif kind == "lhb":
                facts.lhb = _parse_lhb(txt, asof)
            facts.provenance.append({"category": cat, "snapshot_date": sdate})
        except Exception as exc:  # noqa: BLE001  解析失败不拖垮整体
            facts.provenance.append({"category": cat, "snapshot_date": sdate, "error": f"{type(exc).__name__}: {str(exc)[:80]}"})

    facts.events.sort(key=lambda e: e["date"], reverse=True)
    if asof and not (facts.valuation or facts.events or facts.broker["ratings"] or facts.lhb["margin"]):
        facts.honest_note = f"asof {asof} 早于 F10 快照内容,无可用料"
    return facts
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_corpus.py -q`
Expected: PASS(全部)

- [ ] **Step 5: Checkpoint** — 真数据冒烟:
Run: `python -c "import sys;sys.path.insert(0,'engine');from financial_analyst.data import f10_corpus as fc;print(fc.load_facts('000630').to_dict()['valuation'])"`
Expected: 打印真 valuation(total_shares≈1.34e10),无报错(依赖 `G:\stocks` 可达)。

---

## Task 8: quote-fetcher F10 估值兜底(灭②)

**Files:**
- Modify: `engine/financial_analyst/agent/tier1/quote_fetcher.py`
- Test: `tests/test_quote_fetcher_f10.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_quote_fetcher_f10.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd
from financial_analyst.agent.tier1 import quote_fetcher as qf
from financial_analyst.data import f10_corpus as fc


class FakeLoader:
    def fetch_quote(self, code, start, end):
        idx = pd.date_range("2026-05-01", periods=70, freq="D")
        return pd.DataFrame({"close": [6.0] * 70, "vol": [1e6] * 70}, index=idx)

    def fetch_daily_basic(self, code, start, end):
        return pd.DataFrame()   # 空 -> 触发 F10 兜底


def test_quote_fetcher_uses_f10_when_daily_basic_empty(tmp_path, monkeypatch):
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)
    agent = qf.QuoteFetcher(memory_root=tmp_path, loader=FakeLoader())
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    # 总股本 134.0947亿 × 6.0 / 1e8 = 8045.682 亿
    assert out["mv_yi"] is not None and round(out["mv_yi"], 1) == 8045.7
    assert out["pb"] is not None and round(out["pb"], 3) == round(6.0 / 2.7954, 3)
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_quote_fetcher_f10.py -q`
Expected: FAIL — `out["mv_yi"]` 为 None

- [ ] **Step 3: 加 F10 兜底**

在 `quote_fetcher.py` 顶部 import 区加:
```python
from financial_analyst.data import f10_corpus
```
在 `_execute` 的 `if db is not None and not db.empty:` 块**之后**追加:
```python
        # F10 兜底:daily_basic 缺失/为空时,用确定性 F10 估值(灭②市值误判)
        if out.get("mv_yi") is None:
            try:
                v = f10_corpus.load_facts(code, asof).valuation
            except Exception:
                v = None
            if v:
                price = out["price"]
                ts = v.get("total_shares")
                fs = v.get("float_shares")
                bvps = v.get("bvps")
                if ts:
                    out["mv_yi"] = round(ts * price / 1e8, 4)
                if fs:
                    out["circ_mv_yi"] = round(fs * price / 1e8, 4)
                if bvps:
                    out["pb"] = round(price / bvps, 4)
                out["f10_valuation"] = v   # 透传真营收/净利/ROE 供下游
```
在 `QuoteOutput` 模型加可选透传字段:
```python
    f10_valuation: Optional[Dict[str, Any]] = None
```
并确保文件顶部 `from typing import` 含 `Any`(现为 `Any, Dict, Optional` 则已满足;若缺则补 `Any`)。

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_quote_fetcher_f10.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_quote_fetcher_f10.py tests/test_f10_corpus.py -q` 全绿。

---

## Task 9: news-sentiment 折入 F10 events(灭①)

**Files:**
- Modify: `engine/financial_analyst/agent/tier1/news_sentiment.py`
- Test: `tests/test_news_sentiment_f10.py`

- [ ] **Step 1: 写失败测试**(捕获传入 judge_sentiment 的 by_code,断言含 F10 事件)

```python
# tests/test_news_sentiment_f10.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier1 import news_sentiment as ns
from financial_analyst.data import f10_corpus as fc


def test_news_sentiment_folds_f10_into_by_code(monkeypatch, tmp_path):
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)
    monkeypatch.setattr(ns.news_pulse, "fetch_kuaixun", lambda limit=200: [])
    monkeypatch.setattr(ns.news_pulse, "fetch_stock_news", lambda code, limit=30: [])

    captured = {}

    async def fake_judge(market, by_code, stock_news, llm_json_call=None):
        captured["by_code"] = by_code
        return {"ok": True, "sentiment": {"SZ000630": {"tag": "中性", "read": "x"}},
                "evidence_by_code": {"SZ000630": by_code.get("SZ000630", [])},
                "covered": ["SZ000630"], "note": ""}
    monkeypatch.setattr(ns.news_pulse, "judge_sentiment", fake_judge)

    agent = ns.NewsSentiment(memory_root=tmp_path)
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    folded = captured["by_code"]["SZ000630"]
    assert any("权益分派" in it["title"] for it in folded)   # F10 事件已折入
    assert out["covered"] is True
    assert out["evidence"] and "权益分派" in out["evidence"][0]["title"]
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_news_sentiment_f10.py -q`
Expected: FAIL — by_code 无 F10 事件(KeyError 或断言失败)

- [ ] **Step 3: 改 `news_sentiment.py`**

顶部加导入:
```python
from financial_analyst.data import f10_corpus
```
在 `_execute` 里 `filtered = ...; by_code = ...` 之后折入:
```python
        # 折入 PIT 后的 F10 本票事件(灭①:本票真事件优先于大盘)
        try:
            f10_events = f10_corpus.load_facts(code, asof).events
        except Exception:
            f10_events = []
        if f10_events:
            mapped = [{"time": e["date"], "title": e["title"], "codes": [code]} for e in f10_events[:12]]
            by_code[code] = mapped + by_code.get(code, [])
```
把回退大盘那行(现 `ev = ... or r.get('market_evidence') or []`)改为本票无料即诚实空:
```python
        ev = (r.get('evidence_by_code') or {}).get(code) or []
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_news_sentiment_f10.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_news_sentiment_f10.py tests/test_news_pulse.py -q` 全绿(**确认旧契约不破**)。

---

## Task 10: f10-reader 复活(灭③)

**Files:**
- Modify: `engine/financial_analyst/agent/tier1/f10_reader.py`
- Test: `tests/test_f10_reader_resurrect.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_f10_reader_resurrect.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier1 import f10_reader as fr
from financial_analyst.data import f10_corpus as fc


def test_f10_reader_uses_corpus_without_root(monkeypatch, tmp_path):
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)

    async def fake_llm(self, text):
        assert "权益分派" in text       # LLM 收到确定性 F10 事实
        return {"choices": [{"message": {"content": '{"recent_events": [{"date":"2026-05-29","title":"权益分派"}], "lhb_seats": {}, "event_classified": {"positive":[],"negative":[],"calendar":[],"neutral":[]}}'}}]}
    monkeypatch.setattr(fr.F10Reader, "_call_llm", fake_llm)
    agent = fr.F10Reader(memory_root=tmp_path)   # f10_root 默认 None
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    assert out["recent_events"]
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_f10_reader_resurrect.py -q`
Expected: FAIL — 现 `if self.f10_root is None: return empty` 直接空返

- [ ] **Step 3: 改 `f10_reader.py`**

顶部加导入:
```python
from financial_analyst.data import f10_corpus
```
把 `_execute` 整体替换为(F10 主源 + 旧 drop-zone 叠加):
```python
    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code, asof = inputs["code"], inputs["asof_date"]
        empty = {
            "code": code, "asof_date": asof, "recent_events": [], "lhb_seats": {},
            "event_classified": {"positive": [], "negative": [], "calendar": [], "neutral": []},
        }
        parts: list[str] = []

        # 主源:确定性 F10 语料(灭③:不再依赖 loader 线程化 f10_root)
        try:
            facts = f10_corpus.load_facts(code, asof)
        except Exception:
            facts = None
        if facts:
            if facts.events:
                ev = ["--- source: F10 公司大事/业内点评(确定性) ---"]
                ev += [f"{e['date']} [{e['category']}] {e['title']}" for e in facts.events[:20]]
                parts.append("\n".join(ev))
            if facts.lhb.get("margin"):
                mg = ["--- source: F10 融资融券(确定性) ---"]
                for r in facts.lhb["margin"][:10]:
                    mg.append(f"{r['date']} 融资余额={r['margin_balance']} 融资买入={r['margin_buy']}")
                parts.append("\n".join(mg))

        # 叠加旧 drop-zone(若配置了 f10_root)
        if self.f10_root is not None:
            code_dir = self.f10_root / code
            if code_dir.exists():
                for f in sorted(code_dir.glob("*.txt"))[-10:]:
                    parts.append(f"--- {f.name} ---\n{f.read_text(encoding='utf-8', errors='ignore')[:6000]}")

        if not parts:
            return empty

        response = await self._call_llm("\n\n".join(parts))
        parsed = json.loads(response["choices"][0]["message"]["content"])
        return {
            "code": code, "asof_date": asof,
            "recent_events": parsed.get("recent_events", []),
            "lhb_seats": parsed.get("lhb_seats", {}),
            "event_classified": parsed.get("event_classified", {"positive": [], "negative": [], "calendar": [], "neutral": []}),
        }
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_f10_reader_resurrect.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_f10_reader_resurrect.py -q` 全绿。

---

## Task 11: report-writer 券商目标价段 + SYSTEM_PROMPT/契约修

**Files:**
- Modify: `engine/financial_analyst/agent/tier3/report_writer.py`
- Test: `tests/test_report_writer_broker.py`

- [ ] **Step 1: 先读现状**

Run: `python -c "print(open('engine/financial_analyst/agent/tier3/report_writer.py',encoding='utf-8').read())"`
确认:(a)SYSTEM_PROMPT 里 factor-computer/model-predictor/quant-analyst 措辞位置;(b)upstream 收集字典(约 `:169-173`);(c)`:113/:114` covered 矛盾措辞;(d)各 section 如何拼接进 prompt。

- [ ] **Step 2: 写失败测试**(纯函数 render_broker_section)

```python
# tests/test_report_writer_broker.py
import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier3 import report_writer as rw


def test_render_broker_section_quotes_target_price():
    broker = {"ratings": [
        {"date": "2026-03-31", "org": "国泰海通", "rating": "增持", "report_price": 5.81, "target_price": 6.80},
        {"date": "2026-04-22", "org": "国信证券", "rating": "增持", "report_price": None, "target_price": None},
    ]}
    s = rw.render_broker_section(broker)
    assert "国泰海通" in s and "6.80" in s and "增持" in s


def test_render_broker_section_empty_is_honest():
    assert "无" in rw.render_broker_section({"ratings": []})
```

- [ ] **Step 3: 运行,确认失败**

Run: `python -m pytest tests/test_report_writer_broker.py -q`
Expected: FAIL — no attribute `render_broker_section`

- [ ] **Step 4: 实现 + 接线**

加纯函数(确定性渲染,数字逐字):
```python
def render_broker_section(broker: dict) -> str:
    ratings = (broker or {}).get("ratings") or []
    if not ratings:
        return "券商评级与目标价:无(F10 无券商评级记录)"
    lines = ["券商评级与目标价(确定性,源 F10 研究报告):"]
    for r in ratings[:8]:
        seg = f"- {r['date']} {r.get('org','')} {r.get('rating') or '-'}"
        if r.get("report_price") is not None:
            seg += f",报告日价 {r['report_price']}"
        if r.get("target_price") is not None:
            seg += f",目标价 {r['target_price']}"
        lines.append(seg)
    return "\n".join(lines)
```
接线三处(行号以 Step1 现状为准):(a)组装正文/prompt 处调用 `render_broker_section(...)` 注入"券商评级与目标价"段,broker 取自上游 quote 的 `f10_valuation` 同源 facts,或直接 `f10_corpus.load_facts(code, asof).to_dict()["broker"]`;(b)SYSTEM_PROMPT 删/改 factor-computer/model-predictor/quant-analyst 措辞(第5维不再宣称量化模型并列);(c)`:113/:114` 改为 covered=false → 写"本票无相关消息",不逐字引用空证据。

- [ ] **Step 5: 运行,确认通过**

Run: `python -m pytest tests/test_report_writer_broker.py -q`
Expected: PASS

- [ ] **Step 6: Checkpoint** — `python -m pytest tests/ -q -k "f10 or report_writer or news"` 全绿。

---

## Task 12: 帷幄 `ww_f10` 工具

**Files:**
- Modify: 帷幄 console 后端工具注册文件(Grep 定位 `ww_news_search` 定义处)
- Test: console 工具测试文件(Grep 定位 `ww_news_search` 的测试 + 工具白名单/计数守护测试)

- [ ] **Step 1: 定位现有 ww 工具实现与白名单**

用 Grep 搜 `def ww_news_search` 与 `ww_news_search`(白名单/dispatch),确认:工具函数签名约定、如何注册进白名单(记忆载白名单常量与"工具计数"守护测试,新增工具计数 +1)、SSE/dispatch 注册点。记录文件路径与白名单常量名。

- [ ] **Step 2: 写失败测试**(在 Step1 定位的 console 工具测试文件追加;`?` 层级按实际修正)

```python
def test_ww_f10_returns_structured_facts(monkeypatch):
    import pathlib
    from financial_analyst.data import f10_corpus as fc
    fixt = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "f10"  # 层级按实际修正
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)
    res = ww_f10(code="SZ000630")          # 按现有 ww 工具调用约定改写
    assert res["valuation"]["total_shares"] == 13409470000.0
    assert any(r["target_price"] == 6.80 for r in res["broker"]["ratings"])


def test_ww_f10_in_allowed():
    assert "ww_f10" in CONSOLE_ALLOWED      # 白名单常量名以 Step1 为准
```

- [ ] **Step 3: 运行,确认失败**

Run: `python -m pytest <console_tools_test> -q -k ww_f10`
Expected: FAIL — `ww_f10` 未定义/不在白名单

- [ ] **Step 4: 实现 `ww_f10` + 注册**

```python
def ww_f10(code: str, category: str = None, asof: str = None, keyword: str = None) -> dict:
    """帷幄:查本票 F10 结构化事实(估值/事件/龙虎榜/券商目标价),带 asof 即历史口径。"""
    from financial_analyst.data import f10_corpus
    facts = f10_corpus.load_facts(code, asof).to_dict()
    if category:
        keep = {"估值": "valuation", "事件": "events", "龙虎榜": "lhb", "券商": "broker"}.get(category, category)
        meta = {"code", "asof", "snapshot_date", "honest_note", "provenance"}
        facts = {k: v for k, v in facts.items() if k == keep or k in meta}
    if keyword and facts.get("events"):
        facts["events"] = [e for e in facts["events"] if keyword in e["title"]]
    return facts
```
把 `"ww_f10"` 加入白名单常量,并按现有模式注册到 SSE/dispatch 工具表;同步任何"工具计数"守护测试的期望值 +1。

- [ ] **Step 5: 运行,确认通过**

Run: `python -m pytest <console_tools_test> -q -k ww_f10`
Expected: PASS

- [ ] **Step 6: Checkpoint** — 跑 console 工具全量测试 + `python -m pytest tests/ -q` 全绿。

---

## Task 13: 端到端验证(000630 真研报对比)

**Files:** 无代码改动,验证用。

- [ ] **Step 1: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿(含旧 `test_news_pulse.py`)。

- [ ] **Step 2: 跑一份 000630 live 研报**(按仓内既有研报触发方式;记忆载研报配置读 pinned workspace `G:\financial-analyst\config`,改动须同步过去)

验证产物 `.md`/`.json`:
- 市值不再 null(mv_yi≈8000+ 亿,Tier 不再误判 Small)。
- 消息面是本票真事件(权益分派/董事会),`news_covered=true`,无 CAC40/南航类大盘头条。
- 出现"券商评级与目标价"段(国泰海通 目标价 6.80)。
- 抽样核对几处数字与 F10 文件逐字一致(防幻觉)。

- [ ] **Step 3: 回测 PIT 不前视抽查**

Run: `python -c "import sys,json;sys.path.insert(0,'engine');from financial_analyst.data import f10_corpus as fc;print(json.dumps(fc.load_facts('000630','2026-05-15').to_dict()['events'],ensure_ascii=False))"`
Expected: 只见 ≤2026-05-15 的事件(05-29/05-21 被裁)。

- [ ] **Step 4: Checkpoint** — 记录改前/改后差异,更新记忆 `stock-report-pipeline-defects`(标注 ①②③ 已修)。

---

## Self-Review(写计划后自查)

- **Spec 覆盖**:§4 数据模型→Task1-7;§5 PIT→Task3/4/5/6/7;§6 消费方→Task8(②)/9(①)/10(③)/11(C档+契约);§7 帷幄→Task12;§8 防幻觉→确定性 parser + Task11 渲染;§9 错误处理→Task7 降级测试;§10 测试→各 Task TDD + Task13 回归。无遗漏。
- **占位符**:Task11/12 有"以 Step1 读到的现状为准"——因 report_writer/console 后端是大文件且行号会漂,故先读后改;但渲染纯函数、白名单键、测试均给完整代码,非空泛占位。
- **类型一致**:`load_facts(code, asof, root=)` 贯穿;`F10Facts.to_dict()` 字段(valuation/events/broker/lhb/holders/provenance/snapshot_date/asof/honest_note)各 Task 引用一致;`_num/_cells/_find_date/_visible_date/_parse_*` 命名稳定。
- **风险**:真数据正则可能需对 fixture 微调(Task3/5/6 Step4 已提示);quote_fetcher 透传字段 `f10_valuation` 用公开名避开 pydantic 私有名坑。
```
