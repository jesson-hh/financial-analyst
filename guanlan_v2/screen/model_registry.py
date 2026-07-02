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


_RANKING_REQUIRED = ("code", "date", "lgb_pct")


def validate_ranking(df) -> None:
    """入库前校验排名契约的结构完整性;不合格抛 ValueError(诚实失败,不冒充可选股模型)。
    只校验结构(是 DataFrame、含 code/date/lgb_pct、非空);截面厚度由上游 universe 决定,
    不在此硬卡(否则会误伤小股池如 sample30 等合法训练 + 1 行 plumbing 测试夹具)。"""
    if df is None or not hasattr(df, "columns"):
        raise ValueError("ranking 非 DataFrame")
    missing = [c for c in _RANKING_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"ranking 缺列: {missing}(必含 lgb_pct)")
    if len(df) == 0:
        raise ValueError("ranking 为空(无行)")


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
    # 删的若是当前默认变体 → 先清默认指针(回落 prod),避免悬空(读原始指针,不经 get 的自愈)
    p = _default_path()
    if p.exists():
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("id") == vid:
                p.unlink()
        except (json.JSONDecodeError, OSError, AttributeError):
            pass   # 损坏/读不了/非 dict 指针 → 不阻塞删除(get_default_model 也会自降级)
    d = _dir(vid)
    if d.exists():
        shutil.rmtree(d)


def _default_path():
    return MODELS_DIR / "_default.json"


def get_default_model():
    """当前「默认变体」id;无/损坏/指向已删变体 → None(= 用生产 prod,诚实降级)。"""
    p = _default_path()
    if not p.exists():
        return None
    try:
        mid = json.loads(p.read_text(encoding="utf-8")).get("id")
    except Exception:
        return None
    if not mid or not variant_ranking_path(mid).exists():
        return None
    return mid


def set_default_model(model_id) -> None:
    """设/清默认变体。变体 id → 校验存在后写指针;None/""/"prod" → 删指针(回落官方 prod)。"""
    p = _default_path()
    if model_id in (None, "", "prod"):
        if p.exists():
            p.unlink()
        return
    if not variant_ranking_path(model_id).exists():
        raise ValueError(f"变体不存在: {model_id}")
    try:
        _m = variant_meta(model_id)
    except Exception:  # noqa: BLE001 — meta 读不了不拦(存在性已校验)
        _m = {}
    if (_m or {}).get("status") == "draft":
        raise ValueError(f"变体 {model_id} 处于 draft 区(未过 promote 门槛),不能设默认;"
                         f"复核后重训过门再设")
    p.write_text(json.dumps({"id": model_id}, ensure_ascii=False), encoding="utf-8")
