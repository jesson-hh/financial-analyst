"""P2 甲 — BacktestRunner end-to-end tests.

* 甲1 toy: a fully in-memory 3-day run with a stub loader, stub PitReader and a
  scripted ``_StubAgent`` (buy → hold → sell). Asserts a real NAV series comes
  out (>=4 points incl. the seed), exactly one closed round trip, and that no
  LLM was called (n_llm_calls==0).
* 甲2 real smoke: runs against the real cn_data + pit_store if present (else
  skips), still with a scripted agent — proves the loader wiring and that the
  run terminates at the data_end-capped last day (2026-05-29), NOT 2026-12-31.
* 甲3 caps the future-padded calendar to data_end.
* 甲4 missing benchmark (SH000300 has no 2026 rows) → benchmark_nav is None,
  warning recorded, run does not crash.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from financial_analyst.backtest.decision import (
    Decision,
    DecisionInput,
    DecisionLeg,
    build_messages,
)
from financial_analyst.backtest.engine import BacktestRunner, RunConfig
from financial_analyst.backtest.pit_reader import PitReader, VisibleInfo


# --------------------------------------------------------------------------
# Scripted agent: returns a fixed Decision per date, never calls an LLM.
# Mirrors the recommended `_StubAgent` injection in the P2 design §5.
# --------------------------------------------------------------------------
class _StubAgent:
    def __init__(self, scripted):
        self.scripted = scripted
        self._llm_call_count = 0
        self.seen_inputs = []

    async def decide(self, inp):
        self.seen_inputs.append(inp)
        return self.scripted.get(inp.date, Decision("hold", [], [], {}))

    @property
    def n_calls(self):
        return self._llm_call_count


# --------------------------------------------------------------------------
# In-memory toy loader. The RUN window is 3 trading days (2026-04-01..04-03) but
# the calendar/OHLCV carries a day-0 of history (2026-03-31) so day-1 has a
# valid ex-div ref_prev_close and the buy can fill (a real window always has
# prior history). One stock SH600001, clear up move close 10→10→11→12, so a buy
# on day1 + sell on day3 books a gain. Calendar is padded with a future date
# (2026-12-31) to exercise data_end capping.
# --------------------------------------------------------------------------
_TOY_HIST = "2026-03-31"
_TOY_DAYS = ["2026-04-01", "2026-04-02", "2026-04-03"]
_TOY_PAD = [_TOY_HIST] + _TOY_DAYS + ["2026-12-31"]  # history + window + padding


class _ToyLoader:
    def __init__(self):
        self._cal = [pd.Timestamp(d) for d in _TOY_PAD]
        # OHLCV per (code, date) — includes day-0 history so day-1 has prev_close
        self._ohlcv = {
            "SH600001": {
                "2026-03-31": (9.9, 10.1, 9.8, 10.0, 900_000),
                "2026-04-01": (10.0, 10.2, 9.9, 10.0, 1_000_000),
                "2026-04-02": (10.1, 11.2, 10.0, 11.0, 1_200_000),
                "2026-04-03": (11.1, 12.3, 11.0, 12.0, 1_500_000),
            }
        }
        # close + factor series for ref_prev_close
        self._close = {"SH600001": {d: v[3] for d, v in self._ohlcv["SH600001"].items()}}

    def _load_calendar(self, freq="day"):
        if freq == "day":
            return self._cal
        if freq == "5min":
            # last 5min stamp shares the real data's date for data_end min()
            return [pd.Timestamp("2026-04-03 15:00:00")]
        raise ValueError(freq)

    def fetch_quote(self, code, start, end, freq="day"):
        if freq != "day":
            return pd.DataFrame()
        rows = []
        for d, v in self._ohlcv.get(code, {}).items():
            if str(pd.Timestamp(start).date()) <= d <= str(pd.Timestamp(end).date()):
                o, h, l, c, vol = v
                rows.append((pd.Timestamp(d), o, h, l, c, vol))
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low",
                                          "close", "vol"])
        return df.sort_values("trade_date").reset_index(drop=True)

    def _read_bin(self, code, field, freq="day"):
        if code not in self._ohlcv:
            return None
        if field == "close":
            ser = self._close[code]
            return pd.Series(list(ser.values()),
                             index=pd.DatetimeIndex([pd.Timestamp(d) for d in ser]))
        if field == "factor":
            ser = self._close[code]
            return pd.Series([1.0] * len(ser),
                             index=pd.DatetimeIndex([pd.Timestamp(d) for d in ser]))
        return None


class _ToyReader:
    """Minimal PitReader stand-in: real boundary semantics are tested in
    test_backtest_pit_reader.py; here we only need the surface the engine
    + candidate.py call."""

    def __init__(self, loader, cal=_TOY_PAD, data_end="2026-04-03"):
        self._loader = loader
        self._cal = [str(pd.Timestamp(d).date()) for d in cal]
        self._data_end = pd.Timestamp(data_end)

    def data_end(self):
        return self._data_end

    def news_date_max(self):
        return "2026-04-03"

    def prev_trade_date(self, date):
        earlier = [d for d in self._cal if d < date]
        return earlier[-1] if earlier else None

    def trading_days(self, start=None, end=None):
        de = self._data_end
        hi = min(pd.Timestamp(end), de) if end else de
        lo = pd.Timestamp(start) if start else pd.Timestamp(self._cal[0])
        return [d for d in self._cal if lo <= pd.Timestamp(d) <= hi]

    def fetch_quote_leq_prev(self, code, n_days_back=30, freq="day", as_of_date=None):
        prev = self.prev_trade_date(as_of_date)
        if prev is None:
            return pd.DataFrame(columns=["trade_date", "close"])
        return self._loader.fetch_quote(code, "1990-01-01", prev, "day")

    def get_visible_info(self, date, codes=None, as_of="09:25", lookback_days=1,
                         include=("news", "events", "market_eod_prev")):
        prev = self.prev_trade_date(date)
        return VisibleInfo(
            date=date, as_of="09:25:00", boundary_ts=f"{date}T09:25:00",
            news=[], events=[], policy=[],
            market_eod_prev={"prev_trade_date": prev, "pct_up_5d": None,
                             "median_ret_5d": None, "median_ret_20d": None},
        )


# ==========================================================================
# 甲1 — in-memory end-to-end produces a real NAV series + one round trip
# ==========================================================================
async def test_end_to_end_toy_3days():
    loader = _ToyLoader()
    reader = _ToyReader(loader)
    scripted = {
        "2026-04-01": Decision(
            "buy day1",
            [DecisionLeg(code="SH600001", action="buy", target_price=12.0,
                         stop_loss=8.0, weight_pct=90.0, reason="entry")],
            [], {}),
        "2026-04-02": Decision("hold", [], [], {}),
        "2026-04-03": Decision(
            "exit",
            [DecisionLeg(code="SH600001", action="sell", reason="take")],
            [], {}),
    }
    agent = _StubAgent(scripted)
    cfg = RunConfig(start="2026-04-01", end="2026-04-03", init_cash=1_000_000.0,
                    benchmark=None, match_freq="day")
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader, cfg=cfg)
    res = await runner.run()

    # a real NAV series: one point per run day (seed at day0 is overwritten by
    # the day0 record_nav since they share the date).
    assert len(res.nav_history) == 3
    assert [d for d, _ in res.nav_history] == _TOY_DAYS
    assert all(isinstance(v, float) and v > 0 for _, v in res.nav_history)
    # NAV actually moved between day1 (held @~10) and day3 (held/sold @~12) —
    # proves fills + mark-to-market really happened (not a frozen flat series).
    navs = [v for _, v in res.nav_history]
    assert navs[-1] != navs[0]
    # exactly one buy and one sell fill → one closed round trip
    buys = [f for f in res.trade_log.fills if f.side == "buy"]
    sells = [f for f in res.trade_log.fills if f.side == "sell"]
    assert len(buys) == 1 and len(sells) == 1
    assert res.trade_stats["n_trades"] == 1
    # bought ~10, sold ~12 → realized pnl positive
    assert sells[0].realized_pnl > 0
    # scripted agent never calls an LLM
    assert res.n_llm_calls == 0
    # decisions captured for the UI
    assert set(res.decisions_by_date) == set(_TOY_DAYS)
    # the run ended on the last toy day, not the padding date
    assert res.nav_history[-1][0] == "2026-04-03"


# ==========================================================================
# 甲3 — data_end caps the future-padded calendar
# ==========================================================================
async def test_data_end_caps_future_calendar():
    loader = _ToyLoader()
    reader = _ToyReader(loader)
    # trading_days must stop at data_end even when asked for 2026-12-31
    days = reader.trading_days("2026-04-01", "2026-12-31")
    assert days[-1] == "2026-04-03"

    agent = _StubAgent({})
    cfg = RunConfig(start="2026-04-01", end="2026-12-31", benchmark=None)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader, cfg=cfg)
    assert runner._end == "2026-04-03"
    assert any("越界" in w or "data_end" in w or "截断" in w
               for w in runner._init_warnings)


# ==========================================================================
# 甲4 — missing benchmark → benchmark_nav None + warning, no crash
# ==========================================================================
async def test_benchmark_missing_returns_none():
    loader = _ToyLoader()
    reader = _ToyReader(loader)
    agent = _StubAgent({})
    # SH000300 is not in the toy loader → fetch_quote returns empty
    cfg = RunConfig(start="2026-04-01", end="2026-04-03", benchmark="SH000300")
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader, cfg=cfg)
    res = await runner.run()
    assert res.benchmark_nav is None
    assert any("benchmark" in w.lower() or "行情" in w for w in res.warnings)


# ==========================================================================
# 甲2 — real-data smoke (skips if cn_data / pit_store absent). NO real LLM.
# ==========================================================================
def _real_data_available() -> bool:
    try:
        from financial_analyst.data.paths import get_data_paths
        p = get_data_paths()
        uri = p.qlib_uri
        day_root = uri["day"] if isinstance(uri, dict) else uri
        return (Path(day_root).exists()
                and Path(str(p.pit_store_root)).exists())
    except Exception:
        return False


# Capture the REAL data paths at import time — the autouse `_ci_safe_defaults`
# fixture (conftest.py) monkeypatches `find_config` to a fake yaml, so a
# `get_data_paths()` call *inside* a test body resolves to an empty tmp store.
# The skipif decorator runs at collection (real paths), but the body needs them
# too. These globals are frozen before any fixture patches anything.
try:
    from financial_analyst.data.paths import get_data_paths as _gdp_import_time
    _REAL_PATHS = _gdp_import_time()
    _REAL_QLIB_URI = _REAL_PATHS.qlib_uri
    _REAL_PIT_ROOT = Path(str(_REAL_PATHS.pit_store_root))
except Exception:
    _REAL_PATHS = None
    _REAL_QLIB_URI = None
    _REAL_PIT_ROOT = None


def _real_pit_reader():
    """A PitReader bound to the REAL store + loader (bypasses the CI fixture's
    fake-path patching). Returns None if real data is unavailable."""
    if _REAL_PIT_ROOT is None or not _REAL_PIT_ROOT.exists():
        return None
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    return PitReader(store_root=_REAL_PIT_ROOT,
                     day_loader=QlibBinaryLoader(_REAL_QLIB_URI))


@pytest.mark.skipif(not _real_data_available(),
                    reason="real cn_data / pit_store not present")
async def test_end_to_end_real_smoke_skip():
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.data.paths import get_data_paths

    loader = QlibBinaryLoader(get_data_paths().qlib_uri)
    reader = PitReader(day_loader=loader)
    # scripted: buy SH600519 on the first day, then hold
    first_days = reader.trading_days("2026-05-23", "2026-05-29")
    if len(first_days) < 2:
        pytest.skip("not enough real trading days in window")
    scripted = {
        first_days[0]: Decision(
            "buy", [DecisionLeg(code="SH600519", action="buy",
                                weight_pct=50.0, stop_loss=1.0, reason="x")],
            [], {}),
    }
    agent = _StubAgent(scripted)
    cfg = RunConfig(start="2026-05-23", end="2026-05-29", init_cash=1_000_000.0,
                    benchmark=None, match_freq="day")
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader, cfg=cfg)
    res = await runner.run()
    assert len(res.nav_history) >= 2
    assert res.n_llm_calls == 0
    # data_end cap: last nav date must be a real trading day <= 2026-05-29
    assert res.nav_history[-1][0] <= "2026-05-29"
    assert pd.Timestamp(res.nav_history[-1][0]) <= pd.Timestamp("2026-05-29")


# ==========================================================================
# P4 — cross-repo consistency: fa PitReader.policy == raw stocks policy.jsonl
# under the same boundary; never any ts>boundary; sample must be non-empty.
# ==========================================================================
@pytest.mark.skipif(not _real_data_available(),
                    reason="real cn_data / pit_store not present")
def test_policy_fa_equals_stocks():
    reader = _real_pit_reader()
    if reader is None:
        pytest.skip("real pit_store not resolvable at runtime")
    root = _REAL_PIT_ROOT
    found_any = False
    # 04-14 gov 政策窗口 (04-13 政策次日盘前可见); 05-21 kuaixun macro; 05-29 末日
    for d in ["2026-04-14", "2026-05-21", "2026-05-29"]:
        if not reader.is_trade_day(d):
            continue
        for as_of in ["09:25", "11:00", "15:30"]:
            vi = reader.get_visible_info(d, as_of=as_of, include=("policy",))
            b = pd.Timestamp(f"{d} {as_of}:00")
            fa_set = {(p.ts, p.level, p.title, p.code) for p in vi.policy}
            # fa-side PIT red line: no ts > boundary
            assert all(pd.Timestamp(p.ts) <= b for p in vi.policy), \
                f"{d} {as_of}: fa policy has ts > boundary"
            # manual read of stocks policy.jsonl with the same boundary + lookback
            raw_set = set()
            for dd in [reader.prev_trade_date(d), d]:
                if dd is None:
                    continue
                pp = root / dd / "policy.jsonl"
                if not pp.exists():
                    continue
                for line in pp.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if pd.Timestamp(row["ts"]) > b:
                        continue
                    raw_set.add((row["ts"], row.get("level"),
                                 row.get("title"), row.get("code")))
            assert fa_set == raw_set, f"{d} {as_of}: fa != stocks policy"
            if fa_set:
                found_any = True
    assert found_any, "整个抽样没抽到任何 policy (builder 未产 policy / 抽样窗口无政策)"


# ==========================================================================
# P4 — DecisionAgent actually consumes policy: with a gov policy visible on T,
# build_messages must render a policy section containing that policy's text.
# ==========================================================================
@pytest.mark.skipif(not _real_data_available(),
                    reason="real cn_data / pit_store not present")
def test_build_messages_includes_policy():
    reader = _real_pit_reader()
    if reader is None:
        pytest.skip("real pit_store not resolvable at runtime")
    # 04-14 09:25 sees the 04-13 gov policy (visible next pre-open). If absent
    # (store not rebuilt with P4), skip rather than false-fail.
    if not reader.is_trade_day("2026-04-14"):
        pytest.skip("2026-04-14 not a trade day in this calendar")
    vi = reader.get_visible_info("2026-04-14", as_of="09:25", include=("policy",))
    if not vi.policy:
        pytest.skip("no policy visible on 2026-04-14 (store predates P4 rebuild)")
    inp = DecisionInput(date="2026-04-14", as_of="09:25", visible=vi,
                        candidates=[], rev20_rank={}, holdings={},
                        cash=1e6, nav=1e6)
    user = build_messages(inp)[1]["content"]
    assert "政策/宏观" in user, "build_messages 必须含 policy 段"
    assert any(p.title[:8] in user for p in vi.policy if p.title), \
        "policy 标题须进 prompt"
