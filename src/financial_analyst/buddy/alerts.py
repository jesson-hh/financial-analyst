"""Price-alert engine for the buddy 盯盘 (watch) feature.

Pure-Python: rules are stored in ``~/.financial-analyst/alerts.yaml``,
evaluated against a price provider injected by the caller (the buddy
app passes XueqiuStockCollector). No I/O beyond the yaml file, so the
whole thing is unit-testable without a terminal or network.

Rule kinds
----------
- ``price_below``  fire when price <= threshold     (止损位 / 抄底位)
- ``price_above``  fire when price >= threshold     (止盈位 / 突破位)
- ``pct_above``    fire when day change% >= threshold (涨幅突破, e.g. +5)
- ``pct_below``    fire when day change% <= threshold (跌幅突破, e.g. -5)

Each rule has a composite natural key ``{code}:{kind}`` so re-adding the
same code+kind updates the threshold instead of duplicating.

Cooldown
--------
``evaluate`` won't re-fire a rule whose ``last_fired`` is within
``cooldown_min`` minutes, so a stock parked just past a threshold
doesn't spam the transcript every tick.
"""
from __future__ import annotations
import datetime as _dt
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

VALID_KINDS = ("price_below", "price_above", "pct_above", "pct_below")

_KIND_LABEL = {
    "price_below": "跌破",
    "price_above": "涨破",
    "pct_above": "涨幅≥",
    "pct_below": "跌幅≤",
}


def _now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def market_session(now: Optional[_dt.datetime] = None) -> str:
    """Rough A-share trading-session classifier (no holiday calendar).

    Returns one of:
      'open'    9:30-11:30 or 13:00-15:00 on a weekday
      'lunch'   11:30-13:00 weekday (午休)
      'closed'  weekday outside trading hours
      'weekend' Sat/Sun

    Holidays aren't modelled (would need a calendar) — the realtime quote's
    ``market_status`` field is the authoritative tie-breaker for whether a
    price is live; this helper just avoids the obvious off-hours spinning.
    """
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:
        return "weekend"
    t = now.time()
    if _dt.time(9, 30) <= t <= _dt.time(11, 30):
        return "open"
    if _dt.time(13, 0) <= t <= _dt.time(15, 0):
        return "open"
    if _dt.time(11, 30) < t < _dt.time(13, 0):
        return "lunch"
    return "closed"


def is_trading_now(now: Optional[_dt.datetime] = None) -> bool:
    return market_session(now) == "open"


def parse_pct(s: Any) -> Optional[float]:
    """'-0.30%' → -0.30 · 1.5 → 1.5 · '' / None → None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("%", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


@dataclass
class AlertRule:
    code: str
    kind: str
    threshold: float
    note: str = ""
    created_at: str = field(default_factory=_now_str)
    last_fired: Optional[str] = None

    @property
    def id(self) -> str:
        return f"{self.code}:{self.kind}"

    def describe(self) -> str:
        label = _KIND_LABEL.get(self.kind, self.kind)
        unit = "%" if self.kind.startswith("pct") else ""
        note = f" ({self.note})" if self.note else ""
        return f"{self.code} {label} {self.threshold}{unit}{note}"

    def check(self, price: Optional[float], change_pct: Optional[float]) -> bool:
        """True if the rule's condition is met by the given quote."""
        if self.kind == "price_below":
            return price is not None and price <= self.threshold
        if self.kind == "price_above":
            return price is not None and price >= self.threshold
        if self.kind == "pct_above":
            return change_pct is not None and change_pct >= self.threshold
        if self.kind == "pct_below":
            return change_pct is not None and change_pct <= self.threshold
        return False

    def _fired_recently(self, cooldown_min: float) -> bool:
        if not self.last_fired:
            return False
        try:
            t = _dt.datetime.strptime(self.last_fired, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        age_min = (_dt.datetime.now() - t).total_seconds() / 60.0
        return age_min < cooldown_min


class AlertStore:
    """YAML-backed alert list keyed by (code, kind)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (Path.home() / ".financial-analyst" / "alerts.yaml")
        self._rules: Dict[str, AlertRule] = {}
        self.load()

    def load(self) -> None:
        self._rules = {}
        if not self.path.exists():
            return
        try:
            import yaml
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        for item in data.get("alerts", []) or []:
            try:
                rule = AlertRule(
                    code=str(item["code"]).upper(),
                    kind=str(item["kind"]),
                    threshold=float(item["threshold"]),
                    note=str(item.get("note", "")),
                    created_at=str(item.get("created_at", _now_str())),
                    last_fired=item.get("last_fired"),
                )
                if rule.kind in VALID_KINDS:
                    self._rules[rule.id] = rule
            except (KeyError, ValueError, TypeError):
                continue

    def save(self) -> None:
        try:
            import yaml
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"alerts": [asdict(r) for r in self._rules.values()]}
            self.path.write_text(
                yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
            )
        except Exception:
            pass

    def add(self, code: str, kind: str, threshold: float, note: str = "") -> AlertRule:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
        rule = AlertRule(code=code.upper(), kind=kind, threshold=float(threshold), note=note)
        self._rules[rule.id] = rule  # natural-key upsert
        self.save()
        return rule

    def remove(self, rule_id: str) -> bool:
        # Accept either "CODE:kind" or just "CODE" (removes all kinds for it)
        if rule_id in self._rules:
            del self._rules[rule_id]
            self.save()
            return True
        code = rule_id.upper()
        matched = [rid for rid in self._rules if rid.split(":")[0] == code]
        for rid in matched:
            del self._rules[rid]
        if matched:
            self.save()
        return bool(matched)

    def list(self) -> List[AlertRule]:
        return list(self._rules.values())

    def __len__(self) -> int:
        return len(self._rules)


def distinct_codes(store: AlertStore) -> List[str]:
    """Unique stock codes across all rules, in first-seen order."""
    seen: List[str] = []
    for r in store.list():
        if r.code not in seen:
            seen.append(r.code)
    return seen


def evaluate(
    store: AlertStore,
    quote_provider: Callable[[str], Optional[Dict[str, Any]]],
    cooldown_min: float = 30.0,
    max_codes: int = 8,
) -> List[Tuple[AlertRule, Dict[str, Any]]]:
    """Check every rule against a fresh quote. Returns fired (rule, quote)
    pairs and stamps ``last_fired`` on each.

    ``quote_provider(code)`` returns a dict with at least ``price`` and
    ``changePercent`` (XueqiuStockCollector shape), or None.

    Cost protection (v1.9.1): the provider is the slow path (opencli =
    2-5 s/股, Chrome). Two guards:
      - **same-code dedup**: each distinct code is fetched ONCE per round
        even if several rules watch it (price_below + price_above 同股).
      - **max_codes cap**: at most ``max_codes`` distinct codes are
        evaluated per round, so a user with 50 alerts doesn't melt Chrome.
        Codes beyond the cap are skipped this round (caller can warn).
    """
    fired: List[Tuple[AlertRule, Dict[str, Any]]] = []
    dirty = False
    active = set(distinct_codes(store)[:max_codes])
    quote_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    for rule in store.list():
        if rule.code not in active:
            continue
        if rule._fired_recently(cooldown_min):
            continue
        if rule.code not in quote_cache:
            try:
                quote_cache[rule.code] = quote_provider(rule.code)
            except Exception:
                quote_cache[rule.code] = None
        quote = quote_cache[rule.code]
        if not quote:
            continue
        price = quote.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        change_pct = parse_pct(quote.get("changePercent") or quote.get("change_pct"))
        if rule.check(price, change_pct):
            rule.last_fired = _now_str()
            dirty = True
            fired.append((rule, quote))
    if dirty:
        store.save()
    return fired
