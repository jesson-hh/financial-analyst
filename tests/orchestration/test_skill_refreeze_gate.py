# -*- coding: utf-8 -*-
"""SKILL-allowlist mini-refreeze · Task 1 — the CONTROLLER GATE.

Charter: ``docs/superpowers/specs/2026-08-11-skill-allowlist-refreeze-charter.md``.
Ledger:  ``docs/superpowers/ledgers/2026-08-11-skill-refreeze-inventory.md``.

This gate **measures everything and rules nothing**. It is the executable half of
its own inventory: every ``[gate-parsed]`` table in that ledger is parsed here and
asserted equal to what the REAL tree carries. A ledger that drifts from the tree
(or a tree that drifts from the frozen inventory) is RED, which is the whole
point — T2 uses the F-E classification as its edit list, T3 uses the three-class
inventory as its regeneration checklist, and the controller rules D-A/D-B/D-C
against measurements, not prose.

**Zero digest literals live in this file.** Every digest is re-derived from the
real builders and compared against the LEDGER. That is deliberate on two counts:
it makes the gate a pure live-vs-recorded equality (so T3 edits one document plus
the goldens, never this file's expectations), and it keeps the class-(a) literal
scan — which sweeps ``tests/`` — from finding its own expectations and
self-detonating.

Gate items, one class each:

1. **F-E** (ledger §1/§1.1/§1.2) — for all 27 sealed skills: does the worker hold
   a sealed data-prefetch row (⇒ it receives a ``RenderedDataBlock``), and does its
   ``Data Source Priority`` section name any block identity? The classification is
   derived live from ``phase3_data_surface().prefetch_binding`` and the real SKILL
   bytes; the ledger must record exactly that. Today's exclusion phrasing is pinned
   per file so T2's edit is a CONSCIOUS flip.
2. **Chain baseline** (ledger §2) — the whole P2→P10 chain plus the satellite
   identities, re-derived live, equal BOTH this ledger's §2 table AND the L3
   closing ledger's §2 table. Any drift = the inventory is stale = STOP.
3. **Blast radius BY CONSTRUCTION** — one probe byte appended to a skill material,
   the whole chain rebuilt from the real builders, and the measured moved-identity
   set compared against §2's ``FROZEN`` / ``MOVES`` / ``MOVES-IF-IN-WINDOW``
   verdict column. Grep alone could not prove this; a rebuild can.
4. **Class (a)** (ledger §3/§3.1) — every digest literal of a moving identity under
   ``tests/`` + ``guanlan_v2/`` + ``config/``, both directions, plus the
   not-moving carriers' counts.
5. **Class (b)** (ledger §4) — the golden FILE byte-sha pins, which a
   digest-value grep structurally cannot find (L3 §12.5's lesson), plus the four
   mirror-identity guards.
6. **Class (c)** (ledger §5) — positional/indexed access into collections this
   change could grow or reorder, each classified in a closed vocabulary.
7. **Cross-ledger** (ledger §6) — the landing sites that live in ``docs/`` yet are
   enforced by tests (the L3 ledger is a machine-checked artifact, not prose).
8. **STOP conditions** (ledger §7) — schema manifests, registry digests and preset
   records must not move under a skill-material edit.
9. **Ledger discipline** — closed vocabularies, parsed-rows == numbered-rows, no
   table line that is neither header, separator nor a parseable row, and every
   recorded anchor resolves in its file.

Run: ``python -m pytest tests/orchestration/test_skill_refreeze_gate.py -v``
"""
from __future__ import annotations

import hashlib
import importlib
import json
import pathlib
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGERS = _REPO_ROOT / "docs" / "superpowers" / "ledgers"
_LEDGER = _LEDGERS / "2026-08-11-skill-refreeze-inventory.md"
_L3_LEDGER = _LEDGERS / "2026-07-31-L3-refreeze-inventory.md"
_TREE = _REPO_ROOT / "guanlan_v2" / "orchestration" / "skills"
_GOLDEN = _REPO_ROOT / "tests" / "orchestration" / "golden"
_EVENTS = _REPO_ROOT / "var" / "orchestration" / "events" / "main.jsonl"
_PAYLOADS = _REPO_ROOT / "var" / "orchestration" / "payloads" / "main"

_LEDGER_TEXT = _LEDGER.read_text(encoding="utf-8")
_L3_TEXT = _L3_LEDGER.read_text(encoding="utf-8")

#: the canonical Data Source Priority heading every skill-v1 document carries.
_DSP_HEADING = "## ⚠️ CRITICAL: Data Source Priority"

#: the tokens that would make a SKILL's allowlist NAME the L3 rendered-block
#: identity. Today none appears in any DSP section — that is exactly the state
#: this gate pins so T2's edit is conscious.
_BLOCK_IDENTITY_TOKENS = ("RenderedDataBlock", "untrusted_data",
                          "verified_snapshot", "renderer_ref", "block_digest")

#: the five skills whose blast radius is measured by construction (the four
#: charter-named candidates plus ``text.policy``, which T1 measured into the set).
_PROBE_SKILLS = ("dec.pm", "dec.research_mgr", "pv.technical", "text.news",
                 "text.policy")

#: the bytes appended to one skill material to make the rebuild probe falsifiable.
#: A body line, so ``parse_skill_v1`` still accepts the document (an edit the
#: grammar rejects would prove nothing about digest propagation).
_PROBE_BYTES = b"\n- probe byte.\n"

#: closed vocabularies. A spelling outside these is a gate failure: the whole
#: discipline is "act only on the recorded verdict", which is meaningless if the
#: column can carry free text.
_CLASS_VOCAB = frozenset({"GAP-CONFIRMED", "NO-BLOCK", "NAMES-BLOCK"})
_VERDICT_VOCAB = frozenset({"FROZEN", "MOVES", "MOVES-IF-IN-WINDOW"})
_FORM_VOCAB = frozenset({"full64", "prefix8"})
_POSITIONAL_VOCAB = frozenset({"fragile", "self-protecting", "benign"})
_CROSS_VOCAB = frozenset({"LEDGER-EQUALITY", "ANCHOR", "FROZEN-EXPECTATION"})

_SCAN_ROOTS = ("tests", "guanlan_v2", "config")
_SCAN_SUFFIXES = (".py", ".json", ".md", ".txt", ".yaml", ".yml", ".cfg",
                  ".ini", ".toml")


# --------------------------------------------------------------------------- #
# ledger parsing — the document is a machine-checked artifact, not prose        #
# --------------------------------------------------------------------------- #
def _section(text: str, number: str) -> str:
    """One top-level section's text, so a table parser can never reach across
    into a neighbouring section's rows."""
    start = text.index(f"\n## {number}.")
    nxt = text.find("\n## ", start + 1)
    return text[start:nxt if nxt != -1 else len(text)]


def _subsection(text: str, number: str) -> str:
    start = text.index(f"\n### {number}")
    nxt = text.find("\n## ", start + 1)
    return text[start:nxt if nxt != -1 else len(text)]


_NUMBERED_ROW = re.compile(r"^\|\s*\d+\s*\|", re.MULTILINE)
_SEPARATOR = re.compile(r"\|(\s*:?-+:?\s*\|)+")


def _table(text: str, header: str) -> str:
    """The contiguous ``|``-line block that starts at ``header``.

    Section-level slicing is not precise enough: several sections carry more than
    one table, and a parser aimed at one of them would report the other's rows as
    'unparsed'. Keying on the exact header line makes each table its own closed
    universe, which is what the no-silent-skip closure below needs to be honest.
    """
    idx = text.index(header)
    out: list[str] = []
    for line in text[idx:].splitlines():
        if not line.startswith("|"):
            break
        out.append(line)
    assert len(out) >= 3, f"table {header!r} has no rows"
    return "\n".join(out)


def _closure(block: str, row_re: re.Pattern[str], label: str) -> None:
    """No numbered row was silently skipped, and no table line escaped the parser.

    The count check alone is not enough: it keys BOTH sides on a leading
    ``| <digits> |`` cell, so a row that simply omits its number escapes both.
    Every line after the header/separator must therefore be a full parse.
    """
    lines = block.splitlines()
    assert _SEPARATOR.fullmatch(lines[1].strip()), (
        f"{label}: second table line is not a separator: {lines[1]!r}")
    body = lines[2:]
    rows = row_re.findall("\n".join(body))
    numbered = _NUMBERED_ROW.findall("\n".join(body))
    if numbered:
        assert len(rows) == len(numbered), (
            f"{label}: {len(numbered)} numbered rows but only {len(rows)} parsed "
            "— a row fell out of the inventory; restructure the row, never "
            "weaken the parser")
    unparsed = [ln for ln in body if not row_re.match(ln)]
    assert unparsed == [], (
        f"{label}: table lines that are neither header, separator nor a "
        f"parseable row (an unnumbered row escapes both counts): {unparsed}")


_FE_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([a-z0-9_.]+)`\s*\|\s*([^|]+?)\s*\|\s*(yes|no)\s*\|"
    r"\s*([A-Z-]+)\s*\|", re.MULTILINE)
_CLAUSE_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([a-z0-9_.]+)`\s*\|[^|]*\|\s*([^|]+?)\s*\|",
    re.MULTILINE)
_IDENTITY_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`|]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|\s*"
    r"([A-Z-]+)\s*\|", re.MULTILINE)
_LITERAL_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`|]+)`\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*"
    r"(\w+)\s*\|", re.MULTILINE)
_FROZEN_CARRIER_ROW = re.compile(
    r"^\|\s*`?([A-Za-z0-9_./:@-]+)`?\s*\|\s*(\d+)\s*\|$", re.MULTILINE)
_BYTESHA_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`|]+)`\s*\|\s*`([^`|]+)`\s*\|\s*`([^`|]+)`\s*\|"
    r"\s*(?:`([0-9a-f]{64})`|—)\s*\|", re.MULTILINE)
_POSITIONAL_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`|]+)`\s*\|\s*`([^`|]+)`\s*\|[^|]*\|\s*"
    r"\*{0,2}([a-z-]+)\*{0,2}\s*\|", re.MULTILINE)
_CROSS_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`|]+)`\s*\|\s*`([^`|]+)`\s*\|\s*([A-Z-]+)\s*\|",
    re.MULTILINE)
_PM_RUN_ROW = re.compile(
    r"^\|\s*`(deep-[0-9a-f]{16})`\s*\|[^|]*\|[^|]*\|[^|]*\|\s*`(\w+)`\s*\|"
    r"\s*`(\w+)`\s*\|", re.MULTILINE)

#: the L3 ledger's own §2 chain table (node | digest | vs-spec | FROZEN/MOVES).
_L3_CHAIN_ROW = re.compile(
    r"^\|\s*(P[0-9A-Za-z-]+)\s*\|\s*`([0-9a-f]{64})`\s*\|", re.MULTILINE)


_H_FE = "| # | skill | 预取行 | DSP 命名块身份 | 分类 |"
_H_ROWS = "| worker | method | invocation_mode | success_requires_finalized_call | capability |"
_H_CLAUSE = "| # | skill | 白名单命名的来源(逐字) | 排除条款(逐字,空白归一) |"
_H_IDENTITY = "| # | identity | digest (measured @ HEAD) | verdict |"
_H_LITERAL = "| # | file | line | identity | form |"
_H_CARRIER = "| identity | 落点数 |"
_H_BYTESHA = "| # | file | anchor | subject | sha (measured @ HEAD) |"
_H_POSITIONAL = "| # | file | anchor | 索引的集合 | verdict |"
_H_CROSS = "| # | file | anchor | kind | 后果 |"


def _fe_rows() -> list[tuple[int, str, str, bool, str]]:
    block = _table(_LEDGER_TEXT, _H_FE)
    _closure(block, _FE_ROW, "ledger §1")
    rows = [(int(m.group(1)), m.group(2), m.group(3).strip(),
             m.group(4) == "yes", m.group(5))
            for m in _FE_ROW.finditer(block)]
    assert rows, "the ledger's §1 F-E classification table did not parse"
    return rows


def _recorded_prefetch_rows() -> dict[str, tuple[str, ...]]:
    """§1's sealed-prefetch-row table — the structural basis of 'receives a block'."""
    block = _table(_LEDGER_TEXT, _H_ROWS)
    row_re = re.compile(
        r"^\|\s*`([a-z0-9_.]+)`\s*\|\s*`(\w+)`\s*\|\s*`(\w+)`\s*\|\s*(True|False)"
        r"\s*\|\s*`([a-z0-9_.]+)`\s*\|", re.MULTILINE)
    _closure(block, row_re, "ledger §1 prefetch rows")
    out: dict[str, tuple[str, ...]] = {}
    for m in row_re.finditer(block):
        out.setdefault(m.group(1), ())
        out[m.group(1)] = tuple(sorted(out[m.group(1)] + (m.group(2),)))
    assert out, "the ledger's sealed-prefetch-row table did not parse"
    return out


def _clause_rows() -> list[tuple[int, str, str]]:
    block = _table(_LEDGER_TEXT, _H_CLAUSE)
    _closure(block, _CLAUSE_ROW, "ledger §1.1")
    rows = [(int(m.group(1)), m.group(2), m.group(3).strip())
            for m in _CLAUSE_ROW.finditer(block)]
    assert rows, "the ledger's §1.1 exclusion-clause table did not parse"
    return rows


def _identity_rows() -> list[tuple[int, str, str, str]]:
    block = _table(_LEDGER_TEXT, _H_IDENTITY)
    _closure(block, _IDENTITY_ROW, "ledger §2")
    rows = [(int(m.group(1)), m.group(2), m.group(3), m.group(4))
            for m in _IDENTITY_ROW.finditer(block)]
    assert rows, "the ledger's §2 identity table did not parse"
    return rows


def _literal_rows() -> list[tuple[int, str, int, str, str]]:
    block = _table(_LEDGER_TEXT, _H_LITERAL)
    _closure(block, _LITERAL_ROW, "ledger §3")
    rows = [(int(m.group(1)), m.group(2), int(m.group(3)), m.group(4).strip(),
             m.group(5))
            for m in _LITERAL_ROW.finditer(block)]
    assert rows, "the ledger's §3 literal-inventory table did not parse"
    return rows


def _frozen_carrier_rows() -> dict[str, int]:
    block = _table(_LEDGER_TEXT, _H_CARRIER)
    _closure(block, _FROZEN_CARRIER_ROW, "ledger §3.1")
    rows = {m.group(1): int(m.group(2)) for m in _FROZEN_CARRIER_ROW.finditer(block)}
    assert rows, "the ledger's §3.1 not-moving-carrier table did not parse"
    return rows


def _bytesha_rows() -> list[tuple[int, str, str, str, str | None]]:
    block = _table(_LEDGER_TEXT, _H_BYTESHA)
    _closure(block, _BYTESHA_ROW, "ledger §4")
    rows = [(int(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5))
            for m in _BYTESHA_ROW.finditer(block)]
    assert rows, "the ledger's §4 byte-sha table did not parse"
    return rows


def _positional_rows() -> list[tuple[int, str, str, str]]:
    block = _table(_LEDGER_TEXT, _H_POSITIONAL)
    _closure(block, _POSITIONAL_ROW, "ledger §5")
    rows = [(int(m.group(1)), m.group(2), m.group(3), m.group(4))
            for m in _POSITIONAL_ROW.finditer(block)]
    assert rows, "the ledger's §5 positional-access table did not parse"
    return rows


def _cross_rows() -> list[tuple[int, str, str, str]]:
    block = _table(_LEDGER_TEXT, _H_CROSS)
    _closure(block, _CROSS_ROW, "ledger §6")
    rows = [(int(m.group(1)), m.group(2), m.group(3), m.group(4))
            for m in _CROSS_ROW.finditer(block)]
    assert rows, "the ledger's §6 cross-ledger table did not parse"
    return rows


def _l3_chain() -> dict[str, str]:
    sec = _section(_L3_TEXT, "2")
    rows = {m.group(1): m.group(2) for m in _L3_CHAIN_ROW.finditer(sec)}
    assert rows, "the L3 ledger's §2 chain table did not parse"
    return rows


# --------------------------------------------------------------------------- #
# live measurement — the real builders, never a fixture standing in for a fact   #
# --------------------------------------------------------------------------- #
#: the lazily-cached digest globals every chain module keeps. The rebuild probe
#: MUST clear them, or the probe would compare a mutated tree against a cached
#: pre-mutation value (and, worse, leave the mutated value behind for every other
#: test module in the session).
_CACHE_SITES = (
    ("guanlan_v2.orchestration.adapters.chain",
     ("_PHASE9_REGISTRY_DIGEST", "_PHASE9_CATALOG_DIGEST")),
    ("guanlan_v2.orchestration.bootstrap",
     ("_PHASE5_REGISTRY_DIGEST", "_PHASE5_CATALOG_DIGEST")),
    ("guanlan_v2.orchestration.data.catalog", ("_PHASE3_DATA_CATALOG_DIGEST",)),
    ("guanlan_v2.orchestration.data.schema_registry",
     ("_PHASE3_DATA_REGISTRY_DIGEST",)),
    ("guanlan_v2.orchestration.lane_catalog",
     ("_PHASE8_REGISTRY_DIGEST", "_PHASE8_CATALOG_DIGEST")),
    ("guanlan_v2.orchestration.memory.catalog", ("_PHASE3_FULL_CATALOG_DIGEST",)),
    ("guanlan_v2.orchestration.memory.schema_registry",
     ("_PHASE3_FULL_REGISTRY_DIGEST",)),
    ("guanlan_v2.orchestration.phase7_registry",
     ("_PHASE7_REGISTRY_DIGEST", "_PHASE7_CATALOG_DIGEST",
      "_PHASE7_PLANNER_SPEC_DIGEST")),
    ("guanlan_v2.orchestration.pipeline.chain",
     ("_PHASE10_REGISTRY_DIGEST", "_PHASE10_CATALOG_DIGEST")),
)


def _clear_chain_caches() -> None:
    for mod_name, attrs in _CACHE_SITES:
        mod = importlib.import_module(mod_name)
        for attr in attrs:
            setattr(mod, attr, None)


def _measure() -> dict[str, str]:
    """Every §2 identity, re-derived from the real builders."""
    _clear_chain_caches()
    from guanlan_v2.orchestration.adapters import chain as p9
    from guanlan_v2.orchestration.bootstrap import phase5_catalog_snapshot
    from guanlan_v2.orchestration.data.catalog import (
        phase3_data_catalog_snapshot, phase3_data_surface)
    from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot
    from guanlan_v2.orchestration.memory.catalog import phase3_full_catalog_snapshot
    from guanlan_v2.orchestration.phase7_registry import phase7_catalog_snapshot
    from guanlan_v2.orchestration.pipeline.assembly import (
        PRODUCTION_PRESETS_DIR, load_phase10_preset_registry)
    from guanlan_v2.orchestration.pipeline.chain import phase10_catalog_snapshot
    from guanlan_v2.orchestration.presets import load_compat_catalog
    from guanlan_v2.orchestration.shadow import phase6_catalog_snapshot
    from guanlan_v2.orchestration.trial import phase4_catalog_snapshot

    out: dict[str, str] = {
        "P2-static": load_compat_catalog().snapshot.catalog_digest,
        "P3-data": phase3_data_catalog_snapshot().catalog_digest,
        "P3-full": phase3_full_catalog_snapshot().catalog_digest,
        "P4": phase4_catalog_snapshot().catalog_digest,
        "P5": phase5_catalog_snapshot().catalog_digest,
        "P6": phase6_catalog_snapshot().catalog_digest,
        "P7": phase7_catalog_snapshot().catalog_digest,
    }
    p8 = phase8_catalog_snapshot()
    out["P8"] = p8.catalog_digest
    out["P9"] = p9.phase9_catalog_snapshot().catalog_digest
    out["P10"] = phase10_catalog_snapshot().catalog_digest

    surface = phase3_data_surface()
    out["prefetch_binding"] = surface.prefetch_binding.binding_digest
    out["bridge.data_runtime.prefetch@1"] = surface.config_ref.content_digest
    out["bridge.data_runtime.descriptor@1"] = surface.descriptor_ref.content_digest
    out["bridge.data_runtime.provider@1"] = surface.provider_ref.content_digest
    out["bridge.data_runtime.analyzer@1"] = surface.analyzer_ref.content_digest

    for w in p8.workers:
        if w.catalog_role != "final":
            continue
        out[f"worker:{w.id}"] = w.semantic_digest()
        for sb in w.skills:
            out[f"{sb.skill_ref.id}@{sb.skill_ref.version}"] = \
                sb.skill_ref.content_digest

    reg = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    for pid in ("pipeline.luozi_deep_decide", "pipeline.luozi_deep_decide_reduced",
                "pipeline.screening_lane"):
        out[f"preset:{pid}"] = reg.get(pid).semantic_digest()
    return out


def _measure_stop_surface() -> dict[str, str]:
    """The STOP-condition surface: registry digests + schema-manifest bytes."""
    _clear_chain_caches()
    from guanlan_v2.orchestration import lane_catalog as p8
    from guanlan_v2.orchestration import phase7_registry as p7
    from guanlan_v2.orchestration.adapters import chain as p9
    from guanlan_v2.orchestration.data import schema_registry as p3d
    from guanlan_v2.orchestration.memory import schema_registry as p3f
    from guanlan_v2.orchestration.pipeline import chain as p10

    out = {
        "reg:P3-data": p3d.PHASE3_DATA_REGISTRY_DIGEST,
        "reg:P3-full": p3f.PHASE3_FULL_REGISTRY_DIGEST,
        "reg:P7": p7.PHASE7_REGISTRY_DIGEST,
        "reg:P7-planner_spec": p7.PHASE7_PLANNER_SPEC_DIGEST,
        "reg:P8": p8.PHASE8_REGISTRY_DIGEST,
        "reg:P9": p9.PHASE9_REGISTRY_DIGEST,
        "reg:P10": p10.PHASE10_REGISTRY_DIGEST,
    }
    for f in sorted(_GOLDEN.glob("*schema_manifest*.json")):
        out[f"sha:{f.name}"] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


class _AppendedSkillBytes:
    """In-memory one-byte-append to ONE skill material, disk untouched.

    The honest way to answer "which downstream digests move": rebuild the whole
    chain over changed bytes and look. A grep can only find the literals someone
    already wrote down; this finds the identities, including the ones nobody
    wrote down anywhere.

    Nothing is written to the tree or the mirror — ``Path.read_bytes`` is wrapped
    for the duration and restored in ``finally``, and every lazily-cached chain
    digest is cleared on both entry and exit so no mutated value can leak into a
    later test.
    """

    def __init__(self, skill_id: str) -> None:
        self._target = (_TREE / skill_id / "SKILL.md").resolve()
        self._orig = pathlib.Path.read_bytes

    def __enter__(self) -> "_AppendedSkillBytes":
        target, orig = self._target, self._orig

        def patched(this: pathlib.Path) -> bytes:
            raw = orig(this)
            try:
                resolved = this.resolve()
            except OSError:  # pragma: no cover - defensive
                return raw
            return raw + _PROBE_BYTES if resolved == target else raw

        pathlib.Path.read_bytes = patched  # type: ignore[method-assign]
        _clear_chain_caches()
        return self

    def __exit__(self, *exc) -> None:
        pathlib.Path.read_bytes = self._orig  # type: ignore[method-assign]
        _clear_chain_caches()


def _sealed_prefetch_rows() -> dict[str, tuple[str, ...]]:
    """worker id -> the data method ids it holds a SEALED prefetch row for.

    This is what "receives a ``RenderedDataBlock``" means structurally: no row,
    no block. It is read from the real sealed binding, never from a list.
    """
    from guanlan_v2.orchestration.data.catalog import phase3_data_surface
    surface = phase3_data_surface()
    method_of = {m.method_ref: m.method_id for m in surface.method_specs}
    rows: dict[str, set[str]] = {}
    for op in surface.prefetch_binding.operations:
        rows.setdefault(op.worker_id, set()).add(method_of[op.method_ref])
    return {k: tuple(sorted(v)) for k, v in rows.items()}


def _tree_skill_ids() -> tuple[str, ...]:
    from guanlan_v2.orchestration.skilltree import load_skill_tree
    return tuple(s.skill_id for s in load_skill_tree(_TREE))


def _dsp_section(skill_id: str) -> str:
    text = (_TREE / skill_id / "SKILL.md").read_text(encoding="utf-8")
    assert _DSP_HEADING in text, (
        f"{skill_id}: the canonical Data Source Priority heading is gone — the "
        "F-E measurement reads that section and would silently measure nothing")
    return text.split(_DSP_HEADING, 1)[1].split("\n## ", 1)[0]


def _normalized(skill_id: str) -> str:
    text = (_TREE / skill_id / "SKILL.md").read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", text)


def _measured_class(skill_id: str, rows: dict[str, tuple[str, ...]]) -> str:
    named = any(tok in _dsp_section(skill_id) for tok in _BLOCK_IDENTITY_TOKENS)
    if named:
        return "NAMES-BLOCK"
    return "GAP-CONFIRMED" if rows.get(skill_id) else "NO-BLOCK"


def _scan(needles: dict[str, str]) -> set[tuple[str, int, str]]:
    """(path, line, identity-name) for every occurrence of every needle.

    ``docs/`` is deliberately OUT of scope: both this ledger and the L3 ledger
    live there, so scanning it would make the inventory find its own rows. The
    docs-side landing sites are inventoried separately in §6 and asserted by
    their own mechanism.
    """
    hits: set[tuple[str, int, str]] = set()
    for root_name in _SCAN_ROOTS:
        root = _REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
                continue
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                for needle, name in needles.items():
                    if needle in line:
                        hits.add((rel, i, name))
    return hits


# --------------------------------------------------------------------------- #
# module fixtures (expensive real rebuilds, done once)                          #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def measured() -> dict[str, str]:
    return _measure()


@pytest.fixture(scope="module")
def probes() -> dict[str, dict[str, str]]:
    """skill id -> the whole identity map rebuilt with that skill's bytes changed."""
    out: dict[str, dict[str, str]] = {}
    for sid in _PROBE_SKILLS:
        with _AppendedSkillBytes(sid):
            out[sid] = _measure()
    return out


@pytest.fixture(scope="module")
def prefetch_rows() -> dict[str, tuple[str, ...]]:
    return _sealed_prefetch_rows()


# =========================================================================== #
# GATE 1 — F-E: the measurement the charter calls the possibly-biggest finding  #
# =========================================================================== #
class TestGate1FEMeasurement:
    def test_the_ledger_classifies_every_skill_in_the_real_tree(self):
        recorded = {sid for _n, sid, _r, _named, _c in _fe_rows()}
        assert recorded == set(_tree_skill_ids()), (
            "the F-E table must cover the WHOLE sealed set so the controller "
            "rules once; only in ledger: "
            f"{sorted(recorded - set(_tree_skill_ids()))}; only in tree: "
            f"{sorted(set(_tree_skill_ids()) - recorded)}")

    def test_the_row_numbering_is_contiguous_from_one(self):
        nums = [n for n, *_ in _fe_rows()]
        assert nums == list(range(1, len(nums) + 1))

    def test_the_class_column_is_a_closed_vocabulary(self):
        bad = {c for *_r, c in _fe_rows()} - _CLASS_VOCAB
        assert bad == set(), f"unknown F-E classes: {sorted(bad)}"

    def test_the_recorded_prefetch_rows_are_the_real_sealed_binding(
            self, prefetch_rows):
        recorded = {sid: rows for _n, sid, rows, _named, _c in _fe_rows()
                    if rows != "—"}
        measured = {k: ", ".join(v) for k, v in prefetch_rows.items()}
        assert recorded == measured, (
            "the 预取行 column is what makes 'receives a RenderedDataBlock' a "
            f"structural fact rather than an opinion: {recorded} != {measured}")
        # …and the standalone row table (which also records invocation_mode /
        # finalized-call shape) must name exactly the same workers.
        assert _recorded_prefetch_rows() == prefetch_rows

    def test_the_recorded_class_equals_the_measured_class(self, prefetch_rows):
        wrong = {sid: (cls, _measured_class(sid, prefetch_rows))
                 for _n, sid, _r, _named, cls in _fe_rows()
                 if cls != _measured_class(sid, prefetch_rows)}
        assert wrong == {}, f"ledger vs measured F-E class: {wrong}"

    def test_no_skill_names_a_rendered_block_identity_today(self, prefetch_rows):
        """The state T2 flips, pinned so the flip is CONSCIOUS.

        Every sealed skill's Data Source Priority section is silent about the L3
        rendered block. When T2 adds the honest entry this test goes RED for the
        edited files — that redness is the deliverable, not an accident.
        """
        named = {sid: [tok for tok in _BLOCK_IDENTITY_TOKENS
                       if tok in _dsp_section(sid)]
                 for sid in _tree_skill_ids()}
        assert {k: v for k, v in named.items() if v} == {}, (
            "a SKILL already names the rendered-block identity — either T2 "
            "landed (update this pin consciously) or the measurement is wrong")
        assert all(cls != "NAMES-BLOCK" for *_r, cls in _fe_rows())

    def test_the_recorded_exclusion_clause_is_present_verbatim_today(self):
        """Whitespace-normalized containment: the ledger quotes the real bytes.

        Mutating the quote in the ledger reddens exactly this test; mutating the
        SKILL text reddens it too. Both directions are the point — the packet's
        'here is what the material actually says' claim has to be falsifiable.
        """
        for num, sid, clause in _clause_rows():
            assert clause in _normalized(sid), (
                f"§1.1 row {num} ({sid}): the recorded exclusion clause is not "
                "in the skill text (whitespace-normalized)")

    def test_the_clause_table_covers_every_gap_confirmed_skill(self):
        quoted = {sid for _n, sid, _c in _clause_rows()}
        gaps = {sid for _n, sid, _r, _named, cls in _fe_rows()
                if cls == "GAP-CONFIRMED"}
        assert gaps <= quoted, (
            f"GAP-CONFIRMED skills with no quoted clause: {sorted(gaps - quoted)}")

    def test_dec_research_mgr_holds_no_row_so_same_root_is_structurally_refuted(
            self, prefetch_rows):
        """The one measurement that REFUTES a charter hypothesis.

        F-E asked whether ``dec.research_mgr``'s non-citation shares the defect-7
        root. It cannot: the seat holds no sealed prefetch row, so no
        ``RenderedDataBlock`` is ever assembled for it. Its blankness is
        downstream starvation, and it disappears when the upstream seats are
        fixed. Recording that as 'same root' would put an untrue sentence in the
        ledger.
        """
        assert "dec.research_mgr" not in prefetch_rows
        recorded = {sid: cls for _n, sid, _r, _named, cls in _fe_rows()}
        assert recorded["dec.research_mgr"] == "NO-BLOCK"
        assert recorded["dec.pm"] == "GAP-CONFIRMED"

    def test_text_policy_is_the_fourth_gap_the_charter_did_not_name(
            self, prefetch_rows):
        """``text.policy`` holds a ``news`` row with the same always-invoke /
        finalized-call shape as ``text.news`` and the same exclusion phrasing —
        so the charter's '2 confirmed + 2 suspected' set was measured into a
        different 4. It is dormant only because no committed preset selects it."""
        assert prefetch_rows["text.policy"] == ("news",)
        recorded = {sid: cls for _n, sid, _r, _named, cls in _fe_rows()}
        assert recorded["text.policy"] == "GAP-CONFIRMED"

    def test_the_gap_confirmed_set_is_exactly_the_workers_holding_rows(
            self, prefetch_rows):
        gaps = {sid for _n, sid, _r, _named, cls in _fe_rows()
                if cls == "GAP-CONFIRMED"}
        assert gaps == set(prefetch_rows), (
            "GAP-CONFIRMED must be derivable from the sealed binding alone "
            "(no skill names the block today), so the two sets are the same set")
        assert len(gaps) == 6

    def test_the_promise_supply_lint_scans_only_ww_tokens(self):
        """T2's hard constraint, pinned where T2 will read it.

        The 承诺-供给 lint reads the Data Source Priority section and scans for
        ``ww_`` tokens only. An honest rendered-block entry introduces no such
        token, so the lint stays clean by construction — but an entry that names
        a ``ww_*`` the worker's allowlist does not supply goes red immediately.
        """
        from guanlan_v2.orchestration import capability_manifest as CM
        assert "ww_" in CM._WW_TOKEN.pattern
        for sid in ("text.news", "pv.microstructure", "pv.price_action",
                    "pv.technical", "text.policy"):
            section = _dsp_section(sid)
            assert CM._promised_tools((_TREE / sid / "SKILL.md")
                                      .read_text(encoding="utf-8"))
            assert "ww_" in section
        # the two decision seats promise NO tool at all (FORBIDDEN tool policy),
        # so a rendered-block entry there cannot collide with the lint either.
        for sid in ("dec.pm", "dec.research_mgr"):
            assert "ww_" not in _dsp_section(sid)


# =========================================================================== #
# GATE 1b — the durable evidence behind the F-E verdict                         #
# =========================================================================== #
class TestGate1bDurableEvidence:
    """The pm three-runs table, re-derived from the durable event store.

    The store is production state under ``var/`` (git-ignored), so the
    re-derivation is conditional on its presence — and that condition is NAMED
    here rather than hidden: the ledger-internal consistency checks below always
    run, and the full re-derivation runs wherever the durable records live (the
    real machine, which is where this verdict was measured).
    """

    def test_the_ledger_records_exactly_three_runs_with_distinct_identities(self):
        sec = _subsection(_LEDGER_TEXT, "1.2")
        rows = _PM_RUN_ROW.findall(sec)
        assert len(rows) == 3, f"§1.2 must record the three named runs: {rows}"
        assert len({r[0] for r in rows}) == 3
        assert {r[1] for r in rows} == {"ok"}
        assert {r[2] for r in rows} == {"passed"}

    @pytest.mark.skipif(
        not _EVENTS.exists(),
        reason="durable event store absent (var/ is production state, not a "
               "fixture); the ledger-internal §1.2 checks above still run")
    def test_the_three_runs_re_derive_from_the_durable_store(self):
        sec = _subsection(_LEDGER_TEXT, "1.2")
        recorded = {}
        for line in sec.splitlines():
            m = _PM_RUN_ROW.match(line)
            if not m:
                continue
            price = re.search(r"last_price=([0-9.]+)", line)
            length = re.search(r"rendered_length=(\d+)", line)
            assert price and length, line
            recorded[m.group(1)] = (m.group(2), m.group(3), float(price.group(1)),
                                    int(length.group(1)))

        events = [json.loads(ln)["event"]
                  for ln in _EVENTS.read_text(encoding="utf-8").splitlines()
                  if ln.strip()]

        def payload(digest: str) -> dict:
            return json.loads((_PAYLOADS / f"{digest}.json")
                              .read_text(encoding="utf-8"))

        for run_prefix, (status, guard, price, length) in recorded.items():
            runs = [e for e in events if (e.get("run_id") or "").startswith(run_prefix)]
            assert runs, f"{run_prefix}: no durable events"
            pm_nodes = []
            decisions = []
            for e in runs:
                name = e["payload_schema_ref"]["name"]
                if name == "NodeRun":
                    p = payload(e["payload_ref"]["content_digest"])
                    if p.get("worker_id") == "dec.pm":
                        pm_nodes.append(p)
                elif name == "PortfolioDecision":
                    decisions.append(payload(e["payload_ref"]["content_digest"]))
            assert len(pm_nodes) == 1, f"{run_prefix}: {len(pm_nodes)} pm nodes"
            node = pm_nodes[0]
            results = node["data_result_refs"]
            assert len(results) == 1, run_prefix
            result = payload(results[0]["payload_ref"]["content_digest"])
            assert result["status"] == status, run_prefix
            assert result["pit_audit"]["guard_result"] == guard, run_prefix
            assert result["pit_audit"]["future_rows"] == 0, run_prefix
            assert result["data"]["rows"][0]["last_price"] == price, run_prefix
            prompts = [payload(r["payload_ref"]["content_digest"])
                       for r in node["execution_evidence_refs"]
                       if r["schema_ref"]["name"] == "PromptAssemblyRecord"]
            assert len(prompts) == 1, run_prefix
            blocks = prompts[0]["untrusted_blocks"]
            assert len(blocks) == 1, run_prefix
            assert blocks[0]["rendered_length"] == length, run_prefix
            # …and the reading never reaches the prose. This is the whole finding.
            for decision in decisions:
                body = json.dumps(decision, ensure_ascii=False)
                assert str(price) not in body, (
                    f"{run_prefix}: the decision DOES cite the block reading — "
                    "the ledger's 'zero citation' row is wrong")

    @pytest.mark.skipif(
        not _EVENTS.exists(),
        reason="durable event store absent (var/ is production state, not a "
               "fixture)")
    def test_research_mgr_received_no_block_in_the_same_three_runs(self):
        events = [json.loads(ln)["event"]
                  for ln in _EVENTS.read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
        seen = 0
        for e in events:
            if e["payload_schema_ref"]["name"] != "NodeRun":
                continue
            p = json.loads((_PAYLOADS / (e["payload_ref"]["content_digest"] + ".json"))
                           .read_text(encoding="utf-8"))
            if p.get("worker_id") != "dec.research_mgr":
                continue
            seen += 1
            assert p["data_result_refs"] == [], p.get("run_id")
            assert p["tool_call_records"] == [], p.get("run_id")
        assert seen >= 3, (
            "expected at least the three campaign runs' research-mgr nodes in "
            f"the durable store; found {seen}")


# =========================================================================== #
# GATE 2 — chain baseline (three-way: live == L3 ledger == this ledger)         #
# =========================================================================== #
class TestGate2ChainBaseline:
    def test_the_identity_table_is_a_closed_verdict_vocabulary(self):
        bad = {v for *_r, v in _identity_rows()} - _VERDICT_VOCAB
        assert bad == set(), f"unknown §2 verdicts: {sorted(bad)}"
        nums = [n for n, *_ in _identity_rows()]
        assert nums == list(range(1, len(nums) + 1))

    def test_every_recorded_identity_equals_the_live_re_derivation(self, measured):
        wrong = {name: (digest, measured.get(name))
                 for _n, name, digest, _v in _identity_rows()
                 if measured.get(name) != digest}
        assert wrong == {}, (
            "STOP — the sealed chain drifted from the recorded baseline, so this "
            f"whole inventory is stale and may not be used: {wrong}")

    def test_the_chain_equals_the_l3_closing_ledger_byte_for_byte(self, measured):
        """The charter's riskiest assumption made executable, three ways.

        L3 closed on values recorded in its own §12.2 / §2 tables. If the live
        tree, that ledger and this one do not all agree, the blast-radius
        inventory below was priced against a tree that no longer exists.
        """
        l3 = _l3_chain()
        recorded = {name: digest for _n, name, digest, _v in _identity_rows()}
        for node in ("P2-static", "P3-data", "P3-full", "P4", "P5", "P6", "P7",
                     "P8", "P9", "P10"):
            assert l3[node] == measured[node], f"live vs L3 ledger at {node}"
            assert recorded[node] == measured[node], f"live vs this ledger at {node}"
        assert measured["P6"] == measured["P5"], "P6 is the identity node"

    def test_the_post_l3_skill_manifest_addition_is_re_measured_not_trusted(
            self, measured):
        """``skill.text.news@1`` moved during L3 Task 4 (the ww_f10 withdrawal).

        The brief said to re-measure rather than trust the recorded value, so the
        recorded value is asserted against a LIVE catalog build, and the L3
        ledger's own record of it is asserted to be the same string.
        """
        recorded = {name: digest for _n, name, digest, _v in _identity_rows()}
        live = measured["skill.text.news@1"]
        assert recorded["skill.text.news@1"] == live
        assert live in _L3_TEXT, (
            "the L3 ledger records this identity; a mismatch means one of the "
            "two ledgers is stale")


# =========================================================================== #
# GATE 3 — blast radius BY CONSTRUCTION (the probe grep cannot replace)         #
# =========================================================================== #
class TestGate3BlastRadiusByConstruction:
    def test_the_probe_really_changes_the_material_bytes(self):
        """Positive control: without this, every 'unchanged' verdict below could
        be vacuously true because the probe never took effect."""
        sid = "text.news"
        path = _TREE / sid / "SKILL.md"
        real = path.read_bytes()
        with _AppendedSkillBytes(sid):
            patched = path.read_bytes()
            other = (_TREE / "dec.bull" / "SKILL.md").read_bytes()
        assert patched == real + _PROBE_BYTES
        assert path.read_bytes() == real, "the probe must not touch the disk"
        assert other == (_TREE / "dec.bull" / "SKILL.md").read_bytes(), (
            "the probe must patch exactly one path")

    def test_the_probe_document_still_parses_as_skill_v1(self):
        """A mutation the grammar rejects would prove nothing about propagation:
        the catalog would refuse to build for the wrong reason."""
        from guanlan_v2.orchestration.catalog import parse_skill_v1
        for sid in _PROBE_SKILLS:
            raw = (_TREE / sid / "SKILL.md").read_bytes() + _PROBE_BYTES
            parse_skill_v1(raw.decode("utf-8"))

    def test_every_frozen_identity_survives_all_five_probes(self, measured, probes):
        frozen = [name for _n, name, _d, v in _identity_rows() if v == "FROZEN"]
        assert frozen, "the §2 table records no FROZEN identity"
        broken = {(name, sid): (measured[name], probes[sid][name])
                  for name in frozen for sid in _PROBE_SKILLS
                  if probes[sid][name] != measured[name]}
        assert broken == {}, (
            "an identity recorded FROZEN moved when a skill material changed — "
            f"the blast radius is wider than the inventory says: {broken}")

    def test_every_moves_identity_moves_under_every_probe(self, measured, probes):
        moves = [name for _n, name, _d, v in _identity_rows() if v == "MOVES"]
        assert moves, "the §2 table records no MOVES identity"
        stuck = {(name, sid) for name in moves for sid in _PROBE_SKILLS
                 if probes[sid][name] == measured[name]}
        assert stuck == set(), (
            "an identity recorded MOVES did not move — the re-freeze checklist "
            f"would then re-freeze a value nothing changed: {sorted(stuck)}")

    def test_conditional_identities_move_only_with_their_own_skill(
            self, measured, probes):
        """``MOVES-IF-IN-WINDOW`` is the load-bearing verdict for D-A pricing:
        it is what makes 'the marginal cost of the Nth skill is two literals'
        a measurement rather than an estimate."""
        conditional = [name for _n, name, _d, v in _identity_rows()
                       if v == "MOVES-IF-IN-WINDOW"]
        assert conditional, "the §2 table records no conditional identity"
        for name in conditional:
            owner = name.split(":", 1)[1] if name.startswith("worker:") else \
                name[len("skill."):-len("@1")]
            assert owner in _PROBE_SKILLS, (
                f"{name}: the conditional verdict is only falsifiable if its "
                "owning skill is probed")
            assert probes[owner][name] != measured[name], (
                f"{name} did not move under its OWN skill's probe")
            for sid in _PROBE_SKILLS:
                if sid == owner:
                    continue
                assert probes[sid][name] == measured[name], (
                    f"{name} moved under {sid}'s probe — the per-skill "
                    "independence the D-A pricing rests on is false")

    def test_the_charter_estimate_that_preset_records_move_is_refuted(
            self, measured, probes):
        """Charter §3 listed the two deep preset records + the screening-lane
        record in the摘要链. Measured: a skill-material edit moves none of them —
        a preset record is the semantic digest of the plan graph and embeds no
        catalog or worker digest. §3 itself forbids copying its estimate; this is
        the measurement that replaces it."""
        for pid in ("pipeline.luozi_deep_decide", "pipeline.luozi_deep_decide_reduced",
                    "pipeline.screening_lane"):
            for sid in _PROBE_SKILLS:
                assert probes[sid][f"preset:{pid}"] == measured[f"preset:{pid}"]

    def test_the_chain_moves_only_from_p8_downstream(self, measured, probes):
        """The tree file is read by the Phase-8 lane builders and nothing earlier,
        so P2-P7 are structurally out of reach. Asserted, not assumed."""
        for sid in _PROBE_SKILLS:
            for node in ("P2-static", "P3-data", "P3-full", "P4", "P5", "P6", "P7"):
                assert probes[sid][node] == measured[node], (node, sid)
            for node in ("P8", "P9", "P10"):
                assert probes[sid][node] != measured[node], (node, sid)


# =========================================================================== #
# GATE 4 — class (a): digest-value literals, both directions                    #
# =========================================================================== #
def _moving_needles(measured: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    moving = {name: measured[name] for _n, name, _d, v in _identity_rows()
              if v in ("MOVES", "MOVES-IF-IN-WINDOW")}
    full = {d: n for n, d in moving.items()}
    prefix = {d[:8]: n for n, d in moving.items()}
    return full, prefix


class TestGate4ClassALiterals:
    def test_the_form_column_is_a_closed_vocabulary(self):
        bad = {f for *_r, f in _literal_rows()} - _FORM_VOCAB
        assert bad == set(), f"unknown §3 forms: {sorted(bad)}"
        nums = [n for n, *_ in _literal_rows()]
        assert nums == list(range(1, len(nums) + 1))

    def test_every_recorded_identity_is_a_moving_one(self, measured):
        moving = {name for _n, name, _d, v in _identity_rows()
                  if v in ("MOVES", "MOVES-IF-IN-WINDOW")}
        recorded = {ident for _n, _f, _l, ident, _form in _literal_rows()}
        assert recorded <= moving, (
            f"§3 rows naming a non-moving identity: {sorted(recorded - moving)}")

    def test_the_literal_inventory_equals_what_the_tree_carries(self, measured):
        """Both directions, literal by literal. This is what makes the document
        T3's regeneration checklist rather than a snapshot that silently rots."""
        full, prefix = _moving_needles(measured)
        full_hits = _scan(full)
        prefix_hits = _scan(prefix)
        full_sites = {(p, ln) for p, ln, _n in full_hits}
        prefix_only = {h for h in prefix_hits if (h[0], h[1]) not in full_sites}
        measured_rows = {(p, ln, n, "full64") for p, ln, n in full_hits} | \
                        {(p, ln, n, "prefix8") for p, ln, n in prefix_only}
        recorded_rows = {(f, ln, ident, form)
                         for _n, f, ln, ident, form in _literal_rows()}
        assert recorded_rows == measured_rows, (
            "class-(a) inventory drift — only in ledger: "
            f"{sorted(recorded_rows - measured_rows)}; only in tree: "
            f"{sorted(measured_rows - recorded_rows)}")

    def test_no_moving_literal_lives_outside_tests(self, measured):
        """A production module that hard-coded a chain digest would make the
        re-freeze a source change, not a golden change. Measured: none does."""
        full, prefix = _moving_needles(measured)
        outside = {h for h in (_scan(full) | _scan(prefix))
                   if not h[0].startswith("tests/")}
        assert outside == set(), (
            f"moving digest literals outside tests/: {sorted(outside)}")

    def test_the_not_moving_carriers_match_the_recorded_counts(self, measured):
        alias = {"P5/P6": "P5"}
        recorded = _frozen_carrier_rows()
        frozen_names = {name for _n, name, _d, v in _identity_rows()
                        if v == "FROZEN"}
        needles = {}
        for label, count in recorded.items():
            name = alias.get(label, label)
            assert name in frozen_names, (
                f"§3.1 names {label!r}, which §2 does not record as FROZEN")
            needles[measured[name]] = label
        hits = _scan(needles)
        counts: dict[str, int] = {}
        for _p, _l, label in hits:
            counts[label] = counts.get(label, 0) + 1
        assert counts == recorded, (
            "the not-moving carriers T3 must leave untouched changed count: "
            f"{counts} != {recorded}")


# =========================================================================== #
# GATE 5 — class (b): file byte-sha pins a digest grep cannot find              #
# =========================================================================== #
class TestGate5ClassBByteShaPins:
    def test_every_row_names_a_real_file_a_real_anchor_and_a_real_subject(self):
        for num, rel, anchor, subject, _sha in _bytesha_rows():
            path = _REPO_ROOT / rel
            assert path.is_file(), f"§4 row {num}: {rel} is not a file"
            assert anchor in path.read_text(encoding="utf-8"), (
                f"§4 row {num}: anchor {anchor!r} is gone from {rel}")
            sub = _REPO_ROOT / subject
            assert sub.exists(), f"§4 row {num}: subject {subject} missing"

    def test_every_recorded_sha_equals_the_live_subject_bytes(self):
        checked = 0
        for num, _rel, _anchor, subject, sha in _bytesha_rows():
            if sha is None:
                continue
            raw = (_REPO_ROOT / subject).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == sha, (
                f"§4 row {num}: recorded byte-sha of {subject} is stale")
            checked += 1
        assert checked >= 3, (
            "the three golden byte-sha pins are the second-order landing sites "
            "a digest grep structurally cannot find (L3 §12.5); at least those "
            f"must carry a value, got {checked}")

    def test_the_mirror_rows_really_pin_a_tree_equals_mirror_relation(self):
        """The ``—`` sha rows are not unmeasured rows: they assert an equality
        between two live files, which is checked here rather than frozen."""
        from guanlan_v2.orchestration import skilltree
        for num, _rel, _anchor, subject, sha in _bytesha_rows():
            if sha is not None:
                continue
            path = _REPO_ROOT / subject
            if path.is_dir():
                tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
                issues = skilltree.lint_drift(
                    tree, materials_root=skilltree.DEFAULT_MATERIALS_ROOT)
                assert issues == (), f"§4 row {num}: real mirror drifted: {issues}"
                continue
            sid = path.name[: -len(".md")]
            assert path.read_bytes() == (_TREE / sid / "SKILL.md").read_bytes(), (
                f"§4 row {num}: {subject} is not byte-identical to its tree source")

    def test_the_chain_tip_golden_carries_no_byte_sha_pin(self, measured):
        """Recorded as a measurement, not an assumption: nothing downstream of
        P10 exists, so no test pins ``phase10_catalog_manifest_v1.json``'s bytes.
        If someone adds one, this reddens and §4 gains a row."""
        subjects = {s for _n, _r, _a, s, _sha in _bytesha_rows()}
        assert "tests/orchestration/golden/phase10_catalog_manifest_v1.json" \
            not in subjects
        hits = _scan({hashlib.sha256(
            (_GOLDEN / "phase10_catalog_manifest_v1.json").read_bytes()
        ).hexdigest(): "p10-golden-sha"})
        assert hits == set(), f"a byte-sha pin on the chain tip appeared: {hits}"


# =========================================================================== #
# GATE 6 — class (c): positional access (the L3 I-1 lesson made law)            #
# =========================================================================== #
class TestGate6ClassCPositionalAccess:
    def test_the_verdict_column_is_a_closed_vocabulary(self):
        bad = {v for *_r, v in _positional_rows()} - _POSITIONAL_VOCAB
        assert bad == set(), f"unknown §5 verdicts: {sorted(bad)}"
        nums = [n for n, *_ in _positional_rows()]
        assert nums == list(range(1, len(nums) + 1))

    def test_every_row_names_a_real_file_and_a_real_anchor_inside_it(self):
        for num, rel, anchor, _v in _positional_rows():
            path = _REPO_ROOT / rel
            assert path.is_file(), f"§5 row {num}: {rel} is not a file"
            assert anchor in path.read_text(encoding="utf-8"), (
                f"§5 row {num}: anchor {anchor!r} is gone from {rel} — a "
                "positional site was deleted or renamed without re-inventory")

    def test_the_fragile_row_is_the_deliverable3_block_index(self):
        """The one genuinely fragile site, and the hidden cost of admitting the
        two decision seats: their tree files are pinned byte-verbatim against
        fenced blocks in a spec document, addressed BY ORDER."""
        fragile = [(n, f, a) for n, f, a, v in _positional_rows() if v == "fragile"]
        assert len(fragile) == 1, f"expected exactly one fragile row: {fragile}"
        _num, rel, anchor = fragile[0]
        assert "test_lane_batch_decision" in rel
        assert "[idx]" in anchor
        spec = (_REPO_ROOT / "docs" / "superpowers" / "specs"
                / "2026-07-17-dec-research-mgr-pm-skills.md")
        assert spec.is_file(), (
            "the document the fragile index addresses must exist, or T2 cannot "
            "keep the byte-verbatim pin green")
        blocks = spec.read_text(encoding="utf-8").count("```markdown")
        assert blocks == 2, (
            f"the install-block count the index depends on is {blocks}, not 2")

    def test_no_typed_collection_this_change_touches_actually_grows(self):
        """Why nearly every positional row is benign, measured rather than argued:
        Option A edits material TEXT only. Every worker still binds exactly one
        skill, and no capability allowlist changes."""
        from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot
        bound = {}
        for w in phase8_catalog_snapshot().workers:
            if w.catalog_role == "final":
                bound[w.id] = len(w.skills)
        assert set(bound.values()) <= {0, 1}, (
            f"a worker binds more than one skill; every skills[0] row in §5 "
            f"would need re-classification: {bound}")
        for sid in _tree_skill_ids():
            if sid in bound:
                assert bound[sid] == 1, (
                    f"{sid} is a tree skill whose owning worker binds "
                    f"{bound[sid]} skills")


# =========================================================================== #
# GATE 7 — cross-ledger coupling (landing sites in docs/, enforced by tests)    #
# =========================================================================== #
class TestGate7CrossLedgerCoupling:
    def test_the_kind_column_is_a_closed_vocabulary(self):
        bad = {k for *_r, k in _cross_rows()} - _CROSS_VOCAB
        assert bad == set(), f"unknown §6 kinds: {sorted(bad)}"
        nums = [n for n, *_ in _cross_rows()]
        assert nums == list(range(1, len(nums) + 1))

    def test_every_row_names_a_real_file_and_a_real_anchor_inside_it(self):
        for num, rel, anchor, _k in _cross_rows():
            path = _REPO_ROOT / rel
            assert path.is_file(), f"§6 row {num}: {rel} is not a file"
            assert anchor in path.read_text(encoding="utf-8"), (
                f"§6 row {num}: anchor {anchor!r} is gone from {rel}")

    def test_the_anchor_rows_are_digests_that_this_window_moves(self, measured):
        """An ANCHOR row is only a coupling if the anchor is a value the re-freeze
        destroys. Each recorded anchor must be a live moving identity AND must
        currently resolve inside the golden it anchors."""
        moving = {measured[name] for _n, name, _d, v in _identity_rows()
                  if v in ("MOVES", "MOVES-IF-IN-WINDOW")}
        anchors = [a for _n, _f, a, k in _cross_rows() if k == "ANCHOR"]
        assert anchors, "§6 records no ANCHOR row"
        golden = (_GOLDEN / "phase8_catalog_manifest_v1.json").read_text(
            encoding="utf-8")
        for anchor in anchors:
            assert anchor in moving, (
                f"§6 ANCHOR {anchor[:12]}… is not a moving identity, so it is "
                "not a coupling at all")
            assert anchor in golden, (
                f"§6 ANCHOR {anchor[:12]}… no longer resolves in the phase-8 "
                "golden; either the re-freeze already happened or the row is stale")

    def test_the_l3_gate_frozen_expectation_still_carries_the_live_prefixes(
            self, measured):
        """The L3 gate's ``_REFREEZE_CHAIN_PREFIXES`` says, in its own docstring,
        that any movement is now a regression. This charter is the second
        authorization to move the sealed chain, so T3 must rewrite BOTH the three
        prefixes and that wording — consciously, not by quietly editing digits."""
        rows = [(f, a) for _n, f, a, k in _cross_rows()
                if k == "FROZEN-EXPECTATION"]
        assert rows, "§6 records no FROZEN-EXPECTATION row"
        for rel, anchor in rows:
            src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            assert anchor in src
            for node in ("P8", "P9", "P10"):
                assert measured[node][:8] in src, (
                    f"{rel} no longer carries the live {node} prefix")

    def test_the_ledger_equality_row_is_the_l3_chain_table(self, measured):
        rows = [(f, a) for _n, f, a, k in _cross_rows() if k == "LEDGER-EQUALITY"]
        assert rows, "§6 records no LEDGER-EQUALITY row"
        l3 = _l3_chain()
        for node in ("P8", "P9", "P10"):
            assert l3[node] == measured[node], (
                f"the L3 ledger's §2 table and the live tree already disagree at "
                f"{node}; the coupling row is describing a broken state")


# =========================================================================== #
# GATE 8 — the charter's STOP conditions, measured                              #
# =========================================================================== #
class TestGate8StopConditions:
    def test_no_schema_manifest_and_no_registry_digest_moves(self):
        """A schema manifest that moves means a registered model moved — that is
        outside every ruling in this charter and is an unconditional STOP. The
        skill materials are read as bytes by the lane builders and appear in no
        ``ContractModel`` docstring, so nothing should reach the registries."""
        base = _measure_stop_surface()
        assert len(base) >= 18, base
        for sid in _PROBE_SKILLS:
            with _AppendedSkillBytes(sid):
                mutated = _measure_stop_surface()
            moved = {k for k in base if base[k] != mutated.get(k)}
            assert moved == set(), (
                f"STOP — a skill-material edit moved {sorted(moved)}; that is "
                "outside every ruling this charter can make")

    def test_the_ledger_stop_section_claims_zero_triggers(self):
        sec = _section(_LEDGER_TEXT, "7")
        assert "无漂移" in sec and "零触发" in sec
        assert "本任务未触发任何 STOP 条件" in sec


# =========================================================================== #
# GATE 9 — the packets are presented, not ruled                                 #
# =========================================================================== #
class TestGate9PacketsPresentedNotRuled:
    """T1's constitutional constraint: it measures everything and rules nothing.

    A packet section that quietly hardened into a decision would take the choice
    away from the controller, so each one must carry the 待裁 marker and none may
    carry a 裁决 verdict line of the kind §9 of the L3 ledger uses.
    """

    @pytest.mark.parametrize("number,name", [("8", "D-A"), ("9", "D-B"),
                                             ("10", "D-C")])
    def test_each_ruling_packet_is_marked_pending(self, number, name):
        sec = _section(_LEDGER_TEXT, number)
        assert name in sec.splitlines()[1] or name in sec[:200], name
        assert "**待裁**" in sec, f"{name} is not marked 待裁"
        assert "状态:待裁" in sec, f"{name} carries no explicit pending status"

    def test_d_a_prices_every_option_the_charter_left_open(self):
        sec = _section(_LEDGER_TEXT, "8")
        for option in ("2 文件", "3 文件", "4 文件", "5 文件"):
            assert option in sec, f"D-A does not price the {option} window"

    def test_d_a_states_the_same_root_evidence_plainly(self):
        sec = _section(_LEDGER_TEXT, "8")
        assert "CONFIRMED" in sec and "REFUTED" in sec, (
            "D-A must state the pm/research_mgr same-root evidence in the two "
            "directions the measurement actually supports")

    def test_d_b_quotes_what_each_skill_says_about_citing_evidence(self):
        sec = _section(_LEDGER_TEXT, "9")
        for sid in ("text.news", "pv.technical", "dec.pm", "dec.research_mgr"):
            assert f"`{sid}`" in sec, sid
        assert "3/3" in sec, (
            "the three-runs-zero-effect fact is D-B's whole evidentiary basis")

    def test_d_c_presents_two_options_with_their_costs(self):
        sec = _section(_LEDGER_TEXT, "10")
        assert "C-迭代" in sec and "C-记录" in sec
        assert "reduced" in sec


# =========================================================================== #
# GATE 10 — this task moved nothing sealed                                      #
# =========================================================================== #
class TestGate10NothingSealedMoved:
    def test_the_gate_file_contains_no_digest_literal(self):
        """Zero hard-coded digests: every expectation is the ledger's, and the
        class-(a) scan therefore cannot find its own expectations in this file
        (which would make the inventory self-referential and un-falsifiable)."""
        src = Path(__file__).read_text(encoding="utf-8")
        hits = re.findall(r"\b[0-9a-f]{8,}\b", src)
        assert hits == [], f"hard-coded digest-shaped literals: {hits}"

    def test_the_committed_skill_tree_and_mirror_are_untouched_and_clean(self):
        from guanlan_v2.orchestration import skilltree
        tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
        assert len(tree) == 27
        assert skilltree.lint_drift(
            tree, materials_root=skilltree.DEFAULT_MATERIALS_ROOT) == ()
