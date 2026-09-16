# -*- coding: utf-8 -*-
"""Versioned, executable specifications for non-DSL factorlib factors.

The historical factor library stores one ``expr`` and registers the compiled
DSL function in the engine zoo.  A cross-sectional transform such as
orthogonalisation cannot be represented by that expression: persisting only
the base expression silently changes the factor on replay.

This module adds a deliberately small structured contract.  The stored
``factor_spec`` remains the authority, while ``expr`` is retained as the
legacy/display base expression.  Compilation produces the same
``(PanelData) -> Series`` callable expected by :class:`AlphaSpec`, so existing
consumers (report, backtest, portfolio and every other registry caller) need no
special execution branch.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import marshal
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional, Tuple


FACTOR_SPEC_SCHEMA = "guanlan.factor_spec.v1"
ORTHOGONALIZED_KIND = "orthogonalized"
DEPENDENCY_SCHEMA = "factor-dependencies-v1"


class FactorSpecError(ValueError):
    """The structured factor contract is malformed or unsupported."""


_TOP_LEVEL_KEYS = frozenset({
    "schema", "kind", "base_features", "transform", "dependencies", "metadata",
})
_TRANSFORM_KEYS = frozenset({
    "version", "method", "industry", "size", "styles", "existing_factors",
    "old_factors", "min_obs", "max_condition_number",
})
_FALSE_VALUES = (False, 0, "0", "false", "False", "否", "不控制", "")
_STYLE_ALIASES = {
    "beta": "beta", "β": "beta",
    "vol": "volatility", "volatility": "volatility", "波动": "volatility",
    "liquidity": "liquidity", "流动性": "liquidity",
    "momentum": "momentum", "动量": "momentum",
}
_DEPENDENCY_KEYS = frozenset({"version", "base_feature", "existing_factors"})
_DSL_DEFINITION_KEYS = frozenset({
    "kind", "input", "formula_text", "definition_digest",
})
_REGISTRY_DEFINITION_KEYS = frozenset({
    "kind", "input", "name", "family", "formula_text",
    "implementation_digest", "definition_digest",
})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_value(value: Any) -> Any:
    """Return a bounded JSON-safe description for callable state.

    Source text alone does not distinguish two closures produced by the same
    factory.  Defaults and closure cells are therefore included when they are
    ordinary immutable/JSON-like values.  For opaque objects we bind their
    type, never a process-specific repr containing a memory address.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _stable_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _callable_implementation_digest(fn: Any) -> str:
    """Fingerprint executable definition and stable closure/default state."""
    code = getattr(fn, "__code__", None)
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        source = ""
    closure = []
    for cell in (getattr(fn, "__closure__", None) or ()):
        try:
            closure.append(_stable_value(cell.cell_contents))
        except ValueError:
            closure.append({"empty_cell": True})
    normalized_code = code
    if code is not None:
        try:
            # Deployment paths and source line offsets are not implementation
            # semantics.  Excluding them keeps a persisted pin stable when the
            # same release is installed in another directory.
            normalized_code = code.replace(co_filename="", co_firstlineno=1)
        except (AttributeError, TypeError, ValueError):
            normalized_code = code
    payload = {
        "version": "python-callable-definition-v1",
        "module": str(getattr(fn, "__module__", "") or ""),
        "qualname": str(getattr(fn, "__qualname__", type(fn).__qualname__) or ""),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "code_sha256": (
            hashlib.sha256(marshal.dumps(normalized_code)).hexdigest()
            if normalized_code is not None else None
        ),
        "defaults": _stable_value(getattr(fn, "__defaults__", None)),
        "kwdefaults": _stable_value(getattr(fn, "__kwdefaults__", None)),
        "closure": closure,
    }
    return _sha256_json(payload)


def _dependency_definition(input_text: str) -> Tuple[Dict[str, Any], Any]:
    """Resolve one identifier exactly once and return definition + callable."""
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
    from financial_analyst.factors.zoo.registry import get as get_alpha

    text = str(input_text or "").strip()
    if not text:
        raise FactorSpecError("factor_spec 依赖不能为空")
    try:
        registered = get_alpha(text)
    except KeyError:
        try:
            validate_expr(text)
        except Exception as exc:
            raise FactorSpecError(f"依赖 '{text}' 既不是已注册因子也不是合法 DSL: {exc}") from exc
        definition: Dict[str, Any] = {
            "kind": "dsl", "input": text, "formula_text": text,
        }
        compute = compile_factor(text)
    else:
        if isinstance(getattr(registered.compute, "__guanlan_factor_spec__", None), dict):
            raise FactorSpecError(
                f"factor_spec v1 不支持以结构化因子作为 base_feature/控制依赖 "
                f"'{text}'；请引用其原始基础表达式"
            )
        definition = {
            "kind": "registry", "input": text,
            "name": str(registered.name), "family": str(registered.family),
            "formula_text": str(registered.formula_text or ""),
            "implementation_digest": _callable_implementation_digest(registered.compute),
        }
        compute = registered.compute
    definition["definition_digest"] = _sha256_json(definition)
    return definition, compute


def resolve_factor_dependency(input_text: str) -> Dict[str, Any]:
    """Return the current canonical definition of one named/DSL dependency."""
    definition, _compute = _dependency_definition(input_text)
    return definition


def _normalize_dependency_definition(raw: Any, *, expected_input: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FactorSpecError(f"依赖 '{expected_input}' 的 definition 必须是对象")
    kind = str(raw.get("kind") or "")
    allowed = _DSL_DEFINITION_KEYS if kind == "dsl" else (
        _REGISTRY_DEFINITION_KEYS if kind == "registry" else frozenset()
    )
    if not allowed:
        raise FactorSpecError(f"依赖 '{expected_input}' 的 kind 不支持: {kind!r}")
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown or missing:
        detail = []
        if unknown:
            detail.append(f"多余 {', '.join(sorted(unknown))}")
        if missing:
            detail.append(f"缺少 {', '.join(sorted(missing))}")
        raise FactorSpecError(f"依赖 '{expected_input}' definition 字段非法: {'; '.join(detail)}")
    input_value = str(raw.get("input") or "").strip()
    if input_value != expected_input:
        raise FactorSpecError(
            f"依赖 definition.input={input_value!r} 与 factor_spec 引用 {expected_input!r} 不一致"
        )
    if kind == "dsl":
        definition = {"kind": "dsl", "input": input_value,
                      "formula_text": str(raw.get("formula_text") or "")}
        if definition["formula_text"] != input_value:
            raise FactorSpecError(f"DSL 依赖 '{expected_input}' 的 formula_text 必须等于原公式")
    else:
        implementation_digest = str(raw.get("implementation_digest") or "")
        if len(implementation_digest) != 64 or any(
                ch not in "0123456789abcdef" for ch in implementation_digest.lower()):
            raise FactorSpecError(f"注册依赖 '{expected_input}' 的 implementation_digest 非法")
        definition = {
            "kind": "registry", "input": input_value,
            "name": str(raw.get("name") or ""),
            "family": str(raw.get("family") or ""),
            "formula_text": str(raw.get("formula_text") or ""),
            "implementation_digest": implementation_digest.lower(),
        }
        if not definition["name"]:
            raise FactorSpecError(f"注册依赖 '{expected_input}' 缺少 name")
    expected_digest = _sha256_json(definition)
    supplied_digest = str(raw.get("definition_digest") or "").lower()
    if supplied_digest != expected_digest:
        raise FactorSpecError(f"依赖 '{expected_input}' definition_digest 校验失败")
    definition["definition_digest"] = expected_digest
    return definition


def _normalize_dependencies(raw: Any, *, base: str,
                            existing: list[str]) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FactorSpecError("factor_spec.dependencies 必须是对象")
    unknown = set(raw) - _DEPENDENCY_KEYS
    missing = _DEPENDENCY_KEYS - set(raw)
    if unknown or missing:
        raise FactorSpecError(
            "factor_spec.dependencies 字段非法"
            + (f"，多余: {', '.join(sorted(unknown))}" if unknown else "")
            + (f"，缺少: {', '.join(sorted(missing))}" if missing else "")
        )
    if str(raw.get("version") or "") != DEPENDENCY_SCHEMA:
        raise FactorSpecError(f"不支持的 dependencies.version: {raw.get('version')!r}")
    controls = raw.get("existing_factors")
    if not isinstance(controls, list) or len(controls) != len(existing):
        raise FactorSpecError("dependencies.existing_factors 与 transform 引用数量不一致")
    return {
        "version": DEPENDENCY_SCHEMA,
        "base_feature": _normalize_dependency_definition(
            raw.get("base_feature"), expected_input=base,
        ),
        "existing_factors": [
            _normalize_dependency_definition(item, expected_input=expected)
            for item, expected in zip(controls, existing)
        ],
    }


def _split_existing_factors(value: Any) -> list[str]:
    if isinstance(value, list):
        return list(dict.fromkeys(str(x).strip() for x in value if str(x).strip()))
    text = str(value or "").strip()
    if not text:
        return []
    parts = [x.strip() for x in text.replace("；", ";").replace("\r", "\n")
             .replace(";", "\n").split("\n") if x.strip()]
    # Commas inside legal DSL calls are not separators.
    if len(parts) == 1 and "(" not in text and ")" not in text:
        parts = [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]
    return list(dict.fromkeys(parts))


def normalize_factor_spec(raw: Optional[Mapping[str, Any]], *,
                          fallback_expr: str = "", factor_name: str = "") -> Optional[Dict[str, Any]]:
    """Validate and canonicalise one supported factor spec.

    Unknown schema/kind/keys fail closed.  This is intentional: silently
    dropping a future transform field would recreate the exact lossy-replay
    bug this contract prevents.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise FactorSpecError("factor_spec 必须是对象")
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise FactorSpecError(f"factor_spec 含不支持字段: {', '.join(sorted(unknown))}")
    if str(raw.get("schema") or "") != FACTOR_SPEC_SCHEMA:
        raise FactorSpecError(f"不支持的 factor_spec.schema: {raw.get('schema')!r}")
    if str(raw.get("kind") or "") != ORTHOGONALIZED_KIND:
        raise FactorSpecError(f"不支持的 factor_spec.kind: {raw.get('kind')!r}")

    base_raw = raw.get("base_features")
    if not isinstance(base_raw, list):
        raise FactorSpecError("factor_spec.base_features 必须是列表")
    base = list(dict.fromkeys(str(x).strip() for x in base_raw if str(x).strip()))
    if len(base) != 1:
        raise FactorSpecError("orthogonalized v1 必须且只能包含一个 base_feature")
    fallback = str(fallback_expr or "").strip()
    if fallback and fallback != base[0]:
        raise FactorSpecError("expr 与 factor_spec.base_features[0] 不一致，拒绝有歧义的保存")
    name = str(factor_name or "").strip()
    if name and base[0].lower() == name.lower():
        raise FactorSpecError("factor_spec 不能以自身因子名作为 base_feature")

    raw_transform = raw.get("transform")
    if not isinstance(raw_transform, Mapping):
        raise FactorSpecError("factor_spec.transform 必须是对象")
    unknown_transform = set(raw_transform) - _TRANSFORM_KEYS
    if unknown_transform:
        raise FactorSpecError(
            f"factor_spec.transform 含不支持字段: {', '.join(sorted(unknown_transform))}"
        )
    version = str(raw_transform.get("version") or "orthogonalization-v1")
    method = str(raw_transform.get("method") or "ols").lower()
    if version != "orthogonalization-v1" or method != "ols":
        raise FactorSpecError(f"不支持的正交化版本/方法: {version}/{method}")

    industry = raw_transform.get("industry", True) not in _FALSE_VALUES
    size = str(raw_transform.get("size") or "total_mv").strip().lower()
    size = {"不控制": "none", "否": "none", "总市值": "total_mv", "流通市值": "circ_mv"}.get(size, size)
    if size not in ("none", "total_mv", "circ_mv"):
        raise FactorSpecError(f"不支持的规模控制: {size}")

    styles_raw = raw_transform.get("styles") or []
    if isinstance(styles_raw, str):
        styles_raw = [x.strip() for x in styles_raw.replace("，", ",").split(",") if x.strip()]
    if not isinstance(styles_raw, list):
        raise FactorSpecError("factor_spec.transform.styles 必须是列表或逗号分隔文本")
    styles: list[str] = []
    for item in styles_raw:
        style = _STYLE_ALIASES.get(str(item).strip().lower())
        if style is None:
            raise FactorSpecError(f"不支持的风格控制: {item}")
        if style not in styles:
            styles.append(style)

    old = _split_existing_factors(
        raw_transform.get("existing_factors")
        if raw_transform.get("existing_factors") is not None
        else raw_transform.get("old_factors")
    )
    if len(old) > 12:
        raise FactorSpecError("一次最多控制 12 个已有因子")
    if name and any(x.lower() == name.lower() for x in old):
        raise FactorSpecError("factor_spec 不能把自身因子名作为已有因子控制项")

    try:
        min_obs = int(20 if raw_transform.get("min_obs") in (None, "")
                      else raw_transform.get("min_obs"))
    except (TypeError, ValueError) as exc:
        raise FactorSpecError("min_obs 必须是整数") from exc
    if min_obs < 5 or min_obs > 500:
        raise FactorSpecError("min_obs 必须在 5..500 之间")
    try:
        max_condition = float(
            1.0e8 if raw_transform.get("max_condition_number") in (None, "")
            else raw_transform.get("max_condition_number")
        )
    except (TypeError, ValueError) as exc:
        raise FactorSpecError("max_condition_number 必须是有限正数") from exc
    if not math.isfinite(max_condition) or max_condition <= 1.0:
        raise FactorSpecError("max_condition_number 必须是大于 1 的有限数")
    if not industry and size == "none" and not styles and not old:
        raise FactorSpecError("正交化至少需要一个行业/规模/风格/已有因子控制项")

    transform = {
        "version": version,
        "method": method,
        "industry": industry,
        "size": size,
        "styles": styles,
        "existing_factors": old,
        "min_obs": min_obs,
        "max_condition_number": max_condition,
    }
    canonical: Dict[str, Any] = {
        "schema": FACTOR_SPEC_SCHEMA,
        "kind": ORTHOGONALIZED_KIND,
        "base_features": base,
        "transform": transform,
    }
    if "dependencies" in raw:
        canonical["dependencies"] = _normalize_dependencies(
            raw.get("dependencies"), base=base[0], existing=old,
        )
    if "metadata" in raw:
        metadata = raw.get("metadata")
        if not isinstance(metadata, Mapping):
            raise FactorSpecError("factor_spec.metadata 必须是对象")
        # Round-trip through JSON to reject callables/custom objects and detach
        # caller-owned mutable structures before persistence.
        try:
            canonical["metadata"] = json.loads(json.dumps(metadata, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise FactorSpecError(f"factor_spec.metadata 不是合法 JSON: {exc}") from exc
    return canonical


def factor_spec_digest(spec: Mapping[str, Any]) -> str:
    return _sha256_json(spec)


def pin_factor_spec_dependencies(raw: Mapping[str, Any], *,
                                 fallback_expr: str = "",
                                 factor_name: str = "") -> Dict[str, Any]:
    """Construct or verify the immutable dependency envelope for a v1 spec.

    This function is used at unsaved workflow construction and API validation,
    not merely after persistence.  A caller-supplied envelope is never
    refreshed silently: current registry/DSL definitions must still match it.
    """
    canonical = normalize_factor_spec(
        raw, fallback_expr=fallback_expr, factor_name=factor_name,
    )
    if canonical is None:  # defensive: raw is required by the type contract
        raise FactorSpecError("factor_spec 不能为空")
    current = {
        "version": DEPENDENCY_SCHEMA,
        "base_feature": resolve_factor_dependency(canonical["base_features"][0]),
        "existing_factors": [
            resolve_factor_dependency(item)
            for item in canonical["transform"]["existing_factors"]
        ],
    }
    supplied = canonical.get("dependencies")
    if supplied is not None and supplied != current:
        expected = supplied.get("base_feature")
        actual = current.get("base_feature")
        if expected != actual:
            dep = canonical["base_features"][0]
        else:
            dep = next((name for name, before, after in zip(
                canonical["transform"]["existing_factors"],
                supplied.get("existing_factors") or [],
                current.get("existing_factors") or [],
            ) if before != after), "unknown")
        raise FactorSpecError(f"结构化因子依赖 '{dep}' 的定义已漂移或来源已变化")
    canonical["dependencies"] = current
    return canonical


def verify_factor_spec_dependencies(raw: Mapping[str, Any], *,
                                    fallback_expr: str = "",
                                    factor_name: str = "") -> Dict[str, Any]:
    """Verify a persisted/terminal spec without upgrading an unpinned record."""
    canonical = normalize_factor_spec(
        raw, fallback_expr=fallback_expr, factor_name=factor_name,
    )
    if canonical is None:
        raise FactorSpecError("factor_spec 不能为空")
    if "dependencies" not in canonical:
        raise FactorSpecError(
            "结构化 factor_spec 缺少不可变依赖封存；请重新验证并保存，拒绝降级回放"
        )
    return pin_factor_spec_dependencies(
        canonical, fallback_expr=fallback_expr, factor_name=factor_name,
    )


def materialize_factor_dependency(input_text: str, expected: Mapping[str, Any], panel: Any):
    """Compute one dependency only after its current definition matches its pin."""
    expected_canonical = _normalize_dependency_definition(
        expected, expected_input=str(input_text or "").strip(),
    )
    current, compute = _dependency_definition(input_text)
    if current != expected_canonical:
        raise FactorSpecError(
            f"结构化因子依赖 '{input_text}' 的定义已漂移或依赖已被替换"
        )
    return compute(panel)


def compile_factor_entry(entry: Mapping[str, Any]) -> Tuple[str, Any, Optional[Dict[str, Any]]]:
    """Compile a legacy expression or a structured entry to an AlphaFn.

    Returns ``(display/base expression, callable, canonical spec or None)``.
    The structured callable imports the workflow materialiser lazily, avoiding
    a package import cycle during server startup.
    """
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr

    name = str(entry.get("name") or "").strip()
    expr = str(entry.get("expr") or "").strip()
    spec = normalize_factor_spec(entry.get("factor_spec"), fallback_expr=expr,
                                 factor_name=name)
    if spec is None:
        if not expr:
            raise FactorSpecError("空表达式")
        validate_expr(expr)
        return expr, compile_factor(expr), None

    # Persisted structured records must already carry pins.  New/unsaved specs
    # are upgraded explicitly by API validation or the workflow constructor;
    # replay never mutates history in place.
    spec = verify_factor_spec_dependencies(
        spec, fallback_expr=expr, factor_name=name,
    )
    base_expr = spec["base_features"][0]
    base_definition = spec["dependencies"]["base_feature"]
    existing_definitions = spec["dependencies"]["existing_factors"]

    frozen_spec = deepcopy(spec)

    def compute_structured(panel):
        base_series = materialize_factor_dependency(
            base_expr, deepcopy(base_definition), panel,
        )
        from guanlan_v2.workflow.api import _orthogonalize_from_spec
        residual, _audit = _orthogonalize_from_spec(
            base_series, panel, deepcopy(frozen_spec["transform"]),
            dependency_bindings=deepcopy(existing_definitions),
        )
        return residual

    compute_structured.__name__ = f"factor_spec_{factor_spec_digest(spec)[:12]}"
    compute_structured.__qualname__ = compute_structured.__name__
    # Workflow materialisers inspect these immutable-by-convention markers so
    # they can preserve their established preprocessing order (for example,
    # backtest applies winsorize/zscore before the cross-sectional transform)
    # instead of blindly invoking the generic registry callable too early.
    compute_structured.__guanlan_factor_spec__ = deepcopy(frozen_spec)
    compute_structured.__guanlan_base_expr__ = base_expr
    return base_expr, compute_structured, spec


__all__ = [
    "FACTOR_SPEC_SCHEMA", "ORTHOGONALIZED_KIND", "DEPENDENCY_SCHEMA", "FactorSpecError",
    "normalize_factor_spec", "pin_factor_spec_dependencies",
    "verify_factor_spec_dependencies", "resolve_factor_dependency",
    "materialize_factor_dependency", "factor_spec_digest", "compile_factor_entry",
]
