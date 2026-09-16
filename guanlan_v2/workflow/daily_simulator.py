"""Causal daily *research* portfolio ledger, independent of the vector backtest.

The supplied selector must itself be causal.  This module freezes its top-N at
the signal day's close, then attempts those exact orders at the next supplied
calendar day's open.  It never joins selection to future returns or fills an
unavailable entry with a lower-ranked name.  Fractional shares and percentage
fees are deliberate research approximations, not exchange execution simulation.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


def _daily_index(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.MultiIndex):
        raise ValueError(f"{label} requires a (datetime, instrument) MultiIndex DataFrame")
    if frame.index.nlevels != 2 or frame.index.names[0] != "datetime":
        raise ValueError(f"{label} index must be ordered (datetime, instrument/code)")
    if frame.index.names[1] not in ("instrument", "code"):
        raise ValueError(f"{label} second index level must be instrument or code")
    dates = pd.DatetimeIndex(pd.to_datetime(frame.index.get_level_values(0)))
    if dates.tz is not None or dates.isna().any() or not dates.equals(dates.normalize()):
        raise ValueError(f"{label} requires timezone-naive, midnight daily dates")
    codes = frame.index.get_level_values(1)
    if any(pd.isna(code) or not str(code).strip() for code in codes):
        raise ValueError(f"{label} has an empty instrument")
    result = frame.copy(deep=False)
    result.index = pd.MultiIndex.from_arrays(
        [dates, [str(code) for code in codes]], names=["datetime", "instrument"],
    )
    if result.index.has_duplicates:
        raise ValueError(f"{label} has duplicate daily instrument rows")
    if result.columns.has_duplicates:
        raise ValueError(f"{label} has duplicate columns")
    return result.sort_index()


def _positive(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _boolean(value: Any, label: str) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{label} must contain boolean/0/1 values (missing means false)")


def _metrics(returns: list[float], navs: list[float], turns: list[float]) -> dict[str, Any]:
    n = len(returns)
    total_return = navs[-1] - 1.0 if n else 0.0
    try:
        ann_return = math.expm1(math.log(navs[-1]) * 252.0 / n) if n and navs[-1] > 0 else (-1.0 if n else 0.0)
    except OverflowError:
        ann_return = None
    sigma = float(np.std(returns, ddof=1)) if n > 1 else 0.0
    sharpe = float(np.mean(returns)) / sigma * math.sqrt(252) if sigma > 1e-15 else None
    peak = 1.0
    max_drawdown = 0.0
    for nav in navs:
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    return {
        "n_periods": n,
        "total_return": total_return,
        "ann_return": ann_return,
        "volatility": sigma * math.sqrt(252),
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": ann_return / abs(max_drawdown) if ann_return is not None and max_drawdown < -1e-15 else None,
        "turnover": float(np.mean(turns)) if turns else 0.0,
        "daily_win_rate": sum(value > 0 for value in returns) / n if n else None,
        "daily_win_rate_definition": "positive close-to-close account returns / all supplied calendar days, including cash days; not trade win rate",
    }


def simulate_daily_portfolio(
    panel: pd.DataFrame,
    signals: pd.DataFrame,
    calendar: Iterable[Any],
    *,
    top_n: int = 10,
    initial_cash: float = 1_000_000.0,
    commission: float = 0.0003,
    stamp_tax: float = 0.0005,
    slippage_bps: float = 5.0,
    tradability_provenance: str | None = None,
    signal_valid: Mapping[Any, Any] | pd.Series | None = None,
) -> dict[str, Any]:
    """Return JSON-safe NAV, executions, selections, and data-quality evidence.

    ``signals`` requires ``score`` and ``eligible``.  Higher score ranks first;
    ties use instrument ascending.  Missing/nonfinite scores are not eligible.
    ``calendar`` must explicitly contain every evaluation trading day, in order.
    Its first day is cash; warmup data outside it cannot create an initial order.
    ``signal_valid`` marks complete/usable selector dates.  An invalid or absent
    selector date retains holdings without rebalancing, not a clear-to-cash
    instruction.  With no explicit map, nonempty selector rows imply validity;
    rows with all ``eligible=False`` are a genuine empty selection.  An explicit
    True can declare a row-less selector genuinely empty.

    Panel ``open``/``close`` must use the same adjusted-price basis.  Optional
    ``tradable``/``buyable``/``sellable`` flags are accepted only with a provenance
    statement asserting that they were available at the opening execution time.
    We do not infer opening tradability from final daily volume, high, or low.

    Each selected name targets 1/top_n of pre-trade opening NAV, even when there
    are fewer candidates.  Blocked holdings remain; available buy budgets are
    scaled proportionally if cash cannot fund them.  The final day is marked to
    market, never forcibly liquidated.  Missing close marks are explicitly stale.
    """
    if isinstance(top_n, bool) or not isinstance(top_n, (int, np.integer)) or not 1 <= top_n <= 1000:
        raise ValueError("top_n must be an integer from 1 to 1000")
    initial_cash = _positive(initial_cash)
    if initial_cash is None:
        raise ValueError("initial_cash must be positive and finite")
    for label, value in (("commission", commission), ("stamp_tax", stamp_tax), ("slippage_bps", slippage_bps)):
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} must be finite and nonnegative")
    if commission + stamp_tax >= 1 or slippage_bps >= 10_000:
        raise ValueError("cost assumptions must leave positive sale proceeds")
    commission, stamp_tax, slip = float(commission), float(stamp_tax), float(slippage_bps) / 10_000.0
    dates = pd.DatetimeIndex(pd.to_datetime(list(calendar)))
    if dates.tz is not None or dates.isna().any() or not dates.equals(dates.normalize()):
        raise ValueError("calendar requires timezone-naive, midnight daily dates")
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("calendar must be strictly increasing without duplicate dates")
    quotes = _daily_index(panel, "panel")
    selectors = _daily_index(signals, "signals")
    if not {"open", "close"}.issubset(quotes.columns):
        raise ValueError("panel requires open and close columns")
    if not {"score", "eligible"}.issubset(selectors.columns):
        raise ValueError("signals requires score and eligible columns")
    mask_columns = [key for key in ("tradable", "buyable", "sellable") if key in quotes.columns]
    if mask_columns and not str(tradability_provenance or "").strip():
        raise ValueError("opening-time tradability_provenance is required when execution masks are supplied")

    # Slice before converting to records: unused warmup/future rows never supply
    # a mark, selector, or opening order to this evaluation.
    quotes = quotes.loc[quotes.index.get_level_values(0).isin(dates), ["open", "close", *mask_columns]]
    selectors = selectors.loc[selectors.index.get_level_values(0).isin(dates), ["score", "eligible"]]
    quote_days = {date: group.droplevel(0).to_dict("index") for date, group in quotes.groupby(level=0, sort=False)}
    signal_days = {date: group.droplevel(0).to_dict("index") for date, group in selectors.groupby(level=0, sort=False)}
    validity = None
    if signal_valid is not None:
        if not isinstance(signal_valid, (Mapping, pd.Series)):
            raise ValueError("signal_valid must be a date-to-boolean mapping or Series")
        if isinstance(signal_valid, pd.Series) and signal_valid.index.has_duplicates:
            raise ValueError("signal_valid has duplicate dates")
        validity = {}
        for raw_date, value in signal_valid.items():
            valid_date = pd.Timestamp(raw_date)
            if pd.isna(valid_date) or valid_date.tz is not None or valid_date != valid_date.normalize():
                raise ValueError("signal_valid requires timezone-naive daily dates")
            if valid_date in validity:
                raise ValueError("signal_valid has duplicate normalized dates")
            validity[valid_date] = _boolean(value, "signal_valid")
    cash = float(initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    last_close: dict[str, tuple[float, str, int]] = {}
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    closed_positions: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    pending_date: str | None = None
    pending_valid = False
    previous_nav = float(initial_cash)
    total_commission = total_tax = total_slippage = 0.0
    missing_entries = blocked_sales = stale_position_days = cash_limited_days = 0
    no_quote_days = no_signal_days = nonfinite_scores = 0
    skipped_rebalance_days = 0

    for day_index, day in enumerate(dates):
        date = str(day.date())
        rows = quote_days.get(day, {})
        if not rows:
            no_quote_days += 1

        def price(code: str, key: str) -> float | None:
            return _positive(rows.get(code, {}).get(key))

        def executable(code: str, side: str) -> tuple[bool, str | None]:
            if price(code, "open") is None:
                return False, "missing_or_invalid_open"
            row = rows.get(code, {})
            for key in ("tradable", "buyable" if side == "buy" else "sellable"):
                if key in mask_columns and not _boolean(row.get(key), f"panel.{key}"):
                    return False, f"{key}_false_or_unknown"
            return True, None

        def opening_mark(code: str) -> float:
            return price(code, "open") or last_close.get(code, (positions[code]["fallback_mark"], "", 0))[0]

        opening_nav = cash + sum(pos["quantity"] * opening_mark(code) for code, pos in positions.items())
        desired_codes = {item["instrument"] for item in pending} if pending_valid else set(positions)
        target_value = opening_nav / int(top_n)
        blocked_orders: list[dict[str, Any]] = []
        executed_mid_notional = day_commission = day_tax = day_slippage = 0.0
        day_start_trade_count = len(trades)

        def execute(code: str, side: str, quantity: float, reason: str) -> None:
            nonlocal cash, executed_mid_notional, day_commission, day_tax, day_slippage
            mid = price(code, "open")
            assert mid is not None
            execution = mid * (1.0 + slip if side == "buy" else 1.0 - slip)
            notional = quantity * execution
            fee = notional * commission
            tax = notional * stamp_tax if side == "sell" else 0.0
            impact = abs(execution - mid) * quantity
            realized_pnl = None
            if side == "buy":
                outlay = notional + fee
                cash -= outlay
                pos = positions.setdefault(code, {
                    "quantity": 0.0, "cost_basis": 0.0, "episode_buy_cost": 0.0,
                    "episode_realized_pnl": 0.0, "entry_date": date,
                    "fallback_mark": mid, "fallback_date": date, "fallback_day_index": day_index,
                })
                pos["quantity"] += quantity
                pos["cost_basis"] += outlay
                pos["episode_buy_cost"] += outlay
            else:
                pos = positions[code]
                allocated_basis = pos["cost_basis"] * quantity / pos["quantity"]
                proceeds = notional - fee - tax
                cash += proceeds
                realized_pnl = proceeds - allocated_basis
                pos["episode_realized_pnl"] += realized_pnl
                pos["quantity"] -= quantity
                pos["cost_basis"] -= allocated_basis
                if pos["quantity"] <= 1e-12:
                    closed_positions.append({
                        "instrument": code, "entry_date": pos["entry_date"], "exit_date": date,
                        "realized_pnl": pos["episode_realized_pnl"],
                        "total_buy_cost": pos["episode_buy_cost"],
                        "pnl_over_buy_cost": pos["episode_realized_pnl"] / pos["episode_buy_cost"],
                    })
                    del positions[code]
            executed_mid_notional += quantity * mid
            day_commission += fee
            day_tax += tax
            day_slippage += impact
            trades.append({
                "date": date, "signal_date": pending_date, "instrument": code, "side": side,
                "quantity": quantity, "reference_open": mid, "execution_price": execution,
                "notional": notional, "commission": fee, "stamp_tax": tax,
                "slippage_cost": impact, "costs_paid": fee + tax + impact,
                "realized_pnl": realized_pnl, "cash_after": max(0.0, cash), "reason": reason,
            })

        # Sell only overnight inventory, before all buys.  This order makes
        # same-day resale of newly acquired shares impossible (long-only T+1).
        if not pending_valid:
            skipped_rebalance_days += 1
        for code in sorted(list(positions)) if pending_valid else []:
            current = positions[code]["quantity"]
            mid = price(code, "open")
            target_qty = target_value / mid if code in desired_codes and mid is not None else (current if code in desired_codes else 0.0)
            quantity = max(0.0, current - target_qty)
            if quantity <= 1e-12:
                continue
            allowed, reason = executable(code, "sell")
            if not allowed:
                blocked_sales += 1
                blocked_orders.append({"instrument": code, "side": "sell", "reason": reason, "quantity": quantity})
                continue
            execute(code, "sell", quantity, "removed_from_selection" if code not in desired_codes else "daily_target_rebalance")

        buy_orders = []
        for item in pending if pending_valid else []:
            code = item["instrument"]
            mid = price(code, "open")
            if mid is None and code in positions:
                # With no opening mark we cannot know whether this retained
                # selected holding needs an addition or a reduction.  Do not
                # mislabel it as a failed new entry.
                blocked_orders.append({"instrument": code, "side": "rebalance", "reason": "missing_or_invalid_open", "target_weight": 1.0 / top_n})
                continue
            quantity = max(0.0, target_value / mid - positions.get(code, {}).get("quantity", 0.0)) if mid is not None else None
            if quantity is not None and quantity <= 1e-12:
                continue
            allowed, reason = executable(code, "buy")
            if not allowed:
                missing_entries += 1
                blocked_orders.append({"instrument": code, "side": "buy", "reason": reason, "target_weight": 1.0 / top_n})
                continue
            assert mid is not None and quantity is not None
            if quantity > 1e-12:
                full_cost = quantity * mid * (1 + slip) * (1 + commission)
                buy_orders.append((code, quantity, full_cost))
        needed = sum(item[2] for item in buy_orders)
        scale = min(1.0, max(0.0, cash) / needed) if needed > 0 else 1.0
        if scale < 1.0 - 1e-12:
            cash_limited_days += 1
        for code, quantity, _ in buy_orders:
            mid = price(code, "open")
            assert mid is not None
            # Last-leg cap absorbs floating-point residue without borrowing.
            affordable = max(0.0, cash) / (mid * (1 + slip) * (1 + commission))
            quantity = min(quantity * scale, affordable)
            if quantity > 1e-12:
                execute(code, "buy", quantity, "daily_target_rebalance")
        if cash < -max(1e-8, initial_cash * 1e-12):
            raise AssertionError("cash ledger became negative")
        cash = max(0.0, cash)

        for code, row in rows.items():
            close = _positive(row.get("close"))
            if close is not None:
                last_close[code] = (close, date, day_index)
        holding_rows = []
        for code, pos in sorted(positions.items()):
            close_info = last_close.get(code)
            if close_info is None:
                mark, mark_date, mark_index = pos["fallback_mark"], pos["fallback_date"], pos["fallback_day_index"]
                source = "entry_open_no_observed_close"
            else:
                mark, mark_date, mark_index = close_info
                source = "current_close" if mark_date == date else "last_observed_close"
            stale = source != "current_close"
            stale_position_days += int(stale)
            can_sell, sell_reason = executable(code, "sell")
            holding_rows.append({
                "instrument": code, "quantity": pos["quantity"], "mark": mark,
                "mark_date": mark_date, "mark_source": source,
                "stale_mark": stale, "stale_calendar_days": day_index - mark_index,
                "market_value": pos["quantity"] * mark, "cost_basis": pos["cost_basis"],
                "open_executable": can_sell, "open_block_reason": sell_reason,
                "selected_for_open": code in desired_codes,
                "pending_exit": code not in desired_codes and not can_sell,
            })
        nav = cash + sum(row["market_value"] for row in holding_rows)
        for row in holding_rows:
            row["weight"] = row["market_value"] / nav if nav > 0 else 0.0
        daily_return = nav / previous_nav - 1.0
        previous_nav = nav
        total_commission += day_commission
        total_tax += day_tax
        total_slippage += day_slippage
        daily.append({
            "date": date, "signal_date": pending_date, "nav": nav,
            "normalized_nav": nav / initial_cash, "return": daily_return,
            "opening_nav": opening_nav, "cash": cash, "cash_weight": cash / nav if nav > 0 else 0.0,
            "holdings_count": len(holding_rows), "holdings": holding_rows,
            "selected": list(pending), "target_weight_per_slot": 1.0 / top_n,
            "target_cash_weight": 1.0 - len(pending) / top_n if pending_valid else None,
            "signal_valid": pending_valid,
            "rebalance_skipped": not pending_valid,
            "skipped_rebalance_reason": ("initial_cash_no_prior_signal" if pending_date is None else "invalid_or_missing_prior_signal") if not pending_valid else None,
            "blocked_orders": blocked_orders, "trade_count": len(trades) - day_start_trade_count,
            "commission": day_commission, "stamp_tax": day_tax, "slippage_cost": day_slippage,
            "costs_paid": day_commission + day_tax + day_slippage,
            "turnover": executed_mid_notional / (2 * opening_nav) if opening_nav > 0 else 0.0,
            "buy_budget_scale": scale, "quote_rows": len(rows),
            "valid_open_rows": sum(price(code, "open") is not None for code in rows),
            "valid_close_rows": sum(price(code, "close") is not None for code in rows),
            "stale_position_count": sum(row["stale_mark"] for row in holding_rows),
        })

        # Today's close selector is read only after today's ledger is complete.
        signal_rows = signal_days.get(day, {})
        no_signal_days += int(not signal_rows)
        current_valid = validity.get(day, False) if validity is not None else bool(signal_rows)
        eligible = []
        rejected = 0
        for code, row in signal_rows.items():
            if not current_valid or not _boolean(row.get("eligible"), "signals.eligible"):
                continue
            try:
                score = float(row.get("score"))
            except (TypeError, ValueError):
                score = float("nan")
            if not math.isfinite(score):
                rejected += 1
                continue
            eligible.append({"instrument": code, "score": score})
        eligible.sort(key=lambda item: (-item["score"], item["instrument"]))
        pending = eligible[:top_n]
        pending_date = date
        pending_valid = current_valid
        nonfinite_scores += rejected
        candidates.append({
            "date": date, "signal_rows": len(signal_rows), "eligible_count": len(eligible),
            "signal_valid": current_valid,
            "selected_count": len(pending), "selected": list(pending),
            "eligible": eligible, "rejected_nonfinite_scores": rejected,
            "execution_date": str(dates[day_index + 1].date()) if day_index + 1 < len(dates) else None,
        })

    navs = [row["normalized_nav"] for row in daily]
    returns = [row["return"] for row in daily]
    summary = _metrics(returns, navs, [row["turnover"] for row in daily])
    summary.update({
        "initial_cash": initial_cash, "final_equity": previous_nav,
        "final_cash": cash, "costs_paid": total_commission + total_tax + total_slippage,
        "cash_fees_paid": total_commission + total_tax,
        "commission_paid": total_commission, "stamp_tax_paid": total_tax,
        "slippage_cost": total_slippage, "trade_count": len(trades),
        "average_cash_weight": float(np.mean([row["cash_weight"] for row in daily])) if daily else 1.0,
        "max_position_weight": max((holding["weight"] for row in daily for holding in row["holdings"]), default=0.0),
        "closed_position_count": len(closed_positions),
        "closed_position_win_rate": sum(row["realized_pnl"] > 0 for row in closed_positions) / len(closed_positions) if closed_positions else None,
        "closed_position_win_rate_definition": "profitable fully closed position episodes after average-cost purchase fees and sale fees/tax; partial sells and open positions are not counted",
    })
    final_holdings = daily[-1]["holdings"] if daily else []
    diagnostics = {
        "missing_or_blocked_entry_attempts": missing_entries,
        "blocked_sale_attempts": blocked_sales,
        "stale_position_days": stale_position_days,
        "cash_limited_days": cash_limited_days,
        "skipped_rebalance_days": skipped_rebalance_days,
        "days_without_quote_rows": no_quote_days,
        "days_without_signal_rows": no_signal_days,
        "rejected_nonfinite_scores": nonfinite_scores,
        "final_unresolved_positions": [row for row in final_holdings if row["stale_mark"] or row["pending_exit"]],
        "final_open_positions": final_holdings,
        "valuation_has_stale_marks": bool(stale_position_days),
        "forced_final_liquidation": False,
        "future_return_selection_filter": False,
        "opening_tradability_masks": mask_columns,
        "tradability_provenance": str(tradability_provenance or "").strip() or None,
    }
    assumptions = [
        "Research only: fractional shares; no automatic orders, minimum fee, board-lot rounding, market impact, or exchange fill guarantee.",
        "Signal at t close -> exact selected names attempted at next supplied calendar day's open; first evaluation day stays cash.",
        "Each selected name targets 1/top_n of opening equity; unfilled slots remain cash and blocked holdings may exceed target concentration.",
        "Sells use overnight inventory only and occur before buys; unavailable exits are held, never replaced using future-return availability.",
        "Open/close must share a consistent adjusted-price basis; corporate-action cash flows are not modeled independently.",
        "Missing close uses last observed close, or acquisition-day open if no close ever observed, with explicit stale valuation disclosure.",
        "No PIT price-limit/suspension accuracy is claimed without opening-time execution masks; final daily volume is not an execution filter.",
        "Invalid/missing selector dates skip the next rebalance and retain inventory; a valid empty selection exits at the next executable open.",
        "Turnover = sum(abs(executed quantity) * unadjusted-for-slippage reference open) / (2 * pre-trade opening equity), averaged over every supplied calendar day.",
        "Final equity is close mark-to-market, not a forced final sale; unresolved stale marks can distort reported performance.",
    ]
    return {
        "ok": True, "method": "daily_selector_cash_ledger", "rebalance": "day",
        "summary": summary,
        "portfolio": {
            **{key: summary[key] for key in ("ann_return", "sharpe", "max_drawdown", "volatility", "turnover", "calmar")},
            "nav_series": [[row["date"], row["normalized_nav"]] for row in daily],
            "return_series": [[row["date"], row["return"]] for row in daily],
            "daily_win_rate": summary["daily_win_rate"],
        },
        "daily": daily, "trades": trades, "candidates": candidates,
        "closed_positions": closed_positions, "diagnostics": diagnostics,
        "assumptions": assumptions,
    }
