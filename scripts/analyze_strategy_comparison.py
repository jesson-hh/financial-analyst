"""Independently audit six frozen daily ledgers, then compare preset periods.

Counterfactual zero/double-cost runs reuse exact hash-verified historical
signals and panel; no threshold search, stock exclusion, or model refitting.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
OUT = ROOT / "var/trend_strategy_comparison_20260912"
EXPECTED_PLAN_SHA256 = "ccaefca902c2dd8b1c1c47d5a489cdfbe3bbadf5bb5618a475c89e71a2b17cfe"


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def audit_ledger(payload):
    # Reject nonfinite evidence anywhere, including fields not displayed below.
    json.dumps(payload, allow_nan=False)
    daily, trades, candidates = payload["daily"], payload["trades"], payload["candidates"]
    dates = [x["date"] for x in daily]
    if not dates or dates != sorted(set(dates)) or dates != [x["date"] for x in candidates]:
        raise ValueError("Calendar or selection dates not unique/aligned")
    initial = previous = payload["summary"]["initial_cash"]
    if not isinstance(initial, (float, int)) or isinstance(initial, bool) or initial <= 0:
        raise ValueError("Initial cash must be positive")
    errors = []
    trades_by_date = {}
    for trade in trades:
        if trade["date"] not in dates:
            raise ValueError("Execution date outside ledger calendar")
        trades_by_date.setdefault(trade["date"], []).append(trade)
    cash, quantities = initial, {}
    for row in daily:
        for trade in trades_by_date.get(row["date"], []):
            q, price = trade["quantity"], trade["execution_price"]
            if q <= 0 or price <= 0 or trade["side"] not in ("buy", "sell"):
                errors.append("invalid execution")
            notional = q * price
            if not math.isclose(notional, trade["notional"], rel_tol=1e-10, abs_tol=1e-5):
                errors.append("execution notional mismatch")
            if not math.isclose(abs(price - trade["reference_open"]) * q, trade["slippage_cost"], rel_tol=1e-10, abs_tol=1e-5):
                errors.append("slippage mismatch")
            if not math.isclose(trade["commission"] + trade["stamp_tax"] + trade["slippage_cost"], trade["costs_paid"], rel_tol=1e-10, abs_tol=1e-5):
                errors.append("execution cost mismatch")
            side = 1 if trade["side"] == "buy" else -1
            quantities[trade["instrument"]] = quantities.get(trade["instrument"], 0.0) + side * q
            cash -= side * notional + trade["commission"] + trade["stamp_tax"]
        if not math.isclose(cash, row["cash"], rel_tol=1e-10, abs_tol=1e-5):
            errors.append("independent cash ledger mismatch:" + row["date"])
        holding_codes = [h["instrument"] for h in row["holdings"]]
        if len(set(holding_codes)) != len(holding_codes) or row["holdings_count"] != len(holding_codes):
            errors.append("holding count or duplicate mismatch")
        for holding in row["holdings"]:
            if not math.isclose(holding["quantity"], quantities.get(holding["instrument"], 0), rel_tol=1e-10, abs_tol=1e-7):
                errors.append("independent quantity ledger mismatch")
            if not math.isclose(holding["quantity"] * holding["mark"], holding["market_value"], rel_tol=1e-10, abs_tol=1e-5):
                errors.append("holding marked value mismatch")
        if any(abs(quantity) > 1e-7 and code not in holding_codes for code, quantity in quantities.items()):
            errors.append("position disappeared from holdings")
        equity = row["cash"] + sum(x["market_value"] for x in row["holdings"])
        if not math.isclose(equity, row["nav"], rel_tol=1e-10, abs_tol=1e-5):
            errors.append("cash+holdings!=NAV:" + row["date"])
        if not math.isclose(equity / previous - 1, row["return"], abs_tol=1e-10):
            errors.append("return mismatch:" + row["date"])
        if row["cash"] < -1e-5:
            errors.append("negative cash:" + row["date"])
        if equity <= 0 or not math.isclose(equity / initial, row["normalized_nav"], rel_tol=1e-10, abs_tol=1e-10):
            errors.append("normalized NAV mismatch")
        if not math.isclose(row["cash"] / equity, row["cash_weight"], abs_tol=1e-10):
            errors.append("cash weight mismatch")
        if not math.isclose(sum(t["costs_paid"] for t in trades_by_date.get(row["date"], [])), row["costs_paid"], rel_tol=1e-10, abs_tol=1e-5):
            errors.append("daily cost mismatch")
        previous = equity
    selected = {row["date"]: {x["instrument"] for x in row["selected"]} for row in candidates}
    day_index = {d: i for i, d in enumerate(dates)}
    for trade in trades:
        if trade["signal_date"] not in day_index or day_index[trade["date"]] != day_index[trade["signal_date"]] + 1:
            errors.append("non-next-day trade")
        if trade["side"] == "buy" and trade["instrument"] not in selected[trade["signal_date"]]:
            errors.append("purchase outside frozen selection")
    fees = sum(x["costs_paid"] for x in trades)
    if not math.isclose(fees, payload["summary"]["costs_paid"], rel_tol=1e-10, abs_tol=1e-5):
        errors.append("cost mismatch")
    if not math.isclose(previous / initial - 1, payload["summary"]["total_return"], abs_tol=1e-10):
        errors.append("summary total return mismatch")
    if not math.isclose(previous, payload["summary"]["final_equity"], rel_tol=1e-10, abs_tol=1e-5):
        errors.append("summary final equity mismatch")
    if errors:
        raise ValueError(errors[:20])
    return {"passed": True, "checked_days": len(daily), "checked_executions": len(trades),
            "checks": ["cash+marked holdings=NAV", "returns independently recomputed",
                       "independent execution cash and quantity ledger", "marked quantity × price",
                       "nonnegative cash", "exact next-calendar-day timing", "no backfill buys", "cost reconciliation"]}


def period_metrics(payload, start, end):
    rows = [r for r in payload["daily"] if start <= r["date"] <= end]
    cs = [r["eligible_count"] for r in payload["candidates"] if start <= r["date"] <= end]
    if not rows:
        return None
    if len(cs) != len(rows):
        raise ValueError("Period candidate-day denominator does not match all ledger days")
    by_date = {r["date"]: i for i, r in enumerate(payload["daily"])}
    first_index = by_date[rows[0]["date"]]
    previous_nav = (payload["daily"][first_index - 1]["nav"] if first_index else payload["summary"]["initial_cash"])
    absolute_nav = np.array([r["nav"] for r in rows], dtype=float)
    numeric = np.array([[r[key] for key in ("nav", "cash_weight", "turnover", "costs_paid", "holdings_count")] for r in rows], dtype=float)
    if not np.isfinite(numeric).all() or not math.isfinite(previous_nav) or previous_nav <= 0 or not np.isfinite(cs).all():
        raise ValueError("Nonfinite period ledger/candidate evidence")
    returns = absolute_nav / np.r_[previous_nav, absolute_nav[:-1]] - 1.0
    nav = np.cumprod(1 + returns)
    dd = nav / np.maximum.accumulate(np.r_[1, nav])[1:] - 1
    years = ((pd.Timestamp(rows[-1]["date"]) - pd.Timestamp(rows[0]["date"])).days + 1) / 365.25
    # A position exited at today's open still generated today's account return.
    # Include opening or closing exposure; do not count an exit day as cash-only.
    noncash = [i for i, r in enumerate(rows) if r["holdings_count"] > 0 or (
        by_date[r["date"]] > 0 and payload["daily"][by_date[r["date"]] - 1]["holdings_count"] > 0)]
    sigma = returns.std(ddof=1) if len(rows) > 1 else 0
    try:
        annualized = math.expm1(math.log(float(nav[-1])) / years) if nav[-1] > 0 else -1.0
    except OverflowError:
        annualized = None
    return {"start": rows[0]["date"], "end": rows[-1]["date"], "n_days": len(rows),
        "starting_equity_before_first_day": previous_nav, "ending_equity": rows[-1]["nav"],
        "boundary_policy": "continuous account; carry prior close equity and holdings, no segment reset",
        "net_total_return": float(nav[-1] - 1), "year_fraction": years,
        "ann_return_calendar": annualized,
        "max_drawdown": float(dd.min()), "sharpe_rf0": float(returns.mean() / sigma * np.sqrt(252)) if sigma > 1e-15 else None,
        "daily_win_rate_all_days": float((returns > 0).mean()),
        "daily_win_rate_invested_days": sum(returns[i] > 0 for i in noncash) / len(noncash) if noncash else None,
        "invested_day_definition": "opening or closing holdings; includes exit days",
        "mean_exposure": float(np.mean([1 - r["cash_weight"] for r in rows])),
        "max_exposure": max(1 - r["cash_weight"] for r in rows),
        "cash_day_fraction": 1 - len(noncash) / len(rows), "noncash_days": len(noncash),
        "close_cash_day_fraction": sum(r["holdings_count"] == 0 for r in rows) / len(rows),
        "mean_holding_count": float(np.mean([r["holdings_count"] for r in rows])),
        "mean_candidate_count": float(np.mean(cs)), "median_candidate_count": float(np.median(cs)),
        "candidate_count_p10": float(np.quantile(cs, .1)), "at_least_10_candidate_day_fraction": sum(c >= 10 for c in cs) / len(cs),
        "annualized_traded_notional_over_NAV": float(np.mean([r["turnover"] * 2 for r in rows]) * 252),
        "total_cost_CNY": sum(r["costs_paid"] for r in rows),
        "execution_failure_count": sum(len(r["blocked_orders"]) for r in rows),
        "stale_mark_holding_days": sum(h["stale_mark"] for r in rows for h in r["holdings"]),
        "terminal_unresolved_positions": [h for h in rows[-1]["holdings"] if h["stale_mark"] or h.get("pending_exit")],
        "evidence_insufficient_sparse": len(noncash) < 60}


def yearly_and_phase_consistency(payload, plan, periods):
    start, end = plan["common"]["sample"]["eval_start"], plan["common"]["sample"]["eval_end"]
    yearly = {str(year): period_metrics(payload, f"{year}-01-01", f"{year}-12-31")
              for year in range(int(start[:4]), int(end[:4]) + 1)}
    yearly = {key: value for key, value in yearly.items() if value is not None}
    full_growth = 1 + periods["full"]["net_total_return"]
    phase_growth = math.prod(1 + periods[p["id"]]["net_total_return"] for p in plan["common"]["periods"])
    year_growth = math.prod(1 + value["net_total_return"] for value in yearly.values())
    if not math.isclose(full_growth, phase_growth, rel_tol=1e-10, abs_tol=1e-10) or not math.isclose(full_growth, year_growth, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError("Yearly/phase compounded returns do not reconcile to continuous account")
    return {"yearly": yearly, "compound_reconciled": True,
            "phase_net_return_signs": {p["id"]: int(np.sign(periods[p["id"]]["net_total_return"])) for p in plan["common"]["periods"]},
            "inference": "descriptive phase/year consistency, not independent unseen OOS evidence"}


def capacity_proxy(payload, panel, source_calendar):
    calendar = pd.DatetimeIndex(source_calendar)
    if calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("Capacity proxy needs a complete ordered source trading calendar")
    amount = panel["amount"].unstack(level=1).reindex(calendar)
    amount = amount.apply(pd.to_numeric, errors="coerce").where(lambda x: np.isfinite(x) & (x >= 0))
    prior = amount.rolling(20, min_periods=20).mean().shift(1)
    def denominator(date, code):
        value = prior.at[pd.Timestamp(date), code] if pd.Timestamp(date) in prior.index and code in prior.columns else float("nan")
        return float(value) if pd.notna(value) and math.isfinite(value) and value > 0 else None
    ratios, targets = [], []
    for trade in payload["trades"]:
        denom = denominator(trade["date"], trade["instrument"])
        if denom is not None:
            ratios.append(trade["quantity"] * trade["reference_open"] / denom)
    target_count = 0
    topn = int((payload.get("backtest") or {}).get("topn", 10))
    for row in payload["daily"]:
        if not row.get("signal_valid"):
            continue
        for selected in row["selected"]:
            target_count += 1
            denom = denominator(row["date"], selected["instrument"])
            if denom is not None:
                targets.append(row["opening_nav"] / topn / denom)
    return {"definition": "executed reference-open notional / prior20 fixed-calendar mean daily amount; adjusted-share research proxy, not capacity certification",
            "calendar_policy": "full source signal calendar including warmup; missing days stay missing; shifted one trading day",
            "n_trades_with_proxy": len(ratios), "n_trades_without_proxy": len(payload["trades"]) - len(ratios), "n_trades": len(payload["trades"]),
            "max_participation_proxy": max(ratios) if ratios else None,
            "p95_participation_proxy": float(np.quantile(ratios, .95)) if ratios else None,
            "n_target_slots": target_count, "n_target_slots_with_proxy": len(targets),
            "max_target_notional_proxy": max(targets) if targets else None,
            "p95_target_notional_proxy": float(np.quantile(targets, .95)) if targets else None}


def validate_comparison_inputs(plan, receipts, records):
    variants = plan["variants"]
    ids = [v["id"] for v in variants]
    if len(ids) != 6 or len(set(ids)) != 6 or set(records) != set(ids):
        raise ValueError("Comparison requires all six unique frozen strategies")
    receipt_ids = [r["variant_id"] for r in receipts]
    if len(receipt_ids) != 6 or set(receipt_ids) != set(ids):
        raise ValueError("Comparison requires exactly one completed receipt per strategy")
    if len({r["run_id"] for r in receipts}) != 6:
        raise ValueError("Strategies cannot reuse the same workflow run")
    sample = plan["common"]["sample"]
    first, last = sample["eval_start"], sample["eval_end"]
    periods = plan["common"]["periods"]
    if len({p["id"] for p in periods}) != len(periods) or not periods:
        raise ValueError("Comparison periods must be named and unique")
    shared_dates = None
    for variant in variants:
        rid = variant["id"]
        receipt = next(r for r in receipts if r["variant_id"] == rid)
        rec = records[rid]
        if receipt.get("status") != "completed" or rec.get("status") != "completed" or rec.get("id") != receipt["run_id"]:
            raise ValueError("Incomplete/mismatched workflow: " + rid)
        if receipt.get("workspace_id") != "rw_general" or rec.get("workspace_id") != "rw_general":
            raise ValueError("Wrong workflow research namespace")
        if rec.get("overrides"):
            raise ValueError("Unplanned workflow parameter overrides")
        outcome = rec["result"]
        if outcome.get("node_errors"):
            raise ValueError("Workflow has node errors: " + rid)
        nodes = {n["type"]: n for n in rec["graph"]["nodes"]}
        expected_history = {**variant["history_screen_params"], "label": variant["name"], "evaluation_start": first}
        if (len(rec["graph"]["nodes"]) != 3 or set(nodes) != {"source", "history_screen", "daily_backtest"}
                or nodes["source"]["params"] != plan["common"]["source_params"]
                or nodes["history_screen"]["params"] != expected_history
                or nodes["daily_backtest"]["params"] != plan["common"]["daily_backtest_params"]):
            raise ValueError("Run graph differs from frozen strategy/cost plan: " + rid)
        payload = outcome["terminal"]["payload"]
        audit_ledger(payload)
        dates = [r["date"] for r in payload["daily"]]
        if dates[0] != first or dates[-1] != last or (shared_dates is not None and dates != shared_dates):
            raise ValueError("Six strategies must use the exact same complete evaluation calendar")
        shared_dates = dates
        covered = [d for p in periods for d in dates if p["start"] <= d <= p["end"]]
        if covered != dates:
            raise ValueError("Prespecified phases must cover all dates exactly once in order")
    return shared_dates


def validate_artifact_source(plan, payload, panel, signals, meta):
    source, sample = plan["common"]["source_params"], plan["common"]["sample"]
    panel_dates = pd.DatetimeIndex(panel.index.get_level_values(0))
    signal_dates = pd.DatetimeIndex(signals.index.get_level_values(0).unique()).sort_values()
    codes = set(panel.index.get_level_values(1))
    if len(codes) > 1200 or len(panel) > 2_000_000 or len(signals) > 2_000_000:
        raise ValueError("Actual source data exceeds frozen replay bounds")
    import re
    if any(not re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", str(code)) for code in codes):
        raise ValueError("Actual source contains invalid stock codes")
    if (panel_dates.min() < pd.Timestamp(source["start"]) or panel_dates.max() > pd.Timestamp(source["end"])
            or signal_dates[0] < pd.Timestamp(source["start"]) or signal_dates[-1] > pd.Timestamp(source["end"])):
        raise ValueError("Actual data dates exceed source interval")
    if not panel_dates.isin(signal_dates).all():
        raise ValueError("Source quotes use dates absent from fixed signal calendar")
    if not codes.issubset(set(signals.index.get_level_values(1))):
        raise ValueError("Source quote code absent from historical signal grid")
    evidence = meta["evidence"]
    context = evidence["context"]
    if any(context.get(key) != source.get(key) for key in ("start", "end", "universe", "research_freq")):
        raise ValueError("Artifact source context differs from frozen plan")
    if source.get("codes") and codes - set(source["codes"]):
        raise ValueError("Actual source stocks exceed explicit source codes")
    if evidence["load"].get("amount_unit") != "yuan" or not evidence["load"].get("verified"):
        raise ValueError("Capacity/amount evidence is not verified CNY")
    resolved = int(evidence["universe"].get("n_resolved", 0))
    if not len(codes) <= resolved <= 1200:
        raise ValueError("Actual source count inconsistent with resolved universe")
    dates = [r["date"] for r in payload["daily"]]
    if meta["calendar"] != dates or dates != [str(d.date()) for d in signal_dates if sample["eval_start"] <= str(d.date()) <= sample["eval_end"]]:
        raise ValueError("Artifact signal/evaluation calendars do not match ledger")
    return signal_dates


def validate_counterfactual(base, counter):
    if base["history_artifact"] != counter["history_artifact"]:
        raise ValueError("Cost sensitivity changed immutable source artifact")
    a = [(r["date"], r["signal_valid"], r["eligible_count"], r["selected"]) for r in base["candidates"]]
    b = [(r["date"], r["signal_valid"], r["eligible_count"], r["selected"]) for r in counter["candidates"]]
    if a != b:
        raise ValueError("Cost sensitivity changed dates or frozen stock selections")


def source_price_profile(panel, source_calendar):
    """Flag, never remove, inconsistent prices or unit-ratio observations."""
    calendar = pd.DatetimeIndex(source_calendar)
    def wide(field):
        return panel[field].unstack(level=1).reindex(calendar).apply(pd.to_numeric, errors="coerce")
    opened, closed = wide("open"), wide("close")
    def describe(values):
        flat = np.asarray(values, dtype=float).ravel()
        flat = flat[np.isfinite(flat) & (flat > 0)]
        return {"n": len(flat), "p01": float(np.quantile(flat, .01)) if len(flat) else None,
                "median": float(np.median(flat)) if len(flat) else None,
                "p99": float(np.quantile(flat, .99)) if len(flat) else None,
                "min": float(flat.min()) if len(flat) else None, "max": float(flat.max()) if len(flat) else None}
    intraday, overnight = opened / closed, opened / closed.shift(1)
    odd = np.isfinite(overnight) & ((overnight < .75) | (overnight > 1.25))
    sample = []
    for day, code in odd.stack().loc[lambda x: x].index[:20]:
        sample.append({"date": str(day.date()), "code": str(code), "open_over_prior_close": float(overnight.at[day, code])})
    result = {"open_over_same_day_close": describe(intraday), "open_over_prior_calendar_close": describe(overnight),
              "overnight_ratio_outside_075_125": int(odd.sum().sum()), "outlier_sample": sample,
              "interpretation": "diagnostic only: outliers may be market moves, adjustment changes or bad quotes; no exclusion or automatic attribution"}
    volume = "volume" if "volume" in panel.columns else ("vol" if "vol" in panel.columns else None)
    if volume and "amount" in panel.columns:
        result["amount_over_volume_times_close"] = describe(wide("amount") / (wide(volume) * closed))
        result["unit_ratio_caveat"] = "daily VWAP/close approximation only; requires stated shares/CNY units; not independent unit certification"
    return result


def main():
    from guanlan_v2.workflow.strategy_replay import HistoryArtifactStore, run_daily_backtest
    from guanlan_v2.workflow.history_price_quality import assess_history_price_quality
    plan_bytes = (OUT / "experiment_plan.json").read_bytes()
    plan_hash = hashlib.sha256(plan_bytes).hexdigest()
    if plan_hash != EXPECTED_PLAN_SHA256:
        raise ValueError("Frozen experiment plan hash changed; do not rank a changed experiment")
    plan = json.loads(plan_bytes)
    receipts = json.loads((OUT / "workflow_receipts.json").read_text(encoding="utf-8"))
    store = HistoryArtifactStore(ROOT / ".data" / "workflows" / "_history")
    first, last = plan["common"]["sample"]["eval_start"], plan["common"]["sample"]["eval_end"]
    records = {}
    for variant in plan["variants"]:
        raw = json.loads((OUT / "runs" / (variant["id"] + ".json")).read_text(encoding="utf-8"))
        records[variant["id"]] = raw.get("run") or raw.get("item") or raw
    shared_dates = validate_comparison_inputs(plan, receipts, records)
    prepared, panel_hashes, provenance_keys, source_calendar_keys = {}, set(), set(), set()
    for variant in plan["variants"]:
        payload = records[variant["id"]]["result"]["terminal"]["payload"]
        panel, signals, meta = store.load(payload["history_artifact"], owner="rw_general")
        source_calendar = validate_artifact_source(plan, payload, panel, signals, meta)
        panel_hashes.add(meta["files"]["panel.parquet"])
        provenance_keys.add(json.dumps({k: meta["evidence"][k] for k in ("universe", "metadata")}, sort_keys=True, allow_nan=False))
        source_calendar_keys.add(tuple(source_calendar))
        prepared[variant["id"]] = (panel, signals, meta, source_calendar)
    if len(panel_hashes) != 1 or len(provenance_keys) != 1 or len(source_calendar_keys) != 1:
        raise ValueError("Six strategies used different source snapshots/calendars/provenance; no comparison published")
    price_checks = {}
    for vid, (panel, signals, meta, source_calendar) in prepared.items():
        price_checks[vid] = assess_history_price_quality(panel, source_calendar,
            adjustment_provenance=meta["evidence"].get("load", {}).get("price_adjustment"))
    if any(not q["performance_ready"] for q in price_checks.values()):
        quality = next(q for q in price_checks.values() if not q["performance_ready"])
        counts = quality.get("counts", {})
        # Keep the original receipts intact, but never promote invalid price
        # evidence to performance comparison or run cost sensitivity on it.
        blocked = {"schema": "guanlan.strategy_comparison.v1", "status": "diagnostics_only",
            "plan_sha256": plan_hash, "as_of": last, "all_six_complete": True,
            "shared_panel_verified": True, "shared_calendar_days": len(shared_dates),
            "automatic_trading": False, "performance_ready": False,
            "strategies": [{"id": v["id"], "name": v["name"],
                "receipt": next(r for r in receipts if r["variant_id"] == v["id"]),
                "status": "diagnostics_only"} for v in plan["variants"]],
            "diagnostics": {"blocking_reasons": [b["message"] for b in quality["hard_blockers"]],
                "coverage_rows": [{"check": "OHLC 硬约束矛盾", "available": counts.get("OHLC_envelope_bad_rows", 0),
                    "total": counts.get("source_rows", 0), "unit": "行", "available_meaning": "存在矛盾的行数；其他行不代表质量通过"}],
                "source_price_quality": quality, "all_strategy_price_checks": price_checks,
                "next_steps": ["在独立研究快照补齐并核验原始OHLC、累计复权因子和完整交易日历，不覆盖全局原始行情。",
                    "修复同日价格约束矛盾，确认因子完整与因果复权，禁止按收益或异常跳幅直接删股删日。",
                    "保持六组原始规则、日期、仓位和成本不变，使用同一通过核验的快照重跑全部六组后再比较。"],
                "interpretation": "六组已运行及账本数学一致不代表策略绩效可信；没有发布收益排名。"}}
        write("comparison.json", blocked)
        print(json.dumps({"status": "diagnostics_only", "counts": counts,
            "blocking_reasons": blocked["diagnostics"]["blocking_reasons"]}, ensure_ascii=False), flush=True)
        return
    comparison = {"plan_sha256": plan_hash,
        "schema": "guanlan.strategy_comparison.v1", "as_of": last, "strategies": [],
        "status": "complete", "all_six_complete": True, "shared_panel_verified": True,
        "shared_calendar_days": len(shared_dates), "shared_provenance_verified": True,
        "comparison_is_exploratory": True, "unseen_oos_claim": False, "automatic_trading": False}
    comparison["source_price_profile"] = source_price_profile(prepared[plan["variants"][0]["id"]][0], prepared[plan["variants"][0]["id"]][3])
    for variant in plan["variants"]:
        receipt = next(r for r in receipts if r["variant_id"] == variant["id"])
        rec = records[variant["id"]]
        outcome = rec["result"]
        if rec["status"] != "completed" or outcome.get("node_errors"):
            raise ValueError("Incomplete workflow: " + variant["id"])
        payload = outcome["terminal"]["payload"]
        qa = audit_ledger(payload)
        panel, signals, meta, source_calendar = prepared[variant["id"]]
        periods = {p["id"]: period_metrics(payload, p["start"], p["end"]) for p in plan["common"]["periods"]}
        periods["full"] = period_metrics(payload, first, last)
        consistency = yearly_and_phase_consistency(payload, plan, periods)
        sensitivity = {}
        for label, multiple in [("zero_cost", 0), ("double_cost", 2)]:
            settings = dict(plan["common"]["daily_backtest_params"])
            for key in ("commission", "sell_tax", "slippage_bps"):
                settings[key] *= multiple
            counter = run_daily_backtest({"__dt": "screen_history", "history_artifact": payload["history_artifact"]},
                                        settings, owner="rw_general", store=store)
            audit_ledger(counter)
            validate_counterfactual(payload, counter)
            sensitivity[label] = {p["id"]: period_metrics(counter, p["start"], p["end"]) for p in plan["common"]["periods"]}
            sensitivity[label]["full"] = period_metrics(counter, first, last)
            write(f"sensitivity_{variant['id']}_{label}.json", {"method": "same immutable history, counterfactual cost replay",
                "summary": counter["summary"], "portfolio": counter["portfolio"], "diagnostics": counter["diagnostics"]})
        for key, metrics in periods.items():
            metrics["gross_total_return"] = sensitivity["zero_cost"][key]["net_total_return"]
            metrics["gross_return_definition"] = "zero-cost counterfactual replay of identical signals; not net return plus reported cash costs"
        capacity = capacity_proxy(payload, panel, source_calendar)
        weekly = pd.DataFrame(payload["daily"]).set_index(pd.to_datetime([r["date"] for r in payload["daily"]]))
        weekly = weekly.resample("W-FRI").last().dropna(subset=["date"])
        item = {"id": variant["id"], "name": variant["name"], "receipt": receipt, "audit": qa,
                "periods": periods, "cost_sensitivity": sensitivity, "capacity_proxy": capacity,
                "yearly_or_segment_consistency": consistency,
                "quality_flags": {"has_stale_marks": bool(payload["diagnostics"]["stale_position_days"]),
                    "has_execution_failures": bool(payload["diagnostics"]["missing_or_blocked_entry_attempts"] or payload["diagnostics"]["blocked_sale_attempts"]),
                    "has_invalid_screen_dates": bool(payload["screen_diagnostics"].get("invalid_calendar_dates")),
                    "has_terminal_unresolved_positions": bool(payload["diagnostics"]["final_unresolved_positions"]),
                    "validation_sparse": periods["validation"]["evidence_insufficient_sparse"],
                    "source_has_price_ratio_outliers": bool(comparison["source_price_profile"]["overnight_ratio_outside_075_125"])},
                "summary": payload["summary"], "diagnostics": payload["diagnostics"],
                "data_profile": {"panel_rows": len(panel), "panel_sha256": meta["files"]["panel.parquet"],
                    "signal_sha256": meta["files"]["signals.parquet"], "source": meta["evidence"]["load"],
                    "screen_diagnostics": payload["screen_diagnostics"]},
                "latest_candidates": payload["candidates"][-1],
                "weekly_nav": [{"date": r["date"], "nav": float(r["normalized_nav"]),
                    "exposure": 1 - float(r["cash_weight"]), "holdings": int(r["holdings_count"])} for _, r in weekly.iterrows()]}
        comparison["strategies"].append(item)
        print(json.dumps({"variant": variant["id"], "full": periods["full"], "validation": periods["validation"]}, ensure_ascii=False), flush=True)
    comparison["all_validation_net_returns_nonpositive"] = all(s["periods"]["validation"]["net_total_return"] <= 0 for s in comparison["strategies"])
    comparison["interpretation_guardrails"] = [
        "All six frozen variants disclosed; no automatic winner or threshold retuning.",
        "Zero/double-cost sensitivity uses the same immutable signals, dates, top-N and initial capital; quantity/cash paths may differ because costs change NAV.",
        "Negative net returns, stale marks, sparse exposure or source anomalies preclude unconditional superiority claims.",
        "Static universe/current ST metadata and unverified corporate-action execution prices remain conditional research limitations."]
    write("comparison.json", comparison)


if __name__ == "__main__":
    main()
