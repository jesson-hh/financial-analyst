# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.research.loop as rl
import guanlan_v2.research.optimize_adapter as ad
import guanlan_v2.research.store as rstore
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.trial import ValidationMetrics
from guanlan_v2.workflow import api as wapi
from guanlan_v2.workflow import executor as wex
from guanlan_v2.workflow import trial_audit_connector as connector


def _sr(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


def _data_snapshot(tag: str = "shared") -> dict:
    projection = {
        "domain": "workflow-runtime-panel-content-v1",
        "values_digest": connector.content_digest({"panel": tag}),
        "n_rows": 640,
        "n_columns": 3,
        "columns": ["close", "volume", "factor"],
        "dtypes": ["float64", "float64", "float64"],
        "index_names": ["datetime", "code"],
        "date_start": "2012-01-01",
        "date_end": "2025-05-31",
        "n_codes": 4,
    }
    envelope = {
        "version": "workflow-runtime-panel-content-v1",
        "authority": "runtime_panel_content",
        "strict_pit": False,
        **projection,
    }
    return {"digest": connector.content_digest(envelope), **envelope}


def _execution(
    graph, dates, returns, *, candidate_hash, data_digest, freq="month",
    snapshot=None, selection_scores=None, selection_dates=None,
    sharpe=1.5, oos="robust",
):
    selection_dates = list(selection_dates or dates)
    if selection_scores is None:
        selection_scores = [
            0.04 + 0.001 * np.sin(i / 7.0) for i in range(len(selection_dates))
        ]
    rank_ic = float(np.mean(selection_scores))
    return {
        "_trusted_graph": graph,
        "_trusted_candidate_hash": candidate_hash,
        "_trusted_data_snapshot_hash": data_digest,
        "_trusted_backtests": {
            "bt": {
                "ok": True,
                "data_snapshot": snapshot or _data_snapshot(),
                "portfolio": {"return_series": [
                    [d, float(v)] for d, v in zip(dates, returns)
                ], "sharpe": float(sharpe)},
                "ic": {
                    "rank_ic_mean": rank_ic,
                    "rank_ic_series": [
                        [d, float(v)] for d, v in zip(selection_dates, selection_scores)
                    ],
                },
                "oos": {"verdict": oos},
                "backtest": {"rebalance": freq, "rebalance_freq": freq},
            }
        },
        "metrics": {
            "rank_ic": rank_ic, "sharpe": float(sharpe), "oos_verdict": oos,
        },
    }


def _trusted_family(tmp_path, *, periods=160, min_rank_ic=0.02):
    clock = SystemClock()
    registry, stores, ledger, _states, _rounds = ad._build_env(clock)
    study = ad.research_study(
        "可信审计测试", universe="csi300_active", freq="month",
        min_rank_ic=min_rank_ic,
    )
    split = ad.research_split_spec("month")
    split_payload = stores.payloads.put(
        _sr("SplitSpec"), split, registry_digest=registry.registry_digest,
        namespace="main", idempotency_key="audit-test-split",
    )
    split_ref = TypedPayloadRef(schema_ref=_sr("SplitSpec"), payload_ref=split_payload)

    rng = np.random.default_rng(8128)
    common = rng.normal(0.0, 0.003, periods)
    paths = [
        0.018 + common + rng.normal(0.0, 0.002, periods),
        0.007 + common + rng.normal(0.0, 0.004, periods),
        0.002 + common + rng.normal(0.0, 0.006, periods),
        -0.003 + common + rng.normal(0.0, 0.007, periods),
    ]
    dates = pd.date_range("2012-01-31", periods=periods, freq="ME").strftime(
        "%Y-%m-%d"
    ).tolist()
    phase = np.sin(np.arange(periods, dtype="float64") / 7.0) * 0.001
    score_paths = [0.08 + phase, 0.06 + phase * 1.1,
                   0.04 + phase * 1.2, 0.025 + phase * 1.3]
    captures = []
    trial_ids = []
    graphs = []
    candidate_hashes = []
    data_digest = "d" * 64
    code_digest = "c" * 64
    for i, returns in enumerate(paths):
        graph = {
            "nodes": [
                {"id": "f", "type": "formula", "params": {"expr": f"rank(close/{i + 2})"}},
                {"id": "bt", "type": "backtest", "params": {"rebalance": "month"}},
                {"id": "audit", "type": "overfit", "params": {}},
            ],
            "edges": [
                {"from": ["f", "out"], "to": ["bt", "factor"]},
                {"from": ["bt", "result"], "to": ["audit", "result"]},
            ],
        }
        candidate = ad.candidate_from_graph(graph, params={
            "universe": "csi300_active", "freq": "month",
            "start": None, "end": None,
        })
        candidate_payload = stores.payloads.put(
            _sr("OptimizeCandidate"), candidate,
            registry_digest=registry.registry_digest, namespace="main",
            idempotency_key=f"audit-test-candidate-{i}",
        )
        candidate_ref = TypedPayloadRef(
            schema_ref=_sr("OptimizeCandidate"), payload_ref=candidate_payload,
        )
        reserved = ledger.reserve_validation(
            study, candidate_ref, split_ref, data_snapshot_hash=data_digest,
            code_prompt_model_hash=code_digest, idempotency_key=f"audit-test-trial-{i}",
        )
        rank_ic = float(np.mean(score_paths[i]))
        metrics = ValidationMetrics(
            rank_ic=rank_ic, sharpe=1.5 - i * 0.1,
            ann_return=0.2 - i * 0.02, max_drawdown=-0.1,
            turnover=0.2, oos_verdict="robust", n_dates=periods,
            factor=f"factor-{i}", source="run_graph",
        )
        metrics_ref = stores.payloads.put(
            _sr("ValidationMetrics"), metrics,
            registry_digest=registry.registry_digest, namespace="main",
            idempotency_key=f"audit-test-metrics-{i}",
        )
        ledger.reveal_validation(
            reserved.trial_id, metrics_ref.object_id, metrics_ref.content_digest,
        )
        captures.append(connector.capture_trial_return(
            ledger=ledger, trial_id=reserved.trial_id, frequency="month",
            execution=_execution(
                graph, dates, returns, candidate_hash=candidate.candidate_hash,
                data_digest=data_digest,
                selection_scores=score_paths[i], sharpe=1.5 - i * 0.1,
            ),
        ))
        trial_ids.append(reserved.trial_id)
        graphs.append(graph)
        candidate_hashes.append(candidate.candidate_hash)

    store = connector.TrialAuditEvidenceStore(tmp_path / "audit-contexts")
    context = connector.seal_trial_family_context(
        ledger=ledger, study=study, captures=captures,
        selected_trial_id=trial_ids[0], min_rank_ic=min_rank_ic, store=store,
    )
    return {
        "ledger": ledger, "study": study, "captures": captures,
        "trial_ids": trial_ids, "dates": dates, "paths": paths,
        "score_paths": score_paths,
        "graphs": graphs, "candidate_hashes": candidate_hashes,
        "store": store, "context": context,
    }


def test_connector_seals_exact_selected_column_and_content_addressed_store(tmp_path):
    env = _trusted_family(tmp_path)
    context = env["context"]

    assert context["n_trials"] == 4
    assert len(context["trial_returns"]) == len(env["dates"])
    assert len(context["selection_score_matrix"]) == len(env["dates"])
    assert context["selection_objective"] == "mean_rank_ic"
    assert context["selection_score_source"] == "backtest.ic.rank_ic_series"
    assert context["selection_eligibility_scope"] == "full_validation_base_gate"
    assert context["eligible_column_indices"] == [0, 1, 2, 3]
    assert context["columns"][context["selected_column_index"]]["trial_id"] \
        == context["selected_trial_id"] == env["trial_ids"][0]
    assert context["ledger_data_context_hash"] == "d" * 64
    assert context["data_snapshot_digest"] == _data_snapshot()["digest"]
    assert context["data_snapshot_authority"] == "runtime_panel_content"
    assert context["data_snapshot_strict_pit"] is False
    assert context["data_snapshot"]["digest"] == context["data_snapshot_digest"]
    selected = [row[context["selected_column_index"]]
                for row in context["trial_returns"]]
    assert selected == pytest.approx(context["selected_returns"], abs=1e-15)
    selected_scores = [row[context["selected_column_index"]]
                       for row in context["selection_score_matrix"]]
    assert selected_scores == pytest.approx(
        context["selected_selection_scores"], abs=1e-15,
    )
    assert env["store"].resolve(context["context_id"]) == context
    tie_order = [
        (column["trial_created_at"], column["trial_id"])
        for column in context["columns"]
    ]
    assert tie_order == sorted(tie_order)
    resealed = connector.seal_trial_family_context(
        ledger=env["ledger"], study=env["study"],
        captures=list(reversed(env["captures"])),
        selected_trial_id=env["trial_ids"][0], min_rank_ic=0.02,
        store=env["store"],
    )
    assert resealed == context

    # A validly captured but different period index is not silently intersected.
    bad_captures = deepcopy(env["captures"])
    bad = bad_captures[1]
    bad["dates"][0] = "2011-12-31"
    bad["return_series_digest"] = connector.content_digest({
        "domain": "trial-return-series-v1", "frequency": "month",
        "dates": bad["dates"], "returns": bad["returns"],
    })
    bad["capture_digest"] = connector._capture_digest(bad)
    with pytest.raises(connector.TrialAuditConnectorError, match="not exactly aligned"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=bad_captures,
            selected_trial_id=env["trial_ids"][0], min_rank_ic=0.02,
            store=env["store"],
        )


def test_connector_rejects_rankic_axis_drift_and_nonobjective_winner(tmp_path):
    env = _trusted_family(tmp_path)
    drifted = deepcopy(env["captures"])
    drifted[1]["selection_score_dates"][0] = "2011-12-31"
    drifted[1]["selection_score_series_digest"] = connector.content_digest({
        "domain": "trial-selection-score-series-v1",
        "frequency": "month",
        "objective": connector.SELECTION_OBJECTIVE,
        "source": connector.SELECTION_SCORE_SOURCE,
        "dates": drifted[1]["selection_score_dates"],
        "scores": drifted[1]["selection_scores"],
    })
    drifted[1]["capture_digest"] = connector._capture_digest(drifted[1])
    with pytest.raises(connector.TrialAuditConnectorError, match="RankIC selection dates"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=drifted,
            selected_trial_id=env["trial_ids"][0], min_rank_ic=0.02,
            store=env["store"],
        )

    with pytest.raises(connector.TrialAuditConnectorError, match="mean-RankIC winner"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=env["captures"],
            selected_trial_id=env["trial_ids"][1], min_rank_ic=0.02,
            store=env["store"],
        )


def test_capture_fails_closed_on_missing_or_scalar_drifting_rankic(tmp_path):
    env = _trusted_family(tmp_path)
    trial_id = env["trial_ids"][0]
    capture = env["captures"][0]
    graph = {
        "nodes": [{"id": "bt", "type": "backtest", "params": {}}], "edges": [],
    }
    missing = _execution(
        graph, env["dates"], env["paths"][0],
        candidate_hash=capture["candidate_hash"], data_digest="d" * 64,
    )
    del missing["_trusted_backtests"]["bt"]["ic"]["rank_ic_series"]
    with pytest.raises(connector.TrialAuditConnectorError, match="rank_ic_series"):
        connector.capture_trial_return(
            ledger=env["ledger"], trial_id=trial_id,
            frequency="month", execution=missing,
        )

    drift = _execution(
        graph, env["dates"], env["paths"][0],
        candidate_hash=capture["candidate_hash"], data_digest="d" * 64,
    )
    drift["metrics"]["rank_ic"] += 0.01
    with pytest.raises(connector.TrialAuditConnectorError, match="gate rank_ic"):
        connector.capture_trial_return(
            ledger=env["ledger"], trial_id=trial_id,
            frequency="month", execution=drift,
        )


def test_policy_is_immutable_and_context_write_requires_private_capability(
    tmp_path, monkeypatch,
):
    env = _trusted_family(tmp_path)
    with pytest.raises(TypeError):
        connector.SERVER_OVERFIT_AUDIT_POLICY["pbo_max"] = 1.0
    with pytest.raises(connector.TrialAuditConnectorError, match="private server"):
        env["store"].put(env["context"])
    with pytest.raises(connector.TrialAuditConnectorError, match="capability is required"):
        env["store"]._put(env["context"], None)

    # Even rebinding both exported names cannot move the private governor
    # anchor.  Resolve rechecks that anchor on every read, before trusting HMAC.
    monkeypatch.setattr(connector, "SERVER_OVERFIT_AUDIT_POLICY", {"pbo_max": 1.0})
    monkeypatch.setattr(
        connector, "SERVER_OVERFIT_AUDIT_POLICY_DIGEST",
        connector.content_digest({
            "domain": connector.AUDIT_POLICY_VERSION,
            "policy": {"pbo_max": 1.0},
        }),
    )
    with pytest.raises(connector.TrialAuditConnectorError, match="policy integrity"):
        env["store"].resolve(env["context"]["context_id"])


def test_legacy_context_version_cannot_fall_back_to_return_selected_pbo(tmp_path):
    env = _trusted_family(tmp_path)
    legacy = deepcopy(env["context"])
    legacy.pop("context_id", None)
    legacy.pop("evidence_digest", None)
    legacy["version"] = "trial-audit-context-v2"
    with pytest.raises(connector.TrialAuditConnectorError, match="unsupported"):
        env["store"]._prepare(legacy)


def test_persistent_hmac_rejects_rehashed_disk_forgery(tmp_path):
    env = _trusted_family(tmp_path)
    context = env["context"]

    # A fresh process/store instance at the same root loads the persistent key.
    reopened = connector.TrialAuditEvidenceStore(env["store"].root)
    assert reopened.resolve(context["context_id"]) == context

    foreign = connector.TrialAuditEvidenceStore(tmp_path / "foreign-service")
    foreign._path(context["context_id"]).write_text(
        env["store"]._path(context["context_id"]).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(connector.TrialAuditConnectorError, match="HMAC key mismatch"):
        foreign.resolve(context["context_id"])

    # Simulate an attacker who understands the public content-digest scheme:
    # alter evidence, recompute digest/id, and write a new object while retaining
    # the old server tag.  Digest-only sealing would accept this; HMAC must not.
    original_path = env["store"]._path(context["context_id"])
    forged = json.loads(original_path.read_text(encoding="utf-8"))
    forged["selected_returns"][0] += 0.25
    forged_digest = connector._context_digest(forged)
    forged_id = f"tac_{forged_digest}"
    forged["evidence_digest"] = forged_digest
    forged["context_id"] = forged_id
    env["store"]._path(forged_id).write_text(
        json.dumps(forged, ensure_ascii=False), encoding="utf-8",
    )
    with pytest.raises(connector.TrialAuditConnectorError, match="HMAC verification"):
        reopened.resolve(forged_id)


def test_capture_requires_verified_actual_snapshot_and_family_rejects_mixed_snapshot(tmp_path):
    env = _trusted_family(tmp_path)
    trial_id = env["trial_ids"][0]
    capture0 = env["captures"][0]

    missing = _execution(
        {"nodes": [{"id": "bt", "type": "backtest", "params": {}}], "edges": []},
        env["dates"], env["paths"][0], candidate_hash=capture0["candidate_hash"],
        data_digest="d" * 64,
    )
    del missing["_trusted_backtests"]["bt"]["data_snapshot"]
    with pytest.raises(connector.TrialAuditConnectorError, match="lacks data_snapshot"):
        connector.capture_trial_return(
            ledger=env["ledger"], trial_id=trial_id, frequency="month", execution=missing,
        )

    tampered = _execution(
        {"nodes": [{"id": "bt", "type": "backtest", "params": {}}], "edges": []},
        env["dates"], env["paths"][0], candidate_hash=capture0["candidate_hash"],
        data_digest="d" * 64,
    )
    tampered["_trusted_backtests"]["bt"]["data_snapshot"]["n_rows"] += 1
    with pytest.raises(connector.TrialAuditConnectorError, match="content digest mismatch"):
        connector.capture_trial_return(
            ledger=env["ledger"], trial_id=trial_id, frequency="month", execution=tampered,
        )
    for claim, value in (("authority", "caller_claim"), ("strict_pit", True)):
        declaration_tamper = _execution(
            {"nodes": [{"id": "bt", "type": "backtest", "params": {}}], "edges": []},
            env["dates"], env["paths"][0], candidate_hash=capture0["candidate_hash"],
            data_digest="d" * 64,
        )
        declaration_tamper["_trusted_backtests"]["bt"]["data_snapshot"][claim] = value
        with pytest.raises(connector.TrialAuditConnectorError, match="content digest mismatch"):
            connector.capture_trial_return(
                ledger=env["ledger"], trial_id=trial_id, frequency="month",
                execution=declaration_tamper,
            )

    # The TrialLedger scheduling context remains identical, but one candidate
    # claims a different, internally valid materialized panel.  Never mix it
    # into a T x N matrix.
    mixed = deepcopy(env["captures"])
    other = _data_snapshot("different-runtime-panel")
    mixed[1]["backtest_data_snapshot"] = other
    mixed[1]["backtest_data_snapshot_digest"] = other["digest"]
    mixed[1]["backtest_data_snapshot_authority"] = other["authority"]
    mixed[1]["backtest_data_snapshot_strict_pit"] = other["strict_pit"]
    mixed[1]["capture_digest"] = connector._capture_digest(mixed[1])
    with pytest.raises(connector.TrialAuditConnectorError, match="actual backtest data snapshots"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=mixed,
            selected_trial_id=env["trial_ids"][0], min_rank_ic=0.02,
            store=env["store"],
        )


def test_connector_rejects_incomplete_family_wrong_selected_id_and_tampering(tmp_path):
    env = _trusted_family(tmp_path)
    with pytest.raises(connector.TrialAuditConnectorError, match="raw_trial_count=4"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=env["captures"][:-1],
            selected_trial_id=env["trial_ids"][0], min_rank_ic=0.02,
            store=env["store"],
        )
    with pytest.raises(connector.TrialAuditConnectorError, match="not a raw trial"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=env["captures"],
            selected_trial_id="fam.fake::not-a-column", min_rank_ic=0.02,
            store=env["store"],
        )

    wrong_binding = deepcopy(env["captures"])
    wrong_binding[0]["ledger_result_digest"] = "f" * 64
    wrong_binding[0]["capture_digest"] = connector._capture_digest(wrong_binding[0])
    with pytest.raises(connector.TrialAuditConnectorError, match="does not match TrialLedger"):
        connector.seal_trial_family_context(
            ledger=env["ledger"], study=env["study"], captures=wrong_binding,
            selected_trial_id=env["trial_ids"][0], min_rank_ic=0.02,
            store=env["store"],
        )

    execution = _execution(
        env["captures"][0].get("_trusted_graph") or {
            "nodes": [{"id": "bt", "type": "backtest", "params": {}}], "edges": [],
        },
        env["dates"], env["paths"][0], freq="week",
        candidate_hash=env["captures"][0]["candidate_hash"], data_digest="d" * 64,
    )
    with pytest.raises(connector.TrialAuditConnectorError, match="does not match study frequency"):
        connector.capture_trial_return(
            ledger=env["ledger"], trial_id=env["trial_ids"][0],
            frequency="month", execution=execution,
        )

    # On-disk mutation cannot retain the old opaque id.
    path = env["store"]._path(env["context"]["context_id"])
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["selected_returns"][0] += 0.1
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(connector.TrialAuditConnectorError, match="digest verification"):
        env["store"].resolve(env["context"]["context_id"])


def test_executor_runtime_snapshot_is_visible_for_manual_incomplete_audit():
    dates = pd.date_range("2024-01-31", periods=24, freq="ME").strftime("%Y-%m-%d")
    body = wapi.OverfitAuditIn(return_series=[
        [day, 0.01 + (index % 3) * 0.001] for index, day in enumerate(dates)
    ])
    report = json.loads(wapi._overfit_audit(
        body, _runtime_data_snapshot=_data_snapshot("manual"),
    ).body)

    assert report["status"] == "INCOMPLETE"
    assert report["passes_gate"] is False
    assert report["evidence_verified"] is False
    assert report["data_snapshot_authority"] == "runtime_panel_content"
    assert report["data_snapshot_strict_pit"] is False
    assert report["data_snapshot_digest"] == _data_snapshot("manual")["digest"]
    assert report["evidence"]["data_snapshot_strict_pit"] is False


def test_public_endpoint_context_and_copied_returns_cannot_self_attest(tmp_path):
    env = _trusted_family(tmp_path)
    context = env["context"]
    live = [[d, float(v)] for d, v in zip(env["dates"], env["paths"][0])]
    app = FastAPI()
    app.include_router(wapi.build_workflow_router(trial_audit_store=env["store"]))
    client = TestClient(app)

    report = client.post("/workflow/overfit-audit", json={
        "audit_context_id": context["context_id"], "return_series": live,
        "n_blocks": 8,
        # Unknown HTTP fields must be ignored; runtime authority is available
        # only through the in-process executor keyword channel.
        "_runtime_candidate_hash": env["candidate_hashes"][0],
        "_runtime_data_snapshot": _data_snapshot(),
    }).json()
    assert "_runtime_candidate_hash" not in wapi.OverfitAuditIn.model_fields
    assert "_runtime_data_snapshot" not in wapi.OverfitAuditIn.model_fields
    assert report["statistical_status"] == "PASS"
    assert report["status"] == "INCOMPLETE"
    assert report["passes_gate"] is False
    assert report["evidence_verified"] is False
    assert report["evidence_authority"] == "server_trial_ledger_unbound"
    assert report["selected_trial_id"] == env["trial_ids"][0]
    assert report["selection_binding"]["verified"] is False
    assert report["selection_binding"]["candidate_verified"] is False
    assert report["selection_binding"]["data_snapshot_verified"] is False
    assert any("服务端整图执行" in reason for reason in report["reasons"])
    assert any("当前回测 data_snapshot" in reason for reason in report["reasons"])
    assert report["selection_binding"]["selected_column_index"] \
        == context["selected_column_index"]
    assert report["policy_authority"] == "server_governor_policy"
    assert report["audit_policy_version"] == "overfit-audit-policy-v2"
    assert report["audit_policy_digest"] == connector.SERVER_OVERFIT_AUDIT_POLICY_DIGEST
    assert report["thresholds"] == {
        "confidence": 0.95, "benchmark_sharpe": 0.0, "min_observations": 16,
        "dsr_min": 0.95, "psr_min": 0.95, "adjusted_psr_min": 0.95,
        "pbo_max": 0.3, "n_blocks": 8,
    }

    # HTTP may ask exploratory questions, but it cannot loosen the policy of a
    # trusted TrialLedger context used for formal promotion.
    widened = client.post("/workflow/overfit-audit", json={
        "audit_context_id": context["context_id"], "return_series": live,
        "benchmark_sharpe": -5.0, "confidence": 0.51, "n_blocks": 4,
        "min_observations": 10, "dsr_min": 0.0, "psr_min": 0.0,
        "adjusted_psr_min": 0.0, "pbo_max": 1.0,
    }).json()
    assert widened["status"] == "INCOMPLETE"
    assert widened["passes_gate"] == report["passes_gate"]
    assert widened["metrics"] == report["metrics"]
    assert widened["thresholds"] == report["thresholds"]
    assert widened["policy_authority"] == "server_governor_policy"
    assert widened["audit_policy_digest"] == report["audit_policy_digest"]

    diagnostic = client.post("/workflow/overfit-audit", json={
        "return_series": live,
        "trial_returns": context["trial_returns"],
        "trial_dates": context["trial_dates"],
        "selection_score_matrix": context["selection_score_matrix"],
        "selection_dates": context["selection_dates"],
        "eligible_column_indices": context["eligible_column_indices"],
        "selected_column_index": context["selected_column_index"],
        "selection_objective": context["selection_objective"],
        "selection_score_source": context["selection_score_source"],
        "selection_eligibility_scope": context["selection_eligibility_scope"],
        "selection_rule_digest": context["selection_rule_digest"],
        "n_trials": context["n_trials"],
        "family_id": context["family_id"],
        "selected_trial_id": context["selected_trial_id"],
        "benchmark_sharpe": -5.0, "confidence": 0.51, "n_blocks": 4,
        "min_observations": 10, "dsr_min": 0.0, "psr_min": 0.0,
        "adjusted_psr_min": 0.0, "pbo_max": 1.0,
    }).json()
    assert diagnostic["statistical_status"] == "PASS"
    assert diagnostic["status"] == "INCOMPLETE"
    assert diagnostic["passes_gate"] is False
    assert diagnostic["policy_authority"] == "caller_diagnostic"
    assert diagnostic["audit_policy_digest"] is None
    assert diagnostic["thresholds"]["pbo_max"] == 1.0

    wrong_live = deepcopy(live)
    wrong_live[10][1] += 0.002
    refused = client.post("/workflow/overfit-audit", json={
        "audit_context_id": context["context_id"], "return_series": wrong_live,
    }).json()
    assert refused["status"] == "INCOMPLETE"
    assert refused["passes_gate"] is False
    assert refused["evidence_verified"] is False
    assert refused["evidence_authority"] == "server_trial_ledger_unbound"
    assert any("selected_trial_id" in reason for reason in refused["reasons"])


def test_trusted_context_with_one_eligible_candidate_is_incomplete(tmp_path):
    env = _trusted_family(tmp_path, min_rank_ic=0.07)
    context = env["context"]
    assert context["eligible_column_indices"] == [
        context["selected_column_index"]
    ]
    live = [[d, float(v)] for d, v in zip(env["dates"], env["paths"][0])]
    app = FastAPI()
    app.include_router(wapi.build_workflow_router(trial_audit_store=env["store"]))
    report = TestClient(app).post("/workflow/overfit-audit", json={
        "audit_context_id": context["context_id"], "return_series": live,
    }).json()

    assert report["evidence_verified"] is False
    assert report["status"] == "INCOMPLETE"
    assert report["passes_gate"] is False
    assert report["metrics"]["pbo"] is None
    assert any("at least 2 base-gate-eligible" in reason
               for reason in report["reasons"])


def test_workflow_overfit_node_requires_server_candidate_and_snapshot_binding(
    tmp_path, monkeypatch,
):
    env = _trusted_family(tmp_path)
    monkeypatch.setattr(connector, "_DEFAULT_STORE", env["store"])
    current = [[d, float(v)] for d, v in zip(env["dates"], env["paths"][0])]
    postseal_graph = deepcopy(env["graphs"][0])
    postseal_graph["nodes"][-1]["params"]["audit_context_id"] = \
        env["context"]["context_id"]
    runtime_hash = wex._runtime_candidate_hash(postseal_graph, {
        "universe": "csi300_active", "freq": "month",
        "start": None, "end": None,
    })
    assert runtime_hash == env["candidate_hashes"][0]

    audit = wex._exec_overfit({"result": {
        "portfolio": {"return_series": current},
        "backtest": {"rebalance": "month"},
        "data_snapshot": _data_snapshot(),
    }}, {"audit_context_id": env["context"]["context_id"]},
        {"universe": "csi300_active",
         "_runtime_candidate_hash": runtime_hash})["audit"]

    assert audit["status"] == "PASS"
    assert audit["passes_gate"] is True
    assert audit["evidence_verified"] is True
    assert audit["audit_context_id"] == env["context"]["context_id"]
    assert audit["selection_binding"]["candidate_verified"] is True
    assert audit["selection_binding"]["data_snapshot_verified"] is True

    wrong_graph = wex._exec_overfit({"result": {
        "portfolio": {"return_series": current},
        "backtest": {"rebalance": "month"},
        "data_snapshot": _data_snapshot(),
    }}, {"audit_context_id": env["context"]["context_id"]},
        {"universe": "csi300_active",
         "_runtime_candidate_hash": env["candidate_hashes"][1]})["audit"]
    assert wrong_graph["status"] == "INCOMPLETE"
    assert wrong_graph["evidence_verified"] is False
    assert wrong_graph["selection_binding"]["candidate_verified"] is False
    assert any("candidate_hash" in reason for reason in wrong_graph["reasons"])

    wrong_snapshot = wex._exec_overfit({"result": {
        "portfolio": {"return_series": current},
        "backtest": {"rebalance": "month"},
        "data_snapshot": _data_snapshot("different-runtime-panel"),
    }}, {"audit_context_id": env["context"]["context_id"]},
        {"universe": "csi300_active",
         "_runtime_candidate_hash": env["candidate_hashes"][0]})["audit"]
    assert wrong_snapshot["status"] == "INCOMPLETE"
    assert wrong_snapshot["evidence_verified"] is False
    assert wrong_snapshot["selection_binding"]["data_snapshot_verified"] is False
    assert any("data_snapshot" in reason for reason in wrong_snapshot["reasons"])


def test_optimize_adapter_never_promotes_declared_audit_before_postseal_pass(
    tmp_path, monkeypatch,
):
    graph = {
        "nodes": [
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "bt", "type": "backtest", "params": {"rebalance": "month"}},
            {"id": "audit", "type": "overfit", "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["bt", "factor"]},
            {"from": ["bt", "result"], "to": ["audit", "result"]},
        ],
    }
    outcome = {
        "ok": True, "reason": None,
        "metrics": {"rank_ic": 0.08, "sharpe": 2.0, "ann_return": 0.3,
                    "oos_verdict": "robust", "n_dates": 80, "factor": "rank(pb)",
                    "audit_declared": True, "audit_status": "INCOMPLETE",
                    "audit_passes_gate": False},
        "terminal": {"kind": "overfit", "node_id": "audit", "payload": {}},
        "exprs": ["rank(pb)"], "has_ml": False, "node_errors": [], "warnings": [],
    }
    monkeypatch.setattr(rstore, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rstore, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": graph})
    monkeypatch.setattr(rl, "_run_graph_eval", lambda g, p, pr, k, mr: deepcopy(outcome))
    routed = []
    monkeypatch.setattr(rl, "_route_product", lambda *a, **kw: routed.append(a) or {"status": "draft"})
    monkeypatch.setattr(rl, "_save_graph", lambda *a, **kw: {"ok": True, "id": "w1"})
    monkeypatch.setattr(rl, "_write_lesson", lambda *a, **kw: True)

    end = ad.run_research_optimize(
        "rr_audit_guard", "审计后才晋级", 1, 0.02, "csi300_active", "month",
        None, None, progress=lambda **kw: None,
    )

    assert routed == []
    assert end["promoted"] is None
    assert end["audit_postseal"]["status"] == "INCOMPLETE"
    assert end["stop_reason"] != "postseal_audit_pass"
