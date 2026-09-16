# -*- coding: utf-8 -*-
"""Immutable, JSON-safe factor signals passed between workflow nodes.

Formula factors are naturally replayable from their DSL expression.  Learned or
fitted factors are not: an ML prediction, PCA component, Spearman weighting, or
multi-model blend cannot be reconstructed from ``fe.features`` alone.  This
module gives those products a small common contract that carries both the exact
signal and the recipe/weights that produced it.

The signal is intentionally embedded in the workflow payload.  It is therefore
durable with a workflow run, needs no process-local cache, and fails closed when
its content digest is missing or has changed.  Only finite observations are
stored; missing rows remain missing when the artifact is materialised.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


SCHEMA = "guanlan.factor_artifact.v1"
_KINDS = {"ml_prediction", "pca", "spearman", "linear_composite", "multi_model"}


class FactorArtifactError(ValueError):
    """The artifact cannot be trusted or materialised."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FactorArtifactError(f"factor artifact 含不可序列化内容: {exc}") from exc


def _digest_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _normalise_source(source: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if source is None:
        return {}
    if not isinstance(source, Mapping):
        raise FactorArtifactError("factor artifact source 必须是对象")
    # Round-trip through canonical JSON to detach caller-owned mutable objects
    # and reject numpy/sklearn objects that would not survive durable storage.
    return json.loads(_canonical_json(dict(source)))


def build_factor_artifact(
    signal: Any,
    *,
    kind: str,
    label: str,
    source: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a content-addressed artifact from a ``(datetime, code)`` Series."""
    import numpy as np
    import pandas as pd

    if kind not in _KINDS:
        raise FactorArtifactError(f"不支持的 factor artifact kind: {kind}")
    if not isinstance(signal, pd.Series):
        raise FactorArtifactError("factor artifact signal 必须是 pd.Series")
    if not isinstance(signal.index, pd.MultiIndex) or signal.index.nlevels != 2:
        raise FactorArtifactError("factor artifact signal 必须使用 (datetime, code) MultiIndex")
    if not signal.index.is_unique:
        raise FactorArtifactError("factor artifact signal 索引必须唯一")

    numeric = pd.to_numeric(signal, errors="coerce")
    invalid = signal.notna() & numeric.isna()
    if bool(invalid.any()):
        raise FactorArtifactError("factor artifact signal 含非数值观测")
    finite = numeric[np.isfinite(numeric.to_numpy(dtype="float64", na_value=np.nan))]
    finite = finite.sort_index()
    if finite.empty:
        raise FactorArtifactError("factor artifact signal 没有有限观测")

    rows: List[List[Any]] = []
    for idx, value in finite.items():
        dt, code = idx
        rows.append([pd.Timestamp(dt).isoformat(), str(code), float(value)])
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "kind": kind,
        "label": str(label or kind),
        "source": _normalise_source(source),
        "signal": {
            "index_names": [
                str(signal.index.names[0] or "datetime"),
                str(signal.index.names[1] or "code"),
            ],
            "rows": rows,
            "n": len(rows),
        },
    }
    digest = _digest_payload(payload)
    payload["digest"] = digest
    payload["id"] = f"fa_{digest[:24]}"
    return payload


def verify_factor_artifact(raw: Any) -> Dict[str, Any]:
    """Validate schema, signal shape, and the content-addressed identity."""
    if not isinstance(raw, Mapping):
        raise FactorArtifactError("factor artifact 必须是对象")
    artifact = deepcopy(dict(raw))
    if artifact.get("schema") != SCHEMA:
        raise FactorArtifactError("factor artifact schema 不受支持")
    if artifact.get("kind") not in _KINDS:
        raise FactorArtifactError("factor artifact kind 不受支持")
    signal = artifact.get("signal")
    if not isinstance(signal, dict):
        raise FactorArtifactError("factor artifact 缺 signal")
    names = signal.get("index_names")
    rows = signal.get("rows")
    if not isinstance(names, list) or len(names) != 2:
        raise FactorArtifactError("factor artifact index_names 非法")
    if not isinstance(rows, list) or not rows:
        raise FactorArtifactError("factor artifact signal.rows 为空")
    if signal.get("n") != len(rows):
        raise FactorArtifactError("factor artifact signal.n 与 rows 不一致")
    seen = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise FactorArtifactError("factor artifact signal row 非法")
        key = (str(row[0]), str(row[1]))
        if key in seen:
            raise FactorArtifactError("factor artifact signal 索引重复")
        seen.add(key)
        try:
            value = float(row[2])
        except (TypeError, ValueError) as exc:
            raise FactorArtifactError("factor artifact signal 含非数值观测") from exc
        if value != value or value in (float("inf"), float("-inf")):
            raise FactorArtifactError("factor artifact signal 含非有限观测")

    claimed_digest = str(artifact.pop("digest", "") or "")
    claimed_id = str(artifact.pop("id", "") or "")
    actual_digest = _digest_payload(artifact)
    if not claimed_digest or claimed_digest != actual_digest:
        raise FactorArtifactError("factor artifact digest 不一致")
    if claimed_id != f"fa_{actual_digest[:24]}":
        raise FactorArtifactError("factor artifact id 不一致")
    artifact["digest"] = actual_digest
    artifact["id"] = claimed_id
    return artifact


def materialize_factor_artifact(raw: Any):
    """Return the exact immutable signal as a numeric pandas Series."""
    import pandas as pd

    artifact = verify_factor_artifact(raw)
    signal = artifact["signal"]
    rows = signal["rows"]
    index = pd.MultiIndex.from_arrays(
        [pd.to_datetime([row[0] for row in rows]), [str(row[1]) for row in rows]],
        names=list(signal["index_names"]),
    )
    return pd.Series(
        [float(row[2]) for row in rows], index=index, dtype="float64",
        name=str(artifact.get("label") or artifact.get("kind") or "factor"),
    )


def artifact_features(raw: Any) -> List[str]:
    """Extract base DSL features needed to reload the same market panel."""
    artifact = verify_factor_artifact(raw)
    source = artifact.get("source") or {}
    candidates: List[Any] = []
    fe = source.get("fe")
    if isinstance(fe, dict):
        candidates.extend(fe.get("features") or [])
    candidates.extend(source.get("features") or [])
    for child in source.get("children") or []:
        if isinstance(child, dict):
            candidates.extend(child.get("features") or [])
    return list(dict.fromkeys(str(item).strip() for item in candidates if str(item).strip()))


def artifact_weights(raw: Any) -> List[Dict[str, Any]]:
    artifact = verify_factor_artifact(raw)
    source = artifact.get("source") or {}
    weights = source.get("weights")
    return deepcopy(weights) if isinstance(weights, list) else []


def combine_factor_artifacts(
    raw_artifacts: Sequence[Any],
    *,
    method: str = "equal",
    metrics: Optional[Sequence[Mapping[str, Any]]] = None,
    label: str = "多模型合成",
) -> Dict[str, Any]:
    """Combine multiple fitted signals rather than selecting the first model.

    Every child signal is z-scored within date. Labels from the same evaluation
    window must never be used to choose weights applied to that window.
    """
    import numpy as np
    import pandas as pd

    if len(raw_artifacts) < 2:
        raise FactorArtifactError("多模型合成至少需要两个 factor artifacts")
    artifacts = [verify_factor_artifact(item) for item in raw_artifacts]
    series = [materialize_factor_artifact(item) for item in artifacts]
    frame = pd.concat(series, axis=1, join="inner").dropna()
    if frame.empty:
        raise FactorArtifactError("多模型 factor artifacts 没有共同有效观测")
    frame.columns = [str(item["id"]) for item in artifacts]

    method_req = str(method or "equal").lower()
    if method_req not in {"equal"}:
        raise FactorArtifactError("拟合信号合成暂只支持预先固定的 equal 权重；同一测试期 IC/ICIR 不可用于估权再回测，请使用独立校准窗口")
    method_used = method_req if method_req in {"equal", "ic", "icir"} else "equal"
    metric_rows = list(metrics or [])
    raw_weights: List[float] = []
    metric_key = "rank_icir" if method_used == "icir" else "rank_ic"
    if method_used == "equal":
        raw_weights = [1.0] * len(artifacts)
    else:
        for pos in range(len(artifacts)):
            row = metric_rows[pos] if pos < len(metric_rows) and isinstance(metric_rows[pos], Mapping) else {}
            value = row.get(metric_key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = 0.0
            raw_weights.append(number if np.isfinite(number) else 0.0)
        if float(np.sum(np.abs(raw_weights))) <= 0.0:
            raw_weights = [1.0] * len(artifacts)
            method_used = "equal"
    denom = float(np.sum(np.abs(raw_weights)))
    weights = [float(value / denom) for value in raw_weights]

    def _date_zscore(column):
        grouped = column.groupby(level=0)
        mean = grouped.transform("mean")
        std = grouped.transform(lambda x: x.std(ddof=0))
        return (column - mean) / std.replace(0.0, np.nan)

    standardised = frame.apply(_date_zscore)
    combined = standardised.mul(weights, axis=1).sum(axis=1, min_count=len(weights)).dropna()
    if combined.empty:
        raise FactorArtifactError("多模型标准化合成后没有有效截面")
    weight_rows = [
        {
            "artifact_id": item["id"],
            "artifact_digest": item["digest"],
            "kind": item["kind"],
            "label": item.get("label"),
            "weight": weights[pos],
        }
        for pos, item in enumerate(artifacts)
    ]
    features: List[str] = []
    for item in artifacts:
        for feature in artifact_features(item):
            if feature not in features:
                features.append(feature)
    return build_factor_artifact(
        combined.rename("multi_model"), kind="multi_model", label=label,
        source={
            "method_requested": method_req,
            "method": method_used,
            "weights": weight_rows,
            "features": features,
            "children": [
                {"id": item["id"], "digest": item["digest"], "kind": item["kind"],
                 "features": artifact_features(item)}
                for item in artifacts
            ],
        },
    )
