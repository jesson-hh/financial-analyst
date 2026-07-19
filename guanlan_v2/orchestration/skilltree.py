# -*- coding: utf-8 -*-
"""Phase 8 · Task 1 — the single-source skills tree, its mirror, and the drift lint.

Doctrine
--------
* **The tree is the sole human-edited home.** Every Phase-8 skill-v1 playbook lives
  at ``guanlan_v2/orchestration/skills/<worker_id>/SKILL.md``. The *worker id* (dotted,
  e.g. ``text.sentiment`` / ``market.regime`` / ``market.factor_miner``) is the
  authoritative directory name and identity; the frontmatter ``name`` is a display-only
  label (R12). A skill file is a strict skill-v1 document — every load re-validates it
  through the Phase 1 :func:`~guanlan_v2.orchestration.catalog.parse_skill_v1`.

* **The mirror is derived, never authored.** :func:`plan_mirror` / :func:`apply_mirror`
  copy each tree file verbatim to ``config/orchestration/materials/skills/<skill_id>.md``.
  That mirror is a build product: :func:`lint_drift` (and ``check.py``) fail on any hand
  edit of it.

* **No writer capability.** Nothing in the orchestration catalog, no handler and no
  worker-facing API can write under ``guanlan_v2/orchestration/skills/``. Skill changes
  are proposals through the human git-review boundary. The only writer here is
  :func:`apply_mirror`, which writes **only** the mirror (under ``materials_root``) and
  never the tree — a build/CI step, not a runtime capability. A structural test asserts no
  catalog capability whose ``operation``/id names ``skill``.

* **Relocation is digest-preserving.** The pilot (Phase 2), Lane-0 (Phase 5) and planner
  (Phase 7) SKILL sources are relocated *into* the tree byte-for-byte. Their historical
  read-paths — which the P4/P5/P7 catalog builders still read
  (``config/orchestration/materials/phase2-pilot-v1/skills/*.md``,
  ``…/lane0/*_skill.md``, ``…/planner/SKILL.md``) — are left byte-identical, so every
  pinned ``ContentRef.content_digest`` and every P4/P5/P7 catalog golden is unchanged.
  The tree file's :func:`~guanlan_v2.orchestration.catalog.catalog_material_digest`
  (material_kind ``skill``) therefore equals the digest each catalog already pins.

Every run records skill digests via the Phase 1 ``Provenance.skill_refs`` mechanism (no
new mechanism is introduced here).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, NamedTuple

from guanlan_v2.orchestration.catalog import (
    ResolvedTextMaterial,
    SkillFormatError,
    WorkerCatalogSnapshot,
    catalog_material_digest,
    parse_skill_v1,
)
from guanlan_v2.orchestration.digest import DigestHex, canonical_json
from guanlan_v2.orchestration.refs import LOGICAL_ID_PATTERN, ContentRef

__all__ = [
    "SkillTreeError",
    "SkillSourceFile",
    "MirrorAction",
    "DriftIssue",
    "TriggerPrefix",
    "render_trigger_line",
    "load_skill_tree",
    "plan_mirror",
    "apply_mirror",
    "lint_drift",
    "DEFAULT_TREE_ROOT",
    "DEFAULT_MATERIALS_ROOT",
    "MIRROR_SUBDIR",
]

_SKILL_FILENAME = "SKILL.md"
_UTF8_BOM = b"\xef\xbb\xbf"
_PLACEHOLDER_DIGEST = "0" * 64
_LOGICAL_ID_RE = re.compile(LOGICAL_ID_PATTERN)

#: repo layout roots (paths never enter a contract object — they stay local).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TREE_ROOT = _REPO_ROOT / "guanlan_v2" / "orchestration" / "skills"
DEFAULT_MATERIALS_ROOT = _REPO_ROOT / "config" / "orchestration" / "materials"
#: the single mirror sub-directory under ``materials_root`` (R: single mirror root).
MIRROR_SUBDIR = "skills"

_PERFECT_PREFIX = "Perfect for: "
_NOT_IDEAL_PREFIX = "Not ideal for: "
TriggerPrefix = Literal["Perfect for: ", "Not ideal for: "]

DriftCode = Literal[
    "missing_mirror", "orphan_mirror", "byte_drift", "grammar_error",
    "manifest_digest_mismatch", "bound_skill_missing_from_tree",
]


class SkillTreeError(Exception):
    """A structural skill-tree invariant was violated (not a grammar error)."""


class SkillSourceFile(NamedTuple):
    """One human-edited SKILL.md in the tree (the single source of truth).

    ``skill_id`` is the owning worker id (the directory name);
    ``source_identity`` is the provenance ``LogicalId`` ``"skill." + skill_id``;
    ``text`` is the exact decoded UTF-8 body (no BOM, LF-preserving round-trip).
    """

    skill_id: str
    source_identity: str
    path_label: str
    text: str


class MirrorAction(NamedTuple):
    """A planned mirror write for one skill (``noop`` ⇒ already byte-identical)."""

    skill_id: str
    target_label: str
    op: Literal["create", "update", "noop"]
    source_digest: DigestHex


class DriftIssue(NamedTuple):
    """One drift finding; a non-empty tuple ⇒ ``check.py`` exits nonzero."""

    code: DriftCode
    skill_id: str
    detail: str


# --------------------------------------------------------------------------- #
# trigger-line rendering                                                       #
# --------------------------------------------------------------------------- #
def render_trigger_line(prefix: TriggerPrefix, items: tuple[str, ...]) -> str:
    """Render a canonical trigger line so authors never hand-write trigger JSON.

    Uses the Phase 1 ``canonical_json`` (Task-1 canonical form) so the result
    round-trips through :func:`parse_skill_v1` byte-for-byte by construction.
    """
    if prefix not in (_PERFECT_PREFIX, _NOT_IDEAL_PREFIX):
        raise ValueError(
            f"trigger prefix must be {_PERFECT_PREFIX!r} or {_NOT_IDEAL_PREFIX!r}"
        )
    items = tuple(items)
    if not items:
        raise ValueError("trigger items must be a non-empty tuple")
    if any((not isinstance(x, str)) or (not x.strip()) for x in items):
        raise ValueError("trigger items must be non-empty strings")
    if len(set(items)) != len(items):
        raise ValueError("trigger items must be duplicate-free")
    return prefix + canonical_json(list(items))


# --------------------------------------------------------------------------- #
# tree load + validate                                                         #
# --------------------------------------------------------------------------- #
def _decode_skill_bytes(skill_id: str, raw: bytes) -> str:
    if raw[:3] == _UTF8_BOM:
        raise SkillFormatError(f"skill {skill_id!r}: SKILL.md must not carry a UTF-8 BOM")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillFormatError(
            f"skill {skill_id!r}: SKILL.md must be strict UTF-8"
        ) from exc


def load_skill_tree(root: Path) -> tuple[SkillSourceFile, ...]:
    """Load + validate the whole tree, deterministically ordered by ``skill_id``.

    Rejects: a UTF-8 BOM, non-UTF-8 bytes, a duplicate skill id, any file failing
    :func:`parse_skill_v1`, an invalid worker-id directory name, and any skill
    directory without exactly one ``SKILL.md``. Top-level files (the two CLIs) and
    dunder/hidden directories are ignored.
    """
    root = Path(root)
    if not root.is_dir():
        raise SkillTreeError(f"skill tree root not found: {root}")

    out: list[SkillSourceFile] = []
    seen: set[str] = set()
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue  # the hyphenated CLIs / stray top-level files
        if child.name.startswith((".", "_")):
            continue  # __pycache__ and hidden dirs are not skills
        skill_id = child.name
        if not _LOGICAL_ID_RE.match(skill_id):
            raise SkillTreeError(
                f"skill directory name {skill_id!r} is not a valid worker/skill id"
            )
        md_files = [p for p in child.iterdir() if p.name == _SKILL_FILENAME and p.is_file()]
        if len(md_files) != 1:
            raise SkillTreeError(
                f"skill directory {skill_id!r} must contain exactly one {_SKILL_FILENAME}"
            )
        if skill_id in seen:
            raise SkillTreeError(f"duplicate skill id {skill_id!r}")
        seen.add(skill_id)

        raw = md_files[0].read_bytes()
        text = _decode_skill_bytes(skill_id, raw)
        parse_skill_v1(text)  # raises SkillFormatError on any grammar deviation
        out.append(SkillSourceFile(
            skill_id=skill_id,
            source_identity="skill." + skill_id,
            path_label=f"{skill_id}/{_SKILL_FILENAME}",
            text=text,
        ))
    return tuple(out)


# --------------------------------------------------------------------------- #
# mirror plan / apply                                                          #
# --------------------------------------------------------------------------- #
def _mirror_dir(materials_root: Path) -> Path:
    return Path(materials_root) / MIRROR_SUBDIR


def _mirror_path(materials_root: Path, skill_id: str) -> Path:
    return _mirror_dir(materials_root) / f"{skill_id}.md"


def _skill_bytes(skill: SkillSourceFile) -> bytes:
    """The exact mirror bytes for a tree skill (loss-free UTF-8 round-trip)."""
    return skill.text.encode("utf-8")


def _skill_digest(skill: SkillSourceFile) -> DigestHex:
    """``catalog_material_digest`` (material_kind ``skill``) of the tree bytes.

    Equal to the ``ContentRef.content_digest`` each catalog pins for this skill.
    """
    mat = ResolvedTextMaterial(
        ref=ContentRef(id="skill." + skill.skill_id, version="1",
                       content_digest=_PLACEHOLDER_DIGEST),
        kind="skill",
        raw_utf8=_skill_bytes(skill),
    )
    return catalog_material_digest(mat)


def plan_mirror(
    tree: tuple[SkillSourceFile, ...], *, materials_root: Path
) -> tuple[MirrorAction, ...]:
    """Plan the verbatim mirror writes for ``tree`` (idempotent; ``noop`` when clean)."""
    actions: list[MirrorAction] = []
    for skill in tree:
        target = _mirror_path(materials_root, skill.skill_id)
        src = _skill_bytes(skill)
        if not target.exists():
            op: Literal["create", "update", "noop"] = "create"
        elif target.read_bytes() != src:
            op = "update"
        else:
            op = "noop"
        actions.append(MirrorAction(
            skill_id=skill.skill_id,
            target_label=f"{MIRROR_SUBDIR}/{skill.skill_id}.md",
            op=op,
            source_digest=_skill_digest(skill),
        ))
    return tuple(actions)


def apply_mirror(
    actions: tuple[MirrorAction, ...],
    *,
    tree: tuple[SkillSourceFile, ...],
    materials_root: Path,
) -> None:
    """Execute a mirror plan. Writes ONLY under ``materials_root`` — never the tree."""
    by_id = {s.skill_id: s for s in tree}
    mdir = _mirror_dir(materials_root)
    for act in actions:
        if act.op == "noop":
            continue
        skill = by_id.get(act.skill_id)
        if skill is None:
            raise SkillTreeError(
                f"mirror action for {act.skill_id!r} has no matching tree source"
            )
        mdir.mkdir(parents=True, exist_ok=True)
        _mirror_path(materials_root, act.skill_id).write_bytes(_skill_bytes(skill))


# --------------------------------------------------------------------------- #
# drift lint                                                                   #
# --------------------------------------------------------------------------- #
def lint_drift(
    tree: tuple[SkillSourceFile, ...],
    *,
    materials_root: Path,
    catalog: WorkerCatalogSnapshot | None = None,
) -> tuple[DriftIssue, ...]:
    """Return every drift finding (empty ⇒ the mirror is a faithful build product).

    Checks: mirror presence (``missing_mirror``); no un-sourced mirror
    (``orphan_mirror``); byte identity (``byte_drift``); the mirror re-parses as
    skill-v1 (``grammar_error``); and, when a ``catalog`` is supplied, the catalog
    cross-check below.

    **Catalog cross-check (join by content_digest through the owning final
    worker).** A tree ``skill_id`` is the owning WORKER id, and a real P5/P7 catalog
    binds that skill on the matching ``final`` :class:`WorkerSpec` (``worker.id ==
    skill_id``) whose ``SkillBinding.skill_ref.content_digest`` equals the tree
    file's :func:`catalog_material_digest`. The join key is therefore the
    ``content_digest`` reached via the owning worker — never ``source_identity``
    (real catalogs stamp the owning worker's LogicalId there, e.g.
    ``phase5.task8.lane0`` / ``orchestrator.planner``, never ``skill.<id>``):

    * **A — tree→catalog** (``manifest_digest_mismatch``): a tree skill owned by a
      ``final`` worker must hash to exactly the digest that worker binds. A tree
      skill with no owning final worker — the planner (materials-without-worker), or
      a brand-new Phase-8 skill that has not yet been registered into a catalog
      batch — is a legitimate migration state and is NOT flagged.
    * **B — catalog→tree** (``bound_skill_missing_from_tree``): every skill a
      ``final`` worker binds must be present in the tree, unless its owning tree file
      is present but diverged (that is case A's finding, not re-reported here).
      Scoped to ``final`` workers — ``compat.skill.mirror`` (bound only by
      ``compatibility`` workers) legitimately lives outside the tree.
    """
    issues: list[DriftIssue] = []
    tree_ids = {s.skill_id for s in tree}
    mdir = _mirror_dir(materials_root)

    # -- per-tree-skill: presence, grammar, byte identity ------------------ #
    for skill in tree:
        target = _mirror_path(materials_root, skill.skill_id)
        if not target.exists():
            issues.append(DriftIssue("missing_mirror", skill.skill_id,
                                     f"no mirror at {MIRROR_SUBDIR}/{skill.skill_id}.md"))
            continue
        mirror_raw = target.read_bytes()
        try:
            parse_skill_v1(_decode_skill_bytes(skill.skill_id, mirror_raw))
        except SkillFormatError as exc:
            issues.append(DriftIssue("grammar_error", skill.skill_id, str(exc)))
        if mirror_raw != _skill_bytes(skill):
            issues.append(DriftIssue("byte_drift", skill.skill_id,
                                     "mirror bytes differ from the tree source"))

    # -- orphan mirrors (a mirror .md with no tree source) ----------------- #
    if mdir.is_dir():
        for p in sorted(mdir.iterdir(), key=lambda p: p.name):
            if p.is_file() and p.suffix == ".md":
                sid = p.name[: -len(".md")]
                if sid not in tree_ids:
                    issues.append(DriftIssue("orphan_mirror", sid,
                                             f"mirror {MIRROR_SUBDIR}/{p.name} has no tree source"))

    # -- catalog cross-check (join by content_digest, see docstring) -------- #
    if catalog is not None:
        digest_by_id = {s.skill_id: _skill_digest(s) for s in tree}
        tree_digests = set(digest_by_id.values())

        # the digest(s) each FINAL worker binds, keyed by owning worker id (which
        # equals the owning tree skill id). Attributes always exist on WorkerSpec —
        # direct access, never getattr defaults that would silently mask a join bug.
        final_bound_by_worker: dict[str, set[DigestHex]] = {}
        for w in catalog.workers:
            if w.catalog_role == "final":
                for sb in w.skills:
                    final_bound_by_worker.setdefault(w.id, set()).add(
                        sb.skill_ref.content_digest)

        # -- A: a tree skill owned by a final worker must match its bound digest.
        for skill in tree:
            bound = final_bound_by_worker.get(skill.skill_id)
            if bound is not None and digest_by_id[skill.skill_id] not in bound:
                issues.append(DriftIssue(
                    "manifest_digest_mismatch", skill.skill_id,
                    f"tree digest {digest_by_id[skill.skill_id]} != the digest final "
                    f"worker {skill.skill_id!r} binds ({', '.join(sorted(bound))})"))

        # -- B: every final-worker-bound skill must be present in the tree, unless
        #       its owning tree file is present-but-diverged (case A's finding).
        for worker_id, bound in final_bound_by_worker.items():
            if worker_id in digest_by_id:
                continue  # owning tree file exists → integrity is case A's job
            for d in sorted(bound):
                if d not in tree_digests:
                    issues.append(DriftIssue(
                        "bound_skill_missing_from_tree", worker_id,
                        f"final worker {worker_id!r} binds skill digest {d} that is "
                        "absent from the tree"))

    return tuple(issues)
