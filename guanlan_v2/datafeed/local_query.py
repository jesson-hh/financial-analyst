"""Explicit local historical reader for automatically allowed research queries."""
from __future__ import annotations


def local_query_loader(*, freq: str = "day"):
    """Preserve configured Qlib frequencies without the default-loader fallback.

    In particular a missing 5-minute root is missing data, not permission to
    change the observation frequency or construct a network/cache-writing loader.
    The data-path resolver and Qlib constructor perform reads only.
    """
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.data.paths import get_data_paths

    provider = get_data_paths().qlib_uri
    roots = provider if isinstance(provider, dict) else {"day": provider}
    if freq not in roots or not roots[freq]:
        raise ValueError(f"本地 Qlib 未配置 {freq} 数据；不会切换频率或远程数据源")
    return QlibBinaryLoader(provider)
