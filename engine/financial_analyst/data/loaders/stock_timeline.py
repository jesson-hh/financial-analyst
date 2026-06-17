"""Per-stock research timeline loader.

The user maintains markdown research notes per stock (one file per
ticker) — e.g., from years of accumulated analyst memos.
``StockTimelineLoader`` surfaces the latest tail of that file into the
``factor-computer`` output so every report on ``SH600519`` sees prior
research on the same stock instead of starting cold.

Default lookup path: ``~/.financial-analyst/memories/stocks/<CODE>.md``.
Override via ``FA_STOCK_TIMELINE_DIR`` env var or the ``root`` ctor arg.

Companion CLI: ``financial-analyst stocks list / show / import``.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List, Optional


class StockTimelineLoader:
    """Lookup per-stock research markdown by code.

    Files are simple markdown — no schema enforced. Convention: most-recent
    entries at the bottom (so ``load_tail`` surfaces the latest analysis).

    Layout::

        ~/.financial-analyst/memories/stocks/
            SH600519.md
            SH600036.md
            SZ000858.md
            ...
    """

    DEFAULT_DIR_ENV = "FA_STOCK_TIMELINE_DIR"

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            override = os.environ.get(self.DEFAULT_DIR_ENV, "")
            if override:
                root = Path(override).expanduser()
            else:
                root = Path.home() / ".financial-analyst" / "memories" / "stocks"
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, code: str) -> Path:
        return self._root / f"{code}.md"

    def has(self, code: str) -> bool:
        return self.path_for(code).is_file()

    def load(self, code: str) -> Optional[str]:
        """Return the full timeline markdown for ``code``, or ``None`` if
        no file exists for this stock.
        """
        p = self.path_for(code)
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    def load_tail(self, code: str, max_chars: int = 4000) -> Optional[str]:
        """Return only the last ``max_chars`` characters of the timeline.

        Per project convention, newer entries are appended at the bottom,
        so the tail is the most-recent analysis. Capping at ~4 KB keeps
        downstream LLM prompts bounded — large timelines (50 KB+) won't
        blow the context window.
        """
        text = self.load(code)
        if text is None:
            return None
        if len(text) <= max_chars:
            return text
        tail = text[-max_chars:]
        # Try not to cut mid-line — start from the first newline.
        nl = tail.find("\n")
        if 0 < nl < 200:
            tail = tail[nl + 1:]
        return f"... (timeline truncated to last {len(tail)} chars) ...\n\n{tail}"

    def list_codes(self) -> List[str]:
        """Return sorted list of all codes with a timeline file."""
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.md") if p.is_file())

    def append_entry(self, code: str, line: str, *,
                     section: str = "## 觀瀾研报回写 (自动)") -> Path:
        """Append one entry line under ``section`` at the file tail.

        互通审计 P1⑦:研报结论回写时间线,补上「读时间线却从不回写、
        时间线停在导入时刻」的断链。遵守本 loader 的约定 —— 最新在文件底部,
        ``load_tail`` 即可见。文件不存在则新建(带 ``# <CODE>`` 标题)。
        """
        self._root.mkdir(parents=True, exist_ok=True)
        p = self.path_for(code)
        text = ""
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                text = ""
        chunks: List[str] = []
        if not text:
            chunks.append(f"# {code}\n")
        elif not text.endswith("\n"):
            chunks.append("\n")
        if section not in text:
            chunks.append(f"\n{section}\n\n")
        chunks.append(line.rstrip() + "\n")
        with p.open("a", encoding="utf-8") as f:
            f.write("".join(chunks))
        return p

    def import_from(self, source_dir: Path, *, overwrite: bool = False) -> int:
        """Bulk-copy ``<source_dir>/*.md`` into the loader's root. Returns
        the number of files copied. Existing destination files are kept
        unless ``overwrite=True``.

        Typical use:
            ``financial-analyst stocks import G:/stocks/strategy/stocks``
        """
        import shutil
        source_dir = Path(source_dir).expanduser()
        if not source_dir.is_dir():
            raise FileNotFoundError(f"source not a directory: {source_dir}")
        self._root.mkdir(parents=True, exist_ok=True)
        n = 0
        for src in source_dir.glob("*.md"):
            # Skip the INDEX or other meta-files (heuristic: codes start
            # with uppercase ex letters like SH/SZ/BJ)
            stem = src.stem
            if not (stem[:2] in ("SH", "SZ", "BJ") and stem[2:].isdigit()):
                continue
            dst = self._root / src.name
            if dst.exists() and not overwrite:
                continue
            shutil.copy2(src, dst)
            n += 1
        return n

    def stats(self) -> dict:
        codes = self.list_codes()
        if not codes:
            return {"n_codes": 0, "total_bytes": 0, "root": str(self._root)}
        total = sum(self.path_for(c).stat().st_size for c in codes)
        return {
            "n_codes": len(codes),
            "total_bytes": total,
            "avg_kb": round(total / len(codes) / 1024, 1),
            "root": str(self._root),
        }
