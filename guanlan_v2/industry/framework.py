# -*- coding: utf-8 -*-
"""行业框架文件(YAML)加载 + 校验 + 派生工具。

框架 = 唯一事实源:drivers/segments/edges/narratives/signal_defs(spec §3)。
坏引用 fail fast(FrameworkError),绝不带病服务。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

FRAMEWORKS_DIR = Path(__file__).resolve().parent / "frameworks"
DEFAULT_FRAMEWORK = FRAMEWORKS_DIR / "ai_chain.yaml"

_cache: dict = {}
_lock = threading.Lock()


class FrameworkError(Exception):
    """框架文件缺字段/坏引用。"""


def _validate(fw: dict, path: str) -> None:
    for key in ("meta", "drivers", "groups", "segments", "edges", "narratives", "signal_defs"):
        if key not in fw:
            raise FrameworkError(f"{path}: 缺顶层字段 {key}")
    sids = {s.get("id") for s in fw["segments"]}
    dids = {d.get("id") for d in fw["drivers"]}
    gids = {g.get("id") for g in fw["groups"]}
    if len(sids) != len(fw["segments"]):
        raise FrameworkError(f"{path}: segment id 重复")
    for s in fw["segments"]:
        for k in ("id", "name", "group", "logic", "keywords"):
            if k not in s:
                raise FrameworkError(f"{path}: segment {s.get('id')} 缺 {k}")
        if s["group"] not in gids:
            raise FrameworkError(f"{path}: segment {s['id']} 引用不存在的 group {s['group']}")
    for e in fw["edges"]:
        for ref in list(e.get("from", [])) + list(e.get("to", [])):
            if ref not in sids | dids:
                raise FrameworkError(f"{path}: edge {e.get('id')} 引用不存在的节点 {ref}")
    for n in fw["narratives"]:
        for a in n.get("activates", []):
            if a.get("segment") not in sids:
                raise FrameworkError(f"{path}: narrative {n.get('id')} 引用不存在的环节 {a.get('segment')}")


def list_frameworks() -> list:
    """扫描 frameworks/*.yaml → [{id, name}](UI 切换器用;坏文件跳过不崩)。"""
    out = []
    for p in sorted(FRAMEWORKS_DIR.glob("*.yaml")):
        try:
            fw = load_framework(path=str(p))
            meta = fw.get("meta") or {}
            out.append({"id": meta.get("id") or p.stem, "name": meta.get("name") or p.stem})
        except Exception:  # noqa: BLE001 — 单框架坏不拖垮清单
            continue
    return out


def load_framework(path: Optional[str] = None, fw: Optional[str] = None) -> dict:
    """加载并校验框架;带进程内缓存(mtime 失效)。fw=框架 id(frameworks/<fw>.yaml)。"""
    import yaml  # 延迟 import(引擎 venv 自带)

    if path:
        p = Path(path)
    elif fw:
        p = FRAMEWORKS_DIR / f"{fw}.yaml"
        if not p.exists():
            raise FrameworkError(f"框架不存在: {fw}({p})")
    else:
        p = DEFAULT_FRAMEWORK
    key = str(p)
    mtime = p.stat().st_mtime
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    fw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(fw, dict):
        raise FrameworkError(f"{key}: 不是 YAML mapping")
    _validate(fw, key)
    with _lock:
        _cache[key] = (mtime, fw)
    return fw


def segment_ids(fw: dict) -> list:
    return [s["id"] for s in fw["segments"]]


def segment_pool(fw: dict, sid: str) -> list:
    for s in fw["segments"]:
        if s["id"] == sid:
            return [x["code"] for x in s.get("stocks", [])]
    return []


def all_pool_codes(fw: dict) -> set:
    out: set = set()
    for s in fw["segments"]:
        out |= {x["code"] for x in s.get("stocks", [])}
    return out


def framework_digest(fw: dict) -> str:
    """给 LLM 抽取 prompt 的紧凑框架摘要(环节/边/叙事 id 白名单+语义)。"""
    lines = ["【环节】(id|名称|关键词)"]
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        lines.append(f"{s['id']}|{s['name']}|{','.join(s['keywords'][:6])}")
    lines.append("【传导边】(id|from→to|机制)")
    for e in fw["edges"]:
        lines.append(f"{e['id']}|{','.join(e['from'])}→{','.join(e['to'])}|{e['mechanism']}")
    lines.append("【叙事】(id|名称)")
    for n in fw["narratives"]:
        lines.append(f"{n['id']}|{n['name']}")
    return "\n".join(lines)
