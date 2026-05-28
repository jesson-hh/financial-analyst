"""股票池 (universe) 名 → 代码列表。

解析链: 显式文件路径 → find_config('universes/<name>.txt') (走 workspace/
~/.financial-analyst/config/cwd/bundled) → ~/.financial-analyst/universes/<name>.txt
→ bundled_config_dir()/universes/<name>.txt → f10 指数成分回退 (csi300/csi500/csi800/all)。

从 buddy/tools.py 抽出供因子引擎与 buddy 工具共用。
"""
from __future__ import annotations
from pathlib import Path
from typing import List


def _read_codes(path: Path) -> List[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _f10_codes(universe: str) -> List[str]:
    """指数成分回退: 从 index_constituents.parquet 解析 csi300/csi500/csi800/all。
    缺 parquet 或非指数名 → []。"""
    try:
        from financial_analyst.data.paths import get_data_paths
        from financial_analyst.data.updaters.f10 import resolve_universe as _f10_resolve
        return list(_f10_resolve(get_data_paths().parquet_root, universe))
    except Exception:
        return []


def resolve_universe_codes(universe: str) -> List[str]:
    """Resolve a universe label (or file path) to a list of qlib codes. [] if unresolved."""
    p = Path(universe)
    cands: List[Path] = []
    if p.exists():
        cands.append(p)
    else:
        from financial_analyst._config import bundled_config_dir, find_config
        try:
            fc = find_config(f"universes/{universe}.txt")
            if fc is not None:
                cands.append(Path(fc))
        except Exception:
            pass
        cands.append(Path.home() / ".financial-analyst" / "universes" / f"{universe}.txt")
        cands.append(bundled_config_dir() / "universes" / f"{universe}.txt")
    for c in cands:
        try:
            if c.exists():
                codes = _read_codes(c)
                if codes:
                    return codes
        except Exception:
            continue
    return _f10_codes(universe)
