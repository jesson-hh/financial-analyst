# -*- coding: utf-8 -*-
"""Phase 4 Task 4 — governor pure statistics + study-family identity.

Written test-first: with ``guanlan_v2/orchestration/governor.py`` absent the module
fails to import (RED). It then locks, GREEN, the reviewed invariants (brief §Required
invariants 1-7). The D6 revision-throttle (Task 4b) is a separate later task and is
deliberately not covered here.

1. delegate equivalence — ``workflow.api._cscv_pbo`` / ``_oos_verdict`` return byte-
   identical outputs to ``governor.cscv_pbo`` / ``oos_verdict`` (the originals became
   one-line delegates), so the whole workflow suite keeps exact behavior;
2. ``derive_study_family`` is display-text-invariant + deterministic;
   ``verify_family_attestation`` rejects a tampered ``family_id`` / ``identity_digest``
   / ``governor_attestation``;
3. renaming a study (new display text, same digests) yields the same ``family_id``;
4. changing any of the five identity handles yields a new family;
5. ``govern`` with 3 return samples and no perf matrix is honest
   (``status="unavailable"``, both stats ``None``, non-empty reasons);
6. ``effective_n_trials`` never exceeds its raw bound and equals ``max(2, raw)``
   without correlation evidence;
7. ``governor.py`` performs no network/file/store I/O (module-level import surface
   limited to stdlib, numpy, Phase 1/4 contracts).

Run from repo root: ``pytest tests/orchestration/test_governor.py -v``
"""
from __future__ import annotations

import ast
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from guanlan_v2.orchestration.governor import (
    FAMILY_ATTESTATION_DOMAIN,
    GOVERNOR_VERSION,
    REVISION_THROTTLE_MIN_MATURED,
    STUDY_FAMILY_DOMAIN,
    FamilyAttestationError,
    Governor,
    complexity_score,
    cscv_pbo,
    derive_study_family,
    dsr,
    effective_n_trials,
    oos_verdict,
    revision_throttle_check,
    verify_family_attestation,
)
from guanlan_v2.orchestration.trial import (
    GovernanceReport,
    OptimizeCandidate,
    SplitSpec,
    StudyFamily,
    StudySpec,
)

UTC = timezone.utc

DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64
DE = "e" * 64
DF = "f" * 64
DZ = "1" * 64


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
def _study(**over) -> StudySpec:
    base = dict(
        objective="maximize risk-adjusted 5d return",
        objective_digest=DA,
        label_definition="forward 5-day excess return",
        label_digest=DB,
        universe_digest=DC,
        frequency="1d",
        split_policy_digest=DD,
    )
    base.update(over)
    return StudySpec(**base)


def _candidate(**over) -> OptimizeCandidate:
    base = dict(
        candidate_kind="workflow_graph",
        graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}}], "edges": []},
        params={},
    )
    base.update(over)
    return OptimizeCandidate.build(**base)


def _split(**over) -> SplitSpec:
    base = dict(scheme="cpcv", n_groups=6, k=2, purge=5, embargo=5, label_horizon=5)
    base.update(over)
    return SplitSpec(**base)


# --------------------------------------------------------------------------- #
# Module constants                                                            #
# --------------------------------------------------------------------------- #
def test_frozen_constants():
    assert GOVERNOR_VERSION == "governor-v1"
    assert STUDY_FAMILY_DOMAIN == "study-family-v1"
    assert FAMILY_ATTESTATION_DOMAIN == "study-family-attestation-v1"


# --------------------------------------------------------------------------- #
# oos_verdict — threshold matrix + delegate identity (invariant 1)            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "is_v, oos_v, n_oos, verdict",
    [
        (0.10, 0.08, 10, "robust"),     # decay 0.8 >= 0.6
        (0.10, 0.03, 10, "degraded"),   # 0.3 >= 0.2
        (0.10, 0.01, 10, "overfit"),
        (0.10, 0.08, 5, "insufficient"),  # n_oos < 6
        (0.0, 0.08, 10, "na"),          # IS <= 0
        (float("nan"), 0.08, 10, "na"),  # IS NaN
    ],
)
def test_oos_verdict_matrix(is_v, oos_v, n_oos, verdict):
    # oos_verdict is extracted verbatim from workflow.api._oos_verdict and keeps its
    # 3-tuple (verdict, decay_ratio, label) shape (delegate equivalence outranks the
    # brief's abbreviated ``-> str`` annotation; existing callers unpack the tuple).
    result = oos_verdict(is_v, oos_v, n_oos)
    assert isinstance(result, tuple)
    assert result[0] == verdict


def test_oos_verdict_delegate_identity():
    from guanlan_v2.workflow import api as workflow_api

    for is_v, oos_v, n_oos in [
        (0.10, 0.08, 10),
        (0.10, 0.03, 10),
        (0.10, 0.01, 10),
        (0.10, 0.08, 5),
        (0.0, 0.08, 10),
        (0.20, -0.05, 12),
    ]:
        assert workflow_api._oos_verdict(is_v, oos_v, n_oos) == oos_verdict(is_v, oos_v, n_oos)


# --------------------------------------------------------------------------- #
# cscv_pbo — precondition matrix + delegate identity (invariant 1)            #
# --------------------------------------------------------------------------- #
def test_cscv_pbo_disabled_too_few_candidates():
    res = cscv_pbo(np.ones((1, 8)))
    assert res["enabled"] is False
    assert "reason" in res


def test_cscv_pbo_disabled_too_few_blocks():
    res = cscv_pbo(np.random.RandomState(0).rand(3, 3))
    assert res["enabled"] is False
    assert "reason" in res


def test_cscv_pbo_wellformed():
    res = cscv_pbo(np.random.RandomState(1).rand(3, 8))
    assert res["enabled"] is True
    assert res["n_combos"] == math.comb(8, 4) == 70
    assert res["n_candidates"] == 3
    assert res["n_blocks"] == 8
    assert 0.0 <= res["pbo"] <= 1.0


def test_cscv_pbo_delegate_identity():
    from guanlan_v2.workflow import api as workflow_api

    for m in [
        np.ones((1, 8)),
        np.random.RandomState(0).rand(3, 3),
        np.random.RandomState(2).rand(3, 5),   # S=5 odd -> dropped to 4; still enabled
        np.random.RandomState(3).rand(4, 8),
    ]:
        assert workflow_api._cscv_pbo(m) == cscv_pbo(m)


# --------------------------------------------------------------------------- #
# dsr wrapper                                                                 #
# --------------------------------------------------------------------------- #
def test_dsr_floor_and_finite():
    rng = np.random.RandomState(7)
    r = [0.01 + 0.02 * rng.randn() for _ in range(30)]
    v = dsr(r, n_trials=7)
    assert v is not None and math.isfinite(v) and 0.0 <= v <= 1.0
    # < 10 finite samples -> None (mirrors deflated_sharpe's own floor)
    assert dsr([0.01, 0.02, -0.01], n_trials=2) is None


def test_dsr_delegate_identity():
    from guanlan_v2.strategy.compute.cpcv import deflated_sharpe

    rng = np.random.RandomState(11)
    r = [0.01 + 0.02 * rng.randn() for _ in range(30)]
    assert dsr(r, n_trials=5) == deflated_sharpe(list(r), 5)


# --------------------------------------------------------------------------- #
# effective_n_trials (invariant 6)                                            #
# --------------------------------------------------------------------------- #
def test_effective_n_trials_no_evidence_equals_max2raw():
    assert effective_n_trials(7, 7, None) == 7
    assert effective_n_trials(1, 1, None) == 2       # floor
    assert effective_n_trials(0, 0, None) == 2       # floor


def test_effective_n_trials_clusters_correlated():
    base = np.array([0.1, 0.2, -0.1, 0.3, 0.05, -0.2, 0.15, 0.0])
    block = {
        "c1": list(base),
        "c2": list(base * 1.01 + 0.001),
        "c3": list(base * 0.99 - 0.002),
        "c4": [0.2, -0.3, 0.1, -0.05, 0.25, 0.3, -0.15, 0.02],
    }
    # c1,c2,c3 pairwise corr ~1.0 (>=0.9) -> one cluster; c4 independent -> second.
    assert effective_n_trials(4, 4, block) == 2


def test_effective_n_trials_never_exceeds_raw_bound():
    base = np.array([0.1, 0.2, -0.1, 0.3, 0.05, -0.2, 0.15, 0.0])
    block = {f"c{i}": list(base + 0.001 * i) for i in range(6)}
    # even if clustering counts more, result is clamped to <= max(2, raw)
    for raw in (0, 1, 2, 3, 6, 10):
        assert 2 <= effective_n_trials(raw, len(block), block) <= max(2, raw)


# --------------------------------------------------------------------------- #
# complexity_score                                                            #
# --------------------------------------------------------------------------- #
def test_complexity_score_deterministic():
    cand = _candidate()  # 1 node (1 param key), 0 edges
    assert complexity_score(cand) == pytest.approx(1 + 0 + 0.1 * 1)
    two = OptimizeCandidate.build(
        candidate_kind="workflow_graph",
        graph={
            "nodes": [
                {"id": "a", "type": "formula", "params": {"expr": "x", "w": 1}},
                {"id": "b", "type": "rank", "params": {}},
            ],
            "edges": [{"src": "a", "dst": "b"}],
        },
        params={},
    )
    # 2 nodes + 1 edge + 0.1 * (2 params on a + 0 on b)
    assert complexity_score(two) == pytest.approx(2 + 1 + 0.1 * 2)


# --------------------------------------------------------------------------- #
# derive_study_family + verify (invariants 2, 3, 4)                           #
# --------------------------------------------------------------------------- #
def test_derive_family_deterministic_and_shape():
    fam = derive_study_family(_study())
    again = derive_study_family(_study())
    assert isinstance(fam, StudyFamily)
    assert fam.family_id == "fam." + fam.identity_digest[:16]
    assert fam == again  # deterministic
    verify_family_attestation(fam)  # round-trips


def test_derive_family_rename_invariant():
    # invariant 3: new display text, same digests -> same family_id (budgets can't reset)
    a = derive_study_family(_study(objective="A", label_definition="lab A"))
    b = derive_study_family(_study(objective="totally different wording", label_definition="B"))
    assert a.family_id == b.family_id
    assert a.identity_digest == b.identity_digest
    assert a.governor_attestation == b.governor_attestation


@pytest.mark.parametrize(
    "handle",
    ["objective_digest", "label_digest", "universe_digest", "frequency", "split_policy_digest"],
)
def test_derive_family_handle_perturbation_changes_id(handle):
    # invariant 4: any of the five identity handles differing -> new family
    base = derive_study_family(_study())
    new_val = "monthly" if handle == "frequency" else DZ
    perturbed = derive_study_family(_study(**{handle: new_val}))
    assert perturbed.family_id != base.family_id
    assert perturbed.identity_digest != base.identity_digest


def test_verify_rejects_tampered_attestation():
    fam = derive_study_family(_study())
    forged = fam.model_copy(update={"governor_attestation": DF})
    with pytest.raises(FamilyAttestationError):
        verify_family_attestation(forged)


def test_verify_rejects_tampered_identity_digest():
    fam = derive_study_family(_study())
    # re-mint identity_digest (with a matching family_id so the model constructs) but
    # leave the five handles pointing at the real study -> recomputed identity mismatches.
    forged = fam.model_copy(update={"identity_digest": DZ, "family_id": "fam." + DZ[:16]})
    with pytest.raises(FamilyAttestationError):
        verify_family_attestation(forged)


def test_verify_rejects_caller_minted_family_id():
    fam = derive_study_family(_study())
    # a caller who bypassed the StudyFamily constructor (model_construct) with an id
    # inconsistent with its identity_digest is rejected by the governor's re-derivation.
    forged = StudyFamily.model_construct(
        **{**fam.__dict__, "family_id": "fam.deadbeefdeadbeef"}
    )
    with pytest.raises(FamilyAttestationError):
        verify_family_attestation(forged)


def test_lineage_requires_change_reason_at_contract():
    # invariant 4 tail: parent_family_id without change_reason fails at the contract.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _study(parent_family_id="fam.abc123abc123abc1")


# --------------------------------------------------------------------------- #
# Governor.govern honesty (invariant 5) + should_stop                         #
# --------------------------------------------------------------------------- #
def test_govern_unavailable_when_no_numbers():
    gov = Governor(trial_budget=100, peek_budget=10)
    fam = derive_study_family(_study())
    report = gov.govern(
        family=fam,
        stats={"raw_trial_count": 5, "reveal_count": 1},
        returns=(0.01, 0.02, -0.01),   # 3 samples -> no DSR
        perf_matrix=None,               # no PBO
        candidate=_candidate(),
        split_spec=_split(),
    )
    assert report.status == "unavailable"
    assert report.deflated_sharpe is None
    assert report.pbo is None
    assert report.pbo_enabled is False
    assert report.reasons  # non-empty
    joined = " ".join(report.reasons).lower()
    assert "dsr" in joined or "sharpe" in joined
    assert "pbo" in joined
    assert report.family_id == fam.family_id
    assert report.raw_trial_count == 5


def test_govern_ok_with_enough_returns():
    gov = Governor(trial_budget=100, peek_budget=10)
    fam = derive_study_family(_study())
    rng = np.random.RandomState(7)
    returns = tuple(0.01 + 0.02 * rng.randn() for _ in range(30))
    report = gov.govern(
        family=fam,
        stats={"raw_trial_count": 8, "reveal_count": 2},
        returns=returns,
        perf_matrix=None,
        candidate=_candidate(),
        split_spec=_split(),
    )
    assert report.status == "ok"
    assert report.deflated_sharpe is not None
    assert math.isfinite(report.deflated_sharpe)
    assert report.effective_n_trials is not None
    assert report.effective_n_trials <= report.raw_trial_count
    assert report.complexity_score is not None


def test_govern_ok_with_pbo_only():
    gov = Governor(trial_budget=100, peek_budget=10)
    fam = derive_study_family(_study())
    report = gov.govern(
        family=fam,
        stats={"raw_trial_count": 8, "reveal_count": 2},
        returns=None,
        perf_matrix=np.random.RandomState(1).rand(3, 8),
        candidate=_candidate(),
        split_spec=_split(),
    )
    assert report.status == "ok"
    assert report.pbo_enabled is True
    assert report.pbo is not None and 0.0 <= report.pbo <= 1.0
    assert report.deflated_sharpe is None


def test_govern_budget_remaining():
    gov = Governor(trial_budget=10, peek_budget=5)
    fam = derive_study_family(_study())
    report = gov.govern(
        family=fam,
        stats={"raw_trial_count": 12, "reveal_count": 7},   # both over budget
        returns=None,
        perf_matrix=None,
        candidate=_candidate(),
        split_spec=_split(),
    )
    assert report.trial_budget_remaining == 0   # max(0, 10 - 12)
    assert report.peek_budget_remaining == 0    # max(0, 5 - 7)


def _report(**over) -> GovernanceReport:
    base = dict(
        status="unavailable",
        family_id="fam." + DA[:16],
        raw_trial_count=5,
        pbo_reason="no perf matrix",
        trial_budget_remaining=3,
        peek_budget_remaining=3,
        reasons=("no governance numbers",),
    )
    base.update(over)
    return GovernanceReport(**base)


def test_should_stop_matrix():
    gov = Governor(trial_budget=10, peek_budget=10)
    assert gov.should_stop(_report(trial_budget_remaining=0)) == "trial_budget_exhausted"
    assert gov.should_stop(_report(peek_budget_remaining=0)) == "peek_budget_exhausted"
    assert gov.should_stop(_report()) is None


def test_should_stop_via_govern():
    gov = Governor(trial_budget=8, peek_budget=100)
    fam = derive_study_family(_study())
    report = gov.govern(
        family=fam,
        stats={"raw_trial_count": 8, "reveal_count": 0},
        returns=None,
        perf_matrix=None,
        candidate=_candidate(),
        split_spec=_split(),
    )
    assert report.trial_budget_remaining == 0
    assert gov.should_stop(report) == "trial_budget_exhausted"


# --------------------------------------------------------------------------- #
# Import-surface inspection (invariant 7)                                     #
# --------------------------------------------------------------------------- #
def test_no_io_module_level_import_surface():
    src = Path(
        "guanlan_v2/orchestration/governor.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    allowed_roots = {"__future__", "math", "typing", "collections", "numpy"}
    allowed_full = {
        "guanlan_v2.orchestration.digest",
        "guanlan_v2.orchestration.trial",
    }
    module_level: list[str] = []
    for node in tree.body:  # only direct children == module level
        if isinstance(node, ast.Import):
            module_level.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_level.append(node.module or "")

    for name in module_level:
        root = name.split(".")[0]
        assert (
            name in allowed_full or root in allowed_roots
        ), f"unexpected module-level import in governor.py: {name!r}"
        # no I/O / heavy data modules anywhere at module level
        assert root not in {"os", "io", "pathlib", "requests", "httpx", "pandas"}

    # the heavy strategy.compute.cpcv import must stay function-local
    assert "guanlan_v2.strategy.compute.cpcv" not in module_level
    # no engine / workflow / datafeed edges at module level
    for name in module_level:
        assert not name.startswith("guanlan_v2.workflow")
        assert not name.startswith("guanlan_v2.datafeed")
        assert not name.startswith("financial_analyst")


# --------------------------------------------------------------------------- #
# D6 revision-throttle governance primitive (Task 4b)                         #
# --------------------------------------------------------------------------- #
def test_revision_throttle_constant_frozen():
    # the D6 ruling values (spec:289), frozen alongside GOVERNOR_VERSION.
    assert REVISION_THROTTLE_MIN_MATURED == {"monthly": 3, "daily": 20}


@pytest.mark.parametrize(
    "frequency, count, allowed",
    [
        # boundary matrix (brief invariant 1)
        ("monthly", 2, False),
        ("monthly", 3, True),
        ("monthly", 4, True),
        ("daily", 19, False),
        ("daily", 20, True),
        ("daily", 21, True),
    ],
)
def test_revision_throttle_boundary_matrix(frequency, count, allowed):
    ok, reason = revision_throttle_check(
        frequency=frequency, matured_observation_count=count
    )
    assert ok is allowed
    # a reason string accompanies every path (allowed and blocked alike).
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize(
    "frequency, count",
    [("monthly", 0), ("monthly", 2), ("daily", 0), ("daily", 19)],
)
def test_revision_throttle_blocked_reason_names_threshold(frequency, count):
    # every blocked path names the threshold N in the reason (brief §Step 1).
    ok, reason = revision_throttle_check(
        frequency=frequency, matured_observation_count=count
    )
    assert ok is False
    threshold = REVISION_THROTTLE_MIN_MATURED[frequency]
    assert str(threshold) in reason
    assert str(count) in reason


@pytest.mark.parametrize("frequency", ["weekly", "", "1d", "annual"])
def test_revision_throttle_unknown_frequency_rejected(frequency):
    # unknown frequency ⇒ rejected with a named reason, never a silent
    # default-allow — even with an enormous matured count (brief invariant 2).
    ok, reason = revision_throttle_check(
        frequency=frequency, matured_observation_count=10_000
    )
    assert ok is False
    assert isinstance(reason, str) and reason
    assert "frequency" in reason.lower()


def test_revision_throttle_no_default_allow_for_unknown():
    # the honesty construction: there is no frequency that is both unknown to
    # REVISION_THROTTLE_MIN_MATURED and yet admitted.
    for freq in ("MONTHLY", "Daily", "d", "m", "1mon", "bogus"):
        assert freq not in REVISION_THROTTLE_MIN_MATURED
        ok, _ = revision_throttle_check(
            frequency=freq, matured_observation_count=1_000_000
        )
        assert ok is False
