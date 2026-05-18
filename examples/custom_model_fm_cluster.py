"""Example: FM cluster custom model plug-in.

This file shows the pattern for plugging a proprietary model into financial-analyst.
The actual checkpoint loading / inference is sketched — replace with your own.

To use:
    1. Copy / adapt this file into your own private codebase.
    2. Register at startup:
       >>> from financial_analyst.models import ModelRegistry
       >>> from my_models.fm_cluster import FMClusterModel
       >>> ModelRegistry.register("fm_cluster", FMClusterModel)
    3. `model-predictor` agent will automatically include your model in
       `consensus_rank_pct` for every report.
"""
from __future__ import annotations
from typing import Any, Dict
import pandas as pd
from financial_analyst.models.base import BaseModel


class FMClusterModel(BaseModel):
    """Stub example showing how to wrap a flow-matching cluster model.

    Real implementation would:
    - load a torch checkpoint at __init__
    - call self._model(features) inside predict()
    - map output to {"score": float, "rank_pct": float, "cluster": str}
    """

    def __init__(self, checkpoint_path: str = "", n_clusters: int = 6):
        self.checkpoint_path = checkpoint_path
        self.n_clusters = n_clusters
        # In a real implementation:
        # import torch
        # self.model = torch.load(checkpoint_path, map_location="cpu")
        # self.model.eval()

    def predict(self, code: str, asof: str) -> Dict[str, float]:
        """Return prediction dict. Required keys: 'score' or 'rank_pct'.

        In a real implementation:
            features = self._build_features(code, asof)
            with torch.no_grad():
                logits = self.model(features)
            return {
                "score": float(logits.mean().item()),
                "rank_pct": float(percentile_within_cluster(logits)),
                "cluster": int(logits.argmax().item()),
            }
        """
        return {
            "score": 0.5,           # neutral prediction (stub)
            "rank_pct": 0.5,
            "cluster": 0,
        }

    def metadata(self) -> Dict[str, Any]:
        return {
            "name": "fm_cluster",
            "version": "stub-v0",
            "n_clusters": self.n_clusters,
            "horizon_days": 10,
            "note": "stub — replace with real checkpoint",
        }
