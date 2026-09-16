from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


SPEC = importlib.util.spec_from_file_location(
    "trend_training_audit", Path(__file__).resolve().parents[1] / "scripts/audit_trend_pullback_training.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "CHECKOUT", tmp_path / "checkout")
    live = tmp_path / "live"
    old = live / "guanlan_v2/strategy/vendor/artifacts/models/old"
    old.mkdir(parents=True)
    (old / "meta.json").write_text('{"id":"old"}', encoding="utf-8")
    out = audit.CHECKOUT / "var" / "run"
    baseline = out / "baseline.json"
    audit.freeze(live, baseline)
    features = ["close", "open", "volume", "returns"]
    design = out / "design.json"
    audit.write_json(design, {"raw_features": features})
    return live, out, baseline, design, features


def candidate(live, kind, features):
    vid = "m_" + kind
    directory = live / "guanlan_v2/strategy/vendor/artifacts/models" / vid
    directory.mkdir(parents=True)
    ranking = pd.DataFrame({"code": ["SH600001", "SH600002", "SZ000001"],
                            "date": [audit.ASOF] * 3, "lgb_score": [0.01, 0.03, 0.02],
                            "lgb_pct": [1 / 3, 1.0, 2 / 3], "lgb_rank": [3, 1, 2]})
    ranking.to_parquet(directory / "ranking.parquet", index=False)
    (directory / "estimator.pkl").write_bytes(("opaque-local-test-" + kind).encode() * 20)
    recipe = {"features": features, "label": "fwd_ret", "fwd_days": 5,
              "universe": "csi800", "start": audit.START, "end": audit.ASOF,
              "freq": "day", "params": audit.EXPECTED_PARAMS[kind]}
    provenance = {
        "schema": "guanlan.training_provenance.v1", "strict_pit": False,
        "features": {"start": "2023-01-03", "end": audit.ASOF, "n_dates": 105, "n_rows": 315},
        "labels": {"start": "2023-01-03", "end": "2026-09-04", "n_dates": 100, "n_rows": 300},
        "final_fit": {"start": "2023-03-20", "end": "2026-09-04", "n_dates": 100, "n_rows": 900},
        "prediction_date": audit.ASOF, "label_spec": {"expression": "fwd_ret", "horizon": 5, "label_span": 5},
        "feature_expressions": features, "feature_snapshot_sha256": "a" * 64, "label_snapshot_sha256": "b" * 64,
        "evaluation": {"metric_scope": "purged_chronological_holdout", "label_span": 5,
                       "n_test_dates_requested": 20, "n_test_dates_scored": 20, "ready": True,
                       "test_start": "2026-01-05", "test_end": "2026-09-04",
                       "train": {"end": "2025-12-22"},
                       "split": {"policy": "label_endpoint_strictly_before_test", "endpoint_source": "per_code_observations",
                                 "label_horizon": 5, "purged_dates": 5, "train_end": "2025-12-22",
                                 "train_label_end": "2025-12-29", "test_start": "2026-01-05"}},
    }
    meta = {"id": vid, "kind": kind, "name": kind, "source": "workflow", "status": "draft",
            "asof": audit.ASOF, "n_features": 4, "universe": "csi800", "retrainable": True,
            "recipe": recipe, "hyper": audit.EXPECTED_PARAMS[kind], "requested_data_end": audit.ASOF,
            "ranking_rows": 3, "train_rows": 900, "oos_ic": 0.015, "training_provenance": provenance,
            "ranking_artifact": {"file": "ranking.parquet", "sha256": audit.sha256(directory / "ranking.parquet")},
            "estimator_artifact": {"file": "estimator.pkl", "sha256": audit.sha256(directory / "estimator.pkl"),
                                   "available": True, "format": "trusted-local-pickle-v1"}}
    save_meta(directory, meta)
    return {"kind": kind, "variant_id": vid, "request": {"candidate_only": True, "kind": kind, "recipe": recipe}}, meta


def save_meta(directory, meta):
    core = {key: meta.get(key) for key in ("kind", "recipe", "asof", "base_features", "factor_ids", "hyper",
                                          "training_provenance", "estimator_artifact")}
    core["ranking_sha256"] = meta["ranking_artifact"]["sha256"]
    core["estimator_actual_sha256"] = meta["estimator_artifact"]["sha256"]
    meta["model_version"] = "mv_" + hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_freeze_preserves_missing_default_and_refuses_overwrite(setup):
    live, out, baseline, *_ = setup
    frozen = audit.read_json(baseline)
    assert frozen["files"][frozen["default_pointer"]] == {"exists": False}
    assert audit.audit_baseline(live, baseline)["passed"]
    with pytest.raises(ValueError, match="replace"):
        audit.freeze(live, baseline)


def test_detect_default_and_old_model_mutation(setup):
    live, out, baseline, *_ = setup
    pointer = live / "guanlan_v2/strategy/vendor/artifacts/models/_default.json"
    pointer.write_text('{"id":"new"}')
    (pointer.parent / "old/meta.json").write_text('{"id":"tampered"}')
    result = audit.audit_baseline(live, baseline)
    assert not result["passed"]
    assert len(result["changed"]) == 2


def test_valid_candidate_passes_without_unpickling(setup):
    live, out, baseline, design, features = setup
    receipt, meta = candidate(live, "rf", features)
    result = audit.audit_candidate(live, receipt, features, "2026-09-04")
    assert result["passed"], [row for row in result["checks"] if not row["passed"]]
    assert result["warnings"]  # Sparse ranking and absent full technical manifest remain explicit.


@pytest.mark.parametrize("case,expected", [
    ("asof", "meta.asof"), ("maturity", "labels.mature_tail"),
    ("overlap", "holdout.no_label_overlap"), ("hyper", "actual_hyper.n_estimators"),
    ("candidate", "explicit_candidate_only_request"), ("ic", "holdout.finite_ic"),
])
def test_semantic_failures_are_not_http_success(setup, case, expected):
    live, out, baseline, design, features = setup
    receipt, meta = candidate(live, "rf", features)
    if case == "asof": meta["asof"] = "2026-09-03"
    if case == "maturity": meta["training_provenance"]["labels"]["end"] = audit.ASOF
    if case == "overlap": meta["training_provenance"]["evaluation"]["split"]["train_label_end"] = "2026-01-05"
    if case == "hyper": meta["hyper"] = {"n_estimators": 1, "max_depth": None}
    if case == "candidate": receipt["request"]["candidate_only"] = False
    if case == "ic": meta["oos_ic"] = float("nan")
    save_meta(live / "guanlan_v2/strategy/vendor/artifacts/models" / receipt["variant_id"], meta)
    result = audit.audit_candidate(live, receipt, features, "2026-09-04")
    assert not result["passed"]
    assert expected in [row["name"] for row in result["checks"] if not row["passed"]]


def test_hash_tampering_and_reference_escape_rejected(setup):
    live, out, baseline, design, features = setup
    receipt, meta = candidate(live, "rf", features)
    directory = live / "guanlan_v2/strategy/vendor/artifacts/models" / receipt["variant_id"]
    (directory / "estimator.pkl").write_bytes(b"different" * 30)
    result = audit.audit_candidate(live, receipt, features, "2026-09-04")
    assert not result["passed"]
    assert any(row["name"] == "estimator.sha256" and not row["passed"] for row in result["checks"])
    with pytest.raises(ValueError, match="Unsafe"):
        audit.file_reference(directory, "../outside.pkl")


def test_complete_audit_outputs_json_and_md(setup, monkeypatch):
    live, out, baseline, design, features = setup
    monkeypatch.setattr(audit, "trading_calendar", lambda live: ["2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"])
    models = [candidate(live, kind, features)[0] for kind in audit.EXPECTED_PARAMS]
    receipts = out / "receipts.json"
    audit.write_json(receipts, {"started_at": (datetime.now().astimezone() + timedelta(seconds=1)).isoformat(), "models": models})
    result = audit.audit(live, baseline, receipts, design, out)
    assert result["passed"]
    assert (out / "training_qa.md").is_file()
    assert audit.read_json(out / "training_qa.json")["passed"]


def test_output_guard_and_naive_timestamp(setup):
    live, *_ = setup
    with pytest.raises(ValueError, match="inside"):
        audit.output_path(live / "would-write.json")
    assert audit.timestamp("2026-09-12T12:00:00").tzinfo is not None
    assert audit.timestamp("bad") is None
    assert audit.json_safe({"x": float("nan")}) == {"x": "nan"}
