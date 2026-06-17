"""SP-D factor matrix builder.

Turns a list of member factors — each a registered alpha *name* or a whitelisted
factor *expression* — into a single ``(datetime, code) x factor`` DataFrame whose
columns are the member strings. Each member is computed on the panel, then per-date
cross-sectionally winsorized (q=0.01) and zscored (reusing SP-A preprocess) so the
columns share a common scale before any downstream combination.

Members that fail to resolve, compile, or compute are skipped (best-effort): the
returned name list reflects only the members that actually produced a column.
"""
from __future__ import annotations

import pandas as pd

from financial_analyst.factors.eval.preprocess import winsorize, zscore
from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
from financial_analyst.factors.zoo.registry import get as reg_get


def build_factor_matrix(panel, members: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Compute, preprocess, and stack member factors into a matrix.

    Parameters
    ----------
    panel : PanelData
        Quote panel (MultiIndex ``(datetime, code)``) the members compute on.
    members : list[str]
        Each entry is either a registered alpha name (resolved via the zoo
        registry) or a whitelisted factor expression (validated + compiled).

    Returns
    -------
    (matrix, names) : tuple[pd.DataFrame, list[str]]
        ``matrix`` is a ``(datetime, code) x factor`` DataFrame whose columns are
        the member strings that succeeded, each column per-date winsorized then
        zscored. ``names`` is the corresponding ordered list of member strings.
        If no member succeeds, returns ``(pd.DataFrame(), [])``.
    """
    series_list: list[pd.Series] = []
    names: list[str] = []

    for member in members:
        # Resolve the member into a compute fn: registered alpha name first,
        # then fall back to treating it as a whitelisted expression.
        try:
            compute = reg_get(member).compute
        except KeyError:
            try:
                validate_expr(member)
                compute = compile_factor(member)
            except Exception:
                # Unknown name and not a valid/compilable expression → skip.
                continue

        # Compute, then per-date winsorize + zscore. Any failure → skip member.
        try:
            raw = compute(panel)
            s = zscore(winsorize(raw, q=0.01))
        except Exception:
            continue

        series_list.append(s)
        names.append(member)

    if not series_list:
        return pd.DataFrame(), []

    matrix = pd.concat(series_list, axis=1, keys=names)
    return matrix, names
