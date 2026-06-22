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


MIN_CROSS_SECTION = 100      # 最新截面最少票数(沿用 model_health 的 <100 诚实缺席阈值)
_RANKING_REQUIRED = ("code", "date", "lgb_pct")


def validate_ranking(df) -> None:
    """入库前校验排名契约;不合格抛 ValueError(诚实失败,不冒充可选股模型)。"""
    if df is None or not hasattr(df, "columns"):
        raise ValueError("ranking 非 DataFrame")
    missing = [c for c in _RANKING_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"ranking 缺列: {missing}(必含 lgb_pct)")
    last = df[df["date"] == df["date"].max()]
    if int(last["code"].nunique()) < MIN_CROSS_SECTION:
        raise ValueError(f"最新截面票数 {last['code'].nunique()} < {MIN_CROSS_SECTION}(截面太薄)")


def _dir(vid): return MODELS_DIR / vid
def variant_ranking_path(vid): return _dir(vid) / RANKING_FILE


def variant_meta(vid) -> Dict[str, Any]:
    p = _dir(vid) / "meta.json"
    if not p.exists():
        return {}
    return _normalize_meta(json.loads(p.read_text(encoding="utf-8")))


def save_variant(vid, ranking_df, meta) -> None:
    validate_ranking(ranking_df)
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
