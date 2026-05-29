"""SP-C.1: direct factor REST endpoints on buddy/server.py build_app().

These six endpoints (/factor/report|forge|compose|archive|bench|list) call the
already-built factor functions directly (no agent /run loop) and return JSON for
the future quant-workbench UI. Tests drive them through FastAPI's TestClient with
a stub loader + monkeypatched universe/loader home modules (same pattern as
test_factor_report_tool / test_compose), plus a tmp $FINANCIAL_ANALYST_HOME for
the archive.

NaN/Inf discipline: every endpoint runs ``dataclasses.asdict(result)`` through the
module-level ``_jsonable`` helper so NaN/Inf become null. NOTE: httpx's ``.json()``
does NOT raise on a bare ``NaN`` literal (it accepts it), so ``.json()`` alone does
NOT prove valid JSON. The real guard is Starlette's ``JSONResponse`` rendering with
``allow_nan=False`` — a NaN leak makes the endpoint 500. So the load-bearing check
is ``test_report_nan_becomes_null`` (a degenerate constant-price stub yields NaN IC
metrics → asserts 200 + the metric is null + no "NaN" in the raw body).

We never call _clear_registry_for_tests (it would wipe the global alpha registry
and break cross-file tests). The endpoints only read the registry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

# Importing the zoo package auto-registers alpha families (alpha101 etc.) so
# /factor/list and /factor/bench have something to return. Harmless + mirrors
# the sibling factor tests.
import financial_analyst.factors.zoo  # noqa: F401
from financial_analyst.buddy.server import build_app, _jsonable
from financial_analyst.factors.forge import ForgeResult
from financial_analyst.factors.research import ResearchArchive, RunRecord

CODES = ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750", "SH600276"]


# ---------------------------------------------------------------------------
# Stub loader — datetime-indexed OHLCV ~120 business days per code; daily_basic
# returns empty. Mirrors test_factor_report_tool / test_compose.
# ---------------------------------------------------------------------------
def _stub_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame(
                {
                    "open": close, "high": close * 1.01, "low": close * 0.99,
                    "close": close, "volume": np.full(len(dates), 1e6),
                },
                index=dates,
            )
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    return StubLoader()


def _patch_data(monkeypatch, codes=CODES):
    # The engine functions import resolve_universe_codes + get_default_loader
    # from their home modules (local imports), so patch THOSE — not buddy aliases.
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes",
        lambda u: list(codes),
    )
    monkeypatch.setattr(
        "financial_analyst.data.loader_factory.get_default_loader",
        lambda: _stub_loader(),
    )


def _const_loader():
    """Constant price → zero cross-sectional variance → genuinely NaN IC metrics."""
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            close = np.full(len(dates), 50.0)
            df = pd.DataFrame(
                {"open": close, "high": close, "low": close,
                 "close": close, "volume": np.full(len(dates), 1e6)},
                index=dates,
            )
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    return StubLoader()


def test_report_nan_becomes_null(monkeypatch):
    """Load-bearing _jsonable integration guard: a degenerate constant-price panel
    yields NaN IC metrics; the endpoint must return 200 with NaN rendered as null
    (a NaN leak would 500 via Starlette's allow_nan=False). This is the end-to-end
    check the random-walk stub can't provide (its IC is finite)."""
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes", lambda u: list(CODES)
    )
    monkeypatch.setattr(
        "financial_analyst.data.loader_factory.get_default_loader", lambda: _const_loader()
    )
    client = TestClient(build_app())
    resp = client.post(
        "/factor/report",
        json={"expr_or_name": "rank(-delta(close,5))", "universe": "csi500", "freq": "week"},
    )
    assert resp.status_code == 200  # 500 if a NaN leaked (allow_nan=False render)
    assert "NaN" not in resp.text and "Infinity" not in resp.text
    body = resp.json()
    assert body["ic"]["rank_ic_mean"] is None  # NaN → null, not a number


def _client():
    return TestClient(build_app())


# ===========================================================================
# 1. _jsonable unit — NaN/Inf → None, plain values untouched, nested handled.
# ===========================================================================
def test_jsonable_sanitizes_nan_and_inf():
    out = _jsonable({"x": float("nan"), "y": [1.0, float("inf")], "z": 2.0})
    assert out == {"x": None, "y": [1, None], "z": 2.0}


def test_jsonable_nested_and_neg_inf():
    out = _jsonable({"a": {"b": float("-inf")}, "c": (3.0, float("nan"))})
    assert out == {"a": {"b": None}, "c": [3.0, None]}


# ===========================================================================
# 2. POST /factor/report — 200, has meta/ic/portfolio + status, valid JSON.
# ===========================================================================
def test_report_endpoint_ok(monkeypatch):
    _patch_data(monkeypatch)
    client = _client()
    r = client.post("/factor/report",
                    json={"expr_or_name": "rank(-delta(close,5))",
                          "universe": "csi500", "freq": "week"})
    assert r.status_code == 200
    body = r.json()  # would raise if body had a bare NaN literal → valid JSON
    assert "meta" in body
    assert "ic" in body
    assert "portfolio" in body
    assert body["status"] == "ok"
    # IC block present and rank_ic_mean is null or a real number (NaN→null).
    assert body["ic"] is not None
    ric = body["ic"]["rank_ic_mean"]
    assert ric is None or isinstance(ric, (int, float))


def test_report_endpoint_empty_universe_is_200(monkeypatch):
    """Business failure (empty universe) → HTTP 200 with status surfaced."""
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes", lambda u: [])
    client = _client()
    r = client.post("/factor/report",
                    json={"expr_or_name": "rank(-delta(close,5))",
                          "universe": "nonexistent_xyz"})
    assert r.status_code == 200
    assert r.json()["status"] == "empty_universe"


# ===========================================================================
# 3. POST /factor/forge — monkeypatch forge_factor (avoid LLM) → 200, expr.
# ===========================================================================
def test_forge_endpoint_ok(monkeypatch):
    fake = ForgeResult(idea="5日反转", expr="rank(-delta(close,5))",
                       name="usr_rev5", rationale="5日动量取负做反转",
                       compile_ok=True)
    # Endpoint calls financial_analyst.factors.forge.forge_factor via attribute
    # access, so patching the module attribute takes effect.
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor",
                        lambda idea: fake)
    client = _client()
    # quick_eval=False so we don't touch the real-data quick-IC path.
    r = client.post("/factor/forge",
                    json={"idea": "5日反转", "quick_eval": False})
    assert r.status_code == 200
    body = r.json()
    assert body["expr"] == "rank(-delta(close,5))"
    assert body["compile_ok"] is True


def test_forge_endpoint_quick_ic_attached(monkeypatch):
    """compile_ok + quick_eval=True → quick_ic dict attached (uses stub data)."""
    _patch_data(monkeypatch)
    fake = ForgeResult(idea="5日反转", expr="rank(-delta(close,5))",
                       name="usr_rev5", compile_ok=True)
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor",
                        lambda idea: fake)
    client = _client()
    r = client.post("/factor/forge",
                    json={"idea": "5日反转", "universe": "csi500",
                          "quick_eval": True})
    assert r.status_code == 200
    body = r.json()
    assert "quick_ic" in body  # present (dict on success, None if it failed)


# ===========================================================================
# 4. POST /factor/compose — 2 members → 200 (method/verdict/composite);
#    <2 members → 400.
# ===========================================================================
def test_compose_endpoint_ok(monkeypatch):
    _patch_data(monkeypatch)
    client = _client()
    r = client.post("/factor/compose",
                    json={"members": ["rank(close)", "rank(-delta(close,5))"],
                          "method": "equal", "universe": "csi500",
                          "freq": "week"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "equal"
    assert "verdict" in body
    assert "composite" in body  # FactorReport asdict (or null on a sub-failure)
    assert body["status"] == "ok"


def test_compose_endpoint_too_few_members_is_400():
    # No data patch needed: the guard returns before any universe/loader call.
    client = _client()
    r = client.post("/factor/compose", json={"members": ["only_one"]})
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "too_few_factors"


# ===========================================================================
# 5. GET /factor/archive — pre-write 2 runs to a tmp $FINANCIAL_ANALYST_HOME;
#    list / compare / history.
# ===========================================================================
def _seed_archive(monkeypatch, tmp_path):
    """Point $FINANCIAL_ANALYST_HOME at tmp and write two runs via the same
    ResearchArchive() the endpoint constructs (root=None → reads the env)."""
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    arch = ResearchArchive()
    r1 = arch.append(RunRecord(
        id="", timestamp="", kind="report", target="rank(close)",
        formula="rank(close)", universe="csi500", freq="week",
        start="2023-01-02", end="2023-06-19",
        metrics={"rank_ic_mean": 0.03, "sharpe": 1.1}))
    r2 = arch.append(RunRecord(
        id="", timestamp="", kind="report", target="rank(close)",
        formula="rank(close)", universe="csi500", freq="week",
        start="2023-01-02", end="2023-06-19",
        metrics={"rank_ic_mean": 0.05, "sharpe": 1.4}))
    return r1, r2


def test_archive_list(monkeypatch, tmp_path):
    _seed_archive(monkeypatch, tmp_path)
    client = _client()
    r = client.get("/factor/archive")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 2
    assert {x["id"] for x in runs} == {"r0001", "r0002"}


def test_archive_compare(monkeypatch, tmp_path):
    _seed_archive(monkeypatch, tmp_path)
    client = _client()
    r = client.get("/factor/archive", params={"compare": "r0001,r0002"})
    assert r.status_code == 200
    body = r.json()
    assert "metric_diffs" in body
    # b - a: rank_ic_mean 0.05 - 0.03 = 0.02
    assert body["metric_diffs"]["rank_ic_mean"] == pytest.approx(0.02)


def test_archive_history(monkeypatch, tmp_path):
    _seed_archive(monkeypatch, tmp_path)
    client = _client()
    r = client.get("/factor/archive", params={"target": "rank(close)"})
    assert r.status_code == 200
    hist = r.json()["history"]
    assert len(hist) == 2
    assert all(h["target"] == "rank(close)" for h in hist)


def test_archive_empty_is_graceful(monkeypatch, tmp_path):
    """No runs file yet → 200 with empty list (not an error)."""
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    client = _client()
    r = client.get("/factor/archive")
    assert r.status_code == 200
    assert r.json()["runs"] == []


# ===========================================================================
# 6. GET /factor/bench — stub panel → rows is a list; each row has name/rank_ic.
# ===========================================================================
def test_bench_endpoint(monkeypatch):
    _patch_data(monkeypatch)
    client = _client()
    r = client.get("/factor/bench",
                   params={"universe": "csi500", "family": "alpha101"})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert isinstance(rows, list)
    if rows:  # alpha101 family is registered → expect non-empty
        for row in rows:
            assert "name" in row
            assert "rank_ic" in row


def test_bench_empty_universe(monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes", lambda u: [])
    client = _client()
    r = client.get("/factor/bench", params={"universe": "nope"})
    assert r.status_code == 200
    assert r.json()["rows"] == []


# ===========================================================================
# 7. GET /factor/list — registered non-empty (built-in alphas).
# ===========================================================================
def test_list_endpoint():
    client = _client()
    r = client.get("/factor/list")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["registered"], list)
    assert len(body["registered"]) > 0
    assert {"name", "family", "formula"} <= set(body["registered"][0].keys())
    assert isinstance(body["user"], list)


# ===========================================================================
# 8. 500-guard — monkeypatch factor_report to raise → 500 + error, no stack.
# ===========================================================================
def test_report_endpoint_500_on_internal_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kaboom internal")

    # Endpoint calls financial_analyst.factors.eval.factor_report via attribute
    # access → patching the module attribute is what fires.
    monkeypatch.setattr("financial_analyst.factors.eval.factor_report", _boom)
    client = _client()
    r = client.post("/factor/report",
                    json={"expr_or_name": "rank(close)", "universe": "csi500"})
    assert r.status_code == 500
    body = r.json()
    assert "error" in body
    assert "RuntimeError" in body["error"]
    # No traceback leak — just "Type: msg".
    assert "Traceback" not in body["error"]
