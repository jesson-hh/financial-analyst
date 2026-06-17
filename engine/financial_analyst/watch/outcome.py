"""watch/outcome.py — 推荐复盘闭环 (C, self-improving).

Offline (盘后批量, non-realtime) scoring of every persisted ``WatchRec`` against
its **T+1 / T+5 forward daily returns**, plus a hit-rate aggregation for the
命中率看板. Closes the loop: 推荐 → 实测结果 → 命中率, so the advisor's value is
measurable (and, later, feedable back into the A knowledge block).

Reuse (zero new alpha):
  * ``backtest.records._action_verdict`` — the shared verdict oracle
    (buy/hold/sell/avoid × outcome → correct/partial/wrong), synced from
    ``dream.outcome_tracker``. We map the 5 watch actions onto its 4 canonical
    actions via :func:`_watch_action_to_canon`.
  * the fa **day** loader (``fetch_quote(freq="day")``) for forward closes/high/low —
    same source ``RegimeProvider`` uses (``trade_date``/``close`` columns).

Data contract — ``watch_rec_outcomes.parquet`` (:data:`OUTCOME_COLUMNS`), keyed on
``(ts, code, trigger_kind)`` so it joins back to ``watch_recommendations.parquet``::

    ts, code, trigger_kind, action      join key + predicted action
    base_close                          close on the rec's day (entry reference)
    return_t1, return_t5                float|None forward returns (None until mature)
    hit_target, hit_stop                bool — touched target high / stop low within 5d
    verdict                             correct/partial/wrong/pending
    n_fwd                               # forward trading bars available (maturity)
    scored_at                           "YYYY-MM-DD HH:MM:SS" of the backfill run

A rec is **mature** (verdict computed) once ``n_fwd >= 5``; before that it is
``"pending"`` and re-scored on the next backfill. Backfill skips already-final
rows and only (re)scores new / still-pending ones — idempotent.

Single-process writer (mirrors ``store.py`` / the project data-write rule).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

log = logging.getLogger(__name__)

# ──────────────────────── schema ───────────────────────────────────────────────

#: Persisted outcome column order — part of the C data contract. Do not reorder.
OUTCOME_COLUMNS = [
    "ts",
    "code",
    "trigger_kind",
    "action",
    "base_close",
    "return_t1",
    "return_t5",
    "hit_target",
    "hit_stop",
    "verdict",
    "n_fwd",
    "scored_at",
]

_DEDUP_COLS = ["ts", "code", "trigger_kind"]
_FINAL_VERDICTS = ("correct", "partial", "wrong")
_MATURE_FWD = 5
_DEFAULT_FILENAME = "watch_rec_outcomes.parquet"


# ──────────────────────── path resolution ──────────────────────────────────────


def default_outcomes_path() -> Path:
    """``parquet_root/watch_rec_outcomes.parquet`` (resolved via get_data_paths)."""
    from financial_analyst.data.paths import get_data_paths

    return get_data_paths().parquet_root / _DEFAULT_FILENAME


def _resolve(path: Union[str, Path, None]) -> Path:
    return Path(path) if path is not None else default_outcomes_path()


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTCOME_COLUMNS)


# ──────────────────────── action mapping ───────────────────────────────────────


def _watch_action_to_canon(action: str) -> str:
    """Map a 5-value watch action onto the 4 canonical verdict actions.

    ``add`` is a buy (expect up); ``reduce`` is a sell (expect down / avoid loss).
    Unknown → ``hold`` (neutral) so a typo never crashes the scorer.
    """
    a = (action or "").strip().lower()
    if a in ("buy", "add"):
        return "buy"
    if a in ("sell", "reduce"):
        return "sell"
    if a == "hold":
        return "hold"
    return "hold"


# ──────────────────────── pure scorer ──────────────────────────────────────────


def _as_float(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f


def score_one(rec: Dict[str, Any], daily_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Score one rec against its forward daily bars. ``None`` if base day not found.

    Args:
        rec: dict with ``ts/code/trigger_kind/action`` (+ optional
            ``target_price``/``stop_loss``).
        daily_df: this code's daily frame with ``trade_date`` (YYYY-MM-DD-ish) and
            ``close`` (``high``/``low`` optional → default to ``close``), any order.

    Returns the outcome dict (without ``scored_at`` — the writer stamps that), or
    ``None`` when the rec's day predates all available bars.
    """
    from financial_analyst.backtest.records import _action_verdict

    if daily_df is None or len(daily_df) == 0 or "trade_date" not in daily_df.columns \
            or "close" not in daily_df.columns:
        return None

    df = daily_df.copy()
    df["_d"] = df["trade_date"].astype(str).str.slice(0, 10)
    df = df.sort_values("_d", kind="stable").reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce") if "high" in df.columns else close
    low = pd.to_numeric(df["low"], errors="coerce") if "low" in df.columns else close

    rec_date = str(rec.get("ts", ""))[:10]
    mask = df["_d"] <= rec_date
    if not mask.any():
        return None
    base_pos = int(mask.to_numpy().nonzero()[0][-1])   # last bar on/before rec day
    base_close = float(close.iloc[base_pos])
    if base_close <= 0:
        return None

    fwd_close = close.iloc[base_pos + 1:].reset_index(drop=True)
    fwd_high = high.iloc[base_pos + 1:].reset_index(drop=True)
    fwd_low = low.iloc[base_pos + 1:].reset_index(drop=True)
    n_fwd = int(len(fwd_close))

    return_t1 = float(fwd_close.iloc[0] / base_close - 1) if n_fwd >= 1 else None
    return_t5 = float(fwd_close.iloc[4] / base_close - 1) if n_fwd >= _MATURE_FWD else None

    canon = _watch_action_to_canon(rec.get("action", ""))
    is_long = canon in ("buy", "hold")
    target = _as_float(rec.get("target_price"))
    stop = _as_float(rec.get("stop_loss"))
    h5, l5 = fwd_high.iloc[:5], fwd_low.iloc[:5]

    if canon == "buy" and target > 0:
        hit_target = bool((h5 >= target).any())
    elif canon == "sell" and target > 0:
        hit_target = bool((l5 <= target).any())            # downside target reached
    else:
        hit_target = False
    hit_stop = bool(is_long and stop > 0 and (l5 <= stop).any())

    if n_fwd < _MATURE_FWD:
        verdict = "pending"
    else:
        verdict = _action_verdict(canon, return_t5, hit_target, hit_stop)

    return {
        "ts": str(rec.get("ts", "")),
        "code": str(rec.get("code", "")),
        "trigger_kind": str(rec.get("trigger_kind", "")),
        "action": str(rec.get("action", "")),
        "base_close": round(base_close, 4),
        "return_t1": round(return_t1, 6) if return_t1 is not None else None,
        "return_t5": round(return_t5, 6) if return_t5 is not None else None,
        "hit_target": hit_target,
        "hit_stop": hit_stop,
        "verdict": verdict,
        "n_fwd": n_fwd,
    }


# ──────────────────────── hit-rate aggregation ─────────────────────────────────


def _agg(df: pd.DataFrame) -> Dict[str, Any]:
    """{n, correct, partial, wrong, win_rate, avg_return_t1, avg_return_t5}."""
    n = int(len(df))
    if n == 0:
        return {"n": 0, "correct": 0, "partial": 0, "wrong": 0, "win_rate": 0.0,
                "avg_return_t1": 0.0, "avg_return_t5": 0.0}
    v = df["verdict"].astype(str)
    correct = int((v == "correct").sum())
    partial = int((v == "partial").sum())
    wrong = int((v == "wrong").sum())

    def _avg(col: str) -> float:
        if col not in df.columns:
            return 0.0
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        return round(float(s.mean()), 6) if len(s) else 0.0

    return {
        "n": n,
        "correct": correct,
        "partial": partial,
        "wrong": wrong,
        "win_rate": round(correct / n, 4),
        "avg_return_t1": _avg("return_t1"),
        "avg_return_t5": _avg("return_t5"),
    }


def compute_hitrate(outcomes_df: pd.DataFrame) -> Dict[str, Any]:
    """Aggregate scored outcomes → overall + per-trigger + per-action hit rates.

    Only rows with a final verdict (correct/partial/wrong) count; ``pending`` rows
    are excluded. Empty / all-pending input → all-zero overall + empty breakdowns.
    """
    if outcomes_df is None or len(outcomes_df) == 0 or "verdict" not in outcomes_df.columns:
        return {"overall": _agg(_empty_frame()), "by_trigger": {}, "by_action": {}}

    final = outcomes_df[outcomes_df["verdict"].astype(str).isin(_FINAL_VERDICTS)]
    by_trigger: Dict[str, Any] = {}
    if "trigger_kind" in final.columns:
        for k, g in final.groupby("trigger_kind"):
            by_trigger[str(k)] = _agg(g)
    by_action: Dict[str, Any] = {}
    if "action" in final.columns:
        for k, g in final.groupby("action"):
            by_action[str(k)] = _agg(g)
    return {"overall": _agg(final), "by_trigger": by_trigger, "by_action": by_action}


def _fmt_stat_line(label: str, s: Dict[str, Any]) -> str:
    n = int(s.get("n", 0) or 0)
    wr = float(s.get("win_rate", 0.0) or 0.0) * 100
    t1 = float(s.get("avg_return_t1", 0.0) or 0.0) * 100
    t5 = float(s.get("avg_return_t5", 0.0) or 0.0) * 100
    return (f"- {label}: 命中率 {wr:.0f}% (样本 {n}: 命中{int(s.get('correct', 0) or 0)}/"
            f"部分{int(s.get('partial', 0) or 0)}/错{int(s.get('wrong', 0) or 0)}), "
            f"均 T+1 {t1:+.1f}% · T+5 {t5:+.1f}%")


def format_hitrate_context(hitrate: Optional[Dict[str, Any]], trigger_kind: str = "") -> str:
    """Compact track-record block for the advisor prompt (① 回灌 self-improve).

    Renders the advisor's OWN historical hit-rate (this trigger + global) so it can
    calibrate confidence on its past results. Returns ``""`` when there is no track
    record (``overall.n <= 0``) so an un-seasoned advisor's prompt stays byte-identical
    — same discipline as an empty ``knowledge`` block.
    """
    if not hitrate or not isinstance(hitrate, dict):
        return ""
    overall = hitrate.get("overall") or {}
    if int(overall.get("n", 0) or 0) <= 0:
        return ""
    by_trig = hitrate.get("by_trigger") or {}
    lines = ["## 你的历史推荐战绩 (复盘回灌, 你自己过往推荐的真实结果; 用于校准信心, 不覆盖触发逻辑)"]
    t = by_trig.get(trigger_kind) if trigger_kind else None
    if t and int(t.get("n", 0) or 0) > 0:
        lines.append(_fmt_stat_line(f"本触发 {trigger_kind}", t))
    lines.append(_fmt_stat_line("全局", overall))
    return "\n".join(lines)


# ──────────────────────── history join (recs × outcomes) ───────────────────────

_HISTORY_OUT_COLS = ["verdict", "return_t1", "return_t5", "n_fwd", "base_close"]


def _none_if_nan(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _verdict_or_pending(v: Any) -> str:
    return v if isinstance(v, str) and v else "pending"


def join_history(recs_df: pd.DataFrame, outcomes_df: pd.DataFrame,
                 limit: int = 100) -> List[Dict[str, Any]]:
    """Left-join recs with their outcomes on ``(ts,code,trigger_kind)`` → newest-first list.

    Each row = the rec fields (action/target_price/stop_loss/reason/confidence/
    user_action) + the matched outcome (verdict/return_t1/return_t5/n_fwd). A rec with
    no scored outcome yet → verdict ``"pending"``, returns ``None``. JSON-safe (NaN→None).
    """
    if recs_df is None or len(recs_df) == 0:
        return []
    r = recs_df.copy()
    o = outcomes_df.copy() if outcomes_df is not None and len(outcomes_df) else _empty_frame()
    o = o[[c for c in (_DEDUP_COLS + _HISTORY_OUT_COLS) if c in o.columns]]
    merged = r.merge(o, on=_DEDUP_COLS, how="left")
    merged = merged.sort_values("ts", ascending=False, kind="stable").head(int(limit))

    rows: List[Dict[str, Any]] = []
    for rec in merged.to_dict("records"):
        nf = _none_if_nan(rec.get("n_fwd"))
        rows.append({
            "ts": str(rec.get("ts", "")),
            "code": str(rec.get("code", "")),
            "trigger_kind": str(rec.get("trigger_kind", "")),
            "action": str(rec.get("action", "")),
            "target_price": _none_if_nan(rec.get("target_price")),
            "stop_loss": _none_if_nan(rec.get("stop_loss")),
            "confidence": _none_if_nan(rec.get("confidence")),
            "reason": str(rec.get("reason", "")),
            "user_action": str(rec.get("user_action", "none")),
            "verdict": _verdict_or_pending(rec.get("verdict")),
            "return_t1": _none_if_nan(rec.get("return_t1")),
            "return_t5": _none_if_nan(rec.get("return_t5")),
            "n_fwd": int(nf) if nf is not None else 0,
        })
    return rows


# ──────────────────────── scorer (loader-backed) ───────────────────────────────


class OutcomeScorer:
    """Score a frame of recs against forward daily bars from the fa day loader.

    One daily fetch per code (cached), covering [min rec day − buffer, today].
    ``score_recs(recs_df)`` → DataFrame with :data:`OUTCOME_COLUMNS` (drops recs
    whose base day predates the data). Never raises on a single bad rec/code.
    """

    def __init__(self, loader: Any = None, fwd_buffer_days: int = 10) -> None:
        self._loader = loader
        self._buffer = int(fwd_buffer_days)
        self._daily_cache: Dict[str, Optional[pd.DataFrame]] = {}

    def _get_loader(self):
        if self._loader is None:
            from financial_analyst.data.loader_factory import get_default_loader
            self._loader = get_default_loader()
        return self._loader

    def _daily_for(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        if code in self._daily_cache:
            return self._daily_cache[code]
        df = None
        try:
            q = self._get_loader().fetch_quote(code, start, end, freq="day")
            if q is not None and len(q) > 0 and "close" in q.columns and "trade_date" in q.columns:
                df = q
        except Exception as exc:  # noqa: BLE001
            log.debug("OutcomeScorer daily %s failed: %s", code, exc)
            df = None
        self._daily_cache[code] = df
        return df

    def score_recs(self, recs_df: pd.DataFrame) -> pd.DataFrame:
        if recs_df is None or len(recs_df) == 0:
            return _empty_frame()
        scored_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        end = pd.Timestamp.now().strftime("%Y-%m-%d")
        rows: List[Dict[str, Any]] = []
        for code, grp in recs_df.groupby("code"):
            code = str(code)
            min_day = str(grp["ts"].astype(str).min())[:10]
            start = (pd.Timestamp(min_day) - pd.Timedelta(days=self._buffer)).strftime("%Y-%m-%d")
            daily = self._daily_for(code, start, end)
            if daily is None:
                continue
            for rec in grp.to_dict("records"):
                o = score_one(rec, daily)
                if o is not None:
                    o["scored_at"] = scored_at
                    rows.append(o)
        if not rows:
            return _empty_frame()
        return pd.DataFrame(rows, columns=OUTCOME_COLUMNS)


# ──────────────────────── parquet IO + backfill ────────────────────────────────


def load_outcomes(path: Union[str, Path, None] = None) -> pd.DataFrame:
    """Load the outcome log → DataFrame with :data:`OUTCOME_COLUMNS` (empty if missing)."""
    p = _resolve(path)
    if not p.exists():
        return _empty_frame()
    try:
        df = pd.read_parquet(p)
    except Exception as exc:  # noqa: BLE001
        log.warning("watch.outcome: cannot read %s (%s); treating as empty", p, exc)
        return _empty_frame()
    for col in OUTCOME_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[OUTCOME_COLUMNS]


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df[OUTCOME_COLUMNS].reset_index(drop=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    if path.exists():
        path.unlink()
    tmp.rename(path)


def _key(row: Dict[str, Any]) -> tuple:
    return (str(row.get("ts", "")), str(row.get("code", "")), str(row.get("trigger_kind", "")))


def backfill_outcomes(
    recs_path: Union[str, Path, None] = None,
    out_path: Union[str, Path, None] = None,
    loader: Any = None,
) -> pd.DataFrame:
    """Score new / still-pending recs, merge into the outcome log, return the full log.

    Idempotent: rows already at a final verdict are kept as-is; only recs that are
    new or still ``pending`` get (re)scored (so a pending rec matures on a later
    run). Dedups on ``(ts, code, trigger_kind)`` keeping the freshest score.

    Args:
        recs_path: rec log (``None`` → :func:`store.default_recs_path`).
        out_path:  outcome log (``None`` → :func:`default_outcomes_path`).
        loader:    fa day loader (``None`` → ``get_default_loader``).

    Returns the merged outcome DataFrame (empty if there are no recs to score).
    """
    from financial_analyst.watch.store import load_recs

    recs = load_recs(recs_path)
    existing = load_outcomes(out_path)

    final_keys = set()
    if len(existing):
        fin = existing[existing["verdict"].astype(str).isin(_FINAL_VERDICTS)]
        final_keys = {_key(r) for r in fin.to_dict("records")}

    if len(recs):
        to_score = recs[[_key(r) not in final_keys for r in recs.to_dict("records")]]
    else:
        to_score = recs

    new_out = OutcomeScorer(loader=loader).score_recs(to_score) if len(to_score) else _empty_frame()

    if not len(existing) and not len(new_out):
        return _empty_frame()

    combined = pd.concat([existing, new_out], ignore_index=True) if len(existing) else new_out
    # freshest (newly scored) row wins for a given key.
    combined = combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
    combined = combined.sort_values("ts", kind="stable").reset_index(drop=True)
    _atomic_write(combined, _resolve(out_path))
    return combined


__all__ = [
    "OUTCOME_COLUMNS",
    "default_outcomes_path",
    "score_one",
    "_watch_action_to_canon",
    "compute_hitrate",
    "format_hitrate_context",
    "join_history",
    "OutcomeScorer",
    "load_outcomes",
    "backfill_outcomes",
]
