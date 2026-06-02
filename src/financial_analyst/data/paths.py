"""Unified data-path resolution.

Single entry-point for all data locations used by financial-analyst. See
``docs/data_contract.md`` for the full contract and rationale.

Priority order (each resolved independently):

  1. Env vars  — ``FA_QLIB_URI`` / ``FA_PARQUET_ROOT`` / ``FA_NEWS_DATA_ROOT``
  2. config/loaders.yaml  — keys ``qlib_binary.{provider_uri, parquet_root, news_data_root}``
  3. ``~/.financial-analyst/data/``  — populated by ``fa init`` from HuggingFace
  4. ``G:/stocks/stock_data/`` and ``G:/stocks/news_data/``  — dev fallback
     (the author's research lab on Windows)

Callers should NOT hardcode any of the above; instead::

    from financial_analyst.data.paths import get_data_paths
    paths = get_data_paths()
    df = pd.read_parquet(paths.parquet_root / "tushare_stock_basic.parquet")
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union

import yaml

from financial_analyst._config import find_config


# ──────────────────────── fallback constants ────────────────────────

# Dev fallback (author's machine — G:/stocks is the research lab)
_DEV_ROOTS = {
    "qlib_day":   "G:/stocks/stock_data/cn_data",
    "qlib_5min":  "G:/stocks/stock_data/cn_data_5min",
    "parquet":    "G:/stocks/stock_data/parquet",
    "news_data":  "G:/stocks/news_data",
}


def _user_root() -> Path:
    """First-user fallback — workspace-aware.

    Returns ``<workspace>/data/`` (default ``~/.financial-analyst/data/``).
    Recomputed on each call so workspace switches take effect without
    restart.
    """
    try:
        from financial_analyst.workspace import get_workspace
        return get_workspace() / "data"
    except Exception:
        return Path.home() / ".financial-analyst" / "data"


# ──────────────────────── data class ────────────────────────


@dataclass(frozen=True)
class DataPaths:
    """Resolved data locations for the current process."""

    qlib_uri: Union[str, Dict[str, str]]
    """Pass directly to ``qlib.init(provider_uri=...)``. May be a single
    day-data root (str) or a multi-freq dict ``{"day": ..., "5min": ...}``."""

    parquet_root: Path
    """Root directory for non-time-series Parquet files (financials, F10
    index, industry maps, etc.). Read with ``pd.read_parquet``."""

    news_data_root: Path
    """Root directory for news + F10 raw text. Contains ``tdx_f10/{code}/``
    subdirectories of per-stock event files."""

    qlib_etf_uri: Optional[str] = None
    """Override for the ETF Qlib root. Set via ``FA_QLIB_ETF_URI`` env var or
    ``loaders.yaml`` ``qlib_binary.provider_uri.etf``; else defaults to
    ``cn_data_etf`` beside ``qlib_day``."""

    strategy_root_override: Optional[Path] = None
    """Override for the strategy markdown root (where research / wisdom /
    stocks / pitfalls etc live). Set via ``FA_STRATEGY_ROOT`` env var; else
    defaults to ``parquet_root.parent.parent / "strategy"`` (i.e.
    ``G:/stocks/strategy/`` when ``parquet_root`` is the dev fallback)."""

    knowledge_index_root_override: Optional[Path] = None
    """Override for the knowledge-index store directory (Chroma persistent
    client root). Set via ``FA_KNOWLEDGE_INDEX_ROOT`` env var; else
    defaults to ``parquet_root.parent / "knowledge_index"`` (i.e. sibling of
    the parquet store so it lives on the same shared data disk)."""

    @property
    def qlib_day(self) -> Path:
        """Day-frequency Qlib data root (always resolvable)."""
        if isinstance(self.qlib_uri, dict):
            return Path(self.qlib_uri["day"])
        return Path(self.qlib_uri)

    @property
    def qlib_5min(self) -> Optional[Path]:
        """5-minute-frequency Qlib root, or ``None`` if not configured."""
        if isinstance(self.qlib_uri, dict) and "5min" in self.qlib_uri:
            return Path(self.qlib_uri["5min"])
        return None

    @property
    def qlib_etf(self) -> Path:
        """ETF day-frequency Qlib root. Override via FA_QLIB_ETF_URI or
        loaders.yaml provider_uri.etf; else cn_data_etf beside qlib_day."""
        if self.qlib_etf_uri:
            return Path(self.qlib_etf_uri)
        return self.qlib_day.parent / "cn_data_etf"

    @property
    def tdx_f10_root(self) -> Path:
        """Convenience accessor for ``news_data_root / "tdx_f10"``."""
        return self.news_data_root / "tdx_f10"

    @property
    def strategy_root(self) -> Path:
        """Strategy markdown root (knowledge sources for the vector index).

        Override priority: ``strategy_root_override`` (env / explicit) →
        derived from ``parquet_root`` (``parquet_root.parent.parent /
        "strategy"``, i.e. ``G:/stocks/strategy/`` on the dev box)."""
        if self.strategy_root_override is not None:
            return self.strategy_root_override
        return self.parquet_root.parent.parent / "strategy"

    @property
    def knowledge_index_root(self) -> Path:
        """Knowledge-index store root (Chroma persistent client directory).

        Override priority: ``knowledge_index_root_override`` (env / explicit)
        → derived from ``parquet_root`` (``parquet_root.parent /
        "knowledge_index"``, so it lives on the same data disk as parquet)."""
        if self.knowledge_index_root_override is not None:
            return self.knowledge_index_root_override
        return self.parquet_root.parent / "knowledge_index"


# ──────────────────────── resolver ────────────────────────


def _load_yaml(config_path: Optional[Path]) -> dict:
    try:
        cfg_path = find_config("loaders.yaml", explicit=config_path)
    except FileNotFoundError:
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_data_paths(config_path: Optional[Path] = None) -> DataPaths:
    """Resolve all data paths in priority order.

    Each path is resolved independently — you may mix sources (e.g. qlib
    from yaml, parquet from env var).

    Parameters
    ----------
    config_path
        Override path to loaders.yaml. Primarily for tests.
    """
    cfg = _load_yaml(config_path)
    entry = (cfg.get("loaders") or {}).get("qlib_binary") or {}

    _ur = _user_root()
    user_qlib   = _ur / "cn_data"
    user_parquet = _ur / "parquet"
    user_news    = _ur / "news_data"

    # ---- qlib_uri ------------------------------------------------------
    env_qlib = os.getenv("FA_QLIB_URI")
    if env_qlib:
        qlib_uri: Union[str, Dict[str, str]] = env_qlib
    elif entry.get("provider_uri"):
        qlib_uri = entry["provider_uri"]
    elif user_qlib.exists():
        qlib_uri = str(user_qlib)
    else:
        qlib_uri = _DEV_ROOTS["qlib_day"]

    # ---- parquet_root --------------------------------------------------
    env_parquet = os.getenv("FA_PARQUET_ROOT")
    if env_parquet:
        parquet_root = Path(env_parquet)
    elif entry.get("parquet_root"):
        parquet_root = Path(entry["parquet_root"])
    elif user_parquet.exists():
        parquet_root = user_parquet
    else:
        parquet_root = Path(_DEV_ROOTS["parquet"])

    # ---- news_data_root ------------------------------------------------
    env_news = os.getenv("FA_NEWS_DATA_ROOT")
    if env_news:
        news_data_root = Path(env_news)
    elif entry.get("news_data_root"):
        news_data_root = Path(entry["news_data_root"])
    elif user_news.exists():
        news_data_root = user_news
    else:
        news_data_root = Path(_DEV_ROOTS["news_data"])

    # ---- qlib_etf_uri --------------------------------------------------
    env_etf = os.getenv("FA_QLIB_ETF_URI")
    prov = entry.get("provider_uri")
    yaml_etf = prov.get("etf") if isinstance(prov, dict) else None
    etf_uri = env_etf or yaml_etf

    # ---- strategy_root_override ---------------------------------------
    env_strategy = os.getenv("FA_STRATEGY_ROOT")
    strategy_override = Path(env_strategy) if env_strategy else None

    # ---- knowledge_index_root_override ---------------------------------
    env_ki = os.getenv("FA_KNOWLEDGE_INDEX_ROOT")
    ki_override = Path(env_ki) if env_ki else None

    return DataPaths(
        qlib_uri=qlib_uri,
        parquet_root=parquet_root,
        news_data_root=news_data_root,
        qlib_etf_uri=etf_uri,
        strategy_root_override=strategy_override,
        knowledge_index_root_override=ki_override,
    )


__all__ = ["DataPaths", "get_data_paths"]
