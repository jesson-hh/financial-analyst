# ETF UI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ETFs first-class in the 觀瀾 Web UI — typing an ETF code in chat routes to a quick quote or a full ETF deep-dive report (like stocks), and ETFs can be added to the 盯盘 monitoring wall for realtime price.

**Architecture:** Pure backend, reuses existing plumbing. (1) A single shared `etf_exchange()` helper teaches `normalize_code` + `_to_tencent` to map bare ETF codes (`510300`→SH, `159915`→SZ) — this alone makes ETF realtime quotes work everywhere (chat `realtime_quote`, the `/quotes` monitoring wall, watchlist add). (2) A new `run_etf_report` buddy tool (a near-clone of `_tool_report` that shells out to `financial-analyst etf-report`) returns `side_effect={"md_path"}`, which the existing `/run` SSE handler already renders. The LLM agent auto-exposes the tool (it iterates all of `TOOL_REGISTRY`) and routes to it via the tool description + a cheat-sheet row. **No frontend change** — the monitoring wall only renders price + change% (verified), and `liveOf()` already tolerates SH/SZ prefixes.

**Tech Stack:** Python 3 (financial_analyst package), pytest, the fa `.venv` (`G:/financial-analyst/.venv/Scripts/python.exe`). Tencent realtime quote (`qt.gtimg.cn`). Editable install resolves to `G:/financial-analyst/src`.

**Concurrency note:** The quant window shares `G:/financial-analyst`. Execute on an isolated **git worktree / feature branch** (per the project's "never let two windows commit to the same working dir" rule). Invoke `superpowers:using-git-worktrees` at execution start.

**Spec:** `docs/superpowers/specs/2026-05-31-etf-ui-integration-design.md`

---

### Task 1: Shared `etf_exchange` helper

**Files:**
- Create: `src/financial_analyst/data/code_norm.py`
- Test: `tests/test_code_norm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_code_norm.py
from financial_analyst.data.code_norm import etf_exchange


def test_shanghai_etf_prefixes():
    assert etf_exchange("510300") == "SH"   # 沪深300ETF
    assert etf_exchange("512880") == "SH"   # 证券ETF
    assert etf_exchange("560000") == "SH"   # 中证500ETF family
    assert etf_exchange("588000") == "SH"   # 科创50ETF


def test_shenzhen_etf_prefixes():
    assert etf_exchange("159915") == "SZ"   # 创业板ETF
    assert etf_exchange("159919") == "SZ"


def test_non_etf_returns_none():
    assert etf_exchange("600519") is None   # stock (SH main)
    assert etf_exchange("000001") is None   # stock (SZ main)
    assert etf_exchange("300750") is None   # stock (ChiNext)
    assert etf_exchange("430017") is None   # stock (BJ)
    assert etf_exchange("110059") is None   # SH convertible bond — must NOT be ETF
    assert etf_exchange("123120") is None   # SZ convertible bond — must NOT be ETF


def test_malformed_returns_none():
    assert etf_exchange("51030") is None    # 5 digits
    assert etf_exchange("5103000") is None  # 7 digits
    assert etf_exchange("SH510300") is None # not bare 6-digit (caller strips prefix first)
    assert etf_exchange("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_code_norm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'financial_analyst.data.code_norm'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/data/code_norm.py
"""Shared ETF code -> exchange resolver.

A-share ETF listing prefixes (bare 6-digit codes):
  Shanghai (SH): 51x (510-519), 56x (560-563), 58x (580 / 588 STAR)
  Shenzhen (SZ): 15x (159 / 150-159)
Precise 2-char prefixes are used (not broad 5->SH / 1->SZ) so SH/SZ
convertible bonds (11x/12x/13x) are NOT misclassified as ETFs.
"""
from __future__ import annotations
from typing import Optional


def etf_exchange(code6: str) -> Optional[str]:
    """Return 'SH' / 'SZ' for a bare 6-digit ETF code, else None.

    Returns None for non-ETF input (stocks, bonds, malformed) so callers
    fall through to their existing stock/bond logic unchanged.
    """
    c = str(code6).strip()
    if not (c.isdigit() and len(c) == 6):
        return None
    p2 = c[:2]
    if p2 in ("51", "56", "58"):
        return "SH"
    if p2 == "15":
        return "SZ"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_code_norm.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/data/code_norm.py tests/test_code_norm.py
git commit -m "feat(data): etf_exchange helper (shared ETF code->exchange resolver)"
```

---

### Task 2: Teach `normalize_code` about ETF codes

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (the `normalize_code` function, currently lines 42-62; and the import block near the top)
- Test: `tests/test_normalize_code_etf.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalize_code_etf.py
from financial_analyst.buddy.tools import normalize_code


def test_bare_etf_codes_get_prefix():
    assert normalize_code("510300") == "SH510300"
    assert normalize_code("159915") == "SZ159915"
    assert normalize_code("588000") == "SH588000"
    assert normalize_code("512880") == "SH512880"


def test_prefixed_and_suffixed_etf_still_work():
    assert normalize_code("SH510300") == "SH510300"
    assert normalize_code("510300.SH") == "SH510300"
    assert normalize_code("sz159915") == "SZ159915"


def test_stock_codes_unchanged():
    assert normalize_code("600519") == "SH600519"
    assert normalize_code("000001") == "SZ000001"
    assert normalize_code("300750") == "SZ300750"
    assert normalize_code("430017") == "BJ430017"


def test_bond_not_misclassified():
    # 110xxx / 123xxx are convertible bonds — normalize_code has no rule
    # for them, so they fall through unchanged (NOT forced to an exchange).
    assert normalize_code("110059") == "110059"
    assert normalize_code("123120") == "123120"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_normalize_code_etf.py -v`
Expected: FAIL — `test_bare_etf_codes_get_prefix` fails (`normalize_code("510300")` returns `"510300"`, not `"SH510300"`).

- [ ] **Step 3: Add the import**

Near the top of `src/financial_analyst/buddy/tools.py`, after the existing imports, add:

```python
from financial_analyst.data.code_norm import etf_exchange
```

- [ ] **Step 4: Edit `normalize_code`**

Replace the bare-6-digit branch (currently lines 55-61) so the ETF check runs first. The full edited function:

```python
def normalize_code(code: Any) -> str:
    """Normalise a stock/ETF code to the SH/SZ/BJ-prefixed form the loaders
    expect. Accepts bare 6-digit (300750/510300), prefixed (SZ300750), or
    suffixed (300750.SZ). Used by the desktop UI bridge which sends bare
    6-digit codes."""
    c = str(code).upper().strip()
    if "." in c:  # tushare suffix form 300750.SZ
        num, _, suf = c.partition(".")
        if suf in ("SH", "SZ", "BJ") and num.isdigit():
            return suf + num
        c = num
    if c[:2] in ("SH", "SZ", "BJ"):
        return c
    if c.isdigit() and len(c) == 6:
        ex = etf_exchange(c)
        if ex:
            return ex + c
        if c[0] == "6":
            return "SH" + c
        if c[0] in "03":
            return "SZ" + c
        if c[0] in "84":
            return "BJ" + c
    return c
```

- [ ] **Step 5: Run test to verify it passes**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_normalize_code_etf.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/financial_analyst/buddy/tools.py tests/test_normalize_code_etf.py
git commit -m "feat(buddy): normalize_code maps bare ETF codes via etf_exchange"
```

---

### Task 3: Teach `_to_tencent` about ETF codes (realtime quote / 盯盘 wall)

**Files:**
- Modify: `src/financial_analyst/data/collectors/tencent_quote.py` (the `_to_tencent` function, lines 24-41; and add an import)
- Test: `tests/test_tencent_to_etf.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tencent_to_etf.py
from financial_analyst.data.collectors.tencent_quote import _to_tencent


def test_bare_etf_codes_get_tencent_prefix():
    assert _to_tencent("510300") == "sh510300"
    assert _to_tencent("159915") == "sz159915"
    assert _to_tencent("588000") == "sh588000"


def test_prefixed_and_suffixed_etf_still_work():
    assert _to_tencent("SH510300") == "sh510300"
    assert _to_tencent("510300.SH") == "sh510300"


def test_stock_codes_unchanged():
    assert _to_tencent("600519") == "sh600519"
    assert _to_tencent("000001") == "sz000001"
    assert _to_tencent("430017") == "bj430017"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_tencent_to_etf.py -v`
Expected: FAIL — `_to_tencent("510300")` returns `"510300"` (lowercased, no prefix), not `"sh510300"`.

- [ ] **Step 3: Add the import**

At the top of `src/financial_analyst/data/collectors/tencent_quote.py`, after `from financial_analyst.data.net import rate_limited`, add:

```python
from financial_analyst.data.code_norm import etf_exchange
```

- [ ] **Step 4: Edit `_to_tencent`**

Replace the bare-6-digit branch so the ETF check runs first. The full edited function:

```python
def _to_tencent(code: str) -> str:
    """SH600519 -> sh600519 · 600519 -> sh600519 · 510300 -> sh510300 (ETF)."""
    c = str(code).upper().strip()
    if "." in c:
        num, _, suf = c.partition(".")
        if suf in ("SH", "SZ", "BJ"):
            return suf.lower() + num
        c = num
    if c[:2] in ("SH", "SZ", "BJ"):
        return c[:2].lower() + c[2:]
    if c.isdigit() and len(c) == 6:
        ex = etf_exchange(c)
        if ex:
            return ex.lower() + c
        if c[0] == "6":
            return "sh" + c
        if c[0] in "03":
            return "sz" + c
        if c[0] in "84":
            return "bj" + c
    return c.lower()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_tencent_to_etf.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/financial_analyst/data/collectors/tencent_quote.py tests/test_tencent_to_etf.py
git commit -m "feat(data): _to_tencent maps bare ETF codes (realtime quote + 盯盘 wall)"
```

---

### Task 4: `run_etf_report` buddy tool

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (add `_tool_etf_report` next to `_tool_report` at ~line 371; add a `Tool(...)` entry to `TOOL_REGISTRY` right after the `run_report` entry at ~line 1871)
- Test: `tests/test_etf_report_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_etf_report_tool.py
import subprocess
from pathlib import Path

import financial_analyst.buddy.tools as tools


def test_etf_report_tool_returns_md_path(tmp_path, monkeypatch):
    # Fake project root with an out/ dir holding a generated ETF report
    out = tmp_path / "out"
    out.mkdir()
    md = out / "SH510300_2026-05-31.md"
    md.write_text("## 一、综合评级\n总评 1/10\n\n## 二、持仓\n...\n", encoding="utf-8")
    monkeypatch.setattr(tools, "_project_root", lambda: tmp_path)

    def fake_run(cmd, **kw):
        assert cmd[:3] == ["financial-analyst", "etf-report", "SH510300"]
        class P:  # noqa
            returncode = 0
            stderr = ""
            stdout = ""
        return P()
    monkeypatch.setattr(subprocess, "run", fake_run)

    res = tools._tool_etf_report("SH510300")
    assert res.is_error is False
    assert res.side_effect == {"md_path": str(md)}
    assert "一、综合评级" in res.content


def test_etf_report_tool_surfaces_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_project_root", lambda: tmp_path)

    def fake_run(cmd, **kw):
        class P:  # noqa
            returncode = 2
            stderr = "boom"
            stdout = ""
        return P()
    monkeypatch.setattr(subprocess, "run", fake_run)

    res = tools._tool_etf_report("SH510300")
    assert res.is_error is True
    assert "exit 2" in res.content


def test_run_etf_report_registered():
    from financial_analyst.buddy.tools import get_tool
    t = get_tool("run_etf_report")
    assert t is not None
    assert t.cost_hint == "minutes"
    assert t.confirm_required is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_etf_report_tool.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_tool_etf_report'` and `get_tool("run_etf_report")` returns `None`.

- [ ] **Step 3: Add `_tool_etf_report`**

In `src/financial_analyst/buddy/tools.py`, immediately after `_tool_report` (after line 370), add:

```python
def _tool_etf_report(code: str, asof: Optional[str] = None) -> ToolResult:
    """Run a full ETF deep-dive report (13-agent etf-deep-dive swarm)."""
    asof = asof or "today"  # CLI handles 'today' as None
    cmd = ["financial-analyst", "etf-report", code]
    if asof and asof != "today":
        cmd += ["--asof", asof]
    root = _project_root()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              cwd=str(root))
    except subprocess.TimeoutExpired:
        return ToolResult("ETF report timed out after 15 minutes.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(
            f"ETF report failed (exit {proc.returncode}):\n{proc.stderr[-500:]}",
            is_error=True,
        )
    md_files = sorted((root / "out").glob(f"{code}_*.md"))
    if not md_files:
        return ToolResult(f"ETF report finished but no markdown found for {code}.")
    md_path = md_files[-1]
    body = md_path.read_text(encoding="utf-8", errors="replace")
    import re
    summary_parts = []
    for sect in (r"## 一、综合评级.*?(?=## 二)", r"## 八、操作建议.*?(?=---|\Z)"):
        m = re.search(sect, body, re.DOTALL)
        if m:
            summary_parts.append(m.group(0).strip())
    summary = "\n\n".join(summary_parts) or body[:1500]
    return ToolResult(
        f"ETF report written to {md_path}.\n\nExec summary:\n{summary}",
        side_effect={"md_path": str(md_path)},
    )
```

- [ ] **Step 4: Register the tool**

In `TOOL_REGISTRY` (starts at line 1850), add this entry immediately after the `run_report` `Tool(...)` block (i.e. right after its `confirm_required=True,` + closing `),` at ~line 1871):

```python
    Tool(
        name="run_etf_report",
        description=(
            "Run a complete ETF deep-dive research report (中文 ETF 研报). "
            "Takes 5-8 minutes. Outputs a 5-dim rating (持仓/技术/资金流-申赎/"
            "估值-折溢价/风控), target/stop, premium-discount, tracking error, "
            "holdings concentration. Use ONLY for ETF codes (5/15 开头, e.g. "
            "510300 / SH510300 / 159915). For stocks use run_report. "
            "DO NOT use for a quick price quote (use realtime_quote)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "ETF code, e.g. 510300 / SH510300 / 159915 / SZ159915."},
                "asof": {"type": "string", "description": "As-of date YYYY-MM-DD (default: today)."},
            },
            "required": ["code"],
        },
        run=_tool_etf_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_etf_report_tool.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/financial_analyst/buddy/tools.py tests/test_etf_report_tool.py
git commit -m "feat(buddy): run_etf_report tool (chat -> ETF deep-dive, reuses /run md_path render)"
```

---

### Task 5: ETF routing row in the agent cheat-sheet

**Files:**
- Modify: `src/financial_analyst/buddy/agent.py` (the `SYSTEM_PROMPT` tool-routing table, lines 67-86)

This is a prompt string edit (no unit test — the routing behavior is exercised by the manual smoke in Task 6). The new tool is already auto-exposed (agent iterates all of `TOOL_REGISTRY`); this row sharpens the LLM's choice.

- [ ] **Step 1: Edit the cheat-sheet**

In `SYSTEM_PROMPT`, immediately after the `run_report` row (line 72):

```
| "深度研报" / "完整分析" / "跑个研报" | **run_report(code)** (5-8 min, 贵, 会要确认) |
```

add this new row:

```
| ETF "研报" / "分析 510300" / ETF 深度分析 (代码 5/15 开头) | **run_etf_report(code)** (ETF 专用 5-8 min, 会确认; 如 510300 / SH159915) |
```

- [ ] **Step 2: Verify the module still imports + row present**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -c "from financial_analyst.buddy.agent import _build_system_prompt; assert 'run_etf_report' in _build_system_prompt(); print('OK: ETF row present')"`
Expected: prints `OK: ETF row present`

- [ ] **Step 3: Commit**

```bash
git add src/financial_analyst/buddy/agent.py
git commit -m "feat(buddy): add ETF row to agent tool-routing cheat-sheet"
```

---

### Task 6: Full-suite regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the new ETF tests**

Run:
```bash
G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_code_norm.py tests/test_normalize_code_etf.py tests/test_tencent_to_etf.py tests/test_etf_report_tool.py -v
```
Expected: all PASS.

- [ ] **Step 2: Confirm no regression in existing quote/tool/etf tests**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -k "quote or tool or normalize or etf" -q`
Expected: all PASS (no existing test broken by the normalize_code / _to_tencent edits).

- [ ] **Step 3: Manual smoke (requires a running backend `fa serve` + LLM key + market data)**

Restart the backend so the new tool/prompt load (`fa start`, or restart `fa serve`), then in the chat UI:
- Type `510300 现价` → expect a realtime quote card (沪深300ETF, price + change%), NOT a report.
- Type `分析 510300` → expect a confirm prompt, then an ETF deep-dive report renders in the drawer (5-dim rating, 折溢价, tracking error).
- Add `510300` (or `SH510300`) to the 盯盘 watchlist → expect a live price + change% row, refreshing like a stock.

Record the outcome. If the LLM routes `分析 510300` to `run_report` instead of `run_etf_report`, strengthen the `run_etf_report` description / cheat-sheet row wording and re-test.

- [ ] **Step 4: Commit (only if wording tweaks were needed)**

```bash
git add -A
git commit -m "test(etf-ui): manual smoke pass + routing wording tweak"
```

---

## Self-Review (filled in by plan author)

**Spec coverage:**
- Shared ETF normalization → Tasks 1-3 (`etf_exchange` + `normalize_code` + `_to_tencent`). ✅
- A: chat quote for ETF → covered by Task 3 (`realtime_quote` uses `_to_tencent`). ✅
- A: chat ETF deep report → Task 4 (`run_etf_report` tool, reuses `/run` md_path render) + Task 5 (routing). ✅
- B: 盯盘 ETF → covered by Task 3 (`/quotes` + watchlist add use `_to_tencent`); **no frontend task needed** — the wall renders only price+change% and `liveOf()` tolerates prefixes (verified in spec/exploration). ✅
- Testing (unit helper + tool + manual) → Tasks 1-4 unit, Task 6 regression + manual. ✅
- Out-of-scope items (折溢价/IOPV wall, report-renderer change, batch -f) → correctly omitted.

**Placeholder scan:** No TBD/TODO; every code step shows full code; exact paths + commands given. ✅

**Type/name consistency:** `etf_exchange` signature identical across Tasks 1-3; `_tool_etf_report` returns `ToolResult(content, side_effect=...)` matching `_tool_report`; tool name `run_etf_report` consistent across Tasks 4-6. ✅

**Note:** The `/quotes` integration path is exercised indirectly by Task 3's `_to_tencent` unit tests + Task 6's manual smoke (a live `/quotes` test would need a network or a mocked `TencentQuoteCollector.fetch`; deemed unnecessary since `_to_tencent` is the only changed unit on that path).
