# -*- coding: utf-8 -*-
"""Trusted TrialLedger -> workflow overfit-audit evidence connector.

The public audit endpoint is deliberately diagnostic when callers submit a raw
``trial_returns`` matrix.  A governance PASS needs a matrix assembled here from
server-owned research artifacts *and* validation trials already revealed by the
TrialLedger.  The resulting context is content addressed and append-only; the
browser carries only its opaque id.

This module does not widen TrialLedger's frozen contract.  It uses the public
``get_trial`` / ``effective_trial_stats`` reads and requires the caller (the
research adapter, never an HTTP body) to supply one captured return artifact for
every raw revealed trial.  Count, family, result digest, data snapshot, split,
code/prompt/model identity, frequency and exact dates are all checked before a
context can be sealed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.governor import verify_family_attestation
from guanlan_v2.workflow.store import _default_root

__all__ = [
    "AUDIT_POLICY_VERSION",
    "SERVER_OVERFIT_AUDIT_POLICY",
    "SERVER_OVERFIT_AUDIT_POLICY_DIGEST",
    "TrialAuditConnectorError",
    "TrialAuditEvidenceStore",
    "selection_evidence_from_execution",
    "capture_trial_return",
    "seal_trial_family_context",
    "default_trial_audit_store",
]


CONTEXT_VERSION = "trial-audit-context-v3"
CAPTURE_VERSION = "trial-return-capture-v3"
AUDIT_POLICY_VERSION = "overfit-audit-policy-v2"
SELECTION_RULE_VERSION = "research-selection-rule-v1"
SELECTION_OBJECTIVE = "mean_rank_ic"
SELECTION_SCORE_SOURCE = "backtest.ic.rank_ic_series"
SELECTION_ELIGIBILITY_SCOPE = "full_validation_base_gate"
_SERVER_OVERFIT_AUDIT_POLICY_ITEMS: tuple[tuple[str, Any], ...] = (
    ("benchmark_sharpe", 0.0),
    ("confidence", 0.95),
    ("n_blocks", 8),
    ("min_observations", 16),
    ("dsr_min", 0.95),
    ("psr_min", 0.95),
    ("adjusted_psr_min", 0.95),
    ("pbo_max", 0.30),
    ("selection_objective", SELECTION_OBJECTIVE),
    ("selection_score_source", SELECTION_SCORE_SOURCE),
    ("selection_eligibility_scope", SELECTION_ELIGIBILITY_SCOPE),
)
# Policy is a process invariant, not a caller-tunable dictionary.  Keep the
# exported name for the API/report contract while making in-place mutation
# impossible.  ``_verify_server_policy`` also recomputes the digest on every
# seal/resolve so rebinding this module attribute cannot silently alter the
# formal promotion gate.
SERVER_OVERFIT_AUDIT_POLICY: Mapping[str, Any] = MappingProxyType(
    dict(_SERVER_OVERFIT_AUDIT_POLICY_ITEMS)
)


def _server_policy_digest() -> str:
    return content_digest({
        "domain": AUDIT_POLICY_VERSION,
        "policy": dict(SERVER_OVERFIT_AUDIT_POLICY),
    })


_FIXED_SERVER_OVERFIT_AUDIT_POLICY_DIGEST = content_digest({
    "domain": AUDIT_POLICY_VERSION,
    "policy": dict(_SERVER_OVERFIT_AUDIT_POLICY_ITEMS),
})
SERVER_OVERFIT_AUDIT_POLICY_DIGEST = _FIXED_SERVER_OVERFIT_AUDIT_POLICY_DIGEST

_CONTEXT_ID = re.compile(r"^tac_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FREQ = {
    "d": "day", "day": "day", "daily": "day",
    "w": "week", "week": "week", "weekly": "week",
    "m": "month", "month": "month", "monthly": "month",
}
_PPY = {"day": 252.0, "week": 52.0, "month": 12.0}
_HMAC_KEY_FILE = ".trial-audit-context-hmac-v1.key"
_HMAC_KEY_BYTES = 32
_CONTEXT_HMAC_DOMAIN = "trial-audit-context-hmac-v1"
_CAPABILITY_HMAC_DOMAIN = "trial-audit-private-write-capability-v1"
_AUTH_FIELDS = frozenset({"hmac_key_id", "hmac_tag"})
_PRIVATE_CONTEXT_ISSUER = object()


class TrialAuditConnectorError(ValueError):
    """Trusted evidence could not be constructed or verified."""


def _verify_server_policy(policy: Any, declared_digest: Any) -> None:
    """Fail closed unless policy bytes and both policy digests still agree."""
    current_digest = _server_policy_digest()
    if not hmac.compare_digest(current_digest, _FIXED_SERVER_OVERFIT_AUDIT_POLICY_DIGEST):
        raise TrialAuditConnectorError("server overfit-audit policy integrity failed")
    if (not isinstance(policy, Mapping)
            or dict(policy) != dict(_SERVER_OVERFIT_AUDIT_POLICY_ITEMS)):
        raise TrialAuditConnectorError("trial audit context must bind server governor policy")
    if not isinstance(declared_digest, str) or not hmac.compare_digest(
        declared_digest, _FIXED_SERVER_OVERFIT_AUDIT_POLICY_DIGEST
    ):
        raise TrialAuditConnectorError("trial audit context policy digest verification failed")


def _json_copy(value: Any, *, what: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TrialAuditConnectorError(f"{what} is not canonical JSON") from exc


def _verified_backtest_snapshot(raw: Any) -> dict[str, Any]:
    """Validate and copy the exact snapshot emitted by the real backtest.

    The ledger's ``data_snapshot_hash`` binds the research scheduling context;
    it is deliberately *not* accepted as proof of the runtime panel content.
    That proof lives on ``backtest_result.data_snapshot`` and carries its own
    content digest, authority and strict-PIT claim.
    """
    if not isinstance(raw, Mapping):
        raise TrialAuditConnectorError("captured backtest lacks data_snapshot evidence")
    snapshot = _json_copy(dict(raw), what="backtest data_snapshot")
    version = snapshot.get("version")
    authority = snapshot.get("authority")
    strict_pit = snapshot.get("strict_pit")
    digest = snapshot.get("digest")
    if not isinstance(version, str) or not version.strip():
        raise TrialAuditConnectorError("backtest data_snapshot version is missing")
    if not isinstance(authority, str) or not authority.strip():
        raise TrialAuditConnectorError("backtest data_snapshot authority is missing")
    if type(strict_pit) is not bool:  # bool only; 0/1 must not masquerade as an attestation
        raise TrialAuditConnectorError("backtest data_snapshot strict_pit must be boolean")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise TrialAuditConnectorError("backtest data_snapshot digest is invalid")
    envelope = {key: value for key, value in snapshot.items() if key != "digest"}
    if envelope.get("domain") != version:
        raise TrialAuditConnectorError("backtest data_snapshot version/domain mismatch")
    # The claims are security-relevant evidence, not unsigned decoration:
    # version, authority and strict_pit are part of the same canonical digest
    # envelope as the panel-content metadata.
    expected = content_digest(envelope)
    if not hmac.compare_digest(digest, expected):
        raise TrialAuditConnectorError("backtest data_snapshot content digest mismatch")
    return snapshot


def _verify_context_snapshot_bindings(data: Mapping[str, Any]) -> None:
    snapshot = _verified_backtest_snapshot(data.get("data_snapshot"))
    bindings = {
        "data_snapshot_digest": snapshot["digest"],
        "data_snapshot_authority": snapshot["authority"],
        "data_snapshot_strict_pit": snapshot["strict_pit"],
    }
    for key, expected in bindings.items():
        if data.get(key) != expected:
            raise TrialAuditConnectorError(
                f"trial audit context {key} does not match data_snapshot"
            )


def _selection_matrix_digest(
    *, dates: Sequence[str], matrix: Sequence[Sequence[float]],
    columns: Sequence[Mapping[str, Any]],
) -> str:
    return content_digest({
        "domain": "trial-selection-score-matrix-v1",
        "objective": SELECTION_OBJECTIVE,
        "source": SELECTION_SCORE_SOURCE,
        "dates": list(dates),
        "matrix": [list(row) for row in matrix],
        "trial_ids": [str(column.get("trial_id") or "") for column in columns],
    })


def _verify_context_selection_bindings(data: Mapping[str, Any]) -> None:
    """Recompute every semantic binding used by conditional RankIC-PBO."""
    if data.get("selection_objective") != SELECTION_OBJECTIVE:
        raise TrialAuditConnectorError("trial audit selection objective is not server-fixed")
    if data.get("selection_score_source") != SELECTION_SCORE_SOURCE:
        raise TrialAuditConnectorError("trial audit selection score source is not server-fixed")
    if data.get("selection_eligibility_scope") != SELECTION_ELIGIBILITY_SCOPE:
        raise TrialAuditConnectorError(
            "trial audit selection eligibility scope is not server-fixed"
        )
    policy = data.get("audit_policy")
    if not isinstance(policy, Mapping) or (
        policy.get("selection_objective") != SELECTION_OBJECTIVE
        or policy.get("selection_score_source") != SELECTION_SCORE_SOURCE
        or policy.get("selection_eligibility_scope") != SELECTION_ELIGIBILITY_SCOPE
    ):
        raise TrialAuditConnectorError("trial audit policy does not bind selection semantics")

    rule = data.get("selection_rule")
    if not isinstance(rule, Mapping) or rule.get("version") != SELECTION_RULE_VERSION:
        raise TrialAuditConnectorError("trial audit selection rule is missing or unsupported")
    expected_rule_semantics = {
        "objective": SELECTION_OBJECTIVE,
        "direction": "max",
        "score_source": SELECTION_SCORE_SOURCE,
        "eligibility_scope": SELECTION_ELIGIBILITY_SCOPE,
        "oos_required": "robust",
        "sharpe_strictly_greater_than": 0.0,
        "tie_break": ["trial_created_at", "trial_id"],
    }
    if any(rule.get(key) != value for key, value in expected_rule_semantics.items()):
        raise TrialAuditConnectorError("trial audit selection rule semantics drifted")
    rule_digest = content_digest({
        "domain": SELECTION_RULE_VERSION,
        "rule": dict(rule),
    })
    if data.get("selection_rule_digest") != rule_digest:
        raise TrialAuditConnectorError("trial audit selection rule digest mismatch")

    dates = data.get("selection_dates")
    matrix = data.get("selection_score_matrix")
    columns = data.get("columns")
    if not isinstance(dates, list) or not dates:
        raise TrialAuditConnectorError("trial audit selection dates are missing")
    if not isinstance(matrix, list) or not matrix:
        raise TrialAuditConnectorError("trial audit selection score matrix is missing")
    if not isinstance(columns, list) or not columns:
        raise TrialAuditConnectorError("trial audit candidate columns are missing")
    n_columns = len(columns)
    if data.get("n_trials") != n_columns:
        raise TrialAuditConnectorError("trial audit candidate count does not match n_trials")
    if len(matrix) != len(dates):
        raise TrialAuditConnectorError(
            "trial audit selection dates/matrix row count mismatch"
        )
    parsed_dates: tuple[str, ...] | None = None
    parsed_columns: list[tuple[float, ...]] = []
    if any(not isinstance(row, list) or len(row) != n_columns for row in matrix):
        raise TrialAuditConnectorError(
            "trial audit selection score matrix is not rectangular"
        )
    for column_index in range(n_columns):
        try:
            rows = [[dates[row_index], matrix[row_index][column_index]]
                    for row_index in range(len(matrix))]
        except (TypeError, IndexError) as exc:
            raise TrialAuditConnectorError(
                "trial audit selection score matrix is not rectangular"
            ) from exc
        column_dates, scores = _dated_rank_ic(rows)
        if parsed_dates is None:
            parsed_dates = column_dates
        elif column_dates != parsed_dates:
            raise TrialAuditConnectorError("trial audit selection date axes drifted")
        parsed_columns.append(scores)
    if list(parsed_dates or ()) != dates:
        raise TrialAuditConnectorError("trial audit selection dates are not canonical")
    expected_matrix_digest = _selection_matrix_digest(
        dates=dates, matrix=matrix, columns=columns,
    )
    if data.get("selection_score_matrix_digest") != expected_matrix_digest:
        raise TrialAuditConnectorError("trial audit selection score matrix digest mismatch")

    eligible = data.get("eligible_column_indices")
    if (not isinstance(eligible, list)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in eligible)
            or eligible != sorted(set(eligible))
            or any(value < 0 or value >= n_columns for value in eligible)):
        raise TrialAuditConnectorError("trial audit eligible column binding is invalid")
    if data.get("eligible_candidate_count") != len(eligible):
        raise TrialAuditConnectorError("trial audit eligible candidate count mismatch")
    try:
        min_rank_ic = _finite_number(rule.get("min_rank_ic"), name="selection min_rank_ic")
    except TrialAuditConnectorError as exc:
        raise TrialAuditConnectorError("trial audit selection rule threshold is invalid") from exc
    recomputed_eligible: list[int] = []
    for index, column in enumerate(columns):
        if not isinstance(column, Mapping) or column.get("column_index") != index:
            raise TrialAuditConnectorError("trial audit column index binding is invalid")
        expected_series_digest = content_digest({
            "domain": "trial-selection-score-series-v1",
            "frequency": data.get("frequency"),
            "objective": SELECTION_OBJECTIVE,
            "source": SELECTION_SCORE_SOURCE,
            "dates": dates,
            "scores": list(parsed_columns[index]),
        })
        if column.get("selection_score_series_digest") != expected_series_digest:
            raise TrialAuditConnectorError("trial audit column RankIC digest mismatch")
        mean_score = float(math.fsum(parsed_columns[index]) / len(parsed_columns[index]))
        if not math.isclose(
            _finite_number(
                column.get("selection_score_mean"), name="column selection_score_mean",
            ),
            mean_score,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise TrialAuditConnectorError("trial audit column RankIC mean mismatch")
        gate = column.get("base_gate_evidence")
        if not isinstance(gate, Mapping):
            raise TrialAuditConnectorError("trial audit column lacks base gate evidence")
        gate_rank_ic = _finite_number(gate.get("rank_ic"), name="column gate rank_ic")
        gate_sharpe = _finite_number(gate.get("sharpe"), name="column gate sharpe")
        gate_oos = str(gate.get("oos_verdict") or "")
        if not math.isclose(
            gate_rank_ic, mean_score, rel_tol=1e-8, abs_tol=1e-10,
        ):
            raise TrialAuditConnectorError("trial audit column gate RankIC mismatch")
        is_eligible = bool(
            gate_rank_ic >= min_rank_ic
            and gate_oos == "robust"
            and gate_sharpe > 0.0
        )
        if column.get("base_gate_eligible") is not is_eligible:
            raise TrialAuditConnectorError("trial audit column eligibility mismatch")
        if is_eligible:
            recomputed_eligible.append(index)
    column_order = [
        (str(column.get("trial_created_at") or ""), str(column.get("trial_id") or ""))
        for column in columns
    ]
    if (any(not created_at or not trial_id for created_at, trial_id in column_order)
            or len({trial_id for _created_at, trial_id in column_order}) != n_columns
            or column_order != sorted(column_order)):
        raise TrialAuditConnectorError(
            "trial audit columns do not follow canonical created_at/trial_id tie order"
        )
    if recomputed_eligible != eligible:
        raise TrialAuditConnectorError("trial audit eligible shortlist digest drifted")
    selected = data.get("selected_column_index")
    winner = data.get("selection_winner_column_index")
    if (isinstance(selected, bool) or not isinstance(selected, int)
            or selected < 0 or selected >= n_columns
            or selected not in eligible
            or winner != selected):
        raise TrialAuditConnectorError("trial audit selected/winner column binding is invalid")
    if str((columns[selected] or {}).get("trial_id") or "") != str(
        data.get("selected_trial_id") or ""
    ):
        raise TrialAuditConnectorError("trial audit selected trial/column binding is invalid")
    selected_scores = list(parsed_columns[selected])
    if selected_scores != list(data.get("selected_selection_scores") or []):
        raise TrialAuditConnectorError("trial audit selected RankIC series binding is invalid")
    expected_selected_digest = content_digest({
        "domain": "trial-selection-score-series-v1",
        "frequency": data.get("frequency"),
        "objective": SELECTION_OBJECTIVE,
        "source": SELECTION_SCORE_SOURCE,
        "dates": dates,
        "scores": selected_scores,
    })
    if data.get("selected_selection_score_series_digest") != expected_selected_digest:
        raise TrialAuditConnectorError("trial audit selected RankIC digest mismatch")
    if eligible:
        means = {
            index: float(math.fsum(parsed_columns[index]) / len(parsed_columns[index]))
            for index in eligible
        }
        best = max(means.values())
        winners = [index for index in eligible if math.isclose(
            means[index], best, rel_tol=1e-12, abs_tol=1e-12,
        )]
        tie_order = sorted(
            winners,
            key=lambda index: (
                str((columns[index] or {}).get("trial_created_at") or ""),
                str((columns[index] or {}).get("trial_id") or ""),
            ),
        )
        if not tie_order or selected != tie_order[0]:
            raise TrialAuditConnectorError(
                "trial audit selected trial is not the canonical RankIC winner"
            )


def _frequency(value: Any) -> str:
    out = _FREQ.get(str(value or "").strip().lower())
    if out is None:
        raise TrialAuditConnectorError(f"unsupported trial frequency: {value!r}")
    return out


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    if not raw:
        raise TrialAuditConnectorError("return date is empty")
    try:
        # Accept the ISO timestamps emitted by the workflow, but bind audit
        # periods to their exchange-calendar date exactly.
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise TrialAuditConnectorError(f"invalid return date: {raw!r}") from exc


def _dated_returns(rows: Any) -> tuple[tuple[str, ...], tuple[float, ...]]:
    if not isinstance(rows, (list, tuple)) or not rows:
        raise TrialAuditConnectorError("return_series must contain [date, value] rows")
    dates: list[str] = []
    values: list[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise TrialAuditConnectorError("every return_series row must be [date, value]")
        d = _date_text(row[0])
        try:
            v = float(row[1])
        except (TypeError, ValueError) as exc:
            raise TrialAuditConnectorError("return values must be finite numbers") from exc
        if not math.isfinite(v):
            raise TrialAuditConnectorError("return values must be finite numbers")
        if dates and d <= dates[-1]:
            raise TrialAuditConnectorError(
                "return dates must be strictly increasing; connector never sorts or de-duplicates"
            )
        dates.append(d)
        values.append(v)
    return tuple(dates), tuple(values)


def _capture_digest(payload: Mapping[str, Any]) -> str:
    projected = {k: v for k, v in payload.items() if k != "capture_digest"}
    return content_digest(projected)


def _context_digest(payload: Mapping[str, Any]) -> str:
    excluded = {"context_id", "evidence_digest"} | set(_AUTH_FIELDS)
    projected = {k: v for k, v in payload.items() if k not in excluded}
    return content_digest(projected)


def _pick_backtest(execution: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    captured = execution.get("_trusted_backtests")
    if not isinstance(captured, Mapping) or not captured:
        raise TrialAuditConnectorError("research execution did not capture a backtest artifact")
    graph = execution.get("_trusted_graph")
    preferred: list[str] = []
    if isinstance(graph, Mapping):
        nodes = {str(n.get("id")): n for n in (graph.get("nodes") or [])
                 if isinstance(n, Mapping) and n.get("id")}
        for edge in graph.get("edges") or []:
            if not isinstance(edge, Mapping):
                continue
            src, dst = edge.get("from"), edge.get("to")
            if not (isinstance(src, list) and len(src) >= 2
                    and isinstance(dst, list) and len(dst) >= 2):
                continue
            if (nodes.get(str(src[0]), {}).get("type") == "backtest"
                    and nodes.get(str(dst[0]), {}).get("type") == "overfit"
                    and src[1] == "result" and dst[1] == "result"):
                preferred.append(str(src[0]))
    preferred = list(dict.fromkeys(preferred))
    if len(preferred) == 1 and preferred[0] in captured:
        node_id = preferred[0]
    elif len(captured) == 1:
        node_id = str(next(iter(captured)))
    else:
        raise TrialAuditConnectorError(
            "research graph has multiple backtests without one unambiguous overfit binding"
        )
    result = captured.get(node_id)
    if not isinstance(result, Mapping):
        raise TrialAuditConnectorError("captured backtest result is malformed")
    return node_id, result


def _finite_number(value: Any, *, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise TrialAuditConnectorError(f"{name} must be a finite number") from exc
    if not math.isfinite(out):
        raise TrialAuditConnectorError(f"{name} must be a finite number")
    return out


def _dated_rank_ic(rows: Any) -> tuple[tuple[str, ...], tuple[float, ...]]:
    if not isinstance(rows, (list, tuple)) or not rows:
        raise TrialAuditConnectorError(
            "captured backtest lacks exact ic.rank_ic_series selection evidence"
        )
    dates: list[str] = []
    values: list[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise TrialAuditConnectorError(
                "every ic.rank_ic_series row must be [date, rank_ic]"
            )
        day = _date_text(row[0])
        value = _finite_number(row[1], name="rank_ic_series value")
        if value < -1.0 or value > 1.0:
            raise TrialAuditConnectorError("rank_ic_series values must be within [-1, 1]")
        if dates and day <= dates[-1]:
            raise TrialAuditConnectorError(
                "rank_ic_series dates must be strictly increasing; connector never sorts"
            )
        dates.append(day)
        values.append(value)
    return tuple(dates), tuple(values)


def selection_evidence_from_execution(execution: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the exact canonical research-selection evidence from one execution.

    The optimizer and the TrialLedger connector both call this function, which
    prevents a scalar headline RankIC from selecting one candidate while the PBO
    matrix ranks another.  The exact per-period series is authoritative; scalar
    metrics are required to agree with its arithmetic mean and with the bound
    backtest payload.
    """
    node_id, result = _pick_backtest(execution)
    ic = result.get("ic") if isinstance(result.get("ic"), Mapping) else {}
    dates, scores = _dated_rank_ic(ic.get("rank_ic_series"))
    score_mean = float(math.fsum(scores) / len(scores))
    backtest_rank_ic = _finite_number(
        ic.get("rank_ic_mean"), name="backtest ic.rank_ic_mean",
    )
    if not math.isclose(
        backtest_rank_ic, score_mean, rel_tol=1e-8, abs_tol=1e-10,
    ):
        raise TrialAuditConnectorError(
            "backtest ic.rank_ic_mean does not match exact rank_ic_series mean"
        )

    execution_metrics = (
        execution.get("metrics") if isinstance(execution.get("metrics"), Mapping) else None
    )
    if execution_metrics is None:
        raise TrialAuditConnectorError("research execution lacks canonical gate metrics")
    gate_rank_ic = _finite_number(
        execution_metrics.get("rank_ic"), name="research gate rank_ic",
    )
    if not math.isclose(gate_rank_ic, score_mean, rel_tol=1e-8, abs_tol=1e-10):
        raise TrialAuditConnectorError(
            "research gate rank_ic does not match exact rank_ic_series mean"
        )

    portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), Mapping) else {}
    backtest_sharpe = _finite_number(
        portfolio.get("sharpe"), name="backtest portfolio.sharpe",
    )
    gate_sharpe = _finite_number(
        execution_metrics.get("sharpe"), name="research gate sharpe",
    )
    if not math.isclose(backtest_sharpe, gate_sharpe, rel_tol=1e-10, abs_tol=1e-12):
        raise TrialAuditConnectorError(
            "research gate sharpe does not match bound backtest portfolio.sharpe"
        )
    oos = result.get("oos") if isinstance(result.get("oos"), Mapping) else {}
    backtest_oos = str(oos.get("verdict") or "")
    gate_oos = str(execution_metrics.get("oos_verdict") or "")
    if not backtest_oos or gate_oos != backtest_oos:
        raise TrialAuditConnectorError(
            "research gate oos_verdict does not match bound backtest oos.verdict"
        )
    return {
        "backtest_node_id": node_id,
        "selection_score_dates": list(dates),
        "selection_scores": list(scores),
        "selection_score_mean": score_mean,
        "base_gate_evidence": {
            "rank_ic": gate_rank_ic,
            "sharpe": gate_sharpe,
            "oos_verdict": gate_oos,
        },
    }


def capture_trial_return(
    *, ledger: Any, trial_id: str, frequency: str, execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one server-captured backtest series to one revealed raw ledger trial."""
    trial = ledger.get_trial(str(trial_id))
    if trial.stage != "validation" or trial.status != "revealed":
        raise TrialAuditConnectorError(f"trial {trial_id!r} is not a revealed validation trial")
    if trial.reused_from_trial_id is not None:
        raise TrialAuditConnectorError(
            f"trial {trial_id!r} is a cached reuse, not a distinct raw trial column"
        )
    if not trial.result_digest or not trial.validation_result_artifact_id:
        raise TrialAuditConnectorError(f"trial {trial_id!r} lacks ledger result binding")
    if execution.get("_trusted_candidate_hash") != trial.candidate_hash:
        raise TrialAuditConnectorError(
            f"trial {trial_id!r} execution candidate hash does not match TrialLedger"
        )
    # This value is the ledger/research DataContext identity, not the actual
    # materialized panel content.  Preserve it under an explicit name and take
    # runtime data authority solely from the selected backtest result below.
    if execution.get("_trusted_data_snapshot_hash") != trial.data_snapshot_hash:
        raise TrialAuditConnectorError(
            f"trial {trial_id!r} execution ledger data context does not match TrialLedger"
        )

    selection = selection_evidence_from_execution(execution)
    node_id, result = _pick_backtest(execution)
    if node_id != selection["backtest_node_id"]:
        raise TrialAuditConnectorError("selection evidence backtest binding drifted")
    backtest_snapshot = _verified_backtest_snapshot(result.get("data_snapshot"))
    portfolio = result.get("portfolio")
    if not isinstance(portfolio, Mapping):
        raise TrialAuditConnectorError("captured backtest lacks portfolio evidence")
    dates, values = _dated_returns(portfolio.get("return_series"))
    backtest = result.get("backtest") if isinstance(result.get("backtest"), Mapping) else {}
    declared_freq = _frequency(backtest.get("rebalance") or backtest.get("rebalance_freq"))
    expected_freq = _frequency(frequency)
    if declared_freq != expected_freq:
        raise TrialAuditConnectorError(
            f"backtest frequency {declared_freq!r} does not match study frequency {expected_freq!r}"
        )

    capture: dict[str, Any] = {
        "version": CAPTURE_VERSION,
        "trial_id": trial.trial_id,
        "family_id": trial.family_id,
        "candidate_hash": trial.candidate_hash,
        "ledger_result_digest": trial.result_digest,
        "validation_result_artifact_id": trial.validation_result_artifact_id,
        "ledger_data_context_hash": trial.data_snapshot_hash,
        "backtest_data_snapshot": backtest_snapshot,
        "backtest_data_snapshot_digest": backtest_snapshot["digest"],
        "backtest_data_snapshot_authority": backtest_snapshot["authority"],
        "backtest_data_snapshot_strict_pit": backtest_snapshot["strict_pit"],
        "split_spec_hash": trial.split_spec_hash,
        "code_prompt_model_hash": trial.code_prompt_model_hash,
        "trial_created_at": trial.created_at.isoformat(),
        "frequency": expected_freq,
        "backtest_node_id": node_id,
        "dates": list(dates),
        "returns": list(values),
        "return_series_digest": content_digest({
            "domain": "trial-return-series-v1",
            "frequency": expected_freq,
            "dates": list(dates),
            "returns": list(values),
        }),
        "selection_objective": SELECTION_OBJECTIVE,
        "selection_score_source": SELECTION_SCORE_SOURCE,
        "selection_score_dates": selection["selection_score_dates"],
        "selection_scores": selection["selection_scores"],
        "selection_score_mean": selection["selection_score_mean"],
        "base_gate_evidence": selection["base_gate_evidence"],
    }
    capture["selection_score_series_digest"] = content_digest({
        "domain": "trial-selection-score-series-v1",
        "frequency": expected_freq,
        "objective": SELECTION_OBJECTIVE,
        "source": SELECTION_SCORE_SOURCE,
        "dates": capture["selection_score_dates"],
        "scores": capture["selection_scores"],
    })
    capture["capture_digest"] = _capture_digest(capture)
    return capture


@dataclass(frozen=True, slots=True)
class _ContextWriteCapability:
    """Private, payload-bound authority consumed by exactly one store write."""

    store_key_id: str
    payload_digest: str
    nonce: str
    signature: str


def _load_or_create_hmac_key(root: Path) -> bytes:
    """Load the service-persistent signing key, creating it atomically once."""
    path = root / _HMAC_KEY_FILE
    candidate = secrets.token_bytes(_HMAC_KEY_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
    try:
        key = path.read_bytes()
    except OSError as exc:
        raise TrialAuditConnectorError("trial audit HMAC key is unreadable") from exc
    if len(key) != _HMAC_KEY_BYTES:
        raise TrialAuditConnectorError("trial audit HMAC key has invalid length")
    return key


class TrialAuditEvidenceStore:
    """Append-only contexts authenticated by a persistent server HMAC key.

    ``put`` is intentionally not a signing path.  The sole writer is
    ``seal_trial_family_context``, which obtains a private, one-shot,
    payload-bound capability and passes it to ``_put``.  Context ids remain
    content addressed, while HMAC prevents an attacker who can rewrite JSON
    from recomputing a new digest/id and blessing the altered evidence.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else (_default_root() / "_trial_audit")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.__hmac_key = _load_or_create_hmac_key(self.root)
        self.__hmac_key_id = hashlib.sha256(
            b"trial-audit-context-key-id-v1\0" + self.__hmac_key
        ).hexdigest()
        self.__issued_nonces: set[str] = set()

    @staticmethod
    def valid_id(context_id: str) -> bool:
        return bool(_CONTEXT_ID.fullmatch(str(context_id or "")))

    def _path(self, context_id: str) -> Path:
        if not self.valid_id(context_id):
            raise TrialAuditConnectorError("invalid trial audit context id")
        return self.root / f"{context_id}.json"

    @staticmethod
    def _public(data: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in data.items() if key not in _AUTH_FIELDS}

    @staticmethod
    def _prepare(payload: Mapping[str, Any]) -> dict[str, Any]:
        data = _json_copy(dict(payload), what="trial audit context")
        if any(key in data for key in _AUTH_FIELDS):
            raise TrialAuditConnectorError("trial audit auth envelope is server-owned")
        if data.get("version") != CONTEXT_VERSION:
            raise TrialAuditConnectorError("unsupported trial audit context version")
        _verify_server_policy(data.get("audit_policy"), data.get("audit_policy_digest"))
        _verify_context_snapshot_bindings(data)
        _verify_context_selection_bindings(data)
        digest = _context_digest(data)
        declared = data.get("evidence_digest")
        if declared is not None and (
            not isinstance(declared, str) or not hmac.compare_digest(declared, digest)
        ):
            raise TrialAuditConnectorError("trial audit context evidence_digest mismatch")
        context_id = f"tac_{digest}"
        declared_id = data.get("context_id")
        if declared_id not in (None, context_id):
            raise TrialAuditConnectorError("trial audit context id does not bind its digest")
        data["evidence_digest"] = digest
        data["context_id"] = context_id
        return data

    def __sign(self, domain: str, payload: Mapping[str, Any]) -> str:
        message = json.dumps(
            {"domain": domain, **dict(payload)}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return hmac.new(self.__hmac_key, message, hashlib.sha256).hexdigest()

    def _issue_context_capability(
        self, payload: Mapping[str, Any], *, _issuer: object,
    ) -> _ContextWriteCapability:
        if _issuer is not _PRIVATE_CONTEXT_ISSUER:
            raise TrialAuditConnectorError("trial audit write capability issuer is not authorized")
        data = self._prepare(payload)
        digest = str(data["evidence_digest"])
        nonce = secrets.token_hex(32)
        signature = self.__sign(_CAPABILITY_HMAC_DOMAIN, {
            "store_key_id": self.__hmac_key_id,
            "payload_digest": digest,
            "nonce": nonce,
        })
        with self._lock:
            self.__issued_nonces.add(nonce)
        return _ContextWriteCapability(
            store_key_id=self.__hmac_key_id,
            payload_digest=digest,
            nonce=nonce,
            signature=signature,
        )

    def put(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Reject legacy/public unsigned writes; retained for a loud failure."""
        del payload
        raise TrialAuditConnectorError(
            "trial audit contexts require a private server write capability"
        )

    def _put(
        self, payload: Mapping[str, Any], capability: _ContextWriteCapability | None,
    ) -> dict[str, Any]:
        data = self._prepare(payload)
        if not isinstance(capability, _ContextWriteCapability):
            raise TrialAuditConnectorError("trial audit private write capability is required")
        expected_capability = self.__sign(_CAPABILITY_HMAC_DOMAIN, {
            "store_key_id": capability.store_key_id,
            "payload_digest": capability.payload_digest,
            "nonce": capability.nonce,
        })
        with self._lock:
            valid_nonce = capability.nonce in self.__issued_nonces
            # One shot regardless of success; a failed/tampered attempt must be
            # re-sealed from verified captures, never retried with old authority.
            self.__issued_nonces.discard(capability.nonce)
        if (not valid_nonce
                or capability.store_key_id != self.__hmac_key_id
                or capability.payload_digest != data["evidence_digest"]
                or not hmac.compare_digest(capability.signature, expected_capability)):
            raise TrialAuditConnectorError("trial audit private write capability is invalid")

        data["hmac_key_id"] = self.__hmac_key_id
        data["hmac_tag"] = self.__sign(_CONTEXT_HMAC_DOMAIN, {
            "context_id": data["context_id"],
            "evidence_digest": data["evidence_digest"],
            "hmac_key_id": self.__hmac_key_id,
        })
        path = self._path(str(data["context_id"]))
        wire = json.dumps(data, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        with self._lock:
            if path.exists():
                existing = path.read_text(encoding="utf-8")
                if existing != wire:
                    raise TrialAuditConnectorError(
                        "content-addressed audit context already exists with different bytes"
                    )
            else:
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    existing = path.read_text(encoding="utf-8")
                    if existing != wire:
                        raise TrialAuditConnectorError(
                            "concurrent audit context write produced different bytes"
                        )
                else:
                    # O_EXCL is the append-only boundary: never replace an
                    # evidence object.  A crash may leave a corrupt object,
                    # which resolve() refuses rather than repairing silently.
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(wire)
                        stream.flush()
                        os.fsync(stream.fileno())
        return self._public(data)

    def resolve(self, context_id: str) -> dict[str, Any]:
        path = self._path(context_id)
        with self._lock:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise TrialAuditConnectorError("unknown trial audit context id") from exc
            except Exception as exc:  # noqa: BLE001 - corrupt evidence fails closed
                raise TrialAuditConnectorError("trial audit context is unreadable") from exc
        if not isinstance(data, dict):
            raise TrialAuditConnectorError("trial audit context is malformed")
        if data.get("version") != CONTEXT_VERSION:
            raise TrialAuditConnectorError("unsupported trial audit context version")
        _verify_server_policy(data.get("audit_policy"), data.get("audit_policy_digest"))
        _verify_context_snapshot_bindings(data)
        _verify_context_selection_bindings(data)
        digest = _context_digest(data)
        declared_digest = data.get("evidence_digest")
        if (not isinstance(declared_digest, str)
                or not hmac.compare_digest(declared_digest, digest)
                or data.get("context_id") != f"tac_{digest}"
                or data.get("context_id") != context_id):
            raise TrialAuditConnectorError("trial audit context digest verification failed")
        key_id = data.get("hmac_key_id")
        tag = data.get("hmac_tag")
        if not isinstance(key_id, str) or not hmac.compare_digest(
            key_id, self.__hmac_key_id
        ):
            raise TrialAuditConnectorError("trial audit context HMAC key mismatch")
        expected_tag = self.__sign(_CONTEXT_HMAC_DOMAIN, {
            "context_id": data["context_id"],
            "evidence_digest": digest,
            "hmac_key_id": key_id,
        })
        if not isinstance(tag, str) or not hmac.compare_digest(tag, expected_tag):
            raise TrialAuditConnectorError("trial audit context HMAC verification failed")
        return self._public(data)


_DEFAULT_STORE: TrialAuditEvidenceStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def default_trial_audit_store() -> TrialAuditEvidenceStore:
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = TrialAuditEvidenceStore()
        return _DEFAULT_STORE


def seal_trial_family_context(
    *, ledger: Any, study: Any, captures: Sequence[Mapping[str, Any]],
    selected_trial_id: str, min_rank_ic: float,
    store: Optional[TrialAuditEvidenceStore] = None,
) -> dict[str, Any]:
    """Verify a complete raw trial family and persist an aligned T x N context."""
    min_rank_ic_value = _finite_number(min_rank_ic, name="min_rank_ic")
    expected_label_digest = content_digest({
        "domain": "research-label-v1",
        "min_rank_ic": min_rank_ic_value,
        "oos_required": "robust",
        "sharpe_required": True,
    })
    if getattr(study, "label_digest", None) != expected_label_digest:
        raise TrialAuditConnectorError(
            "min_rank_ic does not bind the TrialLedger StudySpec label policy"
        )
    family = ledger.resolve_family(study)
    verify_family_attestation(family)
    stats = ledger.effective_trial_stats(study)
    if stats.get("family_id") != family.family_id:
        raise TrialAuditConnectorError("TrialLedger stats do not bind the resolved family")
    raw_count = int(stats.get("raw_trial_count") or 0)
    if raw_count < 1:
        raise TrialAuditConnectorError("TrialLedger has no revealed raw validation trials")
    from guanlan_v2.workflow.overfit_audit import (
        MAX_CANDIDATES, MAX_MATRIX_CELLS, MAX_OBSERVATIONS,
    )
    if raw_count > MAX_CANDIDATES:
        raise TrialAuditConnectorError(
            f"TrialLedger raw_trial_count={raw_count} exceeds audit limit {MAX_CANDIDATES}"
        )
    if len(captures) != raw_count:
        raise TrialAuditConnectorError(
            f"captured {len(captures)} trial columns but TrialLedger raw_trial_count={raw_count}"
        )

    checked: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    expected_freq = _frequency(family.frequency)
    for raw in captures:
        capture = dict(raw)
        trial_id = str(capture.get("trial_id") or "")
        if not trial_id or trial_id in seen_ids:
            raise TrialAuditConnectorError("trial captures must have unique non-empty trial ids")
        seen_ids.add(trial_id)
        if capture.get("version") != CAPTURE_VERSION:
            raise TrialAuditConnectorError(f"trial {trial_id!r} has unsupported capture version")
        if capture.get("capture_digest") != _capture_digest(capture):
            raise TrialAuditConnectorError(f"trial {trial_id!r} capture digest mismatch")
        trial = ledger.get_trial(trial_id)
        if (trial.family_id != family.family_id or capture.get("family_id") != family.family_id):
            raise TrialAuditConnectorError(f"trial {trial_id!r} belongs to another family")
        if trial.stage != "validation" or trial.status != "revealed" \
                or trial.reused_from_trial_id is not None:
            raise TrialAuditConnectorError(f"trial {trial_id!r} is not a raw revealed trial")
        bindings = {
            "candidate_hash": trial.candidate_hash,
            "ledger_result_digest": trial.result_digest,
            "validation_result_artifact_id": trial.validation_result_artifact_id,
            "ledger_data_context_hash": trial.data_snapshot_hash,
            "split_spec_hash": trial.split_spec_hash,
            "code_prompt_model_hash": trial.code_prompt_model_hash,
            "trial_created_at": trial.created_at.isoformat(),
        }
        for key, expected in bindings.items():
            if capture.get(key) != expected:
                raise TrialAuditConnectorError(
                    f"trial {trial_id!r} capture {key} does not match TrialLedger"
                )
        if _frequency(capture.get("frequency")) != expected_freq:
            raise TrialAuditConnectorError(f"trial {trial_id!r} frequency mismatches family")
        snapshot = _verified_backtest_snapshot(capture.get("backtest_data_snapshot"))
        snapshot_bindings = {
            "backtest_data_snapshot_digest": snapshot["digest"],
            "backtest_data_snapshot_authority": snapshot["authority"],
            "backtest_data_snapshot_strict_pit": snapshot["strict_pit"],
        }
        for key, expected in snapshot_bindings.items():
            if capture.get(key) != expected:
                raise TrialAuditConnectorError(
                    f"trial {trial_id!r} capture {key} does not match backtest data_snapshot"
                )
        capture["backtest_data_snapshot"] = snapshot
        capture_dates = capture.get("dates")
        capture_returns = capture.get("returns")
        if (not isinstance(capture_dates, list) or not isinstance(capture_returns, list)
                or len(capture_dates) != len(capture_returns)):
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} dates/returns lengths do not match"
            )
        dates, values = _dated_returns(list(zip(capture_dates, capture_returns)))
        expected_series_digest = content_digest({
            "domain": "trial-return-series-v1", "frequency": expected_freq,
            "dates": list(dates), "returns": list(values),
        })
        if capture.get("return_series_digest") != expected_series_digest:
            raise TrialAuditConnectorError(f"trial {trial_id!r} return series digest mismatch")
        capture["dates"], capture["returns"] = list(dates), list(values)

        if capture.get("selection_objective") != SELECTION_OBJECTIVE:
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} selection objective is not server-fixed"
            )
        if capture.get("selection_score_source") != SELECTION_SCORE_SOURCE:
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} selection score source is not server-fixed"
            )
        selection_dates_raw = capture.get("selection_score_dates")
        selection_scores_raw = capture.get("selection_scores")
        if (not isinstance(selection_dates_raw, list)
                or not isinstance(selection_scores_raw, list)
                or len(selection_dates_raw) != len(selection_scores_raw)):
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} RankIC dates/scores lengths do not match"
            )
        selection_dates, selection_scores = _dated_rank_ic(
            list(zip(selection_dates_raw, selection_scores_raw))
        )
        expected_selection_digest = content_digest({
            "domain": "trial-selection-score-series-v1",
            "frequency": expected_freq,
            "objective": SELECTION_OBJECTIVE,
            "source": SELECTION_SCORE_SOURCE,
            "dates": list(selection_dates),
            "scores": list(selection_scores),
        })
        if capture.get("selection_score_series_digest") != expected_selection_digest:
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} RankIC selection series digest mismatch"
            )
        score_mean = float(math.fsum(selection_scores) / len(selection_scores))
        if not math.isclose(
            _finite_number(
                capture.get("selection_score_mean"),
                name=f"trial {trial_id!r} selection_score_mean",
            ),
            score_mean,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} selection score mean mismatch"
            )
        gate = capture.get("base_gate_evidence")
        if not isinstance(gate, Mapping):
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} lacks base joint-gate evidence"
            )
        gate_rank_ic = _finite_number(
            gate.get("rank_ic"), name=f"trial {trial_id!r} gate rank_ic",
        )
        gate_sharpe = _finite_number(
            gate.get("sharpe"), name=f"trial {trial_id!r} gate sharpe",
        )
        gate_oos = str(gate.get("oos_verdict") or "")
        if not math.isclose(
            gate_rank_ic, score_mean, rel_tol=1e-8, abs_tol=1e-10,
        ):
            raise TrialAuditConnectorError(
                f"trial {trial_id!r} gate RankIC differs from canonical series mean"
            )
        capture["selection_score_dates"] = list(selection_dates)
        capture["selection_scores"] = list(selection_scores)
        capture["selection_score_mean"] = score_mean
        capture["base_gate_evidence"] = {
            "rank_ic": gate_rank_ic,
            "sharpe": gate_sharpe,
            "oos_verdict": gate_oos,
        }
        capture["base_gate_eligible"] = bool(
            gate_rank_ic >= min_rank_ic_value
            and gate_oos == "robust"
            and gate_sharpe > 0.0
        )
        checked.append(capture)

    # A T x N matrix has one immutable ledger context, one *actual backtest*
    # content snapshot, one split/code identity and one exact period index.
    # Never intersect, fill, sort, resample or mix actual runtime vintages.
    for key in ("ledger_data_context_hash", "split_spec_hash", "code_prompt_model_hash"):
        values = {str(item[key]) for item in checked}
        if len(values) != 1:
            raise TrialAuditConnectorError(f"trial family mixes different {key} values")
    snapshot_identities = {
        (
            item["backtest_data_snapshot_digest"],
            item["backtest_data_snapshot_authority"],
            item["backtest_data_snapshot_strict_pit"],
            item["backtest_data_snapshot"].get("version"),
        )
        for item in checked
    }
    if len(snapshot_identities) != 1:
        raise TrialAuditConnectorError(
            "trial family mixes different actual backtest data snapshots"
        )
    date_axes = {tuple(item["dates"]) for item in checked}
    if len(date_axes) != 1:
        raise TrialAuditConnectorError("trial family return dates are not exactly aligned")
    selection_date_axes = {tuple(item["selection_score_dates"]) for item in checked}
    if len(selection_date_axes) != 1:
        raise TrialAuditConnectorError(
            "trial family RankIC selection dates are not exactly aligned"
        )

    # Canonical column order is independent of callback/round iteration order
    # and is also the declared selector's first-on-tie order.  CSCV's in-sample
    # argmax therefore cannot silently replace (created_at, trial_id) with an
    # unrelated lexicographic trial-id order when block means tie.
    checked.sort(key=lambda item: (
        str(item["trial_created_at"]), str(item["trial_id"]),
    ))
    trial_ids = [str(item["trial_id"]) for item in checked]
    selected_id = str(selected_trial_id or "")
    if selected_id not in trial_ids:
        raise TrialAuditConnectorError("selected_trial_id is not a raw trial matrix column")
    selected_column = trial_ids.index(selected_id)
    eligible_columns = [
        index for index, item in enumerate(checked)
        if item["base_gate_eligible"]
    ]
    if selected_column not in eligible_columns:
        raise TrialAuditConnectorError(
            "selected_trial_id did not pass the base research joint gate"
        )
    score_means = {
        index: float(checked[index]["selection_score_mean"])
        for index in eligible_columns
    }
    best_score = max(score_means.values())
    tied_winners = [
        index for index in eligible_columns
        if math.isclose(
            score_means[index], best_score, rel_tol=1e-12, abs_tol=1e-12,
        )
    ]
    tied_winners.sort(key=lambda index: (
        str(checked[index]["trial_created_at"]), str(checked[index]["trial_id"]),
    ))
    winner_column = tied_winners[0]
    if selected_column != winner_column:
        raise TrialAuditConnectorError(
            "selected_trial_id is not the canonical mean-RankIC winner among "
            "base-gate-eligible candidates"
        )
    dates = list(checked[0]["dates"])
    if len(dates) > MAX_OBSERVATIONS or len(dates) * raw_count > MAX_MATRIX_CELLS:
        raise TrialAuditConnectorError("trial family matrix exceeds overfit-audit size limits")
    matrix = [
        [float(checked[col]["returns"][row]) for col in range(len(checked))]
        for row in range(len(dates))
    ]
    selected_returns = [float(row[selected_column]) for row in matrix]
    selection_dates = list(checked[0]["selection_score_dates"])
    if (len(selection_dates) > MAX_OBSERVATIONS
            or len(selection_dates) * raw_count > MAX_MATRIX_CELLS):
        raise TrialAuditConnectorError(
            "trial family RankIC selection matrix exceeds overfit-audit size limits"
        )
    selection_matrix = [
        [float(checked[col]["selection_scores"][row]) for col in range(len(checked))]
        for row in range(len(selection_dates))
    ]
    selected_selection_scores = [
        float(row[selected_column]) for row in selection_matrix
    ]
    columns = [{
        "column_index": i,
        "trial_id": item["trial_id"],
        "candidate_hash": item["candidate_hash"],
        "capture_digest": item["capture_digest"],
        "return_series_digest": item["return_series_digest"],
        "selection_score_series_digest": item["selection_score_series_digest"],
        "selection_score_mean": item["selection_score_mean"],
        "base_gate_eligible": item["base_gate_eligible"],
        "base_gate_evidence": item["base_gate_evidence"],
        "trial_created_at": item["trial_created_at"],
        "ledger_result_digest": item["ledger_result_digest"],
    } for i, item in enumerate(checked)]

    selection_rule = {
        "version": SELECTION_RULE_VERSION,
        "objective": SELECTION_OBJECTIVE,
        "direction": "max",
        "score_source": SELECTION_SCORE_SOURCE,
        "eligibility_scope": SELECTION_ELIGIBILITY_SCOPE,
        "min_rank_ic": min_rank_ic_value,
        "oos_required": "robust",
        "sharpe_strictly_greater_than": 0.0,
        "tie_break": ["trial_created_at", "trial_id"],
    }
    selection_rule_digest = content_digest({
        "domain": SELECTION_RULE_VERSION,
        "rule": selection_rule,
    })
    selection_matrix_digest = _selection_matrix_digest(
        dates=selection_dates, matrix=selection_matrix, columns=columns,
    )

    payload: dict[str, Any] = {
        "version": CONTEXT_VERSION,
        "evidence_authority": "server_trial_ledger",
        "audit_policy_version": AUDIT_POLICY_VERSION,
        "audit_policy": dict(SERVER_OVERFIT_AUDIT_POLICY),
        "audit_policy_digest": SERVER_OVERFIT_AUDIT_POLICY_DIGEST,
        "selection_objective": SELECTION_OBJECTIVE,
        "selection_score_source": SELECTION_SCORE_SOURCE,
        "selection_eligibility_scope": SELECTION_ELIGIBILITY_SCOPE,
        "selection_rule": selection_rule,
        "selection_rule_digest": selection_rule_digest,
        "family_id": family.family_id,
        "family_identity_digest": family.identity_digest,
        "governor_attestation": family.governor_attestation,
        "frequency": expected_freq,
        "periods_per_year": _PPY[expected_freq],
        # Keep the research/ledger DataContext separate from the actual panel
        # content attested by the backtest result.
        "ledger_data_context_hash": checked[0]["ledger_data_context_hash"],
        "data_snapshot": checked[0]["backtest_data_snapshot"],
        "data_snapshot_digest": checked[0]["backtest_data_snapshot_digest"],
        "data_snapshot_authority": checked[0]["backtest_data_snapshot_authority"],
        "data_snapshot_strict_pit": checked[0]["backtest_data_snapshot_strict_pit"],
        "split_spec_hash": checked[0]["split_spec_hash"],
        "code_prompt_model_hash": checked[0]["code_prompt_model_hash"],
        "n_trials": raw_count,
        "trial_dates": dates,
        "trial_returns": matrix,
        "selection_dates": selection_dates,
        "selection_score_matrix": selection_matrix,
        "selection_score_matrix_digest": selection_matrix_digest,
        "columns": columns,
        "eligible_column_indices": eligible_columns,
        "eligible_candidate_count": len(eligible_columns),
        "selected_trial_id": selected_id,
        "selected_column_index": selected_column,
        "selection_winner_column_index": winner_column,
        "selected_returns": selected_returns,
        "selected_return_series_digest": checked[selected_column]["return_series_digest"],
        "selected_selection_scores": selected_selection_scores,
        "selected_selection_score_series_digest": checked[selected_column][
            "selection_score_series_digest"
        ],
    }
    target = store or default_trial_audit_store()
    capability = target._issue_context_capability(
        payload, _issuer=_PRIVATE_CONTEXT_ISSUER,
    )
    return target._put(payload, capability)
