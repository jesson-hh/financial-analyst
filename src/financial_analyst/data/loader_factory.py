"""Pick a BaseLoader by configuration.

Sub-agents should call ``get_default_loader()`` instead of instantiating
``TushareLoader()`` directly so that the data source is centrally switchable
via ``config/loaders.yaml`` without touching agent code.

Config example (``config/loaders.yaml``)::

    default: tushare

    loaders:
      tushare:
        cache_enabled: true
        cache_ttl_seconds: 86400

      qlib_binary:
        provider_uri: G:/stocks/stock_data/cn_data

Switch to qlib_binary by changing ``default: qlib_binary``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from financial_analyst.data.loaders.base import BaseLoader
from financial_analyst.data.loaders.tushare import TushareLoader
from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader


# Resolve relative to package root: src/financial_analyst/data/loader_factory.py
# → go up 3 levels to reach the project root → config/loaders.yaml
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _PACKAGE_ROOT / "config" / "loaders.yaml"


def _load_config(path: Optional[Path] = None) -> dict:
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return {"default": "tushare", "loaders": {"tushare": {}}}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_default_loader(config_path: Optional[Path] = None) -> BaseLoader:
    """Construct the loader marked ``default:`` in ``config/loaders.yaml``.

    Falls back to ``TushareLoader`` (with cache enabled) when:
    - the config file is missing, or
    - ``default`` names an unknown loader type.

    Parameters
    ----------
    config_path:
        Override path to the YAML config.  Primarily for tests.
    """
    cfg = _load_config(config_path)
    name = cfg.get("default", "tushare")
    loaders = cfg.get("loaders") or {}
    entry = loaders.get(name) or {}

    if name == "qlib_binary":
        provider_uri = entry.get("provider_uri")
        if not provider_uri:
            raise ValueError(
                "qlib_binary loader requires 'provider_uri' in config/loaders.yaml"
            )
        return QlibBinaryLoader(provider_uri=str(provider_uri))

    # Default branch: TushareLoader with optional cache settings
    cache_enabled: bool = bool(entry.get("cache_enabled", True))
    cache_ttl: int = int(entry.get("cache_ttl_seconds", 86400))
    return TushareLoader(enable_cache=cache_enabled, cache_ttl=cache_ttl)
