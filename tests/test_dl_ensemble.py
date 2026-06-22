# tests/test_dl_ensemble.py
# 统一 DL 集成层门禁:多源 z 混合 + 总权重封顶 + per-source 退化 + 单源与旧 b3 字节等价。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.strategy.compute.dl_ensemble import dl_mix_scores, MAX_TOTAL_DL_W  # noqa: E402
from guanlan_v2.strategy.compute.v4_fincast import b3_mix_scores  # noqa: E402


def _mk(n, seed):
    rng = np.random.RandomState(seed)
    idx = [f"SZ{300000 + i:06d}" for i in range(n)]
    return pd.Series(rng.randn(n), index=idx)


def test_single_source_byte_equivalent_to_b3():
    lgb = _mk(200, 1); fc = _mk(200, 2)
    b3_mixed, _ = b3_mix_scores(lgb, fc, w_fc=0.3)
    dl_mixed, info = dl_mix_scores(lgb, {"fincast": fc}, {"fincast": 0.3})
    assert np.allclose(b3_mixed.values, dl_mixed.values, atol=1e-12)
    assert info["active"] is True
    assert abs(info["w_lgb"] - 0.7) < 1e-12


def test_two_sources_weights_sum():
    lgb = _mk(200, 1); a = _mk(200, 2); b = _mk(200, 3)
    _, info = dl_mix_scores(lgb, {"a": a, "b": b}, {"a": 0.2, "b": 0.2})
    assert info["active"] is True
    assert abs(info["w_lgb"] - 0.6) < 1e-9
    ws = {s["model_id"]: s["weight"] for s in info["sources"] if s["active"]}
    assert abs(ws["a"] - 0.2) < 1e-9 and abs(ws["b"] - 0.2) < 1e-9


def test_total_weight_capped():
    lgb = _mk(200, 1); a = _mk(200, 2); b = _mk(200, 3)
    _, info = dl_mix_scores(lgb, {"a": a, "b": b}, {"a": 0.4, "b": 0.4})  # 和 0.8 > 0.5
    assert abs(info["w_lgb"] - (1.0 - MAX_TOTAL_DL_W)) < 1e-9   # w_lgb = 0.5
    ws = {s["model_id"]: s["weight"] for s in info["sources"] if s["active"]}
    assert abs(ws["a"] - 0.25) < 1e-9 and abs(ws["b"] - 0.25) < 1e-9  # 各缩到 0.25


def test_source_below_min_match_drops_out():
    lgb = _mk(200, 1); good = _mk(200, 2)
    thin = _mk(200, 3); thin.iloc[10:] = np.nan   # 仅 10 个非空 < 50
    mixed, info = dl_mix_scores(lgb, {"good": good, "thin": thin}, {"good": 0.3, "thin": 0.3}, min_match=50)
    by = {s["model_id"]: s for s in info["sources"]}
    assert by["thin"]["active"] is False and by["thin"]["weight"] == 0.0
    assert by["good"]["active"] is True
    assert abs(info["w_lgb"] - 0.7) < 1e-9   # 只剩 good 0.3


def test_all_sources_degrade_returns_pure_lgb():
    lgb = _mk(200, 1)
    thin = _mk(200, 3); thin.iloc[5:] = np.nan
    mixed, info = dl_mix_scores(lgb, {"thin": thin}, {"thin": 0.3}, min_match=50)
    assert info["active"] is False and info["w_lgb"] == 1.0
    assert np.allclose(mixed.values, lgb.values)   # 纯 LGB,原样
