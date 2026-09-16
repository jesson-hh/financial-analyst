import math

import numpy as np
import pytest

from guanlan_v2.orchestration.governor import dsr
import guanlan_v2.workflow.overfit_audit as overfit_module
from guanlan_v2.workflow.overfit_audit import (
    FAIL,
    INCOMPLETE,
    PASS,
    SELECTION_ELIGIBILITY_SCOPE,
    SELECTION_OBJECTIVE,
    SELECTION_SCORE_SOURCE,
    audit_overfit,
    block_performance_matrix,
    block_selection_score_matrix,
    bonferroni_haircut,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
)


def _strong_family(periods: int = 160) -> tuple[np.ndarray, np.ndarray]:
    """Stable selected edge plus three strictly weaker trial-family members."""
    rng = np.random.default_rng(8128)
    common = rng.normal(0.0, 0.003, periods)
    selected = 0.018 + common + rng.normal(0.0, 0.002, periods)
    trials = np.column_stack(
        [
            selected,
            0.007 + common + rng.normal(0.0, 0.004, periods),
            0.002 + common + rng.normal(0.0, 0.006, periods),
            -0.003 + common + rng.normal(0.0, 0.007, periods),
        ]
    )
    return selected, trials


def _noise_family(periods: int = 160) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(99)
    trials = rng.normal(0.0, 0.02, (periods, 5))
    return trials[:, 0].copy(), trials


def _selection_scores(periods: int, candidates: int) -> np.ndarray:
    """Stable, distinct RankIC paths whose first column always wins."""
    phase = np.sin(np.arange(periods, dtype="float64") / 7.0) * 0.002
    return np.column_stack([
        (0.09 - i * 0.015) + phase * (i + 1) / candidates
        for i in range(candidates)
    ])


def _selection_kwargs(scores: np.ndarray, *, selected_column: int = 0,
                      eligible=None) -> dict:
    return {
        "selection_score_matrix": scores,
        "eligible_column_indices": (
            list(range(scores.shape[1])) if eligible is None else list(eligible)
        ),
        "selected_column_index": selected_column,
        "selection_objective": SELECTION_OBJECTIVE,
        "selection_score_source": SELECTION_SCORE_SOURCE,
        "selection_eligibility_scope": SELECTION_ELIGIBILITY_SCOPE,
        "selection_rule_digest": "a" * 64,
    }


def test_psr_mintrl_and_haircut_have_expected_direction():
    rng = np.random.default_rng(7)
    strong = rng.normal(0.01, 0.02, 120)
    weak = rng.normal(0.0, 0.02, 120)

    assert probabilistic_sharpe_ratio(strong) > probabilistic_sharpe_ratio(weak)
    min_trl = minimum_track_record_length(strong, confidence=0.95)
    assert min_trl is not None and math.isfinite(min_trl) and min_trl > 1

    h2 = bonferroni_haircut(strong, n_trials=2)
    h100 = bonferroni_haircut(strong, n_trials=100)
    assert h100["adjusted_psr"] <= h2["adjusted_psr"] <= h2["raw_psr"]
    assert h100["confidence_haircut"] >= h2["confidence_haircut"]


def test_mintrl_none_when_observed_sharpe_does_not_beat_benchmark():
    returns = [-0.02, 0.01, -0.01, 0.005] * 10
    assert minimum_track_record_length(returns, benchmark_sharpe=0.0) is None


def test_block_performance_matrix_converts_t_by_n_to_n_by_blocks():
    matrix = np.arange(96, dtype="float64").reshape(32, 3)
    result = block_performance_matrix(matrix, n_blocks=8)
    assert result.shape == (3, 8)
    assert result[0, 0] == pytest.approx(matrix[:4, 0].mean())
    assert result[2, -1] == pytest.approx(matrix[-4:, 2].mean())

    selection = matrix / matrix.max()
    selection_result = block_selection_score_matrix(selection, n_blocks=8)
    assert selection_result.shape == (3, 8)
    assert selection_result[0, 0] == pytest.approx(selection[:4, 0].mean())


def test_complete_strong_family_passes_and_reuses_existing_dsr():
    selected, trials = _strong_family()
    scores = _selection_scores(len(selected), trials.shape[1])
    report = audit_overfit(
        selected, trials, n_trials=trials.shape[1], **_selection_kwargs(scores),
    )

    assert report["status"] == PASS
    assert report["passes_gate"] is True
    assert report["reasons"] == []
    assert report["failed_checks"] == []
    assert report["evidence"]["complete_trial_family"] is True
    assert report["evidence"]["selected_in_trial_matrix"] is True
    assert report["evidence"]["selection_objective"] == "mean_rank_ic"
    assert report["evidence"]["selection_score_source"] \
        == "backtest.ic.rank_ic_series"
    assert report["evidence"]["eligible_candidates"] == trials.shape[1]
    assert report["evidence"]["block_sizes"] == [20] * 8
    assert report["metrics"]["pbo"] <= report["thresholds"]["pbo_max"]

    sharpes = trials.mean(axis=0) / trials.std(axis=0, ddof=1)
    expected = dsr(
        selected,
        n_trials=trials.shape[1],
        sharpes_std=float(sharpes.std(ddof=1)),
    )
    assert report["metrics"]["dsr"] == expected


def test_selection_and_return_matrices_may_have_different_time_axes():
    selected, trials = _strong_family(periods=160)
    scores = _selection_scores(80, trials.shape[1])
    report = audit_overfit(
        selected, trials, n_trials=trials.shape[1], **_selection_kwargs(scores),
    )

    assert report["status"] == PASS
    assert report["evidence"]["matrix_observations"] == 160
    assert report["evidence"]["selection_score_observations"] == 80


def test_complete_noise_family_is_fail_not_incomplete():
    selected, trials = _noise_family()
    report = audit_overfit(
        selected, trials, n_trials=trials.shape[1],
        **_selection_kwargs(_selection_scores(len(selected), trials.shape[1])),
    )

    assert report["status"] == FAIL
    assert report["passes_gate"] is False
    assert report["reasons"] == []
    assert report["failed_checks"]


def test_pbo_ranks_rankic_matrix_not_realised_return_matrix(monkeypatch):
    selected, trials = _strong_family()
    # Deliberately make the realised-return winner differ from the RankIC winner.
    scores = _selection_scores(len(selected), trials.shape[1])[:, ::-1]
    selected_column = trials.shape[1] - 1
    selected_returns = trials[:, selected_column]
    seen = {}

    def fake_pbo(matrix, n_blocks=8):
        seen["matrix"] = np.asarray(matrix)
        return {"enabled": True, "pbo": 0.0, "n_combos": 70,
                "n_candidates": matrix.shape[0], "n_blocks": n_blocks}

    monkeypatch.setattr(overfit_module, "cscv_pbo", fake_pbo)
    report = audit_overfit(
        selected_returns, trials, n_trials=trials.shape[1],
        **_selection_kwargs(scores, selected_column=selected_column),
    )

    expected = block_selection_score_matrix(scores, n_blocks=8)
    assert np.allclose(seen["matrix"], expected)
    assert not np.allclose(seen["matrix"], block_performance_matrix(trials, n_blocks=8))
    assert report["metrics"]["pbo_details"]["selection_objective"] == "mean_rank_ic"


def test_non_rankic_winner_and_single_eligible_candidate_are_incomplete():
    selected, trials = _strong_family()
    scores = _selection_scores(len(selected), trials.shape[1])
    wrong = audit_overfit(
        trials[:, 1], trials, n_trials=trials.shape[1],
        **_selection_kwargs(scores, selected_column=1),
    )
    assert wrong["status"] == INCOMPLETE
    assert any("mean-RankIC winner" in reason for reason in wrong["reasons"])

    single = audit_overfit(
        selected, trials, n_trials=trials.shape[1],
        **_selection_kwargs(scores, eligible=[0]),
    )
    assert single["status"] == INCOMPLETE
    assert any("at least 2 base-gate-eligible" in reason for reason in single["reasons"])


def test_missing_selection_matrix_cannot_reuse_return_pbo():
    selected, trials = _strong_family()
    report = audit_overfit(selected, trials, n_trials=trials.shape[1])
    assert report["status"] == INCOMPLETE
    assert report["metrics"]["pbo"] is None
    assert any("selection_score_matrix" in reason for reason in report["reasons"])


@pytest.mark.parametrize(
    ("selected", "matrix", "n_trials", "reason_fragment"),
    [
        (np.linspace(-0.01, 0.02, 40), None, 3, "trial_returns is missing"),
        (np.linspace(-0.01, 0.02, 40), np.ones((40, 3)), None, "n_trials is missing"),
        (None, np.ones((40, 3)), 3, "selected_returns is missing"),
    ],
)
def test_missing_required_evidence_is_incomplete(
    selected, matrix, n_trials, reason_fragment
):
    candidates = matrix.shape[1] if isinstance(matrix, np.ndarray) else 3
    report = audit_overfit(
        selected, matrix, n_trials,
        **_selection_kwargs(_selection_scores(40, candidates)),
    )
    assert report["status"] == INCOMPLETE
    assert report["passes_gate"] is False
    assert any(reason_fragment in reason for reason in report["reasons"])


def test_trialledger_count_mismatch_is_incomplete_even_with_strong_returns():
    selected, trials = _strong_family()
    report = audit_overfit(
        selected, trials, n_trials=trials.shape[1] + 1,
        **_selection_kwargs(_selection_scores(len(selected), trials.shape[1])),
    )
    assert report["status"] == INCOMPLETE
    assert report["passes_gate"] is False
    assert report["evidence"]["complete_trial_family"] is False
    assert any("complete trial family" in reason for reason in report["reasons"])


def test_selected_candidate_must_be_part_of_trial_family():
    selected, trials = _strong_family()
    alien = selected + 0.0001
    report = audit_overfit(
        alien, trials, n_trials=trials.shape[1],
        **_selection_kwargs(_selection_scores(len(selected), trials.shape[1])),
    )
    assert report["status"] == INCOMPLETE
    assert report["evidence"]["selected_in_trial_matrix"] is False
    assert any("not a column" in reason for reason in report["reasons"])


def test_too_short_and_identical_trial_paths_are_incomplete():
    short = np.linspace(-0.01, 0.02, 12)
    short_matrix = np.column_stack([short, short * 0.5])
    assert audit_overfit(
        short, short_matrix, 2,
        **_selection_kwargs(_selection_scores(len(short), 2)),
    )["status"] == INCOMPLETE

    selected = np.linspace(-0.01, 0.02, 40)
    identical = np.column_stack([selected, selected])
    duplicate_scores = np.column_stack([selected, selected])
    report = audit_overfit(
        selected, identical, 2, **_selection_kwargs(duplicate_scores),
    )
    assert report["status"] == INCOMPLETE
    assert any("distinct RankIC" in reason for reason in report["reasons"])


@pytest.mark.parametrize(
    ("selected", "matrix", "n_trials"),
    [
        ([0.01, math.nan] * 20, np.ones((40, 2)), 2),
        (np.linspace(-0.01, 0.02, 40), [[0.0, math.inf]] * 40, 2),
        (np.linspace(-0.01, 0.02, 40), np.ones((40, 2)), True),
    ],
)
def test_malformed_numeric_evidence_is_rejected(selected, matrix, n_trials):
    with pytest.raises(ValueError):
        candidates = len(matrix[0]) if matrix is not None else 2
        audit_overfit(
            selected, matrix, n_trials,
            **_selection_kwargs(_selection_scores(40, candidates)),
        )


def test_invalid_block_contract_is_rejected():
    with pytest.raises(ValueError, match="even integer"):
        block_performance_matrix(np.ones((40, 3)), n_blocks=5)
    with pytest.raises(ValueError, match="two observations per block"):
        block_performance_matrix(np.ones((8, 3)), n_blocks=8)
    with pytest.raises(ValueError, match="4..16"):
        block_performance_matrix(np.ones((64, 3)), n_blocks=18)


def test_audit_records_unequal_blocks_and_rejects_oversized_candidate_matrix():
    selected, trials = _strong_family(periods=162)
    report = audit_overfit(
        selected, trials, n_trials=trials.shape[1], n_blocks=8,
        **_selection_kwargs(_selection_scores(len(selected), trials.shape[1])),
    )
    assert sum(report["evidence"]["block_sizes"]) == 162
    assert max(report["evidence"]["block_sizes"]) - min(report["evidence"]["block_sizes"]) == 1
    assert any("not divisible" in warning for warning in report["warnings"])

    with pytest.raises(ValueError, match="maximum is 500"):
        block_performance_matrix(np.ones((32, 501)), n_blocks=8)
