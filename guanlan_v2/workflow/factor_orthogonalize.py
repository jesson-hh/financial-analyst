"""Reusable cross-sectional factor orthogonalization.

The existing :mod:`factor_neutralize` helper deliberately owns the narrow
``industry + market-cap`` report switch.  This module provides the more general
artifact contract needed by a workflow node: a candidate factor can be
residualized, independently on every date, against

* an optional point-in-time industry label;
* arbitrary continuous style controls; and
* factors already present in the factor library.

It is a pure function: it performs no data loading and writes no artifacts.  A
caller must therefore provide controls aligned to the factor's ``(date, code)``
MultiIndex.  Missing requested controls are handled with complete cases rather
than silently pretending the exposure was removed.  Dates with too few degrees
of freedom, a rank-deficient design, or an excessively ill-conditioned design
produce ``NaN`` residuals and a machine-readable diagnostic reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(slots=True)
class OrthogonalizationResult:
    """Output of :func:`orthogonalize_factor`.

    ``diagnostics`` has one row per date.  ``exposures`` is a long-form table
    whose ``correlation_before`` and ``correlation_after`` columns make exposure
    clearing auditable for every industry/style/existing-factor control.
    ``summary`` contains only JSON-friendly scalar values.
    """

    residual: pd.Series
    diagnostics: pd.DataFrame
    exposures: pd.DataFrame
    summary: dict[str, Any]


_DIAGNOSTIC_COLUMNS = [
    "status",
    "reason",
    "n_total",
    "n_factor_valid",
    "n_complete",
    "n_residual",
    "input_coverage",
    "output_coverage",
    "n_industries",
    "n_numeric_controls",
    "n_design_columns",
    "design_rank",
    "condition_number",
    "r_squared",
    "max_abs_exposure_before",
    "max_abs_exposure_after",
    "dropped_constant_controls",
]

_EXPOSURE_COLUMNS = [
    "date",
    "exposure",
    "kind",
    "used",
    "reason",
    "correlation_before",
    "correlation_after",
]


def _as_numeric_frame(
    value: pd.Series | pd.DataFrame | None,
    *,
    index: pd.MultiIndex,
    prefix: str,
    default_name: str,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Align a Series/DataFrame and namespace its columns.

    Namespacing prevents an existing factor called ``beta`` from being confused
    with a style control of the same name in diagnostics and provenance.
    """
    if value is None:
        return pd.DataFrame(index=index), {}
    if isinstance(value, pd.Series):
        raw_name = str(value.name) if value.name is not None else default_name
        frame = value.to_frame(raw_name)
    elif isinstance(value, pd.DataFrame):
        frame = value.copy()
    else:
        raise TypeError(f"{prefix} 必须是 pd.Series、pd.DataFrame 或 None")
    if not isinstance(frame.index, pd.MultiIndex):
        raise TypeError(f"{prefix} 必须使用与 factor 相同的 MultiIndex")
    if not frame.index.is_unique:
        raise ValueError(f"{prefix} 索引必须唯一")
    if frame.columns.has_duplicates:
        raise ValueError(f"{prefix} 列名不能重复")

    aligned = frame.reindex(index)
    output = pd.DataFrame(index=index)
    kinds: dict[str, str] = {}
    seen: set[str] = set()
    for column in aligned.columns:
        display = str(column)
        name = f"{prefix}:{display}"
        if name in seen:
            raise ValueError(f"{prefix} 列名转成字符串后重复: {display!r}")
        seen.add(name)
        output[name] = pd.to_numeric(aligned[column], errors="coerce")
        kinds[name] = "existing_factor" if prefix == "factor" else "continuous"
    return output, kinds


def _finite_correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    mask = np.isfinite(left) & np.isfinite(right)
    if int(mask.sum()) < 2:
        return None
    x = left[mask].astype(float)
    y = right[mask].astype(float)
    if np.ptp(x) <= np.finfo(float).eps or np.ptp(y) <= np.finfo(float).eps:
        return None
    value = float(np.corrcoef(x, y)[0, 1])
    return value if np.isfinite(value) else None


def _maximum_absolute(values: list[float | None]) -> float | None:
    finite = [abs(float(v)) for v in values if v is not None and np.isfinite(v)]
    return max(finite) if finite else None


def _make_summary(
    diagnostics: pd.DataFrame,
    exposures: pd.DataFrame,
) -> dict[str, Any]:
    if diagnostics.empty:
        return {
            "n_dates": 0,
            "n_ok_dates": 0,
            "n_passthrough_dates": 0,
            "n_skipped_dates": 0,
            "input_coverage": None,
            "output_coverage": None,
            "usable_fraction": None,
            "mean_condition_number": None,
            "max_condition_number": None,
            "max_abs_exposure_before": None,
            "max_abs_exposure_after": None,
        }

    n_total = int(diagnostics["n_total"].sum())
    n_input = int(diagnostics["n_factor_valid"].sum())
    n_output = int(diagnostics["n_residual"].sum())
    cond = pd.to_numeric(diagnostics["condition_number"], errors="coerce")
    cond = cond[np.isfinite(cond)]
    before = pd.to_numeric(exposures.get("correlation_before"), errors="coerce")
    after = pd.to_numeric(exposures.get("correlation_after"), errors="coerce")
    return {
        "n_dates": int(len(diagnostics)),
        "n_ok_dates": int((diagnostics["status"] == "ok").sum()),
        "n_passthrough_dates": int((diagnostics["status"] == "passthrough").sum()),
        "n_skipped_dates": int((diagnostics["status"] == "skipped").sum()),
        "input_coverage": float(n_input / n_total) if n_total else None,
        "output_coverage": float(n_output / n_total) if n_total else None,
        "usable_fraction": float(n_output / n_input) if n_input else None,
        "mean_condition_number": float(cond.mean()) if len(cond) else None,
        "max_condition_number": float(cond.max()) if len(cond) else None,
        "max_abs_exposure_before": (
            float(before.abs().max()) if before.notna().any() else None
        ),
        "max_abs_exposure_after": (
            float(after.abs().max()) if after.notna().any() else None
        ),
    }


def orthogonalize_factor(
    factor: pd.Series,
    *,
    industry: pd.Series | None = None,
    continuous_controls: pd.Series | pd.DataFrame | None = None,
    existing_factors: pd.Series | pd.DataFrame | None = None,
    min_obs: int = 5,
    min_residual_dof: int = 2,
    max_condition_number: float | None = 1.0e10,
    unknown_industry: object | None = "未知",
) -> OrthogonalizationResult:
    """Residualize ``factor`` against requested controls on each date.

    Parameters
    ----------
    factor:
        Numeric Series indexed by a unique ``(date, code)`` MultiIndex.  The
        first index level is treated as the cross-section date.
    industry:
        Optional point-in-time industry label per row.  If supplied, rows with a
        missing label or ``unknown_industry`` are excluded; the function never
        substitutes a current industry classification for history.
    continuous_controls:
        Optional numeric Series/DataFrame such as log market cap, beta,
        volatility, liquidity, or momentum.
    existing_factors:
        Optional Series/DataFrame of library factors whose span should be removed
        from the candidate.
    min_obs / min_residual_dof:
        A date is usable only when ``n_complete >= max(min_obs,
        n_design_columns + min_residual_dof)``.
    max_condition_number:
        Refuse an unstable design above this threshold.  ``None`` records the
        condition number without imposing a condition gate.

    Notes
    -----
    The design always contains an intercept.  Industry uses ``k-1`` dummies and
    continuous columns are z-scored within the date before OLS.  Constant numeric
    controls are in the intercept span, so they are recorded as dropped rather
    than creating artificial rank deficiency.  Other collinearity fails closed.
    """
    if not isinstance(factor, pd.Series):
        raise TypeError("factor 必须是 pd.Series")
    if not isinstance(factor.index, pd.MultiIndex) or factor.index.nlevels < 2:
        raise TypeError("factor 必须使用至少两层的 (date, code) MultiIndex")
    if not factor.index.is_unique:
        raise ValueError("factor 索引必须唯一")
    if not isinstance(min_obs, int) or min_obs < 1:
        raise ValueError("min_obs 必须是正整数")
    if not isinstance(min_residual_dof, int) or min_residual_dof < 1:
        raise ValueError("min_residual_dof 必须是正整数")
    if max_condition_number is not None:
        if not np.isfinite(max_condition_number) or max_condition_number <= 1:
            raise ValueError("max_condition_number 必须大于 1，或为 None")

    numeric_factor = pd.to_numeric(factor, errors="coerce").astype(float)
    invalid_factor = factor.notna() & numeric_factor.isna()
    if bool(invalid_factor.any()):
        raise ValueError("factor 含无法转换为数值的非空值")

    control_frame, control_kinds = _as_numeric_frame(
        continuous_controls,
        index=factor.index,
        prefix="control",
        default_name="control",
    )
    old_factor_frame, old_factor_kinds = _as_numeric_frame(
        existing_factors,
        index=factor.index,
        prefix="factor",
        default_name="existing",
    )
    numeric_frame = pd.concat([control_frame, old_factor_frame], axis=1)
    kinds = {**control_kinds, **old_factor_kinds}

    aligned_industry: pd.Series | None = None
    if industry is not None:
        if not isinstance(industry, pd.Series):
            raise TypeError("industry 必须是 pd.Series 或 None")
        if not isinstance(industry.index, pd.MultiIndex):
            raise TypeError("industry 必须使用与 factor 相同的 MultiIndex")
        if not industry.index.is_unique:
            raise ValueError("industry 索引必须唯一")
        aligned_industry = industry.reindex(factor.index)

    residual = pd.Series(np.nan, index=factor.index, dtype=float, name=factor.name)
    diagnostic_rows: list[dict[str, Any]] = []
    exposure_rows: list[dict[str, Any]] = []
    date_name = factor.index.names[0] or "date"
    has_requested_controls = aligned_industry is not None or not numeric_frame.empty

    for date, sub in numeric_factor.groupby(level=0, sort=False):
        idx = sub.index
        y = sub.to_numpy(dtype=float)
        n_total = len(sub)
        factor_valid = np.isfinite(y)
        n_factor_valid = int(factor_valid.sum())

        if not has_requested_controls:
            residual.loc[idx] = y
            diagnostic_rows.append({
                "date": date,
                "status": "passthrough",
                "reason": "no_controls",
                "n_total": n_total,
                "n_factor_valid": n_factor_valid,
                "n_complete": n_factor_valid,
                "n_residual": n_factor_valid,
                "input_coverage": n_factor_valid / n_total if n_total else 0.0,
                "output_coverage": n_factor_valid / n_total if n_total else 0.0,
                "n_industries": 0,
                "n_numeric_controls": 0,
                "n_design_columns": 0,
                "design_rank": 0,
                "condition_number": None,
                "r_squared": None,
                "max_abs_exposure_before": None,
                "max_abs_exposure_after": None,
                "dropped_constant_controls": (),
            })
            continue

        valid = factor_valid.copy()
        labels: np.ndarray | None = None
        industry_valid: np.ndarray | None = None
        if aligned_industry is not None:
            label_series = aligned_industry.reindex(idx)
            labels = label_series.to_numpy(dtype=object)
            # pandas 3 copy-on-write may expose ``Series.__array__`` as a
            # read-only view.  This mask is refined in place below, so request
            # an owned buffer explicitly instead of relying on NumPy's
            # historical writable-view behaviour.
            industry_valid = label_series.notna().to_numpy(
                dtype=bool, copy=True,
            )
            if unknown_industry is not None:
                # ``Series.eq(...).fillna(False)`` also handles ``pd.NA``;
                # evaluating ``pd.NA != sentinel`` directly has ambiguous truth.
                unknown = label_series.eq(unknown_industry).fillna(False).to_numpy(dtype=bool)
                industry_valid &= ~unknown
            valid &= industry_valid

        daily_numeric = numeric_frame.reindex(idx)
        numeric_values = daily_numeric.to_numpy(dtype=float)
        if numeric_values.shape[1]:
            valid &= np.isfinite(numeric_values).all(axis=1)

        complete_positions = np.flatnonzero(valid)
        n_complete = int(len(complete_positions))
        n_industries = 0
        dropped_constants: list[str] = []
        local_exposures: list[dict[str, Any]] = []

        def append_diagnostic(
            *,
            status: str,
            reason: str | None,
            n_design_columns: int,
            design_rank: int,
            condition_number: float | None,
            n_residual: int = 0,
            r_squared: float | None = None,
            before: float | None = None,
            after: float | None = None,
        ) -> None:
            diagnostic_rows.append({
                "date": date,
                "status": status,
                "reason": reason,
                "n_total": n_total,
                "n_factor_valid": n_factor_valid,
                "n_complete": n_complete,
                "n_residual": n_residual,
                "input_coverage": n_factor_valid / n_total if n_total else 0.0,
                "output_coverage": n_residual / n_total if n_total else 0.0,
                "n_industries": n_industries,
                "n_numeric_controls": int(numeric_frame.shape[1]),
                "n_design_columns": n_design_columns,
                "design_rank": design_rank,
                "condition_number": condition_number,
                "r_squared": r_squared,
                "max_abs_exposure_before": before,
                "max_abs_exposure_after": after,
                "dropped_constant_controls": tuple(dropped_constants),
            })

        if n_complete == 0:
            reason = "no_complete_cases"
            if aligned_industry is not None and industry_valid is not None and not industry_valid.any():
                reason = "industry_unavailable"
            append_diagnostic(
                status="skipped",
                reason=reason,
                n_design_columns=0,
                design_rank=0,
                condition_number=None,
            )
            continue

        y_valid = y[complete_positions]
        design_columns: list[np.ndarray] = [np.ones(n_complete, dtype=float)]

        if labels is not None:
            used_labels = labels[complete_positions]
            categories = sorted(pd.unique(used_labels), key=lambda item: str(item))
            n_industries = len(categories)
            # Intercept + k-1 dummies spans all k industry group means.
            for category in categories[1:]:
                design_columns.append((used_labels == category).astype(float))
            for category in categories:
                vector = (used_labels == category).astype(float)
                local_exposures.append({
                    "date": date,
                    "exposure": f"industry:{category}",
                    "kind": "industry",
                    "used": True,
                    "reason": None,
                    "correlation_before": _finite_correlation(y_valid, vector),
                    "correlation_after": None,
                    "_vector": vector,
                })

        scale_epsilon = np.sqrt(np.finfo(float).eps)
        for offset, name in enumerate(daily_numeric.columns):
            vector = numeric_values[complete_positions, offset].astype(float)
            mean = float(vector.mean())
            std = float(vector.std(ddof=0))
            tolerance = scale_epsilon * max(1.0, abs(mean))
            used = bool(np.isfinite(std) and std > tolerance)
            reason = None if used else "constant_control"
            if used:
                design_columns.append((vector - mean) / std)
            else:
                dropped_constants.append(name)
            local_exposures.append({
                "date": date,
                "exposure": name,
                "kind": kinds[name],
                "used": used,
                "reason": reason,
                "correlation_before": _finite_correlation(y_valid, vector),
                "correlation_after": None,
                "_vector": vector,
            })

        design = np.column_stack(design_columns)
        n_columns = int(design.shape[1])
        rank = int(np.linalg.matrix_rank(design))
        raw_condition = float(np.linalg.cond(design))
        condition = raw_condition if np.isfinite(raw_condition) else None
        before_max = _maximum_absolute(
            [row["correlation_before"] for row in local_exposures if row["used"]]
        )

        required = max(min_obs, n_columns + min_residual_dof)
        skip_reason: str | None = None
        if n_complete < required:
            skip_reason = f"insufficient_observations:n={n_complete},required={required}"
        elif rank < n_columns:
            skip_reason = f"rank_deficient:rank={rank},columns={n_columns}"
        elif condition is None:
            skip_reason = "condition_number_non_finite"
        elif max_condition_number is not None and condition > max_condition_number:
            skip_reason = (
                f"ill_conditioned:condition={condition:.6g},"
                f"limit={max_condition_number:.6g}"
            )

        if skip_reason is not None:
            exposure_rows.extend(
                [{key: value for key, value in row.items() if key != "_vector"}
                 for row in local_exposures]
            )
            append_diagnostic(
                status="skipped",
                reason=skip_reason,
                n_design_columns=n_columns,
                design_rank=rank,
                condition_number=condition,
                before=before_max,
            )
            continue

        beta, *_ = np.linalg.lstsq(design, y_valid, rcond=None)
        fitted = design @ beta
        resid = y_valid - fitted
        residual.loc[idx[complete_positions]] = resid

        for row in local_exposures:
            row["correlation_after"] = _finite_correlation(resid, row["_vector"])
            exposure_rows.append({key: value for key, value in row.items() if key != "_vector"})
        after_max = _maximum_absolute(
            [row["correlation_after"] for row in local_exposures if row["used"]]
        )
        centered = y_valid - y_valid.mean()
        total_ss = float(centered @ centered)
        residual_ss = float(resid @ resid)
        r_squared = None if total_ss <= np.finfo(float).eps else 1.0 - residual_ss / total_ss
        append_diagnostic(
            status="ok",
            reason=None,
            n_design_columns=n_columns,
            design_rank=rank,
            condition_number=condition,
            n_residual=n_complete,
            r_squared=float(r_squared) if r_squared is not None else None,
            before=before_max,
            after=after_max,
        )

    diagnostics = pd.DataFrame(diagnostic_rows)
    if diagnostics.empty:
        diagnostics = pd.DataFrame(columns=["date", *_DIAGNOSTIC_COLUMNS])
    diagnostics = diagnostics.set_index("date")
    diagnostics.index.name = date_name
    diagnostics = diagnostics[_DIAGNOSTIC_COLUMNS]

    exposures = pd.DataFrame(exposure_rows, columns=_EXPOSURE_COLUMNS)
    summary = _make_summary(diagnostics, exposures)
    return OrthogonalizationResult(
        residual=residual,
        diagnostics=diagnostics,
        exposures=exposures,
        summary=summary,
    )


__all__ = ["OrthogonalizationResult", "orthogonalize_factor"]
