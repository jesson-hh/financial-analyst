import hashlib
import importlib.util
import json
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "workflow_audit", Path(__file__).resolve().parents[1] / "scripts/audit_trend_pullback_workflows.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def artifact():
    out = {"schema": "guanlan.factor_artifact.v1", "kind": "ml_prediction", "label": "model[xgboost]",
           "source": {"fe": {"fwd_days": 5, "label": None, "freq": "day"},
                      "model": {"kind": "xgboost", "hyperparams": {"n_estimators": 100}},
                      "split": {"prediction_scope": "purged_walkforward_oos", "segments": [
                          {"fold": 1, "rank_ic": -0.01, "n_test_dates": 20, "train_label_end": "2023-09-25",
                           "test_start": "2023-09-26", "label_horizon": 5}] }},
           "signal": {"index_names": ["datetime", "code"], "n": 1, "rows": [["2023-09-26", "SH600001", 0.1]]}}
    out["digest"] = hashlib.sha256(json.dumps(out, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    out["id"] = "fa_" + out["digest"][:24]
    return out


def terminal():
    return {"node_id": "analysis", "kind": "analysis", "payload": {
        "ok": True, "fwd_days": 1, "headline_ic": {"rank_ic": -0.012},
        "ic": {"rank_ic_mean": -0.012}, "ic_decay": [{"h": 1, "rank_ic": -0.012}, {"h": 5, "rank_ic": -0.011}],
        "factor_artifact": artifact()}}


def test_model_folds_and_analysis_horizons_not_confused():
    out = audit.terminal_evidence(terminal())
    assert out["evaluation_fwd_days"] == 1
    assert out["headline_ic"]["rank_ic"] == -0.012
    assert out["analysis_reevaluation_at_training_horizon"]["rank_ic"] == -0.011
    assert out["artifact"]["training_target_fwd_days"] == 5
    assert out["artifact"]["native_rolling_folds"][0]["rank_ic"] == -0.01
    assert out["artifact"]["native_model_headline_metrics"] is None
    assert out["artifact"]["native_fold_ic_all_negative"]


def test_artifact_tampering_detected():
    original = artifact()
    assert audit.artifact_evidence(original)["digest_verified"]
    original["signal"]["rows"][0][2] = 3.0
    assert not audit.artifact_evidence(original)["digest_verified"]


def test_result_ok_does_not_hide_failed_backtest(tmp_path):
    run_id = "wr_1234567890abcdef"
    path = tmp_path / ".data/workflows/_runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True)
    record = {"id": run_id, "workspace_id": "rw_general", "scope": "workspace", "status": "completed_with_errors",
              "node_state": {"analysis": {"state": "done"}, "backtest": {"state": "error"}},
              "result": {"ok": True, "terminal": terminal(),
                         "node_errors": [{"nid": "backtest", "type": "backtest", "error": "missing exit quote"}]}}
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    out = audit.extract_run(tmp_path, {"run_id": run_id}, "rw_general")
    assert out["read_ok"] and out["result_ok"]
    assert not out["workflow_execution_passed"]
    assert not out["backtest_account_available"]
    assert out["node_errors"][0]["error"] == "missing exit quote"
    assert path.read_bytes() == before
    mismatch = audit.extract_run(tmp_path, {"run_id": run_id}, "rw_111111111111")
    assert not mismatch["read_ok"]


def test_missing_run_does_not_create_live_state(tmp_path):
    out = audit.extract_run(tmp_path, {"run_id": "wr_1234567890abcdef"}, "rw_general")
    assert not out["read_ok"]
    assert not list(tmp_path.iterdir())
