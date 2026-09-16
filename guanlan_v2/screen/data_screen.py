# -*- coding: utf-8 -*-
"""Deterministic data-screening Skill used by Weiwo and workflow adapters.

The existing ``/screen/run`` endpoint ranks a pre-computed v4 candidate pool.
This module serves a different contract: resolve the requested universe, apply
closed typed rules to every loaded security, and return auditable per-rule
evidence.  Presets are only syntax sugar and are compiled into the same typed
rules accepted by the evaluator.

No model-generated Python or unrestricted expression is evaluated here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "guanlan.data_screen.v1"
RUN_SCHEMA_VERSION = "guanlan.data_screen_run.v1"
_RUN_ID_RE = re.compile(r"^ds_[0-9a-f]{16}$")
_ALLOWED_FIELDS = frozenset({"open", "high", "low", "close", "volume", "amount", "returns"})
_ALLOWED_OPS = frozenset({">", ">=", "<", "<=", "==", "!="})
_UNIVERSE_ALIASES = {
    "全a": "all_a", "全a股": "all_a", "全市场": "all_a", "a股": "all_a",
    "沪深300": "csi300", "中证500": "csi500", "中证800": "csi800",
    "中证1000": "csi1000",
}
_ALL_A_MIN_COVERAGE_RATIO = 0.80


class DataScreenSpecError(ValueError):
    """The caller supplied an unsupported or ambiguous screening rule."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _codes_digest(codes: Iterable[Any]) -> str:
    return _digest(sorted(dict.fromkeys(str(code) for code in codes)))


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _strict_json_copy(value: Any, *, path: str = "request", depth: int = 0) -> Any:
    """Validate and copy the wire contract without Python/JSON coercions."""
    if depth > 32:
        raise DataScreenSpecError(f"{path} 嵌套过深")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataScreenSpecError(f"{path} 含 NaN/Infinity，必须是严格 JSON")
        return value
    if isinstance(value, list):
        return [_strict_json_copy(item, path=f"{path}[{i}]", depth=depth + 1)
                for i, item in enumerate(value)]
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DataScreenSpecError(f"{path} 的键必须是字符串")
            out[key] = _strict_json_copy(item, path=f"{path}.{key}", depth=depth + 1)
        return out
    raise DataScreenSpecError(f"{path} 含非 JSON 类型: {type(value).__name__}")


def _safe_rejected_request(value: Any) -> Any:
    """Produce a bounded, always-serialisable audit value for invalid input."""
    try:
        copied = _strict_json_copy(value)
        encoded = _canonical_json(copied)
        if len(encoded.encode("utf-8")) <= 64 * 1024:
            return copied
        return {
            "truncated": True,
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "preview": encoded[:4096],
        }
    except Exception:
        try:
            preview = repr(value)[:4096]
        except Exception:
            preview = f"<{type(value).__name__}>"
        return {"unserializable_type": type(value).__name__, "preview": preview}


def _reject_unknown_keys(raw: Mapping[str, Any], allowed: Iterable[str], *, path: str) -> None:
    unknown = sorted(str(key) for key in raw.keys() if key not in set(allowed))
    if unknown:
        raise DataScreenSpecError(f"{path} 含未知字段: {', '.join(unknown)}")


def _strict_bool(value: Any, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise DataScreenSpecError(f"{name} 必须是布尔值 true/false")
    return value


def _strict_string(value: Any, *, name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DataScreenSpecError(f"{name} 必须是字符串")
    out = value.strip()
    if not allow_empty and not out:
        raise DataScreenSpecError(f"{name} 不得为空")
    return out


def _bounded_int(value: Any, *, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataScreenSpecError(f"{name} 必须是整数")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataScreenSpecError(f"{name} 必须是整数") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise DataScreenSpecError(f"{name} 必须是整数")
    out = int(number)
    if not low <= out <= high:
        raise DataScreenSpecError(f"{name} 必须在 {low}~{high} 之间")
    return out


def _bounded_float(value: Any, *, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataScreenSpecError(f"{name} 必须是数值")
    out = _finite(value)
    if out is None or not low <= out <= high:
        raise DataScreenSpecError(f"{name} 必须在 {low:g}~{high:g} 之间")
    return out


def _series_field(field: str) -> Dict[str, Any]:
    return {"type": "field", "field": field}


def _rolling_mean(field: str, window: int) -> Dict[str, Any]:
    return {"type": "rolling_mean", "field": field, "window": window}


def _bias(field: str, baseline: Mapping[str, Any]) -> Dict[str, Any]:
    return {"type": "bias", "field": field, "baseline": dict(baseline)}


def _compile_ma_trend(params: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    _reject_unknown_keys(params, {
        "ma_window", "rising_transitions", "below_lookback", "max_consecutive_below",
        "min_bias", "max_bias",
    }, path="selector.params")
    window = _bounded_int(params.get("ma_window", 5), name="ma_window", low=2, high=250)
    transitions = _bounded_int(
        params.get("rising_transitions", 3), name="rising_transitions", low=1, high=20,
    )
    below_lookback = _bounded_int(
        params.get("below_lookback", 5), name="below_lookback", low=2, high=60,
    )
    max_consecutive = _bounded_int(
        params.get("max_consecutive_below", 1), name="max_consecutive_below",
        low=0, high=below_lookback,
    )
    min_bias = _bounded_float(params.get("min_bias", 0.0), name="min_bias", low=-1.0, high=2.0)
    max_bias = _bounded_float(params.get("max_bias", 0.03), name="max_bias", low=-1.0, high=2.0)
    if min_bias > max_bias:
        raise DataScreenSpecError("min_bias 不能大于 max_bias")

    close = _series_field("close")
    ma = _rolling_mean("close", window)
    bias = _bias("close", ma)
    rules = [
        {"id": "price_above_ma", "label": f"收盘价高于{window}日均线", "type": "compare",
         "left": close, "op": ">", "right": ma},
        {"id": "ma_strict_rise", "label": f"{window}日均线连续{transitions}次严格上升",
         "type": "strict_rise", "series": ma, "transitions": transitions},
        {"id": "no_consecutive_break", "label": (
            f"近{below_lookback}日收盘低于{window}日均线的最大连续次数不超过{max_consecutive}"
         ), "type": "max_consecutive", "predicate": {
             "type": "compare", "left": close, "op": "<", "right": ma,
         }, "lookback": below_lookback, "max_count": max_consecutive},
        {"id": "ma_bias_range", "label": (
            f"相对{window}日均线乖离率在{min_bias * 100:.2f}%~{max_bias * 100:.2f}%"
         ), "type": "range", "value": bias, "min": min_bias, "max": max_bias,
         "inclusive": True},
    ]
    score = {
        "type": "ma_fit", "value": bias, "min_bias": min_bias, "max_bias": max_bias,
        "direction": "desc",
        "formula": "100 × (1 - (bias-min_bias)/(max_bias-min_bias)); 越贴近均线越高",
    }
    return rules, score


def _normalise_series_expr(raw: Any, *, path: str = "series") -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DataScreenSpecError(f"{path} 必须是对象")
    typ = _strict_string(raw.get("type"), name=f"{path}.type")
    if typ == "field":
        _reject_unknown_keys(raw, {"type", "field"}, path=path)
        field = _strict_string(raw.get("field"), name=f"{path}.field")
        if field not in _ALLOWED_FIELDS:
            raise DataScreenSpecError(f"{path}.field 不支持: {field or '(空)'}")
        return {"type": "field", "field": field}
    if typ in ("rolling_mean", "rolling_max"):
        _reject_unknown_keys(raw, {"type", "field", "window"}, path=path)
        field = _strict_string(raw.get("field"), name=f"{path}.field")
        if field not in _ALLOWED_FIELDS:
            raise DataScreenSpecError(f"{path}.field 不支持: {field or '(空)'}")
        window = _bounded_int(raw.get("window"), name=f"{path}.window", low=2, high=250)
        return {"type": typ, "field": field, "window": window}
    if typ == "bias":
        _reject_unknown_keys(raw, {"type", "field", "baseline"}, path=path)
        field = _strict_string(raw.get("field"), name=f"{path}.field")
        if field not in _ALLOWED_FIELDS:
            raise DataScreenSpecError(f"{path}.field 不支持: {field or '(空)'}")
        baseline = _normalise_series_expr(raw.get("baseline"), path=f"{path}.baseline")
        return {"type": "bias", "field": field, "baseline": baseline}
    raise DataScreenSpecError(f"{path}.type 不支持: {typ or '(空)'}")


def _normalise_compare(raw: Any, *, path: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DataScreenSpecError(f"{path} 必须是 compare 规则")
    if _strict_string(raw.get("type"), name=f"{path}.type") != "compare":
        raise DataScreenSpecError(f"{path} 必须是 compare 规则")
    _reject_unknown_keys(raw, {"type", "left", "op", "right"}, path=path)
    op = _strict_string(raw.get("op"), name=f"{path}.op")
    if op not in _ALLOWED_OPS:
        raise DataScreenSpecError(f"{path}.op 不支持: {op or '(空)'}")
    return {"type": "compare",
            "left": _normalise_series_expr(raw.get("left"), path=f"{path}.left"),
            "op": op,
            "right": _normalise_series_expr(raw.get("right"), path=f"{path}.right")}


def _normalise_rule(raw: Any, index: int) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DataScreenSpecError(f"conditions 第{index + 1}项必须是对象")
    typ = _strict_string(raw.get("type"), name=f"conditions[{index}].type")
    rid = (_strict_string(raw["id"], name=f"conditions[{index}].id")
           if "id" in raw else f"rule_{index + 1}")
    label = (_strict_string(raw["label"], name=f"conditions[{index}].label")
             if "label" in raw else rid)
    if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", rid):
        raise DataScreenSpecError(f"conditions 第{index + 1}项 id 非法")
    base = {"id": rid, "label": label[:120], "type": typ}
    if typ == "compare":
        _reject_unknown_keys(raw, {"id", "label", "type", "left", "op", "right"}, path=rid)
        compare_raw = {key: raw.get(key) for key in ("type", "left", "op", "right")}
        base.update({k: v for k, v in _normalise_compare(compare_raw, path=rid).items()
                     if k != "type"})
        return base
    if typ == "strict_rise":
        _reject_unknown_keys(raw, {"id", "label", "type", "series", "transitions"}, path=rid)
        base.update({
            "series": _normalise_series_expr(raw.get("series"), path=f"{rid}.series"),
            "transitions": _bounded_int(raw.get("transitions"), name=f"{rid}.transitions",
                                         low=1, high=20),
        })
        return base
    if typ == "max_consecutive":
        _reject_unknown_keys(
            raw, {"id", "label", "type", "predicate", "lookback", "max_count"}, path=rid,
        )
        lookback = _bounded_int(raw.get("lookback"), name=f"{rid}.lookback", low=2, high=60)
        base.update({
            "predicate": _normalise_compare(raw.get("predicate"), path=f"{rid}.predicate"),
            "lookback": lookback,
            "max_count": _bounded_int(raw.get("max_count"), name=f"{rid}.max_count",
                                       low=0, high=lookback),
        })
        return base
    if typ == "range":
        _reject_unknown_keys(raw, {"id", "label", "type", "value", "min", "max", "inclusive"}, path=rid)
        lo = _bounded_float(raw.get("min"), name=f"{rid}.min", low=-1e15, high=1e15)
        hi = _bounded_float(raw.get("max"), name=f"{rid}.max", low=-1e15, high=1e15)
        if lo > hi:
            raise DataScreenSpecError(f"{rid}.min 不能大于 max")
        inclusive = (_strict_bool(raw["inclusive"], name=f"{rid}.inclusive")
                     if "inclusive" in raw else True)
        base.update({"value": _normalise_series_expr(raw.get("value"), path=f"{rid}.value"),
                     "min": lo, "max": hi, "inclusive": inclusive})
        return base
    raise DataScreenSpecError(f"conditions 第{index + 1}项 type 不支持: {typ or '(空)'}")


def _normalise_code(code: Any) -> Optional[str]:
    c = str(code or "").strip().upper().replace(".", "")
    if re.match(r"^(SH|SZ|BJ)\d{6}$", c):
        return c
    if re.match(r"^\d{6}(SH|SZ|BJ)$", c):
        return c[-2:] + c[:6]
    if re.match(r"^\d{6}$", c):
        if c.startswith(("4", "8")):
            return "BJ" + c
        return ("SH" if c.startswith(("5", "6", "9")) else "SZ") + c
    return None


def compile_data_screen_spec(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile a user/tool request into the closed canonical v1 rule schema."""
    if not isinstance(request, Mapping):
        raise DataScreenSpecError("请求必须是对象")
    request = _strict_json_copy(request)
    if len(_canonical_json(request).encode("utf-8")) > 1024 * 1024:
        raise DataScreenSpecError("请求超过 1 MiB 上限")
    _reject_unknown_keys(request, {
        "selector", "preset", "params", "conditions", "universe", "codes",
        "as_of", "end", "freq", "top_n", "topN", "filters", "score",
    }, path="request")
    selector = request.get("selector")
    if "selector" not in request:
        # Friendly flat form used by the console adapter.
        if "preset" in request and "conditions" in request:
            raise DataScreenSpecError("顶层 preset 与 conditions 不得同时提供")
        if "params" in request and "preset" not in request:
            raise DataScreenSpecError("顶层 params 只能与 preset 一起提供")
        selector = ({"preset": request.get("preset"),
                     "params": request["params"] if "params" in request else {}}
                    if "preset" in request else
                    {"conditions": request.get("conditions")})
    elif any(key in request for key in ("preset", "params", "conditions")):
        raise DataScreenSpecError("提供 selector 时不得再提供顶层 preset/params/conditions")
    if not isinstance(selector, Mapping):
        raise DataScreenSpecError("selector 必须是对象")
    has_preset = "preset" in selector
    has_conditions = "conditions" in selector
    if has_preset == has_conditions:
        raise DataScreenSpecError("selector 必须且只能提供 preset 或 conditions 其中一个")

    source_selector: Dict[str, Any]
    if has_preset:
        _reject_unknown_keys(selector, {"preset", "params"}, path="selector")
        preset = _strict_string(selector.get("preset"), name="selector.preset").lower()
        if preset in ("ma5_trend", "ma_trend"):
            raw_params = selector["params"] if "params" in selector else {}
            if not isinstance(raw_params, Mapping):
                raise DataScreenSpecError("selector.params 必须是对象")
            rules, default_score = _compile_ma_trend(raw_params)
            source_selector = {"preset": "ma_trend", "params": {
                "ma_window": rules[0]["right"]["window"],
                "rising_transitions": rules[1]["transitions"],
                "below_lookback": rules[2]["lookback"],
                "max_consecutive_below": rules[2]["max_count"],
                "min_bias": rules[3]["min"],
                "max_bias": rules[3]["max"],
            }}
        else:
            raise DataScreenSpecError(f"不支持的 preset: {preset}")
    else:
        _reject_unknown_keys(selector, {"conditions", "combine"}, path="selector")
        if "combine" in selector and selector.get("combine") != "all":
            raise DataScreenSpecError("selector.combine v1 仅支持 all")
        raw_rules = selector.get("conditions")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise DataScreenSpecError("conditions 必须是非空数组")
        if len(raw_rules) > 16:
            raise DataScreenSpecError("conditions 最多 16 条")
        rules = [_normalise_rule(rule, i) for i, rule in enumerate(raw_rules)]
        source_selector = {"conditions": rules, "combine": "all"}
        default_score = None
    ids = [r["id"] for r in rules]
    if len(ids) != len(set(ids)):
        raise DataScreenSpecError("conditions.id 不得重复")

    score_raw = request.get("score")
    if score_raw is None:
        score = default_score
    elif not isinstance(score_raw, Mapping):
        raise DataScreenSpecError("score 必须是对象")
    elif _strict_string(score_raw.get("type"), name="score.type") == "series":
        _reject_unknown_keys(score_raw, {"type", "value", "direction", "formula"}, path="score")
        direction = (_strict_string(score_raw["direction"], name="score.direction").lower()
                     if "direction" in score_raw else "desc")
        if direction not in ("asc", "desc"):
            raise DataScreenSpecError("score.direction 仅支持 asc/desc")
        score = {"type": "series",
                 "value": _normalise_series_expr(score_raw.get("value"), path="score.value"),
                 "direction": direction,
                 "formula": (_strict_string(score_raw["formula"], name="score.formula")[:200]
                             if "formula" in score_raw else "原始截面值")}
    elif _strict_string(score_raw.get("type"), name="score.type") == "ma_fit" and default_score is not None:
        _reject_unknown_keys(score_raw, {"type"}, path="score")
        score = default_score
    else:
        raise DataScreenSpecError("score.type 不支持")

    filters_raw = request["filters"] if "filters" in request else {}
    if not isinstance(filters_raw, Mapping):
        raise DataScreenSpecError("filters 必须是对象")
    _reject_unknown_keys(filters_raw, {
        "exclude_st", "require_asof_trade", "min_avg_amount_yuan", "amount_window",
        "amount_min_observations", "require_strict_metadata_pit",
    }, path="filters")
    amount_window = _bounded_int(filters_raw.get("amount_window", 20),
                                 name="filters.amount_window", low=1, high=250)
    min_obs = _bounded_int(filters_raw.get("amount_min_observations", amount_window),
                           name="filters.amount_min_observations", low=1, high=amount_window)
    min_avg_amount = _bounded_float(filters_raw.get("min_avg_amount_yuan", 100_000_000),
                                    name="filters.min_avg_amount_yuan", low=0, high=1e15)
    filters = {
        "exclude_st": (_strict_bool(filters_raw["exclude_st"], name="filters.exclude_st")
                       if "exclude_st" in filters_raw else True),
        "require_asof_trade": (
            _strict_bool(filters_raw["require_asof_trade"], name="filters.require_asof_trade")
            if "require_asof_trade" in filters_raw else True
        ),
        "min_avg_amount_yuan": min_avg_amount,
        "amount_window": amount_window,
        "amount_min_observations": min_obs,
        "require_strict_metadata_pit": (
            _strict_bool(filters_raw["require_strict_metadata_pit"],
                         name="filters.require_strict_metadata_pit")
            if "require_strict_metadata_pit" in filters_raw else False
        ),
    }

    raw_universe = (_strict_string(request["universe"], name="universe")
                    if "universe" in request else "all_a")
    universe = _UNIVERSE_ALIASES.get(raw_universe.lower(), raw_universe.lower())
    raw_codes = request["codes"] if "codes" in request else []
    if not isinstance(raw_codes, list):
        raise DataScreenSpecError("codes 必须是数组")
    codes = []
    for raw in raw_codes:
        if not isinstance(raw, str):
            raise DataScreenSpecError("codes 每一项必须是字符串")
        code = _normalise_code(raw)
        if code is None:
            raise DataScreenSpecError(f"无法识别股票代码: {raw}")
        if code not in codes:
            codes.append(code)
    if codes:
        universe = "explicit"
    elif universe not in {"all_a", "csi300", "csi500", "csi800", "csi1000", "csi_fast", "all"}:
        raise DataScreenSpecError(f"不支持的 universe: {raw_universe}")

    if "as_of" in request and "end" in request and request["as_of"] != request["end"]:
        raise DataScreenSpecError("as_of 与 end 不得同时给出不同值")
    raw_as_of = (request["as_of"] if "as_of" in request else
                 (request["end"] if "end" in request else None))
    if raw_as_of is None:
        as_of = None
    else:
        as_of = _strict_string(raw_as_of, name="as_of")
    if as_of:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of) is None:
            raise DataScreenSpecError("as_of 必须是 YYYY-MM-DD")
        try:
            as_of = date.fromisoformat(as_of).isoformat()
        except ValueError as exc:
            raise DataScreenSpecError("as_of 必须是 YYYY-MM-DD") from exc
    freq = (_strict_string(request["freq"], name="freq").lower()
            if "freq" in request else "day")
    if freq != "day":
        raise DataScreenSpecError("数据筛选 Skill v1 仅支持 day；分钟级请使用实时盯盘通道")
    if "top_n" in request and "topN" in request and request["top_n"] != request["topN"]:
        raise DataScreenSpecError("top_n 与 topN 不得同时给出不同值")
    raw_top_n = (request["top_n"] if "top_n" in request else
                 (request["topN"] if "topN" in request else 5))
    top_n = _bounded_int(raw_top_n,
                         name="top_n", low=1, high=200)

    canonical = {
        "schema_version": SCHEMA_VERSION,
        "universe": universe,
        "codes": codes,
        "freq": freq,
        "requested_as_of": as_of,
        "top_n": top_n,
        "combine": "all",
        "rules": rules,
        "filters": filters,
        "score": score,
    }
    canonical["spec_digest"] = _digest(canonical)
    canonical["source_selector"] = source_selector
    return canonical


def _ts_to_qlib(ts_code: Any) -> Optional[str]:
    raw = str(ts_code or "").strip().upper()
    match = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", raw)
    return (match.group(2) + match.group(1)) if match else _normalise_code(raw)


_METADATA_LOCK = threading.Lock()
_METADATA_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "rows": None}


def _stock_basic_snapshot() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Load the current company-master snapshot and disclose its non-PIT nature."""
    import pandas as pd
    from financial_analyst.data.paths import get_data_paths

    path = get_data_paths().parquet_root / "tushare_stock_basic.parquet"
    if not path.exists():
        return {}, {"source": str(path), "available": False, "strict_pit": False,
                    "snapshot_date": None}
    mtime = path.stat().st_mtime_ns
    key = str(path.resolve())
    with _METADATA_LOCK:
        if _METADATA_CACHE["path"] == key and _METADATA_CACHE["mtime"] == mtime:
            rows = _METADATA_CACHE["rows"]
        else:
            frame = pd.read_parquet(path)
            rows = {}
            for row in frame.to_dict(orient="records"):
                code = _ts_to_qlib(row.get("ts_code") or row.get("code"))
                if code:
                    rows[code] = {
                        "name": (None if pd.isna(row.get("name")) else str(row.get("name"))),
                        "industry": (None if pd.isna(row.get("industry")) else str(row.get("industry"))),
                        "list_date": (None if pd.isna(row.get("list_date")) else str(row.get("list_date"))),
                        "list_status": (None if pd.isna(row.get("list_status")) else str(row.get("list_status"))),
                    }
            _METADATA_CACHE.update({"path": key, "mtime": mtime, "rows": rows})
    snapshot_date = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    return dict(rows), {"source": str(path), "available": True, "strict_pit": False,
                        "snapshot_date": snapshot_date, "rows": len(rows)}


def resolve_data_screen_universe(spec: Mapping[str, Any]) -> Tuple[List[str], Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    metadata, metadata_provenance = _stock_basic_snapshot()
    if spec.get("codes"):
        codes = list(spec["codes"])
        return codes, {"id": "explicit", "label": "指定股票", "n_resolved": len(codes),
                       "source": "request.codes", "codes_digest": _codes_digest(codes),
                       "strict_pit": True}, metadata, metadata_provenance
    universe = str(spec.get("universe") or "all_a")
    if universe == "all_a":
        codes = sorted(code for code, row in metadata.items()
                       if str(row.get("list_status") or "L").upper() in ("", "L", "NONE"))
        if not codes:
            raise RuntimeError("全A股票池需要 tushare_stock_basic.parquet，当前文件缺失或为空")
        return codes, {"id": "all_a", "label": "全A（当前上市公司快照）",
                       "n_resolved": len(codes), "source": metadata_provenance.get("source"),
                       "snapshot_date": metadata_provenance.get("snapshot_date"),
                       "codes_digest": _codes_digest(codes), "strict_pit": False}, metadata, metadata_provenance
    from financial_analyst.data.universe import resolve_universe_codes
    codes = sorted(dict.fromkeys(str(c) for c in resolve_universe_codes(universe)))
    if not codes:
        raise RuntimeError(f"股票池 {universe!r} 解析为空")
    label = {"csi300": "沪深300", "csi500": "中证500", "csi800": "中证800",
             "csi1000": "中证1000", "csi_fast": "快速研究池",
             "all": "指数成分并集（旧 all 口径，非全A）"}.get(universe, universe)
    return codes, {"id": universe, "label": label, "n_resolved": len(codes),
                   "source": "resolve_universe_codes", "codes_digest": _codes_digest(codes),
                   "strict_pit": False}, metadata, metadata_provenance


def _required_bars(spec: Mapping[str, Any]) -> int:
    out = int((spec.get("filters") or {}).get("amount_window") or 1)

    def series_need(expr: Mapping[str, Any]) -> int:
        typ = expr.get("type")
        if typ == "field":
            # One return at t consumes close[t] and close[t-1].  Counting that
            # hidden dependency is essential both for loading enough history
            # and for hashing every input that can change a result.
            return 2 if str(expr.get("field")) == "returns" else 1
        if typ in ("rolling_mean", "rolling_max"):
            base = {"type": "field", "field": expr.get("field")}
            return series_need(base) + int(expr.get("window") or 1) - 1
        if typ == "bias":
            numerator = {"type": "field", "field": expr.get("field")}
            return max(series_need(numerator), series_need(expr.get("baseline") or {}))
        return 1

    for rule in spec.get("rules") or []:
        typ = rule.get("type")
        if typ == "compare":
            out = max(out, series_need(rule["left"]), series_need(rule["right"]))
        elif typ == "strict_rise":
            out = max(out, series_need(rule["series"]) + int(rule["transitions"]))
        elif typ == "max_consecutive":
            p = rule["predicate"]
            out = max(out, int(rule["lookback"]) + max(series_need(p["left"]), series_need(p["right"])) - 1)
        elif typ == "range":
            out = max(out, series_need(rule["value"]))
    score = spec.get("score") or {}
    if score.get("value"):
        out = max(out, series_need(score["value"]))
    return max(out, 5)


def _quote_unit_contract(loader: Any) -> Dict[str, Any]:
    """Return the explicit output-unit contract for supported quote loaders.

    Tushare daily bars are raw ``vol=手`` and ``amount=千元``.  Qlib's public
    ``fetch_quote`` path already runs its dimension-ratio repair and returns
    shares/yuan.  Unknown loaders are deliberately not guessed: the liquidity
    filter and ``avg_amount_yuan`` evidence must never silently relabel native
    values as yuan.
    """
    try:
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        from financial_analyst.data.loaders.tushare import TushareLoader

        if isinstance(loader, TushareLoader):
            return {
                "loader_family": "tushare",
                "source_amount_unit": "thousand_yuan",
                "source_volume_unit": "lot_100_shares",
                "amount_unit": "yuan",
                "volume_unit": "share",
                "normalization": "tushare_daily_x1000_amount_x100_volume",
                "verified": True,
            }
        if isinstance(loader, QlibBinaryLoader):
            return {
                "loader_family": "qlib_binary",
                "source_amount_unit": "qlib_auto_checked",
                "source_volume_unit": "qlib_auto_checked",
                "amount_unit": "yuan",
                "volume_unit": "share",
                "normalization": "qlib_fetch_quote_dimension_check",
                "verified": True,
            }
    except Exception:
        # Import/configuration failures do not justify inventing a unit.
        pass
    return {
        "loader_family": type(loader).__name__,
        "source_amount_unit": "unknown",
        "source_volume_unit": "unknown",
        "amount_unit": "unknown",
        "volume_unit": "unknown",
        "normalization": "none",
        "verified": False,
    }


def _normalise_quote_units(frame: Any, contract: Mapping[str, Any]):
    """Normalise a supported loader frame without mutating its cache object."""
    import pandas as pd

    out = frame.copy()
    if contract.get("loader_family") != "tushare":
        return out
    if "amount" in out.columns:
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce") * 1000.0
    volume_col = "vol" if "vol" in out.columns else ("volume" if "volume" in out.columns else None)
    if volume_col:
        out[volume_col] = pd.to_numeric(out[volume_col], errors="coerce") * 100.0
    return out


def _load_light_panel(loader: Any, codes: Sequence[str], start: str, end: str, freq: str):
    import pandas as pd
    from financial_analyst.factors.zoo.panel import PanelData

    unit_contract = _quote_unit_contract(loader)

    def one(code: str):
        try:
            frame = loader.fetch_quote(code, start, end, freq=freq)
        except Exception as exc:  # noqa: BLE001
            return None, (code, f"{type(exc).__name__}: {exc}"[:100])
        if frame is None or len(frame) == 0:
            return None, (code, "empty")
        frame = _normalise_quote_units(frame, unit_contract)
        if isinstance(frame.index, pd.MultiIndex):
            frame = frame.reset_index(level="code" if "code" in frame.index.names else 0, drop=True)
        if "trade_date" in frame.columns:
            frame = frame.set_index("trade_date")
        try:
            frame.index = pd.DatetimeIndex(frame.index)
        except Exception as exc:  # noqa: BLE001
            return None, (code, f"bad_datetime: {exc}"[:100])
        frame = frame[~frame.index.duplicated(keep="last")]
        frame["code"] = code
        frame = frame.set_index("code", append=True)
        frame.index = frame.index.set_names(["datetime", "code"])
        return frame, None

    workers = min(16, (os.cpu_count() or 4) * 2)
    frames: List[Any] = []
    skipped: List[Tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for frame, error in executor.map(one, codes):
            if frame is None:
                skipped.append(error)
            else:
                frames.append(frame)
    if not frames:
        raise RuntimeError(f"没有加载到任何行情（请求 {len(codes)} 只，失败 {len(skipped)} 只）")
    panel = PanelData(pd.concat(frames).sort_index())
    return panel, skipped, unit_contract


def load_data_screen_panel(spec: Mapping[str, Any], *, loader: Any = None):
    codes, universe_meta, metadata, metadata_provenance = resolve_data_screen_universe(spec)
    requested = date.fromisoformat(spec.get("requested_as_of") or date.today().isoformat())
    need = _required_bars(spec)
    start = (requested - timedelta(days=max(90, int(need * 2.3) + 30))).isoformat()
    end = requested.isoformat()
    if loader is None:
        from financial_analyst.data.loader_factory import get_default_loader
        loader = get_default_loader()
    panel, skipped, unit_contract = _load_light_panel(
        loader, codes, start, end, str(spec.get("freq") or "day"),
    )
    load_meta = {
        "start": start, "end": end, "loader": type(loader).__name__,
        "n_requested": len(codes), "n_loaded": panel.n_codes(), "n_skipped": len(skipped),
        "skipped_sample": [{"code": c, "reason": r} for c, r in skipped[:20]],
        **unit_contract,
    }
    return panel, universe_meta, metadata, metadata_provenance, load_meta


def _resolve_series(frame, expr: Mapping[str, Any]):
    import pandas as pd

    typ = expr.get("type")
    if typ == "field":
        field = str(expr["field"])
        if field == "returns":
            return frame["close"].pct_change(fill_method=None)
        if field not in frame.columns:
            return pd.Series(float("nan"), index=frame.index, dtype="float64")
        return pd.to_numeric(frame[field], errors="coerce")
    if typ in ("rolling_mean", "rolling_max"):
        base = _resolve_series(frame, {"type": "field", "field": expr["field"]})
        window = int(expr["window"])
        # Trailing observations, including the current bar; no future values,
        # centering, filling, or partial-window extrema are permitted.
        rolling = base.rolling(window=window, min_periods=window)
        return rolling.max() if typ == "rolling_max" else rolling.mean()
    if typ == "bias":
        numerator = _resolve_series(frame, {"type": "field", "field": expr["field"]})
        denominator = _resolve_series(frame, expr["baseline"])
        return numerator / denominator - 1.0
    raise DataScreenSpecError(f"运行时 series type 不支持: {typ}")


def _compare_values(left, right, op: str):
    # A fixed absolute floor plus a scale-relative tolerance keeps mathematical
    # equality from becoming a false strict rise because of rolling-mean noise.
    tol = (left.abs().combine(right.abs(), max).clip(lower=1.0) * 1e-12)
    if op == ">":
        return left > right + tol
    if op == ">=":
        return left >= right - tol
    if op == "<":
        return left < right - tol
    if op == "<=":
        return left <= right + tol
    if op == "==":
        return (left - right).abs() <= tol
    if op == "!=":
        return (left - right).abs() > tol
    raise DataScreenSpecError(f"运行时 op 不支持: {op}")


def _evaluate_compare(frame, rule: Mapping[str, Any]):
    left = _resolve_series(frame, rule["left"])
    right = _resolve_series(frame, rule["right"])
    valid = left.notna() & right.notna()
    values = _compare_values(left, right, str(rule["op"])).where(valid)
    return values, valid, left, right


def _max_true_streak(values: Iterable[bool]) -> int:
    best = current = 0
    for value in values:
        if bool(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _rule_result(frame, rule: Mapping[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    typ = rule["type"]
    if typ == "compare":
        values, valid, left, right = _evaluate_compare(frame, rule)
        ok = bool(valid.iloc[-1] and values.iloc[-1]) if len(values) else False
        return ok, {"left": _finite(left.iloc[-1]) if len(left) else None,
                    "right": _finite(right.iloc[-1]) if len(right) else None,
                    "op": rule["op"], "valid": bool(valid.iloc[-1]) if len(valid) else False}
    if typ == "strict_rise":
        series = _resolve_series(frame, rule["series"])
        n = int(rule["transitions"]) + 1
        tail = series.tail(n)
        vals = [_finite(v) for v in tail.tolist()]
        deltas = [None if vals[i] is None or vals[i - 1] is None else vals[i] - vals[i - 1]
                  for i in range(1, len(vals))]
        eps = 1e-12
        ok = len(vals) == n and all(d is not None and d > eps for d in deltas)
        return ok, {"values": vals, "deltas": deltas, "transitions": n - 1,
                    "strict": True, "epsilon": eps}
    if typ == "max_consecutive":
        pred, valid, left, right = _evaluate_compare(frame, rule["predicate"])
        n = int(rule["lookback"])
        p_tail, v_tail = pred.tail(n), valid.tail(n)
        complete = len(p_tail) == n and bool(v_tail.all())
        # Pair with the explicit validity mask instead of ``fillna(False)`` on
        # an object-dtype Boolean Series (which triggers pandas' silent
        # downcast warning and is scheduled to change semantics).
        bools = [bool(value) if bool(is_valid) else False
                 for value, is_valid in zip(p_tail.tolist(), v_tail.tolist())]
        streak = _max_true_streak(bools) if complete else None
        ok = bool(complete and streak is not None and streak <= int(rule["max_count"]))
        return ok, {"values": bools, "max_streak": streak, "allowed": int(rule["max_count"]),
                    "lookback": n, "complete": complete,
                    "left_values": [_finite(v) for v in left.tail(n).tolist()],
                    "right_values": [_finite(v) for v in right.tail(n).tolist()]}
    if typ == "range":
        series = _resolve_series(frame, rule["value"])
        value = _finite(series.iloc[-1]) if len(series) else None
        if value is None:
            ok = False
        elif rule.get("inclusive", True):
            eps = max(1.0, abs(value), abs(float(rule["min"])), abs(float(rule["max"]))) * 1e-12
            ok = float(rule["min"]) - eps <= value <= float(rule["max"]) + eps
        else:
            eps = max(1.0, abs(value), abs(float(rule["min"])), abs(float(rule["max"]))) * 1e-12
            ok = float(rule["min"]) + eps < value < float(rule["max"]) - eps
        return ok, {"value": value, "min": float(rule["min"]), "max": float(rule["max"]),
                    "inclusive": bool(rule.get("inclusive", True))}
    raise DataScreenSpecError(f"运行时 rule type 不支持: {typ}")


def _score_result(frame, score: Optional[Mapping[str, Any]]) -> Tuple[Optional[float], Dict[str, Any]]:
    if not score:
        return None, {"formula": None}
    value_series = _resolve_series(frame, score["value"])
    value = _finite(value_series.iloc[-1]) if len(value_series) else None
    if score["type"] == "series":
        return value, {"raw": value, "direction": score["direction"], "formula": score.get("formula")}
    if score["type"] == "ma_fit":
        lo, hi = float(score["min_bias"]), float(score["max_bias"])
        if value is None:
            fit = None
        elif hi == lo:
            fit = 100.0 if abs(value - lo) <= 1e-12 else 0.0
        else:
            fit = max(0.0, min(100.0, 100.0 * (1.0 - (value - lo) / (hi - lo))))
        return fit, {"raw_bias": value, "direction": "desc", "formula": score.get("formula")}
    raise DataScreenSpecError(f"运行时 score type 不支持: {score.get('type')}")


def _frame_snapshot(frame, *, as_of, bars: int,
                    metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
                    metadata_provenance: Optional[Mapping[str, Any]] = None,
                    universe_meta: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    import pandas as pd

    tail = frame.loc[frame.index.get_level_values("datetime") <= as_of]
    tail = tail.groupby(level="code", group_keys=False).tail(bars)
    cols = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in tail.columns]
    stable = tail[cols].sort_index()
    hashed = pd.util.hash_pandas_object(stable, index=True).values.tobytes()
    market_digest = hashlib.sha256(hashed).hexdigest()
    loaded_codes = sorted(str(code) for code in stable.index.get_level_values("code").unique())
    metadata = dict(metadata or {})
    metadata_provenance = dict(metadata_provenance or {})
    universe_meta = dict(universe_meta or {})

    def meta_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass
        return str(value)

    metadata_contract = {
        "rows": [{
            "code": code,
            "name": meta_value((metadata.get(code) or {}).get("name")),
            "industry": meta_value((metadata.get(code) or {}).get("industry")),
            "list_status": meta_value((metadata.get(code) or {}).get("list_status")),
            "list_date": meta_value((metadata.get(code) or {}).get("list_date")),
        } for code in loaded_codes],
        "provenance": {
            key: metadata_provenance.get(key)
            for key in ("source", "available", "strict_pit", "snapshot_date")
        },
    }
    metadata_digest = _digest(metadata_contract)
    resolved_codes_digest = str(
        universe_meta.get("codes_digest") or _codes_digest(loaded_codes)
    )
    universe_contract = {
        key: universe_meta.get(key)
        for key in ("id", "label", "n_resolved", "source", "snapshot_date", "strict_pit")
    }
    universe_contract.update({
        "resolved_codes_digest": resolved_codes_digest,
        "loaded_codes_digest": _codes_digest(loaded_codes),
    })
    universe_digest = _digest(universe_contract)
    composite_digest = _digest({
        "market_digest": market_digest,
        "metadata_digest": metadata_digest,
        "universe_digest": universe_digest,
    })
    strict_pit = bool(metadata_provenance.get("strict_pit")) and bool(
        universe_meta.get("strict_pit", False)
    )
    return {"digest": composite_digest, "algorithm": "sha256-composite-v2",
            "market_digest": market_digest, "metadata_digest": metadata_digest,
            "universe_digest": universe_digest,
            "resolved_codes_digest": resolved_codes_digest,
            "loaded_codes_digest": universe_contract["loaded_codes_digest"],
            "as_of": str(pd.Timestamp(as_of).date()), "rows": int(len(stable)),
            "codes": int(stable.index.get_level_values("code").nunique()), "fields": cols,
            "strict_pit": strict_pit,
            "metadata_vintage": metadata_provenance.get("snapshot_date"),
            "universe_vintage": universe_meta.get("snapshot_date")}


def evaluate_data_screen(panel: Any, spec: Mapping[str, Any], *,
                         universe_meta: Optional[Mapping[str, Any]] = None,
                         metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
                         metadata_provenance: Optional[Mapping[str, Any]] = None,
                         load_meta: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Pure deterministic evaluator. ``panel`` may be PanelData or its DataFrame."""
    import pandas as pd

    frame = getattr(panel, "df", panel)
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.MultiIndex):
        raise RuntimeError("panel 必须是 (datetime, code) MultiIndex DataFrame/PanelData")
    frame = frame.copy().sort_index()
    frame.index = frame.index.set_names(["datetime", "code"])
    if "vol" in frame.columns:
        vol_alias = pd.to_numeric(frame["vol"], errors="coerce")
        if "volume" in frame.columns:
            volume = pd.to_numeric(frame["volume"], errors="coerce")
            overlap = vol_alias.notna() & volume.notna()
            if bool(overlap.any()) and not bool(
                    ((vol_alias[overlap] - volume[overlap]).abs()
                     <= (volume[overlap].abs().clip(lower=1.0) * 1e-12)).all()):
                raise RuntimeError("行情同时包含 vol/volume 且数值冲突，拒绝猜测成交量口径")
            frame["volume"] = volume.combine_first(vol_alias)
        else:
            frame = frame.rename(columns={"vol": "volume"})
        if "vol" in frame.columns:
            frame = frame.drop(columns=["vol"])
    requested = pd.Timestamp(spec.get("requested_as_of") or date.today().isoformat())
    before = frame.index.get_level_values("datetime") <= requested
    frame = frame.loc[before]
    if frame.empty or "close" not in frame.columns:
        raise RuntimeError("请求截止日前没有 close 行情")
    close_values = pd.to_numeric(frame["close"], errors="coerce")
    volume_col = "volume" if "volume" in frame.columns else ("vol" if "vol" in frame.columns else None)
    amount_values = (pd.to_numeric(frame["amount"], errors="coerce")
                     if "amount" in frame.columns else close_values * float("nan"))
    volume_values = (pd.to_numeric(frame[volume_col], errors="coerce")
                     if volume_col else close_values * float("nan"))
    real_bar = close_values.notna() & (close_values > 0)
    if volume_col:
        real_bar &= volume_values.notna() & (volume_values > 0)
    if "amount" in frame.columns:
        real_bar &= amount_values.notna() & (amount_values > 0)
    if not bool(real_bar.any()):
        raise RuntimeError("请求截止日前 close 全空")
    universe_meta = dict(universe_meta or {})
    load_meta = dict(load_meta or {})
    amount_unit = str(load_meta.get("amount_unit") or "unknown")
    if amount_unit != "yuan" or not bool(load_meta.get("verified", amount_unit == "yuan")):
        raise RuntimeError(
            f"行情 amount 单位未经验证为元（当前 {amount_unit}），不能执行流动性筛选或输出元口径证据"
        )
    loaded_n = int(frame.index.get_level_values("code").nunique())
    full_a_contract = str(universe_meta.get("id") or "") == "all_a"
    resolved_raw = universe_meta.get("n_resolved")
    try:
        resolved_n = int(resolved_raw) if resolved_raw is not None else None
    except (TypeError, ValueError):
        resolved_n = None
    minimum_resolved_bars = 1
    loaded_ratio = None
    if full_a_contract:
        if resolved_n is None or resolved_n <= 0:
            raise RuntimeError("全A筛选缺少有效的 n_resolved，无法验证股票池完整性")
        loaded_ratio = loaded_n / resolved_n
        minimum_resolved_bars = int(math.ceil(resolved_n * _ALL_A_MIN_COVERAGE_RATIO))
        if loaded_n < minimum_resolved_bars:
            raise RuntimeError(
                "全A行情加载覆盖不足："
                f"{loaded_n}/{resolved_n}={loaded_ratio:.2%}，"
                f"最低要求 {_ALL_A_MIN_COVERAGE_RATIO:.0%}"
            )
    coverage = real_bar.groupby(level="datetime").sum().astype(int)
    peak_coverage = int(coverage.max())
    coverage_threshold = max(
        1, int(math.ceil(peak_coverage * 0.80)), minimum_resolved_bars,
    )
    eligible_dates = coverage[coverage >= coverage_threshold]
    if eligible_dates.empty:
        raise RuntimeError("找不到达到 80% 股票覆盖率的有效交易日")
    # A single partially-updated symbol must not move the entire cross-section
    # forward and falsely mark the rest of the universe as halted.
    as_of = eligible_dates.index.max()
    required = _required_bars(spec)
    metadata = dict(metadata or {})
    metadata_provenance = dict(metadata_provenance or {})
    filters = spec["filters"]
    warnings: List[str] = []
    requested_date = requested.date().isoformat()
    as_of_date = pd.Timestamp(as_of).date().isoformat()
    if requested_date != as_of_date:
        warnings.append(f"请求截止日 {requested_date} 无完整真实截面，已回落到最新有效交易日 {as_of_date}")
    metadata_available = bool(metadata) and bool(
        metadata_provenance.get("available", bool(metadata))
    )
    if filters.get("exclude_st") and not metadata_available:
        raise RuntimeError("请求要求排除 ST，但公司主表元数据不可用，已失败封闭")
    if filters.get("require_strict_metadata_pit") and not metadata_provenance.get("strict_pit"):
        raise RuntimeError("请求要求严格历史元数据 PIT，但当前元数据不满足")
    if filters.get("exclude_st") and not metadata_provenance.get("strict_pit"):
        warnings.append("ST/名称来自当前公司主表快照，不是历史逐日 PIT；历史回放需审阅 metadata_provenance")
    if not universe_meta.get("strict_pit"):
        if full_a_contract:
            warnings.append("全A范围来自当前上市公司快照，不是历史成分 PIT；历史 as_of 存在幸存者偏差")
        else:
            universe_label = str(
                universe_meta.get("label") or universe_meta.get("id") or "当前股票池"
            )
            warnings.append(
                f"{universe_label}成分来自当前/静态配置，不是历史成分 PIT；"
                "历史 as_of 可能存在成分幸存者偏差"
            )

    rejection_counts: Dict[str, int] = {}
    missing_counts: Dict[str, int] = {"name": 0, "metadata": 0}
    candidates: List[Dict[str, Any]] = []
    evaluated = 0

    def reject(reason: str) -> None:
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    for code, group in frame.groupby(level="code", sort=False):
        code_s = str(code)
        g = group.droplevel("code").sort_index()
        g = g.loc[g.index <= as_of]
        if g.empty:
            reject("empty_series")
            continue
        current_bar = bool(g.index.max() == as_of)
        last = g.iloc[-1]
        close = _finite(last.get("close"))
        volume = _finite(last.get("volume", last.get("vol")))
        amount = _finite(last.get("amount"))
        if filters.get("require_asof_trade") and (
                not current_bar or close is None or close <= 0 or
                volume is None or volume <= 0 or amount is None or amount <= 0):
            reject("no_asof_trade")
            continue

        meta = metadata.get(code_s) or {}
        if not meta:
            missing_counts["metadata"] += 1
        raw_name = str(meta.get("name") or "").strip()
        name_known = bool(raw_name) and raw_name != code_s
        name = raw_name if name_known else code_s
        if not name_known:
            missing_counts["name"] += 1
        if filters.get("exclude_st") and (not meta or not name_known):
            reject("metadata_unknown_st")
            continue
        if filters.get("exclude_st") and "ST" in name.upper():
            reject("st")
            continue

        amount_window = int(filters["amount_window"])
        amount_series = (pd.to_numeric(g["amount"], errors="coerce")
                         if "amount" in g.columns else pd.Series(dtype=float))
        # Fixed recent-bar window: zero turnover is a real observation (e.g.
        # suspension) and missing values reduce completeness.  Never drop them
        # first and pull older high-turnover days into the window.
        amount_tail = amount_series.tail(amount_window)
        amount_valid = amount_tail.notna() & (amount_tail >= 0)
        amount_obs = int(amount_valid.sum())
        avg_amount = (_finite(amount_tail.where(amount_valid).mean())
                      if amount_obs else None)
        if (amount_obs < int(filters["amount_min_observations"]) or avg_amount is None or
                avg_amount < float(filters["min_avg_amount_yuan"])):
            reject("liquidity")
            continue

        evaluated += 1
        passed = True
        evidence: Dict[str, Any] = {}
        for rule in spec["rules"]:
            ok, detail = _rule_result(g, rule)
            evidence[rule["id"]] = {"label": rule["label"], "passed": ok, **detail}
            if not ok:
                reject("rule:" + str(rule["id"]))
                passed = False
        if not passed:
            continue
        score_value, score_evidence = _score_result(g, spec.get("score"))
        candidates.append({
            "code": code_s, "name": name, "industry": meta.get("industry"),
            "as_of": as_of_date, "score": score_value,
            "close": close, "avg_amount_yuan": avg_amount,
            "avg_amount_yi": (avg_amount / 100_000_000.0 if avg_amount is not None else None),
            "amount_observations": amount_obs,
            "score_evidence": score_evidence, "evidence": evidence,
            "filter_evidence": {
                "asof_trade": {"required": bool(filters.get("require_asof_trade")),
                                "bar_date": str(pd.Timestamp(g.index.max()).date()),
                                "effective_asof": as_of_date, "volume": volume,
                                "amount_yuan": amount},
                "st": {"excluded": bool(filters.get("exclude_st")), "name": name,
                       "is_st": "ST" in name.upper(),
                       "strict_pit": bool(metadata_provenance.get("strict_pit"))},
                "liquidity": {"window": amount_window, "observations": amount_obs,
                              "average_yuan": avg_amount,
                              "minimum_yuan": float(filters["min_avg_amount_yuan"]),
                              "passed": True},
            },
            "metadata_source": metadata_provenance.get("source"),
        })

    if filters.get("exclude_st") and rejection_counts.get("metadata_unknown_st"):
        warnings.append(
            f"{rejection_counts['metadata_unknown_st']} 只股票缺少可验证名称，因无法确认 ST 状态已排除"
        )

    direction = str((spec.get("score") or {}).get("direction") or "desc")
    if spec.get("score"):
        if direction == "asc":
            candidates.sort(key=lambda row: (row["score"] is None,
                                              row["score"] if row["score"] is not None else math.inf,
                                              row["code"]))
        else:
            candidates.sort(key=lambda row: (row["score"] is None,
                                              -(row["score"] if row["score"] is not None else -math.inf),
                                              row["code"]))
    else:
        candidates.sort(key=lambda row: row["code"])
    matched_n = len(candidates)
    top = candidates[:int(spec["top_n"])]
    for index, row in enumerate(top, 1):
        row["rank"] = index

    return {
        "ok": True, "status": "complete", "schema_version": SCHEMA_VERSION,
        "requested_as_of": requested_date, "as_of": as_of_date,
        "spec_digest": spec["spec_digest"],
        "source_selector": spec.get("source_selector"),
        "compiled_spec": {k: v for k, v in spec.items() if k not in ("source_selector",)},
        "universe": universe_meta,
        "universe_coverage": {
            "enforced": full_a_contract,
            "n_resolved": resolved_n,
            "n_loaded": loaded_n,
            "loaded_ratio": loaded_ratio,
            "minimum_ratio": (_ALL_A_MIN_COVERAGE_RATIO if full_a_contract else None),
            "effective_valid_bars": int(coverage.loc[as_of]),
            "effective_valid_ratio": (
                int(coverage.loc[as_of]) / resolved_n
                if resolved_n is not None and resolved_n > 0 else None
            ),
        },
        "as_of_coverage": {"valid_bars": int(coverage.loc[as_of]),
                           "peak_valid_bars": peak_coverage,
                           "minimum_valid_bars": coverage_threshold,
                           "minimum_ratio": 0.80,
                           "basis": ("resolved_all_a" if full_a_contract else "loaded_peak")},
        "n_loaded": loaded_n,
        "n_evaluated": evaluated, "n_matched": matched_n,
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "metadata_missing_counts": missing_counts,
        "metadata_provenance": metadata_provenance,
        "data_load": load_meta,
        "data_snapshot": _frame_snapshot(
            frame, as_of=as_of, bars=required, metadata=metadata,
            metadata_provenance=metadata_provenance, universe_meta=universe_meta,
        ),
        "score": spec.get("score"), "candidates": top, "warnings": warnings,
    }


@dataclass
class DataScreenRunStore:
    root: Optional[Path] = None

    def __post_init__(self) -> None:
        if self.root is None:
            configured = os.environ.get("GUANLAN_DATA_SCREEN_RUN_DIR")
            self.root = (Path(configured) if configured else
                         Path(__file__).resolve().parents[2] / ".data" / "data_screen_runs")
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return "ds_" + uuid.uuid4().hex[:16]

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(str(run_id)):
            raise ValueError("非法 data-screen run_id")
        return self.root / f"{run_id}.json"

    def put(self, record: Mapping[str, Any]) -> None:
        run_id = str(record.get("run_id") or "")
        path = self._path(run_id)
        payload = _canonical_json(dict(record))
        fd, tmp_name = tempfile.mkstemp(prefix=f".{run_id}.", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def run_data_screen(request: Mapping[str, Any], *, panel: Any = None,
                    universe_meta: Optional[Mapping[str, Any]] = None,
                    metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
                    metadata_provenance: Optional[Mapping[str, Any]] = None,
                    load_meta: Optional[Mapping[str, Any]] = None,
                    loader: Any = None, store: Optional[DataScreenRunStore] = None,
                    persist: bool = True, parent_run_id: Optional[str] = None) -> Dict[str, Any]:
    """Compile, execute and (for direct Skill calls) durably record one run."""
    run_store = store or (DataScreenRunStore() if persist else None)
    run_id = run_store.new_id() if run_store is not None else "ds_" + uuid.uuid4().hex[:16]
    created_at = _now_iso()
    parent_run_id = (str(parent_run_id)[:128] if parent_run_id is not None else None)
    try:
        request_copy = _strict_json_copy(request)
        spec = compile_data_screen_spec(request_copy)
    except Exception as exc:
        rejected_request = _safe_rejected_request(request)
        failed = {
            "ok": False, "status": "error", "run_id": run_id,
            "parent_run_id": parent_run_id, "created_at": created_at,
            "spec_digest": None, "reason": f"{type(exc).__name__}: {exc}"[:1000],
            "candidates": [],
        }
        if persist and run_store is not None:
            run_store.put({
                "run_schema_version": RUN_SCHEMA_VERSION, "run_id": run_id,
                "created_at": created_at, "updated_at": _now_iso(), "status": "error",
                "parent_run_id": parent_run_id, "request": rejected_request,
                "rejected_request": rejected_request,
                "spec_digest": None, "error": failed["reason"], "result": failed,
            })
        return failed
    base_record = {
        "run_schema_version": RUN_SCHEMA_VERSION, "run_id": run_id,
        "created_at": created_at, "updated_at": created_at,
        "status": "running", "parent_run_id": parent_run_id,
        "request": request_copy, "spec_digest": spec["spec_digest"],
    }
    if persist and run_store is not None:
        run_store.put(base_record)
    try:
        if panel is None:
            panel, universe_meta, metadata, metadata_provenance, load_meta = load_data_screen_panel(
                spec, loader=loader,
            )
        else:
            if universe_meta is None:
                codes = getattr(panel, "codes", lambda: [])()
                universe_meta = {"id": spec["universe"], "label": spec["universe"],
                                 "n_resolved": len(codes), "source": "injected_panel",
                                 "strict_pit": False}
            metadata = metadata or {}
            metadata_provenance = metadata_provenance or {
                "source": "injected_unverified", "available": bool(metadata),
                "strict_pit": False,
                "snapshot_date": spec.get("requested_as_of"),
            }
            load_meta = load_meta or {
                "loader": "injected", "amount_unit": "unknown", "volume_unit": "unknown",
                "verified": False,
            }
        result = evaluate_data_screen(
            panel, spec, universe_meta=universe_meta, metadata=metadata,
            metadata_provenance=metadata_provenance, load_meta=load_meta,
        )
        result["run_id"] = run_id
        result["parent_run_id"] = parent_run_id
        result["created_at"] = created_at
        if persist and run_store is not None:
            run_store.put({**base_record, "updated_at": _now_iso(), "status": "complete",
                           "result": result})
        return result
    except Exception as exc:  # noqa: BLE001 — direct calls must retain an auditable failure
        failed = {
            "ok": False, "status": "error", "run_id": run_id,
            "parent_run_id": parent_run_id, "created_at": created_at,
            "spec_digest": spec["spec_digest"],
            "reason": f"{type(exc).__name__}: {exc}"[:1000],
            "candidates": [],
        }
        if persist and run_store is not None:
            run_store.put({**base_record, "updated_at": _now_iso(), "status": "error",
                           "error": failed["reason"], "result": failed})
        return failed


def read_data_screen_run(run_id: str, *, store: Optional[DataScreenRunStore] = None) -> Optional[Dict[str, Any]]:
    return (store or DataScreenRunStore()).get(run_id)


__all__ = [
    "DataScreenRunStore", "DataScreenSpecError", "SCHEMA_VERSION",
    "compile_data_screen_spec", "evaluate_data_screen", "load_data_screen_panel",
    "read_data_screen_run", "resolve_data_screen_universe", "run_data_screen",
]
