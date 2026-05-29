"""Tests for cross-sectional composite combiners (SP-D combine.py).

OOS discipline is the critical invariant: the composite Series has values ONLY
on test rows; train rows are always NaN. Weights are fitted on TRAIN rows only
(no test leakage).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.compose.combine import combine


# ---------------------------------------------------------------------------
# Synthetic panel fixture: 40 dates x 5 codes, 3 factor columns.
# ---------------------------------------------------------------------------
N_DATES = 40
CODES = ["A", "B", "C", "D", "E"]
FACTORS = ["f1", "f2", "f3"]


def _make_index() -> pd.MultiIndex:
    dates = pd.date_range("2020-01-01", periods=N_DATES, freq="D")
    return pd.MultiIndex.from_product([dates, CODES], names=["datetime", "code"])


def _make_matrix(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = _make_index()
    data = rng.standard_normal((len(idx), len(FACTORS)))
    return pd.DataFrame(data, index=idx, columns=FACTORS)


def _split_masks(idx: pd.MultiIndex, train_frac: float = 0.6):
    """First train_frac of *dates* -> train True, rest -> test True."""
    dates = idx.get_level_values("datetime").unique().sort_values()
    n_train = int(np.floor(len(dates) * train_frac))
    train_dates = set(dates[:n_train])
    dt = idx.get_level_values("datetime")
    train_mask = pd.Series(dt.isin(train_dates), index=idx)
    test_mask = ~train_mask
    return train_mask, test_mask


ALL_METHODS = ["equal", "ic_weighted", "linear", "lgbm"]


# ---------------------------------------------------------------------------
# Shape / OOS-discipline invariants for ALL methods.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ALL_METHODS)
def test_oos_discipline_and_weight_keys(method):
    matrix = _make_matrix(seed=1)
    fwd = pd.Series(
        np.random.default_rng(2).standard_normal(len(matrix)),
        index=matrix.index,
        name="fwd",
    )
    train_mask, test_mask = _split_masks(matrix.index)

    composite, weights = combine(matrix, fwd, method, train_mask, test_mask)

    # composite is a Series aligned to matrix.index
    assert isinstance(composite, pd.Series)
    assert composite.index.equals(matrix.index)

    # OOS discipline: train rows ALL NaN, test rows have at least some values.
    assert composite[train_mask].isna().all(), f"{method}: train rows must be NaN"
    assert composite[test_mask].notna().any(), f"{method}: test rows must have values"

    # weights dict keys == matrix columns
    assert isinstance(weights, dict)
    assert set(weights.keys()) == set(matrix.columns), f"{method}: weight keys mismatch"


# ---------------------------------------------------------------------------
# equal: weights are 1/n; composite on test = row mean of matrix.
# ---------------------------------------------------------------------------
def test_equal_weights_and_values():
    matrix = _make_matrix(seed=3)
    fwd = pd.Series(0.0, index=matrix.index)
    train_mask, test_mask = _split_masks(matrix.index)

    composite, weights = combine(matrix, fwd, "equal", train_mask, test_mask)

    n = matrix.shape[1]
    for col in matrix.columns:
        assert weights[col] == pytest.approx(1.0 / n)

    # composite on test rows equals the row-mean of the matrix.
    expected = matrix.mean(axis=1)
    pd.testing.assert_series_equal(
        composite[test_mask],
        expected[test_mask],
        check_names=False,
    )


# ---------------------------------------------------------------------------
# linear: fwd = 2*f1 - 1*f2 + noise on TRAIN -> coefs ~ [2, -1].
# ---------------------------------------------------------------------------
def test_linear_recovers_coefficients():
    matrix = _make_matrix(seed=4)
    rng = np.random.default_rng(5)
    noise = rng.standard_normal(len(matrix)) * 0.01
    fwd = 2.0 * matrix["f1"] - 1.0 * matrix["f2"] + 0.0 * matrix["f3"] + noise
    fwd.name = "fwd"
    train_mask, test_mask = _split_masks(matrix.index)

    composite, weights = combine(matrix, fwd, "linear", train_mask, test_mask)

    # intercept must be excluded from weights dict
    assert set(weights.keys()) == set(matrix.columns)

    assert weights["f1"] == pytest.approx(2.0, abs=0.15)
    assert weights["f2"] == pytest.approx(-1.0, abs=0.15)
    assert weights["f3"] == pytest.approx(0.0, abs=0.15)

    # OOS still holds
    assert composite[train_mask].isna().all()
    assert composite[test_mask].notna().any()


# ---------------------------------------------------------------------------
# ic_weighted: a column strongly +correlated with fwd on TRAIN gets the
# largest weight.
# ---------------------------------------------------------------------------
def test_ic_weighted_strong_column_gets_largest_weight():
    matrix = _make_matrix(seed=6)
    rng = np.random.default_rng(7)
    # f1 is essentially fwd (strong +corr); f2/f3 are independent noise.
    fwd = matrix["f1"] + rng.standard_normal(len(matrix)) * 0.05
    fwd.name = "fwd"
    train_mask, test_mask = _split_masks(matrix.index)

    composite, weights = combine(matrix, fwd, "ic_weighted", train_mask, test_mask)

    assert weights["f1"] > weights["f2"]
    assert weights["f1"] > weights["f3"]
    # sign is preserved and strongly positive
    assert weights["f1"] > 0.5


# ---------------------------------------------------------------------------
# OOS no-leakage: a column strong on TRAIN but reversed on TEST still gets its
# weight from TRAIN (positive), proving weights ignore test rows.
# ---------------------------------------------------------------------------
def test_ic_weighted_no_test_leakage():
    idx = _make_index()
    rng = np.random.default_rng(8)
    train_mask, test_mask = _split_masks(idx)

    # Build f1 = +fwd on train, -fwd on test (relationship flips out-of-sample).
    fwd = pd.Series(rng.standard_normal(len(idx)), index=idx, name="fwd")
    f1 = fwd.copy()
    f1[test_mask] = -fwd[test_mask] * 3.0  # strongly reversed (and amplified) on test
    f2 = pd.Series(rng.standard_normal(len(idx)), index=idx)
    f3 = pd.Series(rng.standard_normal(len(idx)), index=idx)
    matrix = pd.concat([f1, f2, f3], axis=1)
    matrix.columns = FACTORS

    composite, weights = combine(matrix, fwd, "ic_weighted", train_mask, test_mask)

    # Weight for f1 reflects the TRAIN relationship (positive), NOT the reversed
    # test relationship (which would be strongly negative).
    assert weights["f1"] > 0, "f1 weight must come from TRAIN (positive), not TEST"
    assert weights["f1"] > weights["f2"]
    assert weights["f1"] > weights["f3"]


# ---------------------------------------------------------------------------
# lgbm: just assert it runs and produces test-row output.
# ---------------------------------------------------------------------------
def test_lgbm_runs_and_predicts():
    matrix = _make_matrix(seed=9)
    rng = np.random.default_rng(10)
    fwd = matrix["f1"] - 0.5 * matrix["f2"] + rng.standard_normal(len(matrix)) * 0.1
    fwd.name = "fwd"
    train_mask, test_mask = _split_masks(matrix.index)

    composite, weights = combine(matrix, fwd, "lgbm", train_mask, test_mask)

    assert composite[train_mask].isna().all()
    assert composite[test_mask].notna().any()
    assert set(weights.keys()) == set(matrix.columns)
    # feature importances are normalized -> non-negative and sum ~ 1 (or all 0).
    vals = np.array(list(weights.values()), dtype=float)
    assert (vals >= -1e-9).all()
    s = vals.sum()
    assert s == pytest.approx(1.0, abs=1e-6) or s == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# dispatcher: unknown method -> ValueError.
# ---------------------------------------------------------------------------
def test_unknown_method_raises():
    matrix = _make_matrix(seed=11)
    fwd = pd.Series(0.0, index=matrix.index)
    train_mask, test_mask = _split_masks(matrix.index)
    with pytest.raises(ValueError):
        combine(matrix, fwd, "not_a_method", train_mask, test_mask)
