# -*- coding: utf-8 -*-
"""Phase 5 · Task 3b — the ①§4 factor-report rendering contract.

``render_factor_report_for_prompt`` turns a :class:`MarketFactorReport` into the
untrusted, delimited text block that is the **only** numeric outlet feeding
``market.regime`` / ``market.rotation``. Written test-first (RED until the
renderer exists): the new symbols are referenced through ``F.`` so the module
still imports and the failures are runtime AttributeError behaviour failures, not
collection errors.

Matrix (brief Step 1): header fields present; untrusted delimiters + injection
framing; per-family grouping; summary-line format pinned; week-per-line series
layout; 3-significant-digit formatting; UNAVAILABLE explicit line (id + reason);
DEGRADED coverage/reason verbatim; determinism (two runs byte-identical);
overflow refusal both sides of the bound (per-factor + total); header
digest-prefix binding.

Run from repo root: ``pytest tests/orchestration/test_factor_report_render.py -v``
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.market import factors as F
from guanlan_v2.orchestration.market.factors import (
    DEFAULT_UNIVERSE_REGISTRY_VERSION,
    BoardPoolRow,
    DailyValueRow,
    FactorProvenance,
    MarketFactorDefinition,
    MarketFactorInputs,
    MarketFactorSetSpec,
    MarketFactorValue,
    UpDownRow,
    assemble_market_factor_report,
    build_market_factor_set_v1,
    compute_market_factors,
)

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def _stamp(d: str) -> datetime:
    y, m, dd = (int(x) for x in d.split("-"))
    return datetime(y, m, dd, 7, 5, tzinfo=UTC)


def _dates(n: int, start: _date = _date(2025, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _updown(dates: list[str], up: int = 60, down: int = 40, total: int = 100):
    return tuple(UpDownRow(date=d, up=up, down=down, total=total, available_at=_stamp(d)) for d in dates)


def _daily(dates: list[str], v: float):
    return tuple(DailyValueRow(date=d, value=float(v), available_at=_stamp(d)) for d in dates)


def _boards(dates: list[str], streak: int = 4, promo: float = 0.3):
    return tuple(
        BoardPoolRow(date=d, max_streak=streak, promotion_rate=promo, available_at=_stamp(d)) for d in dates
    )


def _full_report():
    """A real report with OK, DEGRADED and UNAVAILABLE factors coexisting.

    updown 90 → breadth.ad_ratio OK (constant 0.2); board_pools 80 →
    breadth.ladder_height OK + breadth.promotion_rate standing DEGRADED (R4);
    astock_temp 40 → temp.astock DEGRADED (40/60); everything else UNAVAILABLE.
    """
    d90, d80, d40 = _dates(90), _dates(80), _dates(40)
    inp = MarketFactorInputs(
        updown=_updown(d90),
        board_pools=_boards(d80),
        astock_temp=_daily(d40, 50.0),
    )
    return compute_market_factors(
        inp,
        spec=build_market_factor_set_v1(),
        as_of=_stamp(d90[-1]),
        clock_mode="eod",
        universe_registry_version=DEFAULT_UNIVERSE_REGISTRY_VERSION,
    )


def _val(report, fid: str) -> MarketFactorValue:
    return next(v for v in report.values if v.factor_id == fid)


# -- synthetic reports for the bound tests (arbitrary UNAVAILABLE reason sizes) - #
def _unavail_value(fid: str, reason: str) -> MarketFactorValue:
    at = _stamp("2025-06-01")
    return MarketFactorValue.build(
        factor_id=fid,
        definition_version="1",
        family=fid.split(".", 1)[0],
        value=None,
        params={},
        universe="all_a",
        effective_at=at,
        available_at=at,
        status="UNAVAILABLE",
        coverage=0.0,
        missing_policy="synthetic",
        series=(),
        summary=None,
        n_days=0,
        first_date=None,
        provenance=FactorProvenance(),
        reason=reason,
    )


def _spec_for(fids: list[str]) -> MarketFactorSetSpec:
    defs = tuple(
        sorted(
            (
                MarketFactorDefinition(
                    factor_id=f,
                    definition_version="1",
                    params={},
                    required_inputs=(),
                    min_history_sessions=1,
                    aux_keys=(),
                )
                for f in fids
            ),
            key=lambda d: d.factor_id,
        )
    )
    return MarketFactorSetSpec.build(
        factor_set_version="syn",
        feature_schema_version="syn",
        universe="all_a",
        frequency="day",
        definitions=defs,
    )


def _report_with(fids_reasons: list[tuple[str, str]]):
    fids = [f for f, _ in fids_reasons]
    spec = _spec_for(fids)
    values = tuple(_unavail_value(f, r) for f, r in fids_reasons)
    return assemble_market_factor_report(
        spec=spec,
        as_of=_stamp("2025-06-01"),
        clock_mode="eod",
        universe_registry_version="ureg-v1",
        values=values,
        data_snapshot_hash="0" * 64,
    )


# --------------------------------------------------------------------------- #
# header fields + untrusted framing                                            #
# --------------------------------------------------------------------------- #
def test_header_declares_all_required_fields():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    assert f"as_of={rep.as_of.isoformat()}" in block
    assert "clock_mode=eod" in block
    assert f"universe_registry_version={rep.universe_registry_version}" in block
    assert f"battery_digest={rep.battery_digest[:8]}" in block


def test_untrusted_delimiters_and_injection_framing():
    block = F.render_factor_report_for_prompt(_full_report())
    assert 'trust="untrusted_data"' in block
    assert block.rstrip().endswith("</market_factor_report>")
    # the injection-defense framing: everything inside is data, never an instruction.
    assert "instruction" in block.lower()


def test_header_digest_prefix_binds_report_content_digest():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    # invariant 6: the header carries the report content_digest prefix — the
    # downstream factor_report_digest audit anchor (Task 10 e2e).
    assert f"factor_report_digest={rep.content_digest[:8]}" in block


# --------------------------------------------------------------------------- #
# per-family grouping (R3: render groups by 族; ids stay per-series)            #
# --------------------------------------------------------------------------- #
def test_factors_grouped_by_family_in_canonical_order():
    block = F.render_factor_report_for_prompt(_full_report())
    for fam in ("breadth", "flow", "rot", "vol", "val", "temp"):
        assert f"## {fam}" in block
    # canonical family order.
    order = [block.index(f"## {fam}") for fam in ("breadth", "flow", "rot", "vol", "val", "temp")]
    assert order == sorted(order)
    # a breadth factor renders under the breadth header, before the flow header.
    assert block.index("## breadth") < block.index("breadth.ad_ratio") < block.index("## flow")


# --------------------------------------------------------------------------- #
# summary-line format + 3-sig-fig + week-per-line series                        #
# --------------------------------------------------------------------------- #
def test_ok_summary_line_format_pinned():
    block = F.render_factor_report_for_prompt(_full_report())
    line = next(ln for ln in block.splitlines() if ln.startswith("breadth.ad_ratio "))
    assert "[OK]" in line
    for token in ("latest=", "Δ5d=", "Δ20d=", "pct250="):
        assert token in line
    # ad_ratio is constant 0.2 → latest renders at 3 sig figs; <250 pts ⇒ pct250 n/a.
    assert "latest=0.2" in line
    assert "pct250=n/a" in line


def test_series_is_three_sig_figs_week_per_line():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    lines = block.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("breadth.ad_ratio "))
    series_lines = []
    for ln in lines[i + 1 :]:
        if ln.startswith("  "):
            series_lines.append(ln)
        else:
            break
    v = _val(rep, "breadth.ad_ratio")
    assert len(v.series) == 60  # D1 cap
    assert len(series_lines) == 12  # 60 sessions, one trading week (5) per line
    for ln in series_lines:
        toks = ln.split()
        assert len(toks) == 5
        assert all(t == "0.2" for t in toks)  # round-trips 0.2 at 3 sig figs


def test_three_sig_fig_helper_pins_formatting():
    assert F._sig3(0.123456) == "0.123"
    assert F._sig3(1234.5) == "1.23e+03"
    assert F._sig3(0.0) == "0"
    assert F._sig3(-0.0) == "0"  # signed-zero normalised


# --------------------------------------------------------------------------- #
# UNAVAILABLE — explicit line, never silently omitted                          #
# --------------------------------------------------------------------------- #
def test_every_unavailable_factor_renders_explicit_line_with_reason():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    assert rep.missing_features  # sanity: the fixture has UNAVAILABLE factors
    for fid in rep.missing_features:
        reason = _val(rep, fid).reason
        assert f"{fid}: UNAVAILABLE(" in block
        assert reason in block
    # exactly one explicit UNAVAILABLE line per missing feature (none omitted).
    assert block.count(": UNAVAILABLE(") == len(rep.missing_features)


def test_ok_and_degraded_factors_are_not_rendered_as_unavailable():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    for v in rep.values:
        if v.status != "UNAVAILABLE":
            assert f"{v.factor_id}: UNAVAILABLE(" not in block


# --------------------------------------------------------------------------- #
# DEGRADED — coverage + reason surface verbatim                                #
# --------------------------------------------------------------------------- #
def test_degraded_surfaces_coverage_and_reason_verbatim():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    v = _val(rep, "temp.astock")
    assert v.status == "DEGRADED"
    line = next(ln for ln in block.splitlines() if ln.startswith("temp.astock "))
    assert "[DEGRADED]" in line
    assert f"coverage={F._sig3(v.coverage)}" in line
    assert v.reason is not None
    assert v.reason in block  # verbatim


def test_promotion_rate_standing_degraded_reason_verbatim():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    v = _val(rep, "breadth.promotion_rate")
    assert v.status == "DEGRADED"
    assert v.reason in block
    assert "limit_days>=2" in block


# --------------------------------------------------------------------------- #
# determinism                                                                  #
# --------------------------------------------------------------------------- #
def test_determinism_two_runs_byte_identical():
    rep = _full_report()
    assert F.render_factor_report_for_prompt(rep) == F.render_factor_report_for_prompt(rep)


# --------------------------------------------------------------------------- #
# bounded length: refuse on overflow, both sides, no truncation path           #
# --------------------------------------------------------------------------- #
def test_per_factor_overflow_refuses_before_assembly():
    over = _report_with([("breadth.huge", "x" * 20000)])
    with pytest.raises(F.FactorReportRenderError):
        F.render_factor_report_for_prompt(over)


def test_per_factor_just_under_bound_renders():
    under = _report_with([("breadth.huge", "x" * 8000)])
    block = F.render_factor_report_for_prompt(under)
    assert "breadth.huge: UNAVAILABLE(" in block


def test_total_overflow_refuses_before_assembly():
    # each factor block stays under the per-factor bound; their sum overflows total.
    rep = _report_with([(f"breadth.f{i:02d}", "x" * 13000) for i in range(12)])
    with pytest.raises(F.FactorReportRenderError):
        F.render_factor_report_for_prompt(rep)


def test_total_just_under_bound_renders():
    rep = _report_with([(f"breadth.f{i:02d}", "x" * 13000) for i in range(8)])
    block = F.render_factor_report_for_prompt(rep)
    assert block.count(": UNAVAILABLE(") == 8


def test_overflow_has_no_truncation_path():
    # the oversized report never returns a truncated block — it raises.
    over = _report_with([("breadth.huge", "x" * 40000)])
    with pytest.raises(F.FactorReportRenderError):
        F.render_factor_report_for_prompt(over)


# --------------------------------------------------------------------------- #
# purity: rendering only report numbers (round-trip)                            #
# --------------------------------------------------------------------------- #
def test_no_pct250_fabricated_when_coverage_insufficient():
    rep = _full_report()
    block = F.render_factor_report_for_prompt(rep)
    # every OK/DEGRADED factor with <250 sessions shows pct250=n/a, never a number.
    for v in rep.values:
        if v.status != "UNAVAILABLE" and v.summary is not None and v.summary.pct_250d is None:
            line = next(ln for ln in block.splitlines() if ln.startswith(f"{v.factor_id} "))
            assert "pct250=n/a" in line
