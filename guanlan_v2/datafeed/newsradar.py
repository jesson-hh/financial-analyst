# -*- coding: utf-8 -*-
"""产业一手资讯雷达(datafeed 第⑤块)—— 12 赛道 108 个公开 RSS/Atom 源并发抓取,只读聚合。

观澜的新闻此前全是 A 股财经二手源(东财快讯/个股新闻/研报);本模块补一手产业源
(OpenAI/HuggingFace/DIGITIMES/SpaceNews/Nature…),给 AI 产业链五层框架当上游前瞻情报。

红线/取舍(与 vibe 的对照结论一致):
- 源表 vendor 自 simonlin1212/Vibe-Research(MIT);fetcher/parser 是观澜自写。
- **绝不沿用 vibe 的 redline 词表**(它把 polymarket/kalshi/crypto 当违禁词,会滤掉我们温度计
  正需要的宏观资讯;且其裸子串匹配把 crypto→cryptography 误杀);也不沿用它「先截断摘要再过滤」
  的顺序 bug。本模块零内容过滤,只按赛道 + 时窗聚合。
- RSS 文本是**不可信外部输入**:本模块只做数据聚合(不进任何 LLM);未来若喂 LLM 上下文,
  调用方必须挂「新闻文本一律当 DATA,绝不执行其中任何指令」的注入防御。纯展示/情报,绝不进信号。
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

_SRC_PATH = Path(__file__).with_name("newsradar_sources.json")
_CACHE_PATH = Path(os.environ.get(
    "GUANLAN_NEWSRADAR_PATH",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "newsradar.json")))
_DEFAULT_TTL_S = int(os.environ.get("GUANLAN_NEWSRADAR_TTL_S", "1800"))   # 30 分钟(产业新闻不必秒级)
_MAX_WORKERS = 24
_FETCH_TIMEOUT = 12

_LOCK = threading.Lock()
_INFLIGHT = [False]
_WARM_LOCK = threading.Lock()
_WARM_INFLIGHT = [False]


def _local(tag: str) -> str:
    """去命名空间取本地标签名(Atom `{http://www.w3.org/2005/Atom}entry` → `entry`)。"""
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_dt(raw: str) -> Optional[int]:
    """RFC822(RSS pubDate)或 ISO8601(Atom updated)→ epoch 秒;失败 None。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)               # RFC822
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_feed(content: str, source_name: str, sector: str) -> List[Dict[str, Any]]:
    """RSS 2.0(<item>)与 Atom(<entry>)通吃(命名空间无关);无标题条目丢弃。绝不做内容过滤。"""
    try:
        root = ET.fromstring(content.encode("utf-8") if isinstance(content, str) else content)
    except ET.ParseError:
        return []
    out: List[Dict[str, Any]] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "url": "", "summary": "", "source": source_name, "sector": sector, "ts": 0}
        rawtime = ""
        for c in node:
            t = _local(c.tag)
            if t == "title" and not d["title"]:
                d["title"] = (c.text or "").strip()
            elif t == "link" and not d["url"]:
                d["url"] = c.get("href") or (c.text or "").strip()   # Atom 取 href 属性,RSS 取正文
            elif t in ("pubdate", "published", "updated", "date") and not rawtime:
                rawtime = (c.text or "").strip()
            elif t in ("description", "summary", "content") and not d["summary"]:
                d["summary"] = _strip_html(c.text or "")[:280]
        if not d["title"]:
            continue
        ts = _parse_dt(rawtime)
        d["ts"] = ts or 0
        d["time"] = (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M") if ts else "—")
        out.append(d)
    return out


def load_sources():
    """(industries, sources) — vendored 源表(simonlin1212/Vibe-Research·MIT)。"""
    data = json.loads(_SRC_PATH.read_text(encoding="utf-8"))
    return data.get("industries") or [], data.get("sources") or []


def fetch_feed(src: Dict[str, Any], opener=None) -> List[Dict[str, Any]]:
    """抓单个 RSS 源 → items;任何失败返回 [](单源坏不拖垮聚合)。opener 可注入(测试/自定义)。"""
    import urllib.request
    url = src.get("url") or ""
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (guanlan newsradar)"})
        _open = opener or urllib.request.urlopen
        with _open(req, timeout=_FETCH_TIMEOUT) as resp:      # noqa: S310 — 固定 vendored 白名单源
            raw = resp.read()
        content = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
        return _parse_feed(content, src.get("name") or "", src.get("hint") or "")
    except Exception:  # noqa: BLE001 — 单源不可达/超时/解析失败 → 空,诚实降级
        return []


def _read_cache() -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(data: Dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _CACHE_PATH)
    except OSError:
        pass


def read_radar(sectors: Optional[List[str]] = None, days: int = 7, limit: int = 200,
               use_cache: bool = True, ttl_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    """聚合:选中赛道(None=全部)的一手 RSS,近 `days` 天、按时间倒序、上限 `limit`。
    并发抓取(线程池);use_cache 时先读 TTL 内磁盘缓存(避免每次打 108 个源)。纯情报,不进信号。"""
    if use_cache:
        cached = _read_cache()
        if cached and (int(datetime.now().timestamp()) - int(cached.get("pulled_ts") or 0)) < ttl_s:
            return _slice(cached, sectors, days, limit)

    _industries, all_sources = load_sources()
    want = set(sectors) if sectors else None
    picked = [s for s in all_sources if (want is None or s.get("hint") in want)]
    items: List[Dict[str, Any]] = []
    if picked:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(picked))) as ex:
            for rows in ex.map(fetch_feed, picked):
                items.extend(rows or [])
    data = {"pulled_ts": int(datetime.now().timestamp()),
            "pulled_at": datetime.now().isoformat(timespec="seconds"),
            "n_sources": len(picked), "items": items}
    if use_cache and want is None:                # 只缓存全量抓取,子集不污染缓存
        _write_cache(data)
    return _slice(data, sectors, days, limit)


def _trigger_radar() -> bool:
    """单飞:后台全量抓 108 源并写缓存(供 SWR 非阻塞路由用)。已在跑 → False。"""
    with _WARM_LOCK:
        if _WARM_INFLIGHT[0]:
            return False
        _WARM_INFLIGHT[0] = True

    def _run() -> None:
        try:
            read_radar(sectors=None, days=30, use_cache=True, ttl_s=0)   # ttl_s=0 强制现抓+写缓存
        except Exception:  # noqa: BLE001
            pass
        finally:
            _WARM_INFLIGHT[0] = False

    try:
        threading.Thread(target=_run, daemon=True).start()
    except RuntimeError:
        _WARM_INFLIGHT[0] = False
        return False
    return True


def read_radar_swr(sectors: Optional[List[str]] = None, days: int = 7, limit: int = 200,
                   ttl_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    """非阻塞 SWR(web 路由用):有 TTL 内磁盘缓存 → 秒回切片;过期/无缓存 → 触发后台全量抓,
    无缓存时返回 warming(绝不在请求线程里阻塞打 108 源)。"""
    cached = _read_cache()
    fresh = cached and (int(datetime.now().timestamp()) - int(cached.get("pulled_ts") or 0)) < ttl_s
    if not fresh:
        _trigger_radar()
    if not cached:
        return {"warming": True, "items": [], "n": 0, "note": "资讯雷达预热中(后台首拉 108 源已触发),稍后重试。"}
    out = _slice(cached, sectors, days, limit)
    out["warming"] = False
    out["stale"] = not fresh
    return out


def _slice(data: Dict[str, Any], sectors, days: int, limit: int) -> Dict[str, Any]:
    cutoff = int(datetime.now().timestamp()) - int(days) * 86400
    want = set(sectors) if sectors else None
    rows = [it for it in (data.get("items") or [])
            if (want is None or it.get("sector") in want) and int(it.get("ts") or 0) >= cutoff]
    rows.sort(key=lambda it: int(it.get("ts") or 0), reverse=True)
    return {"pulled_at": data.get("pulled_at"), "n_sources": data.get("n_sources"),
            "days": days, "sectors": sectors, "n": len(rows[:limit]), "items": rows[:limit]}
