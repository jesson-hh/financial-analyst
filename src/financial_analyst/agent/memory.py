from __future__ import annotations
from pathlib import Path
from typing import List, Optional


class AgentMemory:
    def __init__(
        self,
        agent_name: str,
        memory_root: Path,
        borrows: Optional[List[str]] = None,
    ):
        self.agent_name = agent_name
        self.memory_root = Path(memory_root)
        self.borrows = borrows or []
        self._cache: Optional[str] = None

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

    def load_all(self) -> str:
        if self._cache is not None:
            return self._cache
        chunks: List[str] = []
        for p in self._collect_files():
            label = f"{p.parent.name}/{p.stem}"
            chunks.append(f"# {label}\n{p.read_text(encoding='utf-8')}\n")
        self._cache = "\n".join(chunks)
        return self._cache

    def reload(self) -> None:
        self._cache = None
