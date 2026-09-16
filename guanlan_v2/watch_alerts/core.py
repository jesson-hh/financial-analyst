# -*- coding: utf-8 -*-
"""全局预警的耐久规则库与确定性执行器。

LLM 只通过工具研究、起草和管理规则；本模块不调用 LLM。后台循环在交易时段批量读取
可核验报价并计算条件，即使没有打开任何页面也会持续工作。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

_TZ = timezone(timedelta(hours=8))
_ROOT = Path(__file__).resolve().parents[2] / "var" / "watch_alerts"
_CODE_RE = re.compile(r"^(SH|SZ|BJ)\d{6}$")

SUPPORTED_KINDS = frozenset({
    "stock_price_below",
    "stock_price_above",
    "stock_pct_below",
    "stock_pct_above",
    "stock_drawdown_from_high_pct",
    "index_pct_below",
    "index_drop_window_pct",
    "market_proxy_drop_window_pct",
})
_STOCK_KINDS = frozenset({
    "stock_price_below", "stock_price_above", "stock_pct_below",
    "stock_pct_above", "stock_drawdown_from_high_pct",
    "index_pct_below", "index_drop_window_pct",
})


def _now() -> datetime:
    return datetime.now(_TZ)


def _iso(dt: Optional[datetime] = None) -> str:
    value = dt or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=_TZ)
    return value.astimezone(_TZ).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:  # noqa: BLE001 - 缺失/损坏在状态接口显形，执行器保持冷启动
        return fallback


def _number(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是数字") from exc
    if not math.isfinite(out):
        raise ValueError(f"{label} 必须是有限数字")
    return out


def normalize_code(value: Any) -> str:
    raw = str(value or "").strip().upper().replace(".", "")
    if re.fullmatch(r"\d{6}", raw):
        if raw.startswith("6"):
            raw = "SH" + raw
        elif raw.startswith(("0", "3")):
            raw = "SZ" + raw
        elif raw.startswith(("4", "8")):
            raw = "BJ" + raw
    if not _CODE_RE.fullmatch(raw):
        raise ValueError("证券代码须为 SH/SZ/BJ + 6 位数字")
    return raw


def validate_rule(payload: dict[str, Any], *, existing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Validate and normalize one public rule document."""
    src = dict(existing or {})
    src.update(payload or {})
    kind = str(src.get("kind") or "").strip()
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"不支持的预警类型: {kind or '空'}")
    name = str(src.get("name") or "").strip()
    if not name:
        raise ValueError("规则名称不能为空")
    if len(name) > 80:
        raise ValueError("规则名称最多 80 字")

    out = dict(src)
    out["kind"] = kind
    out["name"] = name
    out["threshold"] = _number(src.get("threshold"), "threshold")
    out["enabled"] = bool(src.get("enabled", False))
    out["cooldown_minutes"] = min(1440, max(1, int(src.get("cooldown_minutes") or 30)))
    out["hysteresis"] = max(0.0, _number(src.get("hysteresis", 0.2), "hysteresis"))
    out["note"] = str(src.get("note") or "").strip()[:500]
    out["creator"] = str(src.get("creator") or "human").strip()[:40] or "human"

    if kind in _STOCK_KINDS:
        out["code"] = normalize_code(src.get("code"))
    else:
        out.pop("code", None)

    if kind in {"index_drop_window_pct", "market_proxy_drop_window_pct"}:
        out["window_minutes"] = min(240, max(1, int(src.get("window_minutes") or 15)))
    else:
        out.pop("window_minutes", None)

    if kind == "market_proxy_drop_window_pct":
        out["universe"] = str(src.get("universe") or "csi300_active").strip()
        if out["universe"] != "csi300_active":
            raise ValueError("首版全盘代理只支持 csi300_active")
        out["sample_size"] = min(120, max(20, int(src.get("sample_size") or 60)))
        out["down_ratio_min"] = _number(src.get("down_ratio_min", 0.7), "down_ratio_min")
        if not 0 <= out["down_ratio_min"] <= 1:
            raise ValueError("down_ratio_min 必须在 0 到 1 之间")
    else:
        out.pop("universe", None)
        out.pop("sample_size", None)
        out.pop("down_ratio_min", None)

    if kind.endswith("_below") or kind in {
        "stock_drawdown_from_high_pct", "index_drop_window_pct", "market_proxy_drop_window_pct"
    }:
        if kind not in {"stock_price_below"} and out["threshold"] > 0:
            raise ValueError("跌幅/回撤类 threshold 应为 0 或负数（百分比）")
    return out


class AlertStore:
    """Small atomic JSON/JSONL store. All mutation is serialized in-process."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or os.environ.get("GUANLAN_WATCH_ALERT_ROOT") or _ROOT)
        self.rules_path = self.root / "rules.json"
        self.events_path = self.root / "events.jsonl"
        self.runtime_path = self.root / "runtime.json"
        self._lock = threading.RLock()

    def list_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = _json(self.rules_path, [])
            return [dict(x) for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    def get_rule(self, rule_id: str) -> Optional[dict[str, Any]]:
        return next((x for x in self.list_rules() if x.get("id") == rule_id), None)

    def create_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self.list_rules()
            rule = validate_rule(payload)
            now = _iso()
            rule.update({"id": "wa_" + uuid.uuid4().hex[:12], "created_at": now,
                         "updated_at": now})
            rows.append(rule)
            _atomic_json(self.rules_path, rows)
            return dict(rule)

    def update_rule(self, rule_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self.list_rules()
            for idx, current in enumerate(rows):
                if current.get("id") != rule_id:
                    continue
                forbidden = {"id", "created_at"} & set(patch)
                if forbidden:
                    raise ValueError("id/created_at 不可修改")
                updated = validate_rule(patch, existing=current)
                updated["id"] = current["id"]
                updated["created_at"] = current["created_at"]
                updated["updated_at"] = _iso()
                rows[idx] = updated
                _atomic_json(self.rules_path, rows)
                return dict(updated)
        raise KeyError(rule_id)

    def delete_rule(self, rule_id: str) -> dict[str, Any]:
        with self._lock:
            rows = self.list_rules()
            kept = [x for x in rows if x.get("id") != rule_id]
            if len(kept) == len(rows):
                raise KeyError(rule_id)
            removed = next(x for x in rows if x.get("id") == rule_id)
            _atomic_json(self.rules_path, kept)
            runtime = self.load_runtime()
            (runtime.get("rules") or {}).pop(rule_id, None)
            _atomic_json(self.runtime_path, runtime)
            return dict(removed)

    def append_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def list_events(self, *, limit: int = 50, since: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            if not self.events_path.exists():
                return []
            lines = self.events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        rows: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if since and str(row.get("fired_at") or "") <= since:
                continue
            rows.append(row)
            if len(rows) >= min(500, max(1, int(limit))):
                break
        return rows

    def load_runtime(self) -> dict[str, Any]:
        with self._lock:
            value = _json(self.runtime_path, {"rules": {}, "last_tick": None})
            if not isinstance(value, dict):
                value = {"rules": {}, "last_tick": None}
            if not isinstance(value.get("rules"), dict):
                value["rules"] = {}
            return value

    def save_runtime(self, value: dict[str, Any]) -> None:
        with self._lock:
            _atomic_json(self.runtime_path, value)


def _fetch_quotes(codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector

    out: dict[str, dict[str, Any]] = {}
    unique = sorted(set(codes))
    collector = TencentQuoteCollector()
    for start in range(0, len(unique), 60):
        out.update(collector.fetch(unique[start:start + 60]))
    return out


def _market_codes(sample_size: int) -> list[str]:
    from financial_analyst.data.universe import resolve_universe_codes

    rows = resolve_universe_codes("csi300_active")
    return [normalize_code(x) for x in rows[:sample_size]]


def _parse_iso(raw: Any) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(raw))
        return value.replace(tzinfo=_TZ) if value.tzinfo is None else value.astimezone(_TZ)
    except Exception:
        return None


def _window_observation(state: dict[str, Any], now: datetime, minutes: int) -> Optional[dict[str, Any]]:
    target = now - timedelta(minutes=minutes)
    candidates = []
    for sample in state.get("samples") or []:
        stamp = _parse_iso(sample.get("at"))
        if stamp and stamp <= target:
            candidates.append((stamp, sample))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _condition(rule: dict[str, Any], observed: float) -> bool:
    kind = rule["kind"]
    threshold = float(rule["threshold"])
    if kind in {"stock_price_above", "stock_pct_above"}:
        return observed >= threshold
    return observed <= threshold


def _latched_condition(rule: dict[str, Any], result: dict[str, Any], previous: bool) -> bool:
    """Apply hysteresis only while a rule is latched, so small rebounds do not re-arm it."""
    if not previous:
        return bool(result["condition"])
    observed = float(result["observed"])
    threshold = float(rule["threshold"])
    hysteresis = float(rule.get("hysteresis") or 0.0)
    if rule["kind"] in {"stock_price_above", "stock_pct_above"}:
        return observed >= threshold - hysteresis
    if rule["kind"] == "market_proxy_drop_window_pct":
        detail = result.get("detail") or {}
        return (observed <= threshold + hysteresis
                and float(detail.get("down_ratio") or 0) >= float(rule["down_ratio_min"]))
    return observed <= threshold + hysteresis


class WatchAlertEngine:
    def __init__(self, store: Optional[AlertStore] = None,
                 quote_fn: Optional[Callable[[Iterable[str]], dict[str, dict[str, Any]]]] = None,
                 universe_fn: Optional[Callable[[int], list[str]]] = None):
        self.store = store or AlertStore()
        self.quote_fn = quote_fn or _fetch_quotes
        self.universe_fn = universe_fn or _market_codes
        self._tick_lock = threading.Lock()

    def status(self, now: Optional[datetime] = None) -> dict[str, Any]:
        from guanlan_v2.seats.watcher import market_session_state

        rules = self.store.list_rules()
        runtime = self.store.load_runtime()
        return {
            "ok": True,
            "engine": "deterministic",
            "session": market_session_state(now),
            "rules_total": len(rules),
            "rules_enabled": sum(bool(x.get("enabled")) for x in rules),
            "last_tick": runtime.get("last_tick"),
            "latest_event": (self.store.list_events(limit=1) or [None])[0],
        }

    def tick(self, now: Optional[datetime] = None) -> dict[str, Any]:
        from guanlan_v2.seats.watcher import market_session_state, quote_session_state

        local = now or _now()
        if local.tzinfo is None:
            local = local.replace(tzinfo=_TZ)
        session = market_session_state(local)
        if not self._tick_lock.acquire(blocking=False):
            return {"ok": True, "skipped": "tick_running", "session": session}
        try:
            runtime = self.store.load_runtime()
            rules = [r for r in self.store.list_rules() if r.get("enabled")]
            tick_info = {"at": _iso(local), "market_phase": session["market_phase"],
                         "rules_checked": 0, "events": 0, "errors": []}
            if not session["market_open"]:
                runtime["last_tick"] = tick_info
                self.store.save_runtime(runtime)
                return {"ok": True, **tick_info}

            stock_codes = [r["code"] for r in rules if r["kind"] in _STOCK_KINDS]
            market_rules = [r for r in rules if r["kind"] == "market_proxy_drop_window_pct"]
            max_sample = max((int(r["sample_size"]) for r in market_rules), default=0)
            market_codes = self.universe_fn(max_sample) if max_sample else []
            try:
                quotes = self.quote_fn(stock_codes + market_codes)
            except Exception as exc:  # noqa: BLE001
                tick_info["errors"].append(f"quote_fetch:{type(exc).__name__}:{exc}")
                runtime["last_tick"] = tick_info
                self.store.save_runtime(runtime)
                return {"ok": False, **tick_info}

            states = runtime.setdefault("rules", {})
            fresh: dict[str, dict[str, Any]] = {}
            for code, quote in quotes.items():
                if quote_session_state(quote.get("asof"), local).get("fresh"):
                    fresh[normalize_code(code)] = quote

            for rule in rules:
                rid = rule["id"]
                state = states.setdefault(rid, {})
                try:
                    result = self._evaluate(rule, state, fresh, market_codes, local)
                    if result is None:
                        continue
                    tick_info["rules_checked"] += 1
                    previous = bool(state.get("condition", False))
                    condition = _latched_condition(rule, result, previous)
                    state.update({"condition": condition, "observed": result["observed"],
                                  "checked_at": _iso(local), "detail": result.get("detail")})
                    last_fired = _parse_iso(state.get("last_fired_at"))
                    cooldown_ok = (last_fired is None or
                                   (local - last_fired).total_seconds() >=
                                   int(rule["cooldown_minutes"]) * 60)
                    if condition and not previous and cooldown_ok:
                        event = self._event(rule, result, local)
                        self.store.append_event(event)
                        state["last_fired_at"] = event["fired_at"]
                        state["last_event_id"] = event["id"]
                        tick_info["events"] += 1
                except Exception as exc:  # noqa: BLE001 - 单条坏规则不拖垮全盘
                    tick_info["errors"].append(f"{rid}:{type(exc).__name__}:{exc}")
            # Keep only live rules and a bounded sample history.
            ids = {r["id"] for r in rules}
            runtime["rules"] = {k: v for k, v in states.items() if k in ids}
            runtime["last_tick"] = tick_info
            self.store.save_runtime(runtime)
            return {"ok": not tick_info["errors"], **tick_info}
        finally:
            self._tick_lock.release()

    def _evaluate(self, rule: dict[str, Any], state: dict[str, Any],
                  fresh: dict[str, dict[str, Any]], market_codes: list[str],
                  now: datetime) -> Optional[dict[str, Any]]:
        kind = rule["kind"]
        if kind in _STOCK_KINDS:
            quote = fresh.get(rule["code"])
            if not quote:
                return None
            if kind in {"stock_price_below", "stock_price_above"}:
                observed = _number(quote.get("price"), "price")
            elif kind in {"stock_pct_below", "stock_pct_above", "index_pct_below"}:
                observed = _number(quote.get("changePercent"), "changePercent")
            elif kind == "stock_drawdown_from_high_pct":
                price = _number(quote.get("price"), "price")
                high = _number(quote.get("high"), "high")
                if high <= 0:
                    return None
                observed = (price / high - 1.0) * 100.0
            else:  # index_drop_window_pct
                samples = state.setdefault("samples", [])
                samples.append({"at": _iso(now), "price": _number(quote.get("price"), "price")})
                cutoff = now - timedelta(minutes=int(rule["window_minutes"]) + 10)
                state["samples"] = [x for x in samples if (_parse_iso(x.get("at")) or now) >= cutoff]
                base = _window_observation(state, now, int(rule["window_minutes"]))
                if not base or not base.get("price"):
                    return None
                observed = (state["samples"][-1]["price"] / float(base["price"]) - 1.0) * 100.0
            return {"observed": round(observed, 4), "condition": _condition(rule, observed),
                    "detail": {"code": rule["code"], "name": quote.get("name"),
                               "price": quote.get("price"), "quote_asof": quote.get("asof")}}

        codes = market_codes[:int(rule["sample_size"])]
        prices = {c: float(fresh[c]["price"]) for c in codes
                  if c in fresh and fresh[c].get("price") and float(fresh[c]["price"]) > 0}
        minimum = max(10, int(len(codes) * 0.6))
        if len(prices) < minimum:
            return None
        samples = state.setdefault("samples", [])
        samples.append({"at": _iso(now), "prices": prices})
        cutoff = now - timedelta(minutes=int(rule["window_minutes"]) + 10)
        state["samples"] = [x for x in samples if (_parse_iso(x.get("at")) or now) >= cutoff]
        base = _window_observation(state, now, int(rule["window_minutes"]))
        if not base:
            return None
        old = base.get("prices") or {}
        returns = [(prices[c] / float(old[c]) - 1.0) * 100.0 for c in prices
                   if c in old and float(old[c]) > 0]
        if len(returns) < minimum:
            return None
        proxy = sum(returns) / len(returns)
        down_ratio = sum(x < 0 for x in returns) / len(returns)
        cond = proxy <= float(rule["threshold"]) and down_ratio >= float(rule["down_ratio_min"])
        return {"observed": round(proxy, 4), "condition": cond,
                "detail": {"proxy_return_pct": round(proxy, 4),
                           "down_ratio": round(down_ratio, 4), "valid_codes": len(returns),
                           "window_minutes": rule["window_minutes"], "universe": rule["universe"]}}

    @staticmethod
    def _event(rule: dict[str, Any], result: dict[str, Any], now: datetime) -> dict[str, Any]:
        return {
            "id": "we_" + uuid.uuid4().hex[:14],
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "kind": rule["kind"],
            "severity": "red",
            "title": f"预警触发 · {rule['name']}",
            "message": f"观测值 {result['observed']:.2f}，阈值 {float(rule['threshold']):.2f}",
            "observed": result["observed"],
            "threshold": rule["threshold"],
            "detail": result.get("detail") or {},
            "fired_at": _iso(now),
        }


_DEFAULT_ENGINE: Optional[WatchAlertEngine] = None


def get_default_engine() -> WatchAlertEngine:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = WatchAlertEngine()
    return _DEFAULT_ENGINE


async def run_loop(*, engine: Optional[WatchAlertEngine] = None,
                   interval_seconds: Optional[int] = None) -> None:
    """Always-on process loop. Closed sessions still update visible status, never fetch quotes."""
    worker = engine or get_default_engine()
    interval = max(10, int(interval_seconds or os.environ.get("GUANLAN_WATCH_ALERT_INTERVAL", "30")))
    while True:
        try:
            await asyncio.to_thread(worker.tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - next tick recovers; status records ordinary failures
            pass
        await asyncio.sleep(interval)
