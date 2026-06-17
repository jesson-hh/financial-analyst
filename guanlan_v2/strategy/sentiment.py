# -*- coding: utf-8 -*-
"""L3 量能状态(vol_regime)— 暴露 vendored 评分器 ``compute_vol_regime``。

评分器自包含(纯 pandas),byte-identical vendored 自引擎 sentiment(见 _PROVENANCE.md)。
经 importlib 按路径加载 ``vendor/sentiment/volume_regime.py`` —— 不必把 vendor/ 做成包,
且不改副本内容(保漂移哨兵哈希)。缺文件 → compute_vol_regime 为 None(调用方降级跳过)。
"""
from __future__ import annotations

import importlib.util

from guanlan_v2.strategy.paths import VENDOR_DIR

_VR_PATH = VENDOR_DIR / "sentiment" / "volume_regime.py"


def _load_compute():
    if not _VR_PATH.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("guanlan_vendored_volume_regime", _VR_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "compute_vol_regime", None)
    except Exception:  # noqa: BLE001
        return None


compute_vol_regime = _load_compute()
