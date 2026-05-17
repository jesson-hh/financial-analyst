from __future__ import annotations
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd
import lightgbm as lgb
from financial_analyst.models.base import BaseModel
from financial_analyst.data.loaders.tushare import TushareLoader

FEATURE_WINDOWS = [5, 10, 20, 60]
LABEL_HORIZON = 5


class LGBMomentumModel(BaseModel):
    def __init__(self, loader: Optional[Any] = None, training_lookback: int = 250):
        self._loader = loader
        self._lookback = training_lookback

    def metadata(self) -> Dict[str, Any]:
        return {"name": "lgb_momentum", "version": "0.1", "horizon_days": LABEL_HORIZON}

    def _fetch_quote(self, code: str, asof: str) -> pd.DataFrame:
        loader = self._loader or TushareLoader()
        asof_ts = pd.Timestamp(asof)
        start = (asof_ts - pd.Timedelta(days=self._lookback * 2)).strftime("%Y-%m-%d")
        return loader.fetch_quote(code, start, asof)

    @staticmethod
    def _build_features(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        out["ret_1d"] = df["close"].pct_change()
        for w in FEATURE_WINDOWS:
            out[f"ret_{w}d"] = df["close"].pct_change(w)
            out[f"vol_{w}d"] = df["close"].pct_change().rolling(w).std()
            out[f"ma_diff_{w}"] = (df["close"] / df["close"].rolling(w).mean()) - 1.0
            out[f"vol_ratio_{w}"] = df["vol"] / df["vol"].rolling(w).mean()
        out["amount_log"] = np.log1p(df["amount"])
        return out

    @staticmethod
    def _build_label(df: pd.DataFrame, horizon: int = LABEL_HORIZON) -> pd.Series:
        return df["close"].pct_change(horizon).shift(-horizon)

    def predict(self, code: str, asof: str) -> Dict[str, float]:
        df = self._fetch_quote(code, asof)
        if df is None or len(df) < 80:
            return {"score": float("nan"), "rank_pct": float("nan")}

        feats = self._build_features(df)
        labels = self._build_label(df)
        mask = feats.notna().all(axis=1) & labels.notna()
        X_train = feats[mask].values[:-1]
        y_train = labels[mask].values[:-1]
        if len(X_train) < 50:
            return {"score": float("nan"), "rank_pct": float("nan")}

        params = {
            "objective": "regression",
            "metric": "rmse",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbosity": -1,
        }
        train_set = lgb.Dataset(X_train, y_train)
        booster = lgb.train(params, train_set, num_boost_round=200)

        X_now = feats.iloc[[-1]].values
        score = float(booster.predict(X_now)[0])

        all_scores = booster.predict(feats.dropna().values)
        rank_pct = float((all_scores < score).mean())

        return {"score": score, "rank_pct": rank_pct}
