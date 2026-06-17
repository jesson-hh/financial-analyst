"""watch/signals.py — ported / parquet-backed signals for the 盯盘 触发器 (B 扩容).

fa runs py3.13 without qlib and **cannot import** the stocks/research signal
modules, so validated signals are brought over as faithful pure ports or direct
parquet readers (zero cross-repo import).

B1 — **negative-event warnings**: read ``tdx_f10_warnings_latest.parquet`` (written
by stocks ``scripts/scan_negative_events.py``) → ``{code: {severity,title,event_date}}``
(max severity per code). severity≥2 (立案/处罚/减持/业绩预减/退市风险...) drives a
**hard** sell (held) / 禁建仓 (not held) in the loop — no LLM. Mirrors
``scan_negative_events.load_warnings_dict`` semantics. Defensive: a missing /
unreadable parquet yields ``{}`` so the channel degrades silently to off.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

log = logging.getLogger(__name__)

_WARN_FILE = "tdx_f10_warnings_latest.parquet"


def _warnings_path(explicit: Optional[Union[str, Path]]) -> Optional[Path]:
    """Resolve the warnings parquet path (explicit override, else fa parquet_root)."""
    if explicit is not None:
        return Path(explicit)
    try:
        from financial_analyst.data.paths import get_data_paths
        return Path(get_data_paths().parquet_root) / _WARN_FILE
    except Exception as exc:  # noqa: BLE001
        log.debug("watch.signals: parquet_root resolve failed: %s", exc)
        return None


def load_negative_warnings(
    parquet_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Dict[str, Any]]:
    """``{code: {severity:int, title:str, event_date:str}}`` (max severity per code).

    Reads ``tdx_f10_warnings_latest.parquet``; ``{}`` if missing / unreadable /
    schema-mismatched (never raises — the negative-event channel degrades to off).
    """
    p = _warnings_path(parquet_path)
    if p is None or not p.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_parquet(p)
    except Exception as exc:  # noqa: BLE001
        log.warning("watch.signals: read %s failed: %s", p, exc)
        return {}
    if df is None or len(df) == 0 or "code" not in df.columns or "severity" not in df.columns:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    df = df.sort_values("severity", ascending=False)   # highest severity first
    for _, r in df.iterrows():
        code = str(r["code"])
        if code in out:                                 # keep first = max severity
            continue
        try:
            sev = int(r["severity"])
        except (TypeError, ValueError):
            continue
        out[code] = {
            "severity": sev,
            "title": str(r.get("title", "")),
            "event_date": str(r.get("event_date", "")),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────
# B2 — 量能 regime (R9 distr / R11 tail_surge / R14 super_distr), ported pure
# from stocks ``strategy/sentiment/volume_regime.py`` (faithful copy; fa cannot
# import the qlib-dependent original). super_distr fwd_5d -4.2pp (月 11/12 SS).
# ──────────────────────────────────────────────────────────────────────────


def _compute_tr_surge_60(turnover_rate) -> Optional[float]:
    """MA5(tr) / MA60(tr). Needs >=60 days history. Latest value or None."""
    tr = turnover_rate.fillna(0)
    if len(tr) < 60:
        return None
    ma5 = tr.rolling(5).mean().iloc[-1]
    ma60 = tr.rolling(60).mean().iloc[-1]
    import pandas as pd
    if pd.isna(ma5) or pd.isna(ma60) or ma60 == 0:
        return None
    return float(ma5 / ma60)


def _compute_ret_20d(close) -> Optional[float]:
    """close_today / close_20d_ago - 1."""
    import pandas as pd
    if len(close) < 21:
        return None
    c_now = close.iloc[-1]
    c_20 = close.iloc[-21]
    if pd.isna(c_now) or pd.isna(c_20) or c_20 == 0:
        return None
    return float(c_now / c_20 - 1)


def _compute_intraday_tail(bars_5m_day) -> Dict[str, Any]:
    """尾盘 30min 特征 (ret_close_30m + vs_close_30m). bars_5m_day: 单日 close/volume."""
    if bars_5m_day is None or len(bars_5m_day) == 0:
        return {"ret_close_30m": None, "vs_close_30m": None, "n_bars": 0}
    n = len(bars_5m_day)
    if n < 30:
        return {"ret_close_30m": None, "vs_close_30m": None, "n_bars": n}
    closes = bars_5m_day["close"].values
    volumes = bars_5m_day["volume"].values
    idx_30m_start = max(0, n - 7)
    px_close_30m_start = closes[idx_30m_start]
    px_close = closes[-1]
    ret_close_30m = (px_close / px_close_30m_start - 1) if px_close_30m_start > 0 else None
    vol_total = float(volumes.sum())
    vs_close_30m = float(volumes[idx_30m_start + 1:].sum() / vol_total) if vol_total > 0 else None
    return {"ret_close_30m": ret_close_30m, "vs_close_30m": vs_close_30m, "n_bars": n}


def compute_vol_regime(close_day, turnover_rate_day, bars_5m_last_day=None) -> Dict[str, Any]:
    """量能 regime (R9 + R11 + R14). Faithful port — see module note.

    close_day / turnover_rate_day: daily Series (>=60 days incl today).
    bars_5m_last_day: today's 5min DataFrame with ``close``/``volume`` (or None →
    R11 part is None). Returns the regime dict (regime_label / super_distr / ...).
    """
    ret_20d = _compute_ret_20d(close_day)
    tr_surge = _compute_tr_surge_60(turnover_rate_day)
    tail = _compute_intraday_tail(bars_5m_last_day)
    ret_close_30m = tail["ret_close_30m"]
    vs_close_30m = tail["vs_close_30m"]

    r9_distr = bool(ret_20d is not None and tr_surge is not None
                    and ret_20d >= 0.10 and tr_surge >= 2.5)
    bounce_a = (ret_20d is not None and tr_surge is not None
                and -0.10 <= ret_20d < -0.02 and tr_surge >= 2.5)
    bounce_b = (ret_20d is not None and tr_surge is not None
                and ret_20d <= -0.10 and tr_surge < 0.8)
    r9_bounce = bool(bounce_a or bounce_b)
    r11_tail_surge = bool(ret_close_30m is not None and vs_close_30m is not None
                          and ret_close_30m > 0.02 and vs_close_30m > 0.18)
    super_distr = r9_distr and r11_tail_surge

    if super_distr:
        label, spread_pp = "super_distr", -4.20
    elif r9_distr:
        label, spread_pp = "distr", -1.42
    elif r9_bounce:
        label, spread_pp = "bounce", (0.94 if bounce_a else 0.85)
    elif r11_tail_surge:
        label, spread_pp = "tail_surge", -1.40
    else:
        label, spread_pp = "neutral", 0.0

    parts = []
    if ret_20d is not None:
        parts.append(f"ret_20d={ret_20d*100:+.1f}%")
    if tr_surge is not None:
        parts.append(f"tr_surge_60={tr_surge:.2f}x")
    if ret_close_30m is not None:
        parts.append(f"ret_close_30m={ret_close_30m*100:+.2f}%")
    if vs_close_30m is not None:
        parts.append(f"vs_close_30m={vs_close_30m*100:.1f}%")
    if super_distr:
        parts.append("🔴 super_distr: R9+R11 同时触发, 超叠加派发")
    elif r9_distr:
        parts.append("🟡 R9 distr: 跨日派发")
    elif r9_bounce:
        parts.append("🟢 R9 bounce: " + ("超跌+爆量" if bounce_a else "跌多+缩量"))
    elif r11_tail_surge:
        parts.append("🟡 R11 tail_surge: 尾盘爆量拉升")

    return {
        "ret_20d": round(ret_20d, 4) if ret_20d is not None else None,
        "tr_surge_60": round(tr_surge, 3) if tr_surge is not None else None,
        "ret_close_30m": round(ret_close_30m, 4) if ret_close_30m is not None else None,
        "vs_close_30m": round(vs_close_30m, 4) if vs_close_30m is not None else None,
        "r9_distr": r9_distr,
        "r9_bounce": r9_bounce,
        "r11_tail_surge": r11_tail_surge,
        "super_distr": super_distr,
        "regime_label": label,
        "expected_spread_pp": spread_pp,
        "detail": "; ".join(parts) if parts else "(数据不足)",
    }


def _neutral_regime() -> Dict[str, Any]:
    return {
        "ret_20d": None, "tr_surge_60": None, "ret_close_30m": None,
        "vs_close_30m": None, "r9_distr": False, "r9_bounce": False,
        "r11_tail_surge": False, "super_distr": False,
        "regime_label": "neutral", "expected_spread_pp": 0.0, "detail": "(数据不足)",
    }


class RegimeProvider:
    """Per-code 量能 regime for the 盯盘 loop (B2).

    Daily close + turnover_rate come from the fa loader (cached per code+day); the
    intraday tail uses today's 5min bars passed in by the loop (no double fetch).
    ``__call__(code, bars_5min_today)`` → :func:`compute_vol_regime` dict, or a
    neutral dict on insufficient data / error (never raises).
    """

    def __init__(self, loader: Any = None, lookback_days: int = 90) -> None:
        self._loader = loader
        self._lookback = int(lookback_days)
        self._daily_cache: Dict[Any, Any] = {}   # (code, asof) -> daily df | None

    def _get_loader(self):
        if self._loader is None:
            from financial_analyst.data.loader_factory import get_default_loader
            self._loader = get_default_loader()
        return self._loader

    def daily_series(self, code: str, asof: Optional[str] = None):
        import pandas as pd
        if asof is None:
            asof = pd.Timestamp.now().strftime("%Y-%m-%d")
        key = (code, asof)
        if key in self._daily_cache:
            return self._daily_cache[key]
        df = None
        try:
            loader = self._get_loader()
            start = (pd.Timestamp(asof) - pd.Timedelta(days=self._lookback * 2)).strftime("%Y-%m-%d")
            q = loader.fetch_quote(code, start, asof, freq="day")
            if q is not None and len(q) > 0 and "close" in q.columns and "trade_date" in q.columns:
                q = q[["trade_date", "close"]].copy()
                b = loader.fetch_daily_basic(code, start, asof)
                if b is not None and len(b) > 0 and "turnover_rate" in b.columns:
                    q = q.merge(b[["trade_date", "turnover_rate"]], on="trade_date", how="left")
                else:
                    q["turnover_rate"] = float("nan")
                df = q.sort_values("trade_date").reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("RegimeProvider.daily_series %s failed: %s", code, exc)
            df = None
        self._daily_cache[key] = df
        return df

    def __call__(self, code: str, bars_5min_today: Any = None) -> Dict[str, Any]:
        try:
            df = self.daily_series(code)
            if df is None or len(df) < 60:
                return _neutral_regime()
            return compute_vol_regime(df["close"], df["turnover_rate"], bars_5min_today)
        except Exception as exc:  # noqa: BLE001
            log.debug("RegimeProvider %s failed: %s", code, exc)
            return _neutral_regime()


__all__ = ["load_negative_warnings", "compute_vol_regime", "RegimeProvider"]
