"""v4 变体注册表:models/<id>/{v4_ranking.parquet, meta.json}。生产 v4=prod(只读老路径,不在此)。"""
from __future__ import annotations
import hashlib, json, os, pickle, shutil
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
def variant_ranking_path(vid):
    directory = _dir(vid)
    meta_path = directory / "meta.json"
    if meta_path.exists():
        try:
            artifact = json.loads(meta_path.read_text(encoding="utf-8")).get("ranking_artifact") or {}
        except (ValueError, OSError, AttributeError):
            artifact = {}
        name = str(artifact.get("file") or "")
        if name:
            if os.path.basename(name) != name or "/" in name or "\\" in name:
                raise ValueError("ranking artifact 路径非法")
            return directory / name
    return directory / RANKING_FILE


def variant_meta(vid) -> Dict[str, Any]:
    p = _dir(vid) / "meta.json"
    if not p.exists():
        return {}
    return _normalize_meta(json.loads(p.read_text(encoding="utf-8")))


def _file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_version(meta, ranking_hash, estimator_hash=None):
    core = {key: meta.get(key) for key in (
        "kind", "recipe", "asof", "base_features", "factor_ids", "hyper",
        "training_provenance", "estimator_artifact")}
    core["ranking_sha256"] = ranking_hash
    if estimator_hash is not None:
        core["estimator_actual_sha256"] = estimator_hash
    return "mv_" + hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False,
                                             default=str).encode()).hexdigest()


def current_model_version(vid) -> str | None:
    """Bind validation to model content, not a mutable human-readable id.

    Hash the current ranking bytes AND estimator/recipe/evidence identity.
    Legacy models remain versionable but are not thereby PIT-certified.
    """
    if vid in (None, "", "prod"):
        from guanlan_v2.strategy import paths
        ranking, meta = paths.V4_RANKING_PARQUET, {"kind": "v4-lgb"}
    else:
        ranking, meta = variant_ranking_path(vid), variant_meta(vid)
    if not ranking.exists():
        return None
    estimator_hash = None
    est = meta.get("estimator_artifact") or {}
    if est.get("available") and vid not in (None, "", "prod"):
        ep = _dir(vid) / str(est.get("file", ""))
        if ep.parent.resolve() != _dir(vid).resolve() or not ep.is_file():
            return None
        estimator_hash = _file_sha256(ep)
    return _content_version(meta, _file_sha256(ranking), estimator_hash)


def save_variant(vid, ranking_df, meta, *, estimator=None, preprocessing=None) -> None:
    validate_ranking(ranking_df)
    dr, mr = _dir(vid).resolve(), MODELS_DIR.resolve()
    if dr == mr or mr not in dr.parents or vid == "prod":
        raise ValueError("变体保存路径非法，不能覆盖生产 prod")
    d = _dir(vid); d.mkdir(parents=True, exist_ok=True)
    out = _normalize_meta(meta)
    if estimator is not None:
        try:
            # Only locally trained estimators are serialised. No pickle-loading
            # endpoint is introduced; never unpickle untrusted uploads.
            payload = pickle.dumps({"estimator": estimator,
                                    "preprocessing": preprocessing or {}}, protocol=5)
            digest = hashlib.sha256(payload).hexdigest()
            ep = d / f"estimator_{digest[:20]}.pkl"
            etmp = str(ep) + ".tmp"
            with open(etmp, "wb") as stream:
                stream.write(payload)
            os.replace(etmp, ep)
            out["estimator_artifact"] = {"available": True, "file": ep.name,
                                         "sha256": digest, "format": "trusted-local-pickle-v1"}
        except Exception as exc:
            # Ranking still usable; absence must remain explicit, never claim
            # a persisted estimator merely because a ranking was saved.
            out["estimator_artifact"] = {"available": False,
                                         "reason": f"{type(exc).__name__}: {exc}"}
            out["status"] = "draft"
    else:
        out.setdefault("estimator_artifact", {"available": False, "reason": "ranking-only"})
    # Stage content-addressed artifacts first. A single metadata replacement is
    # the commit point: readers of an older meta retain older immutable files.
    import uuid
    tmp = str(d / f".ranking-{uuid.uuid4().hex}.tmp")
    ranking_df.to_parquet(tmp, index=False)
    ranking_hash = _file_sha256(tmp)
    pq = d / f"ranking_{ranking_hash[:20]}.parquet"
    os.replace(tmp, str(pq))
    out["ranking_artifact"] = {"file": pq.name, "sha256": ranking_hash}
    est = out.get("estimator_artifact") or {}
    out["model_version"] = _content_version(out, ranking_hash,
                                            est.get("sha256") if est.get("available") else None)
    mp = d / "meta.json"; mtmp = str(mp) + ".tmp"
    with open(mtmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1))
    os.replace(mtmp, str(mp))
    # Non-authoritative compatibility mirror for older direct-file consumers.
    # The registry resolves ranking_artifact; mirror failure cannot undo commit.
    try:
        legacy_tmp = str(d / (RANKING_FILE + ".tmp"))
        shutil.copyfile(pq, legacy_tmp)
        os.replace(legacy_tmp, d / RANKING_FILE)
    except OSError:
        pass
    meta.update(out)


def list_variants() -> List[Dict[str, Any]]:
    if not MODELS_DIR.exists():
        return []
    out = [variant_meta(d.name) for d in MODELS_DIR.iterdir()
           if d.is_dir() and (d / "meta.json").exists()]
    out.sort(key=lambda m: (m.get("oos_ic") if m.get("oos_ic") is not None else -1e9), reverse=True)
    return out


def delete_variant(vid) -> None:
    if not str(vid or "").strip():
        raise ValueError("变体 id 为空,拒绝删除")   # 守卫:_dir('')==MODELS_DIR,空 id 会 rmtree 整库
    if vid == "prod":
        raise ValueError("生产 v4(prod)不可删")
    d = _dir(vid)
    _dr, _mr = d.resolve(), MODELS_DIR.resolve()
    if _dr == _mr or _mr not in _dr.parents:
        raise ValueError(f"变体路径越界(须在 models/ 库内),拒绝删除: {vid}")
    # 删的若是当前默认变体 → 先清默认指针(回落 prod),避免悬空(读原始指针,不经 get 的自愈)
    p = _default_path()
    if p.exists():
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("id") == vid:
                p.unlink()
        except (json.JSONDecodeError, OSError, AttributeError):
            pass   # 损坏/读不了/非 dict 指针 → 不阻塞删除(get_default_model 也会自降级)
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
