from __future__ import annotations
import json
import pytest
from financial_analyst.factors.forge.forge import forge_factor, ForgeResult


def _fake(*contents):
    """Return a complete_fn that yields the given canned LLM contents in order."""
    seq = list(contents)
    calls = {"n": 0}
    def fn(messages):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c
    fn.calls = calls
    return fn


def test_forge_happy_path():
    good = json.dumps({"expr": "rank(-delta(close,5))", "parsed": [{"k": "方向", "v": "反转"}],
                       "name": "usr_rev5", "rationale": "5日反转", "out_of_vocab": False})
    r = forge_factor("5日反转", complete_fn=_fake(good))
    assert isinstance(r, ForgeResult)
    assert r.compile_ok is True
    assert r.expr == "rank(-delta(close,5))"
    assert r.name == "usr_rev5"
    assert r.out_of_vocab is False
    assert r.parsed == [{"k": "方向", "v": "反转"}]
    assert r.rationale == "5日反转"
    assert r.error == ""


def test_forge_repair_then_succeed():
    bad = json.dumps({"expr": "rank(-delta(close))", "parsed": [], "name": "x", "rationale": "", "out_of_vocab": False})
    good = json.dumps({"expr": "rank(-delta(close,5))", "parsed": [], "name": "usr_rev5", "rationale": "", "out_of_vocab": False})
    fn = _fake(bad, good)
    r = forge_factor("5日反转", complete_fn=fn)
    assert r.compile_ok is True
    assert r.expr == "rank(-delta(close,5))"
    assert fn.calls["n"] == 2  # repaired once


def test_forge_out_of_vocab():
    oov = json.dumps({"expr": "", "parsed": [], "name": "", "rationale": "需要股息率",
                      "out_of_vocab": True, "error": "需要 dv_ttm 字段"})
    r = forge_factor("高股息低负债", complete_fn=_fake(oov))
    assert r.out_of_vocab is True
    assert r.compile_ok is False  # no exception


def test_forge_bad_json():
    r = forge_factor("乱七八糟", complete_fn=_fake("not json at all", "still not json"))
    assert r.compile_ok is False
    assert "JSON" in r.error or "解析" in r.error


def test_forge_empty_idea():
    r = forge_factor("   ", complete_fn=_fake("{}"))
    assert r.compile_ok is False
    assert "想法" in r.error or "idea" in r.error.lower()


def test_forge_uncompilable_expr_after_retries():
    bad = json.dumps({"expr": "close + nonexistent_field", "parsed": [], "name": "x",
                      "rationale": "", "out_of_vocab": False})
    r = forge_factor("怪因子", complete_fn=_fake(bad, bad))
    assert r.compile_ok is False
    assert r.expr == "close + nonexistent_field"  # surfaced for debugging
    assert r.error


def test_forge_stale_metadata_cleared_on_second_attempt_failure():
    import json
    # attempt 1: valid JSON but bad expr (compiles-fails) → sets parsed/name; attempt 2: bad JSON
    good_json_bad_expr = json.dumps({"expr": "close + nonexistent", "parsed": [{"k": "x", "v": "y"}],
                                     "name": "usr_bad", "rationale": "r", "out_of_vocab": False})
    r = forge_factor("怪", complete_fn=_fake(good_json_bad_expr, "not json"))
    assert r.compile_ok is False
    assert "JSON" in r.error  # final failure is the bad-JSON one
    assert r.parsed == [] and r.name == ""  # NOT stale from attempt 1
