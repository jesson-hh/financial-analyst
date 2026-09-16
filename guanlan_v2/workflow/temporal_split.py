"""Label-maturity splits on the observation calendar, never rebalance counts."""
from __future__ import annotations


def label_horizon(body):
    """Only the built-in forward-return label has an audited endpoint contract."""
    label = str(getattr(body, "label", None) or "").strip().lower()
    if label not in {"", "ic", "fwd_ret", "fwd", "forward", "forward_return", "return", "ret"}:
        raise ValueError("自定义标签尚无可验证的未来跨度；请使用默认前向收益标签后运行样本外训练")
    return max(1, int(getattr(body, "fwd_days", 5) or 5))


def purged_dates(calendar, candidates, test_dates, horizon, observation_index=None):
    import pandas as pd

    days = pd.DatetimeIndex(calendar).drop_duplicates().sort_values()
    test = pd.DatetimeIndex(test_dates).sort_values()
    if test.empty:
        return set(), {"purged_dates": len(candidates), "label_horizon": int(horizon)}
    boundary = test[0]
    actual_ends = None
    if observation_index is not None:
        observed = pd.Series(observation_index.get_level_values("datetime"), index=observation_index).sort_index()
        actual_ends = observed.groupby(level="code").shift(-int(horizon)).groupby(level="datetime").max()
    kept, endpoints = [], []
    for value in sorted(candidates):
        value = pd.Timestamp(value)
        pos = days.get_indexer([value])[0]
        end = pos + int(horizon)
        endpoint = (actual_ends.get(value, pd.NaT) if actual_ends is not None
                    else (days[end] if pos >= 0 and end < len(days) else pd.NaT))
        if pd.notna(endpoint) and endpoint < boundary:
            kept.append(value)
            endpoints.append(endpoint)
    return set(kept), {
        "policy": "label_endpoint_strictly_before_test",
        "endpoint_source": "per_code_observations" if actual_ends is not None else "daily_calendar",
        "label_horizon": int(horizon),
        "purged_dates": len(candidates) - len(kept),
        "train_end": str(max(kept).date()) if kept else None,
        "train_label_end": str(max(endpoints).date()) if endpoints else None,
        "test_start": str(boundary.date()),
    }


def next_open_period_returns(panel, rebalance):
    """Close-time signal, next-session open entry, next rebalance open exit.

    Missing quotes remain missing (no padding, survivor filtering, or assumed
    zero return). The final unclosed holding period is not a realised return.
    """
    import pandas as pd

    days = pd.DatetimeIndex(panel.dates()).sort_values()
    prices = panel.df["open"].unstack("code").reindex(days)
    rebs = sorted(pd.DatetimeIndex(rebalance))
    parts = []
    for signal, next_signal in zip(rebs[:-1], rebs[1:]):
        entry, leave = days.get_indexer([signal, next_signal]) + 1
        if min(entry, leave) <= 0 or leave >= len(days):
            continue
        start_price, end_price = prices.iloc[entry], prices.iloc[leave]
        returns = (end_price / start_price - 1).where((start_price > 0) & (end_price > 0))
        returns.index = pd.MultiIndex.from_product([[signal], returns.index], names=["datetime", "code"])
        parts.append(returns)
    return pd.concat(parts) if parts else pd.Series(dtype=float)
