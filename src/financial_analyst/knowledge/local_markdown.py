from __future__ import annotations
from pathlib import Path
from typing import Dict, List
from financial_analyst.knowledge.base import KnowledgeBase


class LocalMarkdownKB(KnowledgeBase):
    def __init__(self, root: Path):
        self.root = Path(root)

    def query(self, query: str, top_k: int = 5) -> List[Dict]:
        q_lower = query.lower()
        scored: List[tuple[int, Dict]] = []
        for md in self.root.rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            score = text.lower().count(q_lower)
            if score > 0:
                scored.append((score, {"path": str(md.relative_to(self.root)), "content": text, "score": score}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def get_related(self, code: str) -> List[Dict]:
        return self.query(code, top_k=5)
