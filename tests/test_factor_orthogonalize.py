"""Tests for the reusable, workflow-level factor orthogonalizer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.factor_orthogonalize import orthogonalize_factor


def _index(dates: list[str], codes: list[str]) -> pd.MultiIndex:
    return pd.MultiIndex.from_product(
        [pd.to_datetime(dates), codes], names=["datetime", "code"]
    )


def test_joint_industry_style_and_existing_factor_exposures_are_removed():
    idx = _index(["2024-01-02"], list("abcdefghijkl"))
    industry = pd.Series(
        ["银行"] * 4 + ["电子"] * 4 + ["医药"] * 4, index=idx, dtype=object
    )
    size = np.array([1.0, 2.2, 3.1, 4.4, 1.3, 2.8, 3.7, 5.2, 1.8, 2.5, 4.1, 5.7])
    beta = np.array([0.7, 1.2, 0.9, 1.5, 1.1, 0.6, 1.4, 0.8, 1.6, 1.0, 0.5, 1.3])
    old = np.array([-1.3, 0.1, 1.2, -0.4, 0.8, -1.0, 0.4, 1.5, -0.7, 1.0, -1.6, 0.3])
    effect = np.array([3.0] * 4 + [-2.0] * 4 + [0.5] * 4)
    noise = np.array([0.13, -0.17, 0.08, -0.04, -0.11, 0.19, -0.07, 0.03,
                      0.16, -0.14, 0.06, -0.09])
    factor = pd.Series(2.0 * size - 1.4 * beta + 2.7 * old + effect + noise,
                       index=idx, name="candidate")
    styles = pd.DataFrame({"log_mktcap": size, "beta": beta}, index=idx)
    library = pd.DataFrame({"quality": old}, index=idx)

    result = orthogonalize_factor(
        factor,
        industry=industry,
        continuous_controls=styles,
        existing_factors=library,
    )

    assert result.residual.index.equals(factor.index)
    assert result.residual.name == "candidate"
    assert result.residual.notna().all()
    daily = result.diagnostics.iloc[0]
    assert daily["status"] == "ok"
    assert daily["n_design_columns"] == 6  # intercept + 2 industry + 3 numeric
    assert daily["design_rank"] == 6
    assert daily["condition_number"] > 0
    assert daily["output_coverage"] == pytest.approx(1.0)
    assert daily["max_abs_exposure_after"] < 1e-10
    used = result.exposures[result.exposures["used"]]
    assert set(used["kind"]) == {"industry", "continuous", "existing_factor"}
    assert used["correlation_after"].abs().max() < 1e-10
    assert result.summary["n_ok_dates"] == 1
    assert result.summary["output_coverage"] == pytest.approx(1.0)


def test_dates_are_independent_and_insufficient_date_fails_closed():
    idx = _index(["2024-01-02", "2024-01-03"], list("abcdefgh"))
    values = np.array([1.0, 1.8, 3.1, 4.2, 5.4, 6.3, 7.7, 8.1] * 2)
    factor = pd.Series(values + np.linspace(-0.2, 0.2, len(idx)), index=idx)
    control = pd.Series(values, index=idx, name="size")
    # The second date has only three usable candidate values.
    factor.loc[(pd.Timestamp("2024-01-03"), list("defgh"))] = np.nan

    result = orthogonalize_factor(factor, continuous_controls=control, min_obs=5)

    first = result.residual.xs(pd.Timestamp("2024-01-02"))
    second = result.residual.xs(pd.Timestamp("2024-01-03"))
    assert first.notna().all()
    assert second.isna().all()
    assert result.diagnostics.iloc[0]["status"] == "ok"
    assert result.diagnostics.iloc[1]["status"] == "skipped"
    assert result.diagnostics.iloc[1]["reason"].startswith("insufficient_observations")
    assert result.summary["n_ok_dates"] == 1
    assert result.summary["n_skipped_dates"] == 1


def test_duplicate_control_span_is_reported_as_rank_deficient():
    idx = _index(["2024-01-02"], list("abcdefgh"))
    base = np.arange(1.0, 9.0)
    factor = pd.Series(base + np.array([0.1, -0.1] * 4), index=idx)
    controls = pd.DataFrame({"size": base, "same_size": 2.0 * base}, index=idx)

    result = orthogonalize_factor(factor, continuous_controls=controls)

    daily = result.diagnostics.iloc[0]
    assert daily["status"] == "skipped"
    assert daily["reason"] == "rank_deficient:rank=2,columns=3"
    assert daily["design_rank"] < daily["n_design_columns"]
    assert result.residual.isna().all()
    assert result.exposures["correlation_after"].isna().all()


def test_unknown_industry_rows_are_excluded_and_coverage_is_honest():
    idx = _index(["2024-01-02"], list("abcdefgh"))
    industry = pd.Series(
        ["银行", "银行", "银行", "电子", "电子", "电子", "未知", pd.NA],
        index=idx,
        dtype=object,
    )
    factor = pd.Series([3.2, 2.8, 3.0, 0.8, 1.2, 1.0, 9.0, -9.0], index=idx)

    result = orthogonalize_factor(factor, industry=industry)

    assert result.residual.iloc[:6].notna().all()
    assert result.residual.iloc[6:].isna().all()
    daily = result.diagnostics.iloc[0]
    assert daily["n_complete"] == 6
    assert daily["input_coverage"] == pytest.approx(1.0)
    assert daily["output_coverage"] == pytest.approx(0.75)
    assert result.summary["usable_fraction"] == pytest.approx(0.75)
    for label in ("银行", "电子"):
        group = result.residual[industry == label]
        assert abs(group.mean()) < 1e-12


def test_industry_mask_does_not_mutate_a_pandas_read_only_array(monkeypatch):
    """pandas 3 copy-on-write can return a read-only ``Series.__array__``."""
    idx = _index(["2024-01-02"], list("abcdefgh"))
    industry = pd.Series(
        ["银行", "银行", "银行", "电子", "电子", "电子", "未知", pd.NA],
        index=idx,
        dtype=object,
    )
    factor = pd.Series([3.2, 2.8, 3.0, 0.8, 1.2, 1.0, 9.0, -9.0], index=idx)
    original_array = pd.Series.__array__

    def readonly_array(series, dtype=None, *args, **kwargs):
        try:
            values = original_array(series, dtype=dtype, *args, **kwargs)
        except TypeError:  # pandas < 3 has no ``copy`` argument here.
            values = original_array(series, dtype=dtype)
        frozen = np.array(values, copy=True)
        frozen.setflags(write=False)
        return frozen

    monkeypatch.setattr(pd.Series, "__array__", readonly_array)

    result = orthogonalize_factor(factor, industry=industry)

    assert result.diagnostics.iloc[0]["status"] == "ok"
    assert result.diagnostics.iloc[0]["n_complete"] == 6
    assert result.residual.iloc[:6].notna().all()
    assert result.residual.iloc[6:].isna().all()


def test_requested_but_unavailable_control_does_not_silently_fallback():
    idx = _index(["2024-01-02"], list("abcdef"))
    factor = pd.Series(np.arange(6.0), index=idx)
    missing = pd.Series(np.nan, index=idx, name="beta")

    result = orthogonalize_factor(factor, continuous_controls=missing)

    assert result.residual.isna().all()
    assert result.diagnostics.iloc[0]["status"] == "skipped"
    assert result.diagnostics.iloc[0]["reason"] == "no_complete_cases"
    assert result.summary["output_coverage"] == 0.0


def test_no_controls_is_explicit_passthrough_not_fake_orthogonalization():
    idx = _index(["2024-01-02"], list("abcdef"))
    factor = pd.Series([1.0, np.nan, 2.0, 3.0, 4.0, 5.0], index=idx, name="raw")

    result = orthogonalize_factor(factor)

    pd.testing.assert_series_equal(result.residual, factor)
    assert result.diagnostics.iloc[0]["status"] == "passthrough"
    assert result.diagnostics.iloc[0]["reason"] == "no_controls"
    assert result.exposures.empty
    assert result.summary["n_passthrough_dates"] == 1


def test_invalid_contracts_are_rejected_early():
    with pytest.raises(TypeError, match="MultiIndex"):
        orthogonalize_factor(pd.Series([1.0, 2.0]))

    idx = _index(["2024-01-02"], ["a", "b"])
    with pytest.raises(ValueError, match="无法转换"):
        orthogonalize_factor(pd.Series([1.0, "bad"], index=idx),
                             continuous_controls=pd.Series([1.0, 2.0], index=idx))
    with pytest.raises(ValueError, match="大于 1"):
        orthogonalize_factor(pd.Series([1.0, 2.0], index=idx),
                             continuous_controls=pd.Series([1.0, 2.0], index=idx),
                             max_condition_number=1.0)
