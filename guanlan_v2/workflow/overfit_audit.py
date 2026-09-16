# -*- coding: utf-8 -*-
"""Pure, workflow-facing overfitting audit primitives.

The lower-level statistics already live in two established Guanlan modules:

* :func:`guanlan_v2.orchestration.governor.cscv_pbo` implements CSCV/PBO over a
  ``candidate x time-block`` performance matrix.
* :func:`guanlan_v2.orchestration.governor.dsr` delegates to the CPCV engine's
  deflated-Sharpe implementation.

This module does not duplicate either algorithm.  It adds the strict evidence
contract needed by a generic workflow terminal:

``selected_returns``
    One-dimensional realised return series of the selected candidate.
``trial_returns``
    The *complete* trial-family matrix in ``T x N`` orientation (time rows,
    candidate columns).  It is deliberately never transposed or imputed here.
``selection_score_matrix``
    A second, independently aligned ``T_ic x N`` matrix of exact period RankIC
    observations.  This is the only matrix CSCV/PBO may rank.  Realised returns
    remain the evidence for PSR/DSR/haircut and are never silently reinterpreted
    as the optimizer's selection objective.
``n_trials``
    The immutable raw trial count supplied by TrialLedger.  A matrix with fewer or
    more columns than the ledger count is incomplete evidence, never a pass.

PSR, minimum track-record length and a transparent Bonferroni confidence haircut
are provided as supplementary diagnostics.  The combined result has only three
states: ``PASS``, ``FAIL`` and ``INCOMPLETE``.  Missing/misaligned trial evidence can
therefore never be presented as a successful audit.

All functions are deterministic and perform no network, filesystem or store I/O.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist
from typing import Any

import numpy as np

from guanlan_v2.orchestration.governor import cscv_pbo, dsr

__all__ = [
    "AUDIT_VERSION",
    "PASS",
    "FAIL",
    "INCOMPLETE",
    "SELECTION_OBJECTIVE",
    "SELECTION_SCORE_SOURCE",
    "SELECTION_ELIGIBILITY_SCOPE",
    "probabilistic_sharpe_ratio",
    "minimum_track_record_length",
    "bonferroni_haircut",
    "block_performance_matrix",
    "block_selection_score_matrix",
    "audit_overfit",
]

AUDIT_VERSION = "overfit-audit-v2"
PASS = "PASS"
FAIL = "FAIL"
INCOMPLETE = "INCOMPLETE"

# These strings are governance semantics, not UI labels or caller-tunable knobs.
SELECTION_OBJECTIVE = "mean_rank_ic"
SELECTION_SCORE_SOURCE = "backtest.ic.rank_ic_series"
SELECTION_ELIGIBILITY_SCOPE = "full_validation_base_gate"

DEFAULT_DSR_MIN = 0.95
DEFAULT_PSR_MIN = 0.95
DEFAULT_ADJUSTED_PSR_MIN = 0.95
DEFAULT_PBO_MAX = 0.30
DEFAULT_CONFIDENCE = 0.95
DEFAULT_N_BLOCKS = 8
DEFAULT_MIN_OBSERVATIONS = 16  # >= 10 for DSR and >= 2 observations per block.
MAX_N_BLOCKS = 16
MAX_OBSERVATIONS = 10_000
MAX_CANDIDATES = 500
MAX_MATRIX_CELLS = 2_000_000

_NORMAL = NormalDist()
_EPS = 1e-12


def _validate_n_blocks(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError("n_blocks must be an integer")
    result = int(value)
    if result < 4 or result > MAX_N_BLOCKS or result % 2:
        raise ValueError(f"n_blocks must be an even integer in 4..{MAX_N_BLOCKS}")
    return result


def _validate_matrix_size(matrix: np.ndarray, *, name: str = "trial_returns") -> None:
    rows, columns = (int(matrix.shape[0]), int(matrix.shape[1]))
    if rows > MAX_OBSERVATIONS:
        raise ValueError(
            f"{name} has {rows} rows; maximum is {MAX_OBSERVATIONS}"
        )
    if columns > MAX_CANDIDATES:
        raise ValueError(
            f"{name} has {columns} candidates; maximum is {MAX_CANDIDATES}"
        )
    if matrix.size > MAX_MATRIX_CELLS:
        raise ValueError(
            f"{name} has {matrix.size} cells; maximum is {MAX_MATRIX_CELLS}"
        )


def _as_finite_vector(values: Sequence[float], *, name: str) -> np.ndarray:
    """Return a finite float64 vector, rejecting silent deletion/imputation."""
    try:
        arr = np.asarray(values, dtype="float64")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a numeric one-dimensional sequence") from exc
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional; got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} must contain only finite values; no silent imputation")
    return arr


def _as_finite_matrix(values: Sequence[Sequence[float]], *, name: str) -> np.ndarray:
    """Return a finite float64 matrix without guessing its orientation."""
    try:
        arr = np.asarray(values, dtype="float64")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a rectangular numeric matrix") from exc
    if arr.ndim != 2:
        raise ValueError(f"{name} must be two-dimensional; got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} must contain only finite values; no silent imputation")
    return arr


def _validate_probability(value: float, *, name: str, open_interval: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    lower_ok = result > 0.0 if open_interval else result >= 0.0
    upper_ok = result < 1.0 if open_interval else result <= 1.0
    if not math.isfinite(result) or not (lower_ok and upper_ok):
        interval = "(0, 1)" if open_interval else "[0, 1]"
        raise ValueError(f"{name} must be in {interval}")
    return result


def _return_moments(returns: Sequence[float]) -> dict[str, float | int]:
    """Per-period Sharpe and moment terms used by PSR/MinTRL.

    Skewness and kurtosis use population central moments.  That keeps this module
    NumPy-only while following the standard PSR denominator.  The observed Sharpe
    uses the sample standard deviation, matching Guanlan's existing DSR input scale.
    """
    r = _as_finite_vector(returns, name="returns")
    if len(r) < 3:
        raise ValueError("returns needs at least 3 observations")
    sample_std = float(r.std(ddof=1))
    if sample_std <= 0.0:
        raise ValueError("returns must have non-zero sample variance")
    mean = float(r.mean())
    centered = r - mean
    m2 = float(np.mean(centered * centered))
    if m2 <= 0.0:
        raise ValueError("returns must have non-zero variance")
    skewness = float(np.mean(centered**3) / (m2**1.5))
    kurtosis = float(np.mean(centered**4) / (m2**2))
    sharpe = mean / sample_std
    variance_term = max(
        _EPS,
        1.0
        - skewness * sharpe
        + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe,
    )
    standard_error = math.sqrt(variance_term / (len(r) - 1))
    return {
        "n_observations": int(len(r)),
        "mean": mean,
        "sample_std": sample_std,
        "sharpe": float(sharpe),
        "skewness": skewness,
        "kurtosis": kurtosis,
        "variance_term": float(variance_term),
        "standard_error": float(standard_error),
    }


def probabilistic_sharpe_ratio(
    returns: Sequence[float], *, benchmark_sharpe: float = 0.0
) -> float:
    """Probability that the per-period Sharpe exceeds ``benchmark_sharpe``.

    This is the Bailey/López de Prado PSR moment correction.  It is intentionally
    separate from DSR: PSR answers whether one observed Sharpe exceeds a specified
    benchmark, while DSR raises the benchmark to account for repeated trials.
    """
    benchmark = float(benchmark_sharpe)
    if not math.isfinite(benchmark):
        raise ValueError("benchmark_sharpe must be finite")
    stats = _return_moments(returns)
    z = (float(stats["sharpe"]) - benchmark) / float(stats["standard_error"])
    return float(min(1.0, max(0.0, _NORMAL.cdf(z))))


def minimum_track_record_length(
    returns: Sequence[float],
    *,
    benchmark_sharpe: float = 0.0,
    confidence: float = DEFAULT_CONFIDENCE,
) -> float | None:
    """Minimum observations needed to beat the benchmark at ``confidence``.

    ``None`` is returned when observed Sharpe does not exceed the benchmark: no
    finite extension of the same non-positive edge can establish the claim.
    """
    benchmark = float(benchmark_sharpe)
    if not math.isfinite(benchmark):
        raise ValueError("benchmark_sharpe must be finite")
    confidence = _validate_probability(confidence, name="confidence", open_interval=True)
    stats = _return_moments(returns)
    excess = float(stats["sharpe"]) - benchmark
    if excess <= 0.0:
        return None
    z = _NORMAL.inv_cdf(confidence)
    value = 1.0 + (z * math.sqrt(float(stats["variance_term"])) / excess) ** 2
    return float(value) if math.isfinite(value) else None


def bonferroni_haircut(
    returns: Sequence[float],
    *,
    n_trials: int,
    benchmark_sharpe: float = 0.0,
) -> dict[str, float | int | str]:
    """Apply a transparent Bonferroni haircut to PSR confidence.

    The returned ``adjusted_psr`` is ``1 - min(1, n_trials * (1 - PSR))``.  For UI
    comparability, ``equivalent_sharpe`` inverts that adjusted confidence using the
    observed Sharpe standard error; it is a diagnostic equivalent, not a replacement
    for realised returns.  DSR remains the primary selection-adjusted Sharpe test.
    """
    if isinstance(n_trials, bool) or not isinstance(n_trials, (int, np.integer)):
        raise ValueError("n_trials must be an integer")
    if int(n_trials) < 1:
        raise ValueError("n_trials must be positive")
    benchmark = float(benchmark_sharpe)
    if not math.isfinite(benchmark):
        raise ValueError("benchmark_sharpe must be finite")
    stats = _return_moments(returns)
    raw_psr = probabilistic_sharpe_ratio(returns, benchmark_sharpe=benchmark)
    adjusted_p = min(1.0, max(0.0, (1.0 - raw_psr) * int(n_trials)))
    adjusted_psr = 1.0 - adjusted_p
    # Keep the display-equivalent finite even when floating point PSR is exactly 0/1.
    clipped = min(1.0 - _EPS, max(_EPS, adjusted_psr))
    equivalent = benchmark + _NORMAL.inv_cdf(clipped) * float(stats["standard_error"])
    observed = float(stats["sharpe"])
    return {
        "method": "bonferroni",
        "n_trials": int(n_trials),
        "raw_psr": float(raw_psr),
        "adjusted_psr": float(adjusted_psr),
        "confidence_haircut": float(raw_psr - adjusted_psr),
        "observed_sharpe": observed,
        "equivalent_sharpe": float(equivalent),
        "sharpe_haircut": float(observed - equivalent),
    }


def block_performance_matrix(
    trial_returns: Sequence[Sequence[float]], *, n_blocks: int = DEFAULT_N_BLOCKS
) -> np.ndarray:
    """Convert strict ``T x N`` trial returns to Governor's ``N x S`` contract.

    Each block statistic is its mean realised return.  Ranking candidates by an
    affine annualisation would be identical, so no frequency assumption is smuggled
    into the PBO calculation.
    """
    matrix = _as_finite_matrix(trial_returns, name="trial_returns")
    _validate_matrix_size(matrix)
    n_blocks = _validate_n_blocks(n_blocks)
    if matrix.shape[0] < n_blocks * 2:
        raise ValueError(
            f"trial_returns needs at least {n_blocks * 2} rows "
            f"(two observations per block); got {matrix.shape[0]}"
        )
    blocks = np.array_split(matrix, n_blocks, axis=0)
    return np.asarray([block.mean(axis=0) for block in blocks], dtype="float64").T


def block_selection_score_matrix(
    selection_score_matrix: Sequence[Sequence[float]], *,
    n_blocks: int = DEFAULT_N_BLOCKS,
) -> np.ndarray:
    """Convert exact ``T_ic x N`` RankIC evidence to Governor's ``N x S`` shape.

    The function is deliberately separate from :func:`block_performance_matrix`:
    using realised returns here would silently change the optimizer's objective.
    Every score is a finite Spearman correlation in ``[-1, 1]`` and each CSCV
    block is represented by its mean period RankIC, matching the governed
    research selector's canonical objective.
    """
    matrix = _as_finite_matrix(selection_score_matrix, name="selection_score_matrix")
    _validate_matrix_size(matrix, name="selection_score_matrix")
    if matrix.size and (float(matrix.min()) < -1.0 or float(matrix.max()) > 1.0):
        raise ValueError("selection_score_matrix RankIC values must be within [-1, 1]")
    n_blocks = _validate_n_blocks(n_blocks)
    if matrix.shape[0] < n_blocks * 2:
        raise ValueError(
            f"selection_score_matrix needs at least {n_blocks * 2} rows "
            f"(two observations per block); got {matrix.shape[0]}"
        )
    blocks = np.array_split(matrix, n_blocks, axis=0)
    return np.asarray([block.mean(axis=0) for block in blocks], dtype="float64").T


def _incomplete_result(
    *,
    reasons: list[str],
    warnings: list[str],
    metrics: dict[str, Any],
    evidence: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": AUDIT_VERSION,
        "status": INCOMPLETE,
        "passes_gate": False,
        "metrics": metrics,
        "thresholds": thresholds,
        "evidence": evidence,
        "failed_checks": [],
        "reasons": reasons or ["required audit evidence is incomplete"],
        "warnings": warnings,
    }


def audit_overfit(
    selected_returns: Sequence[float] | None,
    trial_returns: Sequence[Sequence[float]] | None,
    n_trials: int | None,
    *,
    selection_score_matrix: Sequence[Sequence[float]] | None = None,
    eligible_column_indices: Sequence[int] | None = None,
    selected_column_index: int | None = None,
    selection_tie_break_keys: Sequence[Any] | None = None,
    selection_objective: str | None = None,
    selection_score_source: str | None = None,
    selection_eligibility_scope: str | None = None,
    selection_rule_digest: str | None = None,
    benchmark_sharpe: float = 0.0,
    confidence: float = DEFAULT_CONFIDENCE,
    n_blocks: int = DEFAULT_N_BLOCKS,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    dsr_min: float = DEFAULT_DSR_MIN,
    psr_min: float = DEFAULT_PSR_MIN,
    adjusted_psr_min: float = DEFAULT_ADJUSTED_PSR_MIN,
    pbo_max: float = DEFAULT_PBO_MAX,
) -> dict[str, Any]:
    """Run a strict generic overfitting audit and return a JSON-ready report.

    Missing evidence returns ``INCOMPLETE``.  Malformed numeric evidence raises
    :class:`ValueError`; callers should not silently repair, transpose, drop or fill
    financial observations.  Realised ``trial_returns`` drive risk statistics, while
    CSCV/PBO ranks only ``selection_score_matrix`` under the frozen RankIC objective.
    A complete report passes only when all configured gates pass: PSR, DSR,
    Bonferroni-adjusted PSR, conditional RankIC-PBO and MinTRL coverage.
    """
    confidence = _validate_probability(confidence, name="confidence", open_interval=True)
    dsr_min = _validate_probability(dsr_min, name="dsr_min")
    psr_min = _validate_probability(psr_min, name="psr_min")
    adjusted_psr_min = _validate_probability(
        adjusted_psr_min, name="adjusted_psr_min"
    )
    pbo_max = _validate_probability(pbo_max, name="pbo_max")
    benchmark = float(benchmark_sharpe)
    if not math.isfinite(benchmark):
        raise ValueError("benchmark_sharpe must be finite")
    n_blocks = _validate_n_blocks(n_blocks)
    if isinstance(min_observations, bool) or not isinstance(
        min_observations, (int, np.integer)
    ):
        raise ValueError("min_observations must be an integer")
    min_observations = int(min_observations)
    if min_observations < 10:
        raise ValueError("min_observations must be >= 10 (the existing DSR floor)")
    required_observations = max(min_observations, n_blocks * 2)

    thresholds = {
        "confidence": confidence,
        "benchmark_sharpe": benchmark,
        "min_observations": required_observations,
        "dsr_min": dsr_min,
        "psr_min": psr_min,
        "adjusted_psr_min": adjusted_psr_min,
        "pbo_max": pbo_max,
        "n_blocks": n_blocks,
    }
    evidence: dict[str, Any] = {
        "selected_observations": None,
        "matrix_observations": None,
        "matrix_candidates": None,
        "selection_score_observations": None,
        "selection_score_candidates": None,
        "eligible_candidates": None,
        "n_trials": n_trials,
        "selected_in_trial_matrix": False,
        "selected_column_verified": False,
        "selected_is_objective_winner": False,
        "complete_trial_family": False,
        "selection_objective": selection_objective,
        "selection_score_source": selection_score_source,
        "selection_eligibility_scope": selection_eligibility_scope,
        "selection_rule_digest": selection_rule_digest,
        "selected_column_index": selected_column_index,
        "eligible_column_indices": [],
        "block_sizes": [],
        "selection_block_sizes": [],
    }
    metrics: dict[str, Any] = {
        "observed_sharpe": None,
        "psr": None,
        "dsr": None,
        "pbo": None,
        "min_track_record_length": None,
        "haircut": None,
    }
    reasons: list[str] = []
    warnings: list[str] = []

    selected: np.ndarray | None = None
    if selected_returns is None:
        reasons.append("selected_returns is missing")
    else:
        selected = _as_finite_vector(selected_returns, name="selected_returns")
        if len(selected) > MAX_OBSERVATIONS:
            raise ValueError(
                f"selected_returns has {len(selected)} observations; "
                f"maximum is {MAX_OBSERVATIONS}"
            )
        evidence["selected_observations"] = int(len(selected))
        if len(selected) < required_observations:
            reasons.append(
                f"selected_returns has {len(selected)} observations; "
                f"at least {required_observations} required"
            )
        elif float(selected.std(ddof=1)) <= 0.0:
            reasons.append("selected_returns has zero sample variance")
        else:
            stats = _return_moments(selected)
            metrics["observed_sharpe"] = float(stats["sharpe"])
            metrics["psr"] = probabilistic_sharpe_ratio(
                selected, benchmark_sharpe=benchmark
            )
            metrics["min_track_record_length"] = minimum_track_record_length(
                selected,
                benchmark_sharpe=benchmark,
                confidence=confidence,
            )

    ledger_trials: int | None = None
    if n_trials is None:
        reasons.append("n_trials is missing; TrialLedger evidence is required")
    elif isinstance(n_trials, bool) or not isinstance(n_trials, (int, np.integer)):
        raise ValueError("n_trials must be an integer supplied by TrialLedger")
    elif int(n_trials) < 1:
        raise ValueError("n_trials must be positive")
    else:
        ledger_trials = int(n_trials)
        evidence["n_trials"] = ledger_trials
        if ledger_trials < 2:
            reasons.append("n_trials must be >= 2 for selection-overfit auditing")

    matrix: np.ndarray | None = None
    n_candidates = 0
    if trial_returns is None:
        reasons.append("trial_returns is missing; the complete T x N trial matrix is required")
    else:
        matrix = _as_finite_matrix(trial_returns, name="trial_returns")
        _validate_matrix_size(matrix, name="trial_returns")
        t_rows, n_candidates = (int(matrix.shape[0]), int(matrix.shape[1]))
        evidence["matrix_observations"] = t_rows
        evidence["matrix_candidates"] = n_candidates
        if t_rows < required_observations:
            reasons.append(
                f"trial_returns has {t_rows} rows; at least {required_observations} required"
            )
        if n_candidates < 2:
            reasons.append("trial_returns needs at least 2 candidate columns")
        if ledger_trials is not None and n_candidates != ledger_trials:
            reasons.append(
                f"trial matrix has {n_candidates} candidates but TrialLedger records "
                f"n_trials={ledger_trials}; complete trial family not supplied"
            )
        if selected is not None and t_rows != len(selected):
            reasons.append(
                f"trial matrix has {t_rows} rows but selected_returns has {len(selected)}"
            )
        elif selected is not None and n_candidates:
            selected_matches = any(
                np.allclose(selected, matrix[:, i], rtol=1e-10, atol=1e-12)
                for i in range(n_candidates)
            )
            evidence["selected_in_trial_matrix"] = bool(selected_matches)
            if not selected_matches:
                reasons.append("selected_returns is not a column of the supplied trial family")

        if selected_column_index is None:
            reasons.append("selected_column_index is missing")
        elif isinstance(selected_column_index, bool) or not isinstance(
            selected_column_index, (int, np.integer)
        ):
            raise ValueError("selected_column_index must be an integer")
        elif not (0 <= int(selected_column_index) < n_candidates):
            raise ValueError("selected_column_index is outside the trial matrix")
        elif selected is not None and t_rows == len(selected):
            exact_selected = bool(np.allclose(
                selected, matrix[:, int(selected_column_index)], rtol=1e-10, atol=1e-12,
            ))
            evidence["selected_column_verified"] = exact_selected
            if not exact_selected:
                reasons.append(
                    "selected_returns does not match selected_column_index in trial_returns"
                )

        if n_candidates >= 2:
            unique_columns = len({matrix[:, i].tobytes() for i in range(n_candidates)})
            evidence["unique_matrix_candidates"] = unique_columns
            if unique_columns < n_candidates:
                warnings.append(
                    f"trial matrix contains {n_candidates - unique_columns} duplicate candidate paths"
                )

    if selection_objective is None:
        reasons.append("selection_objective is missing")
    elif selection_objective != SELECTION_OBJECTIVE:
        raise ValueError(
            f"selection_objective must be the frozen {SELECTION_OBJECTIVE!r}"
        )
    if selection_score_source is None:
        reasons.append("selection_score_source is missing")
    elif selection_score_source != SELECTION_SCORE_SOURCE:
        raise ValueError(
            f"selection_score_source must be the frozen {SELECTION_SCORE_SOURCE!r}"
        )
    if selection_eligibility_scope is None:
        reasons.append("selection_eligibility_scope is missing")
    elif selection_eligibility_scope != SELECTION_ELIGIBILITY_SCOPE:
        raise ValueError(
            "selection_eligibility_scope must be the frozen "
            f"{SELECTION_ELIGIBILITY_SCOPE!r}"
        )

    eligible: list[int] = []
    if eligible_column_indices is None:
        reasons.append("eligible_column_indices is missing")
    elif isinstance(eligible_column_indices, (str, bytes)):
        raise ValueError("eligible_column_indices must be an integer sequence")
    else:
        for value in eligible_column_indices:
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise ValueError("eligible_column_indices must contain only integers")
            eligible.append(int(value))
        if len(set(eligible)) != len(eligible):
            raise ValueError("eligible_column_indices must be unique")
        if eligible != sorted(eligible):
            raise ValueError("eligible_column_indices must be sorted")
        candidate_bound = n_candidates or (ledger_trials or 0)
        if candidate_bound and any(
            value < 0 or value >= candidate_bound for value in eligible
        ):
            raise ValueError("eligible_column_indices contains an out-of-range column")
        evidence["eligible_column_indices"] = list(eligible)
        evidence["eligible_candidates"] = len(eligible)
        if len(eligible) < 2:
            reasons.append(
                "conditional RankIC PBO needs at least 2 base-gate-eligible candidates"
            )
        if (selected_column_index is not None
                and isinstance(selected_column_index, (int, np.integer))
                and int(selected_column_index) not in eligible):
            reasons.append("selected_column_index is not base-gate eligible")

    score_matrix: np.ndarray | None = None
    if selection_score_matrix is None:
        reasons.append(
            "selection_score_matrix is missing; exact period RankIC evidence is required"
        )
    else:
        score_matrix = _as_finite_matrix(
            selection_score_matrix, name="selection_score_matrix",
        )
        _validate_matrix_size(score_matrix, name="selection_score_matrix")
        if score_matrix.size and (
            float(score_matrix.min()) < -1.0 or float(score_matrix.max()) > 1.0
        ):
            raise ValueError("selection_score_matrix RankIC values must be within [-1, 1]")
        score_rows, score_candidates = (
            int(score_matrix.shape[0]), int(score_matrix.shape[1])
        )
        evidence["selection_score_observations"] = score_rows
        evidence["selection_score_candidates"] = score_candidates
        selection_block_sizes = [
            int(len(block))
            for block in np.array_split(np.arange(score_rows), n_blocks)
        ]
        evidence["selection_block_sizes"] = selection_block_sizes
        # Preserve the established report key, now with its honest objective.
        evidence["block_sizes"] = list(selection_block_sizes)
        if len(set(selection_block_sizes)) > 1:
            warnings.append(
                "RankIC selection rows are not divisible by n_blocks; CSCV blocks "
                "differ by one observation and use equal-weight block means"
            )
        if score_rows < required_observations:
            reasons.append(
                f"selection_score_matrix has {score_rows} rows; "
                f"at least {required_observations} required"
            )
        if ledger_trials is not None and score_candidates != ledger_trials:
            reasons.append(
                f"selection score matrix has {score_candidates} candidates but "
                f"TrialLedger records n_trials={ledger_trials}"
            )
        if matrix is not None and score_candidates != matrix.shape[1]:
            reasons.append(
                "selection score and return matrices do not share the same candidate columns"
            )
        if eligible and all(value < score_candidates for value in eligible):
            selected_scores = score_matrix[:, eligible]
            unique_scores = len({
                selected_scores[:, i].tobytes()
                for i in range(selected_scores.shape[1])
            })
            evidence["unique_eligible_selection_candidates"] = unique_scores
            if len(eligible) >= 2 and unique_scores < 2:
                reasons.append(
                    "eligible selection score matrix has fewer than 2 distinct RankIC paths"
                )
            elif unique_scores < len(eligible):
                warnings.append(
                    "eligible selection score matrix contains "
                    f"{len(eligible) - unique_scores} duplicate RankIC paths"
                )
            if (selected_column_index is not None
                    and isinstance(selected_column_index, (int, np.integer))
                    and int(selected_column_index) in eligible):
                means = score_matrix[:, eligible].mean(axis=0)
                best_mean = float(np.max(means))
                winners = {
                    eligible[i] for i, value in enumerate(means)
                    if math.isclose(
                        float(value), best_mean, rel_tol=1e-12, abs_tol=1e-12,
                    )
                }
                selected_is_winner = int(selected_column_index) in winners
                evidence["selected_is_objective_winner"] = selected_is_winner
                evidence["selected_selection_score_mean"] = float(
                    score_matrix[:, int(selected_column_index)].mean()
                )
                evidence["best_eligible_selection_score_mean"] = best_mean
                if not selected_is_winner:
                    reasons.append(
                        "selected_column_index is not the mean-RankIC winner among "
                        "base-gate-eligible candidates"
                    )

    evidence["complete_trial_family"] = bool(
        ledger_trials is not None
        and matrix is not None
        and score_matrix is not None
        and matrix.shape[1] == ledger_trials
        and score_matrix.shape[1] == ledger_trials
    )

    # DSR/haircut are meaningful only after both the selected series and immutable
    # trial count are present.  Use the observed cross-trial Sharpe dispersion when
    # available; otherwise the established DSR implementation uses its analytic SE.
    selected_ready = (
        selected is not None
        and len(selected) >= required_observations
        and len(selected) >= 2
        and float(selected.std(ddof=1)) > 0.0
    )
    if selected_ready and ledger_trials is not None and ledger_trials >= 2:
        trial_sharpes: list[float] = []
        if matrix is not None and matrix.shape[0] == len(selected):
            for i in range(matrix.shape[1]):
                col_std = float(matrix[:, i].std(ddof=1))
                if col_std > 0.0:
                    trial_sharpes.append(float(matrix[:, i].mean() / col_std))
        sharpe_dispersion: float | None = None
        if len(trial_sharpes) >= 2:
            value = float(np.std(trial_sharpes, ddof=1))
            if value > 0.0 and math.isfinite(value):
                sharpe_dispersion = value
        metrics["trial_sharpe_std"] = sharpe_dispersion
        metrics["dsr"] = dsr(
            selected,
            n_trials=ledger_trials,
            sharpes_std=sharpe_dispersion,
        )
        metrics["haircut"] = bonferroni_haircut(
            selected,
            n_trials=ledger_trials,
            benchmark_sharpe=benchmark,
        )

    selection_ready = (
        score_matrix is not None
        and score_matrix.shape[0] >= required_observations
        and len(eligible) >= 2
        and all(value < score_matrix.shape[1] for value in eligible)
        and ledger_trials is not None
        and score_matrix.shape[1] == ledger_trials
        and bool(evidence["selected_column_verified"])
        and bool(evidence["selected_is_objective_winner"])
        and int(evidence.get("unique_eligible_selection_candidates", 0)) >= 2
        and selection_objective == SELECTION_OBJECTIVE
        and selection_score_source == SELECTION_SCORE_SOURCE
        and selection_eligibility_scope == SELECTION_ELIGIBILITY_SCOPE
    )
    if selection_ready:
        conditional_scores = score_matrix[:, eligible]
        block_matrix = block_selection_score_matrix(
            conditional_scores, n_blocks=n_blocks,
        )
        if selection_tie_break_keys is not None:
            if isinstance(selection_tie_break_keys, (str, bytes)):
                raise ValueError("selection_tie_break_keys must be a candidate sequence")
            tie_keys = list(selection_tie_break_keys)
            if len(tie_keys) != score_matrix.shape[1]:
                raise ValueError(
                    "selection_tie_break_keys must align with candidate columns"
                )
            conditional_tie_keys = [tie_keys[index] for index in eligible]
            pbo_result = cscv_pbo(
                block_matrix, n_blocks=n_blocks,
                tie_break_keys=conditional_tie_keys,
            )
        else:
            pbo_result = cscv_pbo(block_matrix, n_blocks=n_blocks)
        pbo_result["selection_objective"] = SELECTION_OBJECTIVE
        pbo_result["selection_score_source"] = SELECTION_SCORE_SOURCE
        pbo_result["selection_eligibility_scope"] = SELECTION_ELIGIBILITY_SCOPE
        pbo_result["raw_trial_count"] = ledger_trials
        pbo_result["eligible_candidate_count"] = len(eligible)
        metrics["pbo_details"] = pbo_result
        if pbo_result.get("enabled") and pbo_result.get("pbo") is not None:
            metrics["pbo"] = float(pbo_result["pbo"])
        else:
            reasons.append(
                "PBO unavailable: "
                + str(pbo_result.get("reason") or "no valid CSCV combinations")
            )

    required_metrics = (
        "observed_sharpe",
        "psr",
        "dsr",
        "pbo",
        "haircut",
    )
    if reasons or any(metrics.get(key) is None for key in required_metrics):
        if not reasons:
            missing = [key for key in required_metrics if metrics.get(key) is None]
            reasons.append("required metrics unavailable: " + ", ".join(missing))
        return _incomplete_result(
            reasons=reasons,
            warnings=warnings,
            metrics=metrics,
            evidence=evidence,
            thresholds=thresholds,
        )

    failed_checks: list[str] = []
    if float(metrics["psr"]) < psr_min:
        failed_checks.append(f"PSR {metrics['psr']:.4f} < {psr_min:.4f}")
    if float(metrics["dsr"]) < dsr_min:
        failed_checks.append(f"DSR {metrics['dsr']:.4f} < {dsr_min:.4f}")
    if float(metrics["pbo"]) > pbo_max:
        failed_checks.append(f"PBO {metrics['pbo']:.4f} > {pbo_max:.4f}")
    adjusted_psr = float(metrics["haircut"]["adjusted_psr"])
    if adjusted_psr < adjusted_psr_min:
        failed_checks.append(
            f"Bonferroni-adjusted PSR {adjusted_psr:.4f} < {adjusted_psr_min:.4f}"
        )
    min_trl_value = metrics["min_track_record_length"]
    if min_trl_value is None:
        # This is a substantive failed result (SR <= benchmark), not missing
        # evidence: no finite track-record extension can establish the claim.
        failed_checks.append(
            "observed Sharpe does not exceed benchmark; MinTRL is not finite"
        )
    elif len(selected) < math.ceil(float(min_trl_value)):
        failed_checks.append(
            f"track record {len(selected)} < MinTRL "
            f"{math.ceil(float(min_trl_value))} observations"
        )

    status = FAIL if failed_checks else PASS
    return {
        "version": AUDIT_VERSION,
        "status": status,
        "passes_gate": status == PASS,
        "metrics": metrics,
        "thresholds": thresholds,
        "evidence": evidence,
        "failed_checks": failed_checks,
        "reasons": [],
        "warnings": warnings,
    }
