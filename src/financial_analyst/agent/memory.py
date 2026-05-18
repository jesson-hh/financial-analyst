from __future__ import annotations
from pathlib import Path
from typing import List, Optional

from financial_analyst.agent.memory_index import MemoryIndex


class AgentMemory:
    def __init__(
        self,
        agent_name: str,
        memory_root: Path,
        borrows: Optional[List[str]] = None,
        index: Optional[MemoryIndex] = None,
    ):
        self.agent_name = agent_name
        self.memory_root = Path(memory_root)
        self.borrows = borrows or []
        self.index = index
        self._cache: Optional[str] = None
        self._shared_cache: Optional[str] = None
        self._always_include_cache: Optional[str] = None

    def _collect_files(self) -> List[Path]:
        paths: List[Path] = []
        shared = self.memory_root / "_shared"
        if shared.exists():
            paths.extend(sorted(shared.glob("*.md")))
        own = self.memory_root / self.agent_name
        if own.exists():
            paths.extend(sorted(own.glob("*.md")))
        for other in self.borrows:
            other_dir = self.memory_root / other
            if other_dir.exists():
                paths.extend(sorted(other_dir.glob("*.md")))
        return paths

    def _load_always_include(self) -> str:
        """Read memories/<agent>/always_include.txt (newline-separated filenames),
        return concatenated content. These files load regardless of retrieval results.
        """
        if self._always_include_cache is not None:
            return self._always_include_cache
        include_path = self.memory_root / self.agent_name / "always_include.txt"
        if not include_path.exists():
            self._always_include_cache = ""
            return ""
        chunks: List[str] = []
        for line in include_path.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if not name:
                continue
            f = self.memory_root / self.agent_name / name
            if f.exists():
                chunks.append(f"# {self.agent_name}/{f.stem}\n{f.read_text(encoding='utf-8')}\n")
        self._always_include_cache = "\n".join(chunks)
        return self._always_include_cache

    def _load_shared(self) -> str:
        if self._shared_cache is not None:
            return self._shared_cache
        shared = self.memory_root / "_shared"
        if not shared.exists():
            self._shared_cache = ""
            return ""
        chunks = []
        for p in sorted(shared.glob("*.md")):
            label = f"_shared/{p.stem}"
            chunks.append(f"# {label}\n{p.read_text(encoding='utf-8')}\n")
        self._shared_cache = "\n".join(chunks)
        return self._shared_cache

    def load_all(self) -> str:
        if self._cache is not None:
            return self._cache
        chunks: List[str] = []
        for p in self._collect_files():
            label = f"{p.parent.name}/{p.stem}"
            chunks.append(f"# {label}\n{p.read_text(encoding='utf-8')}\n")
        self._cache = "\n".join(chunks)
        return self._cache

    def load_relevant(self, query: str, top_k: int = 5, always_include_shared: bool = True) -> str:
        if self.index is None:
            raise RuntimeError(
                "AgentMemory.load_relevant requires MemoryIndex; pass index= to constructor"
            )

        parts: List[str] = []

        if always_include_shared:
            shared = self._load_shared()
            if shared:
                parts.append(shared)

        # Always include critical files listed in always_include.txt
        critical = self._load_always_include()
        if critical:
            parts.append(critical)

        own_hits = self.index.search(query, agent=self.agent_name, top_k=top_k)
        borrowed_hits: List[dict] = []
        for other in self.borrows:
            borrowed_hits.extend(self.index.search(query, agent=other, top_k=top_k))

        # If FTS5 returned 0 results for both own + borrowed, fall back to load_all
        if not own_hits and not borrowed_hits:
            full = self.load_all()
            joined = "\n".join(parts)
            if full and full not in joined:
                parts.append(full)
            return "\n".join(parts)

        seen_keys: set = set()
        # Avoid duplicates between critical and search hits
        for h in own_hits + borrowed_hits:
            key = (h["agent"], h["filename"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            label = f"{h['agent']}/{Path(h['filename']).stem}"
            parts.append(f"# {label}\n{h['content']}\n")

        return "\n".join(parts)

    def reload(self) -> None:
        self._cache = None
        self._shared_cache = None
        self._always_include_cache = None
