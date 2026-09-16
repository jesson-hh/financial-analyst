"""Explicit, side-effect-free training-process bootstrap and evidence helpers.

Importing the web server to initialise a worker also starts service schedulers.
Workers instead pin the same engine/config here before any engine imports.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys


def bootstrap_training_engine() -> str:
    repo = Path(__file__).resolve().parents[3]
    source = Path(os.environ.get("GUANLAN_FA_SRC") or repo / "engine").resolve()
    if not (source / "financial_analyst" / "__init__.py").exists():
        raise RuntimeError(f"training engine missing: {source}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    engine = importlib.import_module("financial_analyst")
    resolved = Path(engine.__file__).resolve()
    if source not in resolved.parents:
        raise RuntimeError(f"training engine conflict: expected {source}, loaded {resolved}")
    os.environ.setdefault("FA_CONFIG_DIR", str(repo / "config"))
    # Local factor definitions are read and registered in this process only.
    from guanlan_v2.factorlib import register_library_factors
    register_library_factors()
    print(f"[training] engine={resolved}", flush=True)
    return str(resolved)


def frame_digest(frame) -> str:
    """Fingerprint the actual numeric feature/label snapshot, including index.

    This is a content identity, not a claim that the data is historical PIT.
    """
    import pandas as pd
    h = hashlib.sha256()
    cols = list(frame.columns) if hasattr(frame, "columns") else [frame.name]
    h.update(json.dumps([str(c) for c in cols]).encode())
    h.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    return h.hexdigest()


def date_bounds(index) -> dict:
    import pandas as pd
    dts = index.get_level_values("datetime") if hasattr(index, "names") and "datetime" in index.names else index
    dates = pd.DatetimeIndex(dts)
    return {"start": str(dates.min().date()) if len(dates) else None,
            "end": str(dates.max().date()) if len(dates) else None,
            "n_dates": int(dates.nunique()), "n_rows": int(len(index))}


def training_provenance(features, labels, fit_index, *, prediction_date, label_spec,
                        evaluation=None, feature_expressions=None, preprocessing=None) -> dict:
    versions = {}
    for package in ("numpy", "pandas", "lightgbm", "xgboost", "scikit-learn"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {"schema": "guanlan.training_provenance.v1", "strict_pit": False,
            "data_identity_kind": "computed_feature_label_snapshot",
            "features": date_bounds(features.index),
            "labels": date_bounds(labels.dropna().index),
            "final_fit": date_bounds(fit_index),
            "prediction_date": str(prediction_date), "label_spec": dict(label_spec),
            "feature_expressions": list(feature_expressions or []),
            "feature_snapshot_sha256": frame_digest(features),
            "label_snapshot_sha256": frame_digest(labels),
            "evaluation": dict(evaluation or {}),
            "preprocessing": dict(preprocessing or {}), "dependencies": versions}
