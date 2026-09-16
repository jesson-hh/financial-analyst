"""Local, explainable recall for Weiwo's line-oriented memory files.

Phase one deliberately keeps Markdown as the source of truth.  This module is
only a deterministic read index: it scans the existing global/workspace/session
files (and the global archive), ranks entries against the current user query,
and returns small receipts that can be shown in the UI.  There is no embedding
service, network call, or second writable database to drift out of sync.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set


_LINE_RE = re.compile(
    r"^- \[(?P<date>\d{4}-\d{2}-\d{2})\] "
    r"(?:\((?P<key>[^)]+)\) )?(?P<text>.+)$"
)
_CODE_RE = re.compile(r"(?<!\d)(?:(?:SH|SZ|BJ)\s*)?\d{6}(?!\d)", re.I)
_ASCII_RE = re.compile(r"[a-z][a-z0-9_.-]{1,}", re.I)
_CN_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_LOW_SIGNAL = {
    "一下", "一个", "这个", "那个", "现在", "今天", "还有", "可以", "需要",
    "看看", "进行", "问题", "怎么", "什么", "是否", "以及", "然后", "目前",
    "我们", "你们", "他们", "自己", "就是", "已经", "继续", "帮我", "请你",
    "不要", "不能", "没有", "说明", "根据", "已有", "之前", "说过", "再次",
    "一次", "来自", "哪个", "范围", "系统", "回执", "准确", "混淆", "只根",
    "请按", "一下", "记忆", "内容", "相关", "关于", "其中", "这里", "那里",
}
_SCOPE_LABELS = {
    "global": "全局",
    "global_archive": "全局归档",
    "workspace": "当前研究空间",
    "session": "本会话",
}


@dataclass(frozen=True)
class MemoryEntry:
    """One parsed Markdown memory line."""

    scope: str
    source: str
    line: str
    day: str
    key: str
    text: str

    @property
    def memory_id(self) -> str:
        raw = f"{self.scope}\0{self.source}\0{self.key}\0{self.line}".encode("utf-8")
        return "mem_" + hashlib.sha256(raw).hexdigest()[:12]


def parse_memory_sources(sources: Mapping[str, str]) -> List[MemoryEntry]:
    """Parse valid memory rows, ignoring archive headings and malformed lines."""
    out: List[MemoryEntry] = []
    for scope, body in sources.items():
        if not body:
            continue
        for number, raw in enumerate(body.splitlines(), start=1):
            line = raw.strip()
            match = _LINE_RE.match(line)
            if not match:
                continue
            out.append(MemoryEntry(
                scope=scope,
                source=f"{scope}:{number}",
                line=line,
                day=match.group("date"),
                key=(match.group("key") or "").strip(),
                text=match.group("text").strip(),
            ))
    return out


def _tokens(value: str) -> Set[str]:
    """Small Chinese-friendly lexical index with exact codes and CN n-grams."""
    text = (value or "").lower()
    found: Set[str] = set()
    for code in _CODE_RE.findall(text):
        canonical = code.replace(" ", "").upper()
        found.add(canonical)
        found.add(canonical[-6:])
    for word in _ASCII_RE.findall(text):
        lowered = word.lower()
        found.add(lowered)
        found.update(part for part in re.split(r"[_.-]+", lowered) if len(part) >= 2)
    for chunk in _CN_RE.findall(text):
        if chunk not in _LOW_SIGNAL and len(chunk) <= 12:
            found.add(chunk)
        for width in (2, 3):
            for idx in range(max(0, len(chunk) - width + 1)):
                token = chunk[idx:idx + width]
                if token not in _LOW_SIGNAL:
                    found.add(token)
    return found


def _age_bonus(day: str) -> float:
    try:
        age = max(0, (date.today() - date.fromisoformat(day)).days)
    except (TypeError, ValueError):
        return 0.0
    return 0.35 / (1.0 + age / 45.0)


def recall_memories(query: str, sources: Mapping[str, str], *, top_k: int = 6,
                    exclude_lines: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Return top relevant memories with an auditable lexical explanation.

    A candidate must share at least one meaningful token or an exact key/code;
    recency alone can never make an unrelated memory appear relevant.
    """
    entries = parse_memory_sources(sources)
    live_keys = {entry.key for entry in entries
                 if entry.scope == "global" and entry.key}
    live_unkeyed = {entry.line for entry in entries
                    if entry.scope == "global" and not entry.key}
    entries = [
        entry for entry in entries
        if not (
            entry.scope == "global_archive"
            and ((entry.key and entry.key in live_keys)
                 or (not entry.key and entry.line in live_unkeyed))
        )
    ]
    query_tokens = _tokens(query)
    if not entries or not query_tokens or top_k <= 0:
        return []

    doc_tokens: List[Set[str]] = [_tokens(f"{e.key} {e.text}") for e in entries]
    document_frequency: Dict[str, int] = {}
    for tokens in doc_tokens:
        for token in tokens & query_tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1

    excluded = {line.strip() for line in exclude_lines if str(line).strip()}
    ranked: List[tuple[float, MemoryEntry, Sequence[str]]] = []
    n_docs = len(entries)
    for entry, tokens in zip(entries, doc_tokens):
        matched = tokens & query_tokens
        haystack = f"{entry.key} {entry.text}".lower().replace(" ", "")
        exact_key = bool(entry.key and entry.key.lower().replace(" ", "") in
                         (query or "").lower().replace(" ", ""))
        query_codes = {t for t in query_tokens if _CODE_RE.fullmatch(t)}
        entry_codes = {t for t in tokens if _CODE_RE.fullmatch(t)}
        # A question naming one security must never actively recall a memory
        # explicitly bound to another security merely because both say "成本".
        if query_codes and entry_codes and not (query_codes & entry_codes):
            continue
        exact_codes = {t for t in matched if t in query_codes}
        if not matched and not exact_key and not exact_codes:
            continue

        score = 0.0
        for token in matched:
            df = document_frequency.get(token, 1)
            idf = math.log(1.0 + (n_docs + 0.5) / (df + 0.5))
            score += idf * (1.0 + min(len(token), 6) / 6.0)
        if exact_key:
            score += 8.0
        score += 8.0 * len(exact_codes)
        compact_query = (query or "").lower().replace(" ", "")
        if len(compact_query) >= 4 and compact_query in haystack:
            score += 5.0
        score += {"workspace": 0.65, "session": 0.45,
                  "global": 0.25, "global_archive": 0.0}.get(entry.scope, 0.0)
        score += _age_bonus(entry.day)
        ranked.append((score, entry, sorted(matched, key=lambda x: (-len(x), x))))

    ranked.sort(key=lambda row: (-row[0], row[1].day, row[1].memory_id))
    hits: List[Dict[str, Any]] = []
    seen_lines: Set[str] = set()
    for score, entry, matched in ranked:
        if entry.line in seen_lines:
            continue
        seen_lines.add(entry.line)
        hits.append({
            "id": entry.memory_id,
            "scope": entry.scope,
            "scope_label": _SCOPE_LABELS.get(entry.scope, entry.scope),
            "date": entry.day,
            "key": entry.key or None,
            "text": entry.text,
            "line": entry.line,
            "score": round(score, 4),
            "matched": list(matched[:4]),
            "reason": "匹配 " + "、".join(matched[:4]) if matched else "主题键精确匹配",
            "injected_extra": entry.line not in excluded,
        })
        if len(hits) >= top_k:
            break
    return hits


def format_recall_block(hits: Sequence[Mapping[str, Any]]) -> str:
    """Prompt projection for hits not already present in the baseline block."""
    rows = []
    for hit in hits:
        if not hit.get("injected_extra"):
            continue
        label = hit.get("scope_label") or hit.get("scope") or "记忆"
        rows.append(
            f"- [{label}·{hit.get('date')}] {hit.get('text')} "
            f"(记忆ID:{hit.get('id')}; {hit.get('reason')})"
        )
    if not rows:
        return ""
    return "[本轮主动召回·仅作为历史上下文，仍需核对时效]\n" + "\n".join(rows)
