"""v4 变体注册表:models/<id>/{v4_ranking.parquet, meta.json}。生产 v4=prod(只读老路径,不在此)。"""
from __future__ import annotations
import json, os, shutil
from typing import Any, Dict, List
from guanlan_v2.strategy.paths import MODELS_DIR

RANKING_FILE = "v4_ranking.parquet"   # 排名契约文件名(沿用,保 loader/prod 后兼容)
_PROVENANCE_DEFAULTS = {"source": "workshop", "kind": "v4-lgb", "recipe": {}, "retrainable": False}


def _normalize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """补 provenance 缺省(老 meta / 缺字段兜底);不覆盖已有值。"""
    out = dict(meta or {})
    for k, v in _PROVENANCE_DEFAULTS.items():
        out.setdefault(k, v.copy() if isinstance(v, dict) else v)
    return out


def _dir(vid): return MODELS_DIR / vid
def variant_ranking_path(vid): return _dir(vid) / RANKING_FILE


def variant_meta(vid) -> Dict[str, Any]:
    p = _dir(vid) / "meta.json"
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _normalize_meta(raw) if raw else {}


def save_variant(vid, ranking_df, meta) -> None:
    d = _dir(vid); d.mkdir(parents=True, exist_ok=True)
    pq = variant_ranking_path(vid); tmp = str(pq) + ".tmp"
    ranking_df.to_parquet(tmp, index=False); os.replace(tmp, str(pq))
    mp = d / "meta.json"; mtmp = str(mp) + ".tmp"
    with open(mtmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(_normalize_meta(meta), ensure_ascii=False, indent=1))
    os.replace(mtmp, str(mp))


def list_variants() -> List[Dict[str, Any]]:
    if not MODELS_DIR.exists():
        return []
    out = [variant_meta(d.name) for d in MODELS_DIR.iterdir()
           if d.is_dir() and (d / "meta.json").exists()]
    out.sort(key=lambda m: (m.get("oos_ic") if m.get("oos_ic") is not None else -1e9), reverse=True)
    return out


def delete_variant(vid) -> None:
    if vid == "prod":
        raise ValueError("生产 v4(prod)不可删")
    d = _dir(vid)
    if d.exists():
        shutil.rmtree(d)
