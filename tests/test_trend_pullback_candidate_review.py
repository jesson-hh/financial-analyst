from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location(
    "candidate_review_script", Path(__file__).resolve().parents[1] / "scripts/build_trend_pullback_candidate_review.py")
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def candidate(code, branch):
    keys = ["ma5_gt_ma10", "ma10_gt_ma20", "ma20_gt_ma50", "close_above_ma50",
            "ma5_rising_3", "ma10_rising_3", "ma20_rising_3", "ma50_rising_3",
            "pullback_from_high10", "near_ma10" if branch.endswith("10") else "near_ma20"]
    ma = {5: 10.1, 10: 10.0, 20: 9.9, 50: 9.8}
    evidence = {k: {"passed": True, "label": k} for k in keys}
    for left, right in ((5, 10), (10, 20), (20, 50)):
        evidence[f"ma{left}_gt_ma{right}"].update(left=ma[left], right=ma[right], op=">")
    for n in ma:
        evidence[f"ma{n}_rising_3"].update(values=[ma[n] - .03, ma[n] - .02, ma[n] - .01, ma[n]], transitions=3, strict=True)
    evidence["pullback_from_high10"]["value"] = -.02
    line = 10 if branch.endswith("10") else 20
    evidence[f"near_ma{line}"]["value"] = 10 / ma[line] - 1
    return {"code": code, "name": "fixture", "as_of": review.ASOF, "close": 10,
            "avg_amount_yuan": 60000000,
            "evidence": evidence,
            "filter_evidence": {"asof_trade": {"required": True, "bar_date": review.ASOF,
                "effective_asof": review.ASOF, "volume": 10000, "amount_yuan": 100000},
                "st": {"excluded": True, "is_st": False},
                "liquidity": {"passed": True, "observations": 20, "average_yuan": 60000000}}}


@pytest.fixture
def fixture(tmp_path):
    live = tmp_path / "live"
    settings = {"universe": "csi800", "features": ["close"], "freq": "day", "fwd_days": 5,
                "start": "2023-01-01", "end": review.ASOF}
    models = []
    for i, kind in enumerate(review.KINDS):
        vid = "m_" + str(i + 1) * 10
        directory = live / "guanlan_v2/strategy/vendor/artifacts/models" / vid
        directory.mkdir(parents=True)
        ep, rp = directory / "estimator.pkl", directory / "ranking.parquet"
        ep.write_bytes(b"fixture; deliberately NOT deserializable")
        pd.DataFrame({"code": ["SH600001", "SH600002", "SH600003"], "date": review.ASOF,
                      "lgb_score": [0.01, 0.02, 0.03], "lgb_pct": [1 / 3, 2 / 3, 1.0]}).to_parquet(rp)
        meta = {"id": vid, "kind": kind, "status": "draft", "recipe": settings,
                "asof": review.ASOF, "universe": "csi800",
                "training_provenance": {"prediction_date": review.ASOF, "features": {"end": review.ASOF}},
                "estimator_artifact": {"file": ep.name, "sha256": review.digest(ep), "available": True,
                                       "format": "trusted-local-pickle-v1"},
                "ranking_artifact": {"file": rp.name, "sha256": review.digest(rp)}}
        dump(directory / "meta.json", meta)
        models.append({"kind": kind, "variant_id": vid, "status": "completed", "final_state": {"ok": True},
                       "request": {"candidate_only": True}})
    training_path = tmp_path / "training_receipts.json"
    dump(training_path, {"settings": settings, "models": models})
    rid = "wr_" + "a" * 24
    workflow_path = tmp_path / "workflow_receipts.json"
    dump(workflow_path, {"workspace_id": "rw_general", "workflows": [{"file": "01_strict_pullback_screen.json",
        "run_id": rid, "workflow_id": "w123", "workspace_id": "rw_general"}]})
    terminals = []
    for branch in review.BRANCHES:
        rows = [candidate("SH600001", branch)]
        if branch.endswith("20"):
            rows += [candidate("SH600099", branch)]  # No model has this candidate; must remain missing.
        terminals.append({"kind": "data_screen", "node_id": branch,
                          "payload": {"ok": True, "as_of": review.ASOF, "requested_as_of": review.ASOF,
                                      "n_matched": len(rows), "candidates": rows}})
    run_path = live / ".data/workflows/_runs" / f"{rid}.json"
    dump(run_path, {"id": rid, "workspace_id": "rw_general", "status": "completed",
                    "result": {"ok": True, "node_errors": [], "all_terminals": terminals}})
    return live, training_path, workflow_path, run_path


def test_union_no_rerank_no_fill_no_unpickle(fixture):
    live, train, work, _ = fixture
    report = review.build_review(live, train, work)
    assert report["status"] == "complete"
    assert report["available_model_count"] == 3
    assert len(report["candidates"]) == 2
    one, missing = report["candidates"]
    assert one["branches"] == list(review.BRANCHES)
    assert one["models"]["rf"]["percentile_100"] == pytest.approx(100 / 3)
    assert missing["models"]["rf"]["percentile"] is None
    assert missing["models"]["rf"]["missing_reason"] == "candidate_not_in_full_pool_ranking"
    assert "不是胜率" in review.render_markdown(report)


@pytest.mark.parametrize("where", ["payload", "quote", "candidate"])
def test_stale_market_dates_block_candidates(fixture, where):
    live, train, work, run = fixture
    data = review.read_json(run)
    payload = data["result"]["all_terminals"][0]["payload"]
    if where == "payload":
        payload["as_of"] = "2026-09-10"
    elif where == "quote":
        payload["candidates"][0]["filter_evidence"]["asof_trade"]["bar_date"] = "2026-09-10"
    else:
        payload["candidates"][0]["as_of"] = "2026-09-10"
    dump(run, data)
    report = review.build_review(live, train, work)
    assert report["status"] == "blocked"
    assert report["candidates"] == []


@pytest.mark.parametrize("mutation", ["model_date", "ranking_date", "duplicate", "estimator_hash", "not_draft", "range", "path"])
def test_invalid_model_is_never_joined(fixture, mutation):
    live, train, work, _ = fixture
    directory = live / "guanlan_v2/strategy/vendor/artifacts/models/m_1111111111"
    meta_path = directory / "meta.json"
    meta = review.read_json(meta_path)
    if mutation == "model_date":
        meta["asof"] = "2026-09-10"
    elif mutation == "estimator_hash":
        meta["estimator_artifact"]["sha256"] = "0" * 64
    elif mutation == "not_draft":
        meta["status"] = "production"
    elif mutation == "path":
        meta["ranking_artifact"]["file"] = "../outside.parquet"
    else:
        path = directory / "ranking.parquet"
        frame = pd.read_parquet(path)
        if mutation == "ranking_date":
            frame.loc[0, "date"] = "2026-09-10"
        elif mutation == "duplicate":
            frame.loc[1, "code"] = frame.loc[0, "code"]
        else:
            frame.loc[0, "lgb_pct"] = 99.0
        frame.to_parquet(path)
        meta["ranking_artifact"]["sha256"] = review.digest(path)
    dump(meta_path, meta)
    report = review.build_review(live, train, work)
    assert report["status"] == "partial"
    assert report["available_model_count"] == 2
    assert report["candidates"][0]["models"]["rf"]["percentile"] is None


def test_empty_candidates_do_not_trigger_fallback(fixture):
    live, train, work, run = fixture
    data = review.read_json(run)
    for terminal in data["result"]["all_terminals"]:
        terminal["payload"].update(candidates=[], n_matched=0)
    dump(run, data)
    report = review.build_review(live, train, work)
    assert report["status"] == "complete"
    assert report["candidate_count"] == 0
    assert "未放宽条件" in review.render_markdown(report)


def test_missing_branch_and_failed_run_are_blocked(fixture):
    live, train, work, run = fixture
    data = review.read_json(run)
    data["result"]["all_terminals"].pop()
    dump(run, data)
    assert review.build_review(live, train, work)["status"] == "blocked"
    data["status"] = "running"
    dump(run, data)
    assert review.build_review(live, train, work)["status"] == "blocked"


def test_negative_model_evidence_is_visible_before_candidate_table(fixture):
    live, train, work, run = fixture
    report = review.build_review(live, train, work)
    report["models"][0]["oos_ic_5day"] = -0.0046
    rendered = review.render_markdown(report)
    assert "-0.0046" in rendered
    assert rendered.index("未显示正向排序优势") < rendered.index("| 代码 |")


@pytest.mark.parametrize("mutation", ["shape", "liquidity", "trading"])
def test_false_pass_flags_do_not_bypass_numeric_evidence(fixture, mutation):
    live, train, work, run = fixture
    data = review.read_json(run)
    row = data["result"]["all_terminals"][0]["payload"]["candidates"][0]
    if mutation == "shape":
        row["evidence"]["pullback_from_high10"]["value"] = -.001
    elif mutation == "liquidity":
        row["avg_amount_yuan"] = 1000
    else:
        row["filter_evidence"]["asof_trade"]["volume"] = 0
    dump(run, data)
    assert review.build_review(live, train, work)["status"] == "blocked"
