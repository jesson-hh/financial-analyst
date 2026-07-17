# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — READ-ONLY adapters over the two real memory stores.

* :class:`AgentMemoryAdapter` — the engine ``memories/`` root: ``_shared/*.md``
  (scope ``agent_shared``) plus every non-underscore agent directory's ``*.md``
  (scope ``agent_own``), preserving ``always_include.txt`` coverage as a
  mandatory hint. ``_proposed`` / ``_pending_introspections`` / ``_buddy`` /
  ``_coordination`` and every other underscore staging dir are excluded.
* :class:`ConsoleMemoryAdapter` — the console ``var/console`` root: global
  ``memory.md`` (keyed/unkeyed dated lines), the reviewed ``memory.archive.md``
  TAIL window and exactly the matching session's ``sessions/<sid>/notes.md``.

Both adapters require an EXPLICIT service-owned root — they never call the
seeding default-root helper (``financial_analyst.memory_paths``
``default_memory_root`` creates + seeds a directory as a resolution side
effect, which a capture path must never do). They never write, never cross
session IDs, never follow an absolute/traversal ``always_include`` target, and
parse reserved exact-apply markers as metadata, not user memory.

PIT note: existing accepted files carry NO usable PIT metadata. Adapters expose
content + logical identity ONLY — no mtime, no Git time, no prose-date guess.
Availability is assigned by the capture service (baseline = cutover attestation
time; every later new revision = its own capture-completed instant).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.memory.models import MemoryContractError

__all__ = [
    "RawSourceUnit",
    "RawMemoryRow",
    "AgentMemoryAdapter",
    "ConsoleMemoryAdapter",
    "APPLY_MARKER_LINE_RE",
]

#: the closed ASCII apply-marker grammar (validated in the proposal layer; the
#: adapters only *recognize* it so it is never parsed as user memory).
APPLY_MARKER_LINE_RE = re.compile(
    r"^<!-- guanlan-memory-apply-v1 marker_id=(apply\.[0-9a-f]{64}) "
    r"request_digest=([0-9a-f]{64}) payload_digest=([0-9a-f]{64}) -->$"
)

_AGENT_STAGING_EXCLUDED = ("_proposed", "_pending_introspections", "_buddy", "_coordination")


@dataclass(frozen=True)
class RawSourceUnit:
    """One stable-scan unit: a logical source/locator plus normalized bytes."""

    source_id: str
    locator: str
    text: str
    apply_marker_id: str | None = None

    @property
    def store_content_digest(self) -> str:
        return content_digest(self.text)


@dataclass(frozen=True)
class RawMemoryRow:
    """One parsed candidate memory row (identity is logical, never a path)."""

    source_id: str
    locator: str
    owner_id: str
    scope: str
    session_id: str | None
    text: str
    identity: dict[str, Any] = field(hash=False)
    mandatory_hint: bool = False


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


# --------------------------------------------------------------------------- #
# AgentMemoryAdapter                                                           #
# --------------------------------------------------------------------------- #
class AgentMemoryAdapter:
    """Read-only view over one explicit agent ``memories/`` root."""

    def __init__(self, root: Path) -> None:
        if not str(root).strip():
            raise MemoryContractError("AgentMemoryAdapter requires an explicit root")
        self.root = Path(root).resolve()

    # -- scan ----------------------------------------------------------------- #
    def _agent_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        out = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("_"):
                continue  # every staging/private underscore dir except the shared scan below
            out.append(child)
        return out

    def _always_include_names(self, agent_dir: Path) -> set[str]:
        """Filenames listed in ``always_include.txt`` — targets must stay under
        the explicit root; absolute / traversal entries fail loud."""
        inc = agent_dir / "always_include.txt"
        if not inc.exists():
            return set()
        names: set[str] = set()
        for line in inc.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if not name:
                continue
            if name.startswith(("/", "\\")) or ":" in name or ".." in name or "/" in name or "\\" in name:
                raise MemoryContractError(
                    f"always_include target {name!r} escapes the agent directory "
                    f"({agent_dir.name}); absolute/traversal targets are rejected"
                )
            names.add(name)
        return names

    def scan_units(self) -> tuple[RawSourceUnit, ...]:
        units: list[RawSourceUnit] = []
        shared = self.root / "_shared"
        if shared.is_dir():
            for p in sorted(shared.glob("*.md")):
                units.append(RawSourceUnit(
                    source_id="agent.shared",
                    locator=f"_shared/{p.name}",
                    text=_normalize(p.read_text(encoding="utf-8")),
                ))
        for agent_dir in self._agent_dirs():
            for p in sorted(agent_dir.glob("*.md")):
                units.append(RawSourceUnit(
                    source_id="agent.own",
                    locator=f"{agent_dir.name}/{p.name}",
                    text=_normalize(p.read_text(encoding="utf-8")),
                ))
        units.sort(key=lambda u: (u.source_id, u.locator))
        return tuple(units)

    def rows(self) -> tuple[RawMemoryRow, ...]:
        rows: list[RawMemoryRow] = []
        include_by_agent = {
            d.name: self._always_include_names(d) for d in self._agent_dirs()
        }
        for unit in self.scan_units():
            dir_name, _, file_name = unit.locator.partition("/")
            if unit.source_id == "agent.shared":
                owner, scope, mandatory = "shared", "agent_shared", False
            else:
                owner, scope = dir_name, "agent_own"
                mandatory = file_name in include_by_agent.get(dir_name, set())
            if not unit.text.strip():
                continue  # an empty document is a unit (continuity) but not a row
            rows.append(RawMemoryRow(
                source_id=unit.source_id,
                locator=unit.locator,
                owner_id=owner,
                scope=scope,
                session_id=None,
                text=unit.text,
                identity={"store": "agent", "owner": owner, "locator": unit.locator},
                mandatory_hint=mandatory,
            ))
        return tuple(rows)


# --------------------------------------------------------------------------- #
# ConsoleMemoryAdapter                                                        #
# --------------------------------------------------------------------------- #
def _is_heading_or_blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _classify_console_line(line: str) -> str | None:
    """``keyed`` / ``unkeyed`` / ``marker`` / None(skip) — mirrors the existing
    console curator grammar (``- [YYYY-MM-DD] (key) ...`` = keyed 常驻)."""
    if _is_heading_or_blank(line):
        return None
    if APPLY_MARKER_LINE_RE.match(line.strip()):
        return "marker"
    # the exact existing keyed grammar (guanlan_v2/console/curator._KEYED_RE).
    if re.match(r"^- \[\d{4}-\d{2}-\d{2}\] \([^)]+\) ", line):
        return "keyed"
    return "unkeyed"


_KEY_RE = re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \(([^)]+)\) ")


class ConsoleMemoryAdapter:
    """Read-only view over one explicit console root (``var/console``-shaped)."""

    def __init__(self, root: Path, *, session_id: str | None, archive_tail_chars: int) -> None:
        if not str(root).strip():
            raise MemoryContractError("ConsoleMemoryAdapter requires an explicit root")
        if archive_tail_chars < 0:
            raise MemoryContractError("archive_tail_chars must be non-negative")
        self.root = Path(root).resolve()
        self.session_id = session_id
        self.archive_tail_chars = archive_tail_chars

    # -- unit reads ------------------------------------------------------------ #
    def _read(self, rel: str) -> str | None:
        p = self.root / rel
        if not p.exists():
            return None
        return _normalize(p.read_text(encoding="utf-8"))

    def _archive_tail(self) -> str | None:
        text = self._read("memory.archive.md")
        if text is None:
            return None
        if not self.archive_tail_chars:
            return ""
        if len(text) <= self.archive_tail_chars:
            return text
        tail = text[-self.archive_tail_chars:]
        # the window truncated mid-line: only COMPLETE lines within the reviewed
        # tail are user memory; a leading fragment is dropped, never guessed at.
        _fragment, sep, rest = tail.partition("\n")
        return rest if sep else ""

    def scan_units(self) -> tuple[RawSourceUnit, ...]:
        units: list[RawSourceUnit] = []
        memory = self._read("memory.md")
        if memory is not None:
            # one physical file backs both logical console.global views; their
            # continuity advances in lockstep on any byte change.
            units.append(RawSourceUnit("console.global.keyed", "memory.md", memory))
            units.append(RawSourceUnit("console.global.unkeyed", "memory.md", memory))
        tail = self._archive_tail()
        if tail is not None:
            units.append(RawSourceUnit(
                "console.global.archive", "memory.archive.md#tail", tail))
        if self.session_id is not None:
            notes = self._read(f"sessions/{self.session_id}/notes.md")
            if notes is not None:
                units.append(RawSourceUnit(
                    "console.session", f"sessions/{self.session_id}/notes.md", notes))
        units.sort(key=lambda u: (u.source_id, u.locator))
        return tuple(units)

    # -- row parsing ------------------------------------------------------------ #
    def _rows_from_text(
        self, *, text: str, source_id: str, locator: str, scope: str,
        session_id: str | None, want: str,
    ) -> list[RawMemoryRow]:
        rows: list[RawMemoryRow] = []
        occurrence: dict[str, int] = {}
        for line in text.splitlines():
            kind = _classify_console_line(line)
            if kind != want:
                continue
            line = line.rstrip()
            if kind == "keyed":
                m = _KEY_RE.match(line)
                key = m.group(1) if m else ""
                identity: dict[str, Any] = {
                    "store": "console", "scope": scope, "session": session_id, "key": key,
                }
                mandatory = True  # keyed 常驻行 = the existing always-injected set
            else:
                digest = content_digest(line)
                idx = occurrence.get(digest, 0)
                occurrence[digest] = idx + 1
                # identity deliberately excludes the source view/locator so the
                # SAME logical record keeps its identity when the curator moves
                # it from memory.md into the reviewed archive window.
                identity = {
                    "store": "console", "scope": scope, "session": session_id,
                    "content": digest, "occurrence": idx,
                }
                mandatory = False
            rows.append(RawMemoryRow(
                source_id=source_id, locator=locator,
                owner_id="console", scope=scope, session_id=session_id,
                text=line, identity=identity, mandatory_hint=mandatory,
            ))
        return rows

    def rows(self) -> tuple[RawMemoryRow, ...]:
        rows: list[RawMemoryRow] = []
        memory = self._read("memory.md")
        if memory is not None:
            rows += self._rows_from_text(
                text=memory, source_id="console.global.keyed", locator="memory.md",
                scope="console_global", session_id=None, want="keyed")
            rows += self._rows_from_text(
                text=memory, source_id="console.global.unkeyed", locator="memory.md",
                scope="console_global", session_id=None, want="unkeyed")
        tail = self._archive_tail()
        if tail:
            # the archived rows are 易逝 unkeyed lines by construction; a keyed
            # line never reaches the archive (curator invariant) but is treated
            # as archive content honestly if ever present.
            for want in ("keyed", "unkeyed"):
                rows += self._rows_from_text(
                    text=tail, source_id="console.global.archive",
                    locator="memory.archive.md#tail",
                    scope="console_global", session_id=None, want=want)
        if self.session_id is not None:
            notes = self._read(f"sessions/{self.session_id}/notes.md")
            if notes is not None:
                for want in ("keyed", "unkeyed"):
                    rows += self._rows_from_text(
                        text=notes, source_id="console.session",
                        locator=f"sessions/{self.session_id}/notes.md",
                        scope="console_session", session_id=self.session_id, want=want)
        return tuple(rows)
