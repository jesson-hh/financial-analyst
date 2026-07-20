# -*- coding: utf-8 -*-
"""Phase 8 · Task 1 — the single-source skills tree, mirror sync, and drift lint.

Covers (per brief Step 1):
* valid tree load ordering + field derivation;
* rejection routing (BOM / non-UTF-8 / 4th description line / folded scalar /
  missing critical heading / missing SKILL.md) to ``SkillFormatError`` / load errors;
* ``render_trigger_line`` round-trip through the real ``parse_skill_v1``;
* mirror plan create / update / noop + ``apply_mirror`` idempotence;
* every ``DriftIssue`` code (missing_mirror / orphan_mirror / byte_drift /
  grammar_error / manifest_digest_mismatch);
* relocation digest preservation against the recorded Phase 2/5/7 material digests;
* the structural no-skill-write invariant;
* the two hyphenated CLIs are runnable (never imported as a module).
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from guanlan_v2.orchestration import skilltree
from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    DataMode,
    ExecutionKind,
    ExecutionSpec,
    OutputBinding,
    ResolvedTextMaterial,
    SkillBinding,
    SkillFormatError,
    SkillManifest,
    Tier,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
    parse_skill_v1,
)
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef

# --------------------------------------------------------------------------- #
# recorded relocation digests (Task 0 evidence — catalog_material_digest of the
# Phase 2 pilot / Phase 5 lane0 / Phase 7 planner SKILL bytes; identical to the
# ContentRef.content_digest each catalog already pins).                        #
# --------------------------------------------------------------------------- #
RECORDED_SKILL_DIGESTS = {
    "text.sentiment": "ce0c846d230ad9c2bb094489f7250f01cde029d1e27549a5f2bea81f6ddd0593",
    "dec.research_mgr": "8b2a50e19b73c9da5f81aacae8607b0aeca5501a01987dcc0754843b715df315",
    "dec.pm": "efb77d7ece37ca5dcfd1aa387f5e7c69bc215fd10b7553bdd483deb1ab565a7e",
    "market.regime": "dd25de11ccc406d68638cff9c8ea710515a5d6ff702c6747e4d0259511e52415",
    "market.rotation": "1829402ad939e1fcc3e0604ae75c04aa83e3356c67b04f431289fc56e383f466",
    "market.factor_miner": "1eb38398427b7f02564f4231a961711897e73c6c99310920a5d3932019991587",
    "orchestrator.planner": "6c0bf23539d1e906faadd31d07ca9b5e64b32f6044e5fed43743ee6996d9a572",
}

#: the historical (golden-read) source path each relocated tree skill was copied from.
LEGACY_SOURCE_PATHS = {
    "text.sentiment": "config/orchestration/materials/phase2-pilot-v1/skills/sentiment.md",
    "dec.research_mgr": "config/orchestration/materials/phase2-pilot-v1/skills/research_mgr.md",
    "dec.pm": "config/orchestration/materials/phase2-pilot-v1/skills/pm.md",
    "market.regime": "config/orchestration/materials/lane0/regime_skill.md",
    "market.rotation": "config/orchestration/materials/lane0/rotation_skill.md",
    "market.factor_miner": "config/orchestration/materials/lane0/factor_miner_skill.md",
    "orchestrator.planner": "config/orchestration/materials/planner/SKILL.md",
}

#: Tree skills whose file Task 4/10 install WHOLESALE from the 交付物②③ 逐字安装件,
#: superseding the Phase-2 pilot relocation. For these the tree now holds the NEW
#: install bytes (a different digest, verified byte-verbatim in test_lane_batch_text.py),
#: while the maps above still record the pilot-era relocation — which is exactly what the
#: FROZEN Phase-7 pilot catalog still binds (D11: the pilot catalog is never
#: retro-modified). So a case-A ``manifest_digest_mismatch`` on ONLY these ids, against the
#: real Phase-7 catalog, is the sanctioned Phase-8 transitional state — not drift.
#: CONTRACT (binding on the later tasks; inherited from p8-task-4-report interface notes):
#:   * Task 10 ADDS "dec.research_mgr" / "dec.pm" here when it installs 交付物③ into their
#:     tree files (the same wholesale replacement of the other two pilots).
#:   * Task 11 RE-POINTS the real-catalog cross-check test at the Phase-8 catalog (which
#:     binds the NEW install digests) and EMPTIES this set — the exception must not
#:     outlive Phase 8.
SUPERSEDED_IN_PHASE8 = {"text.sentiment"}

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _skill_text(
    *,
    name: str = "demo skill",
    summary: str = "one-line summary of what this skill does",
    perfect=("盘中快讯速读", "全球市场隔夜要闻"),
    not_ideal=("情绪打分", "研报深读"),
    body_extra: str = "",
) -> str:
    """Build a valid skill-v1 document using the module-under-test renderers."""
    p_line = skilltree.render_trigger_line("Perfect for: ", tuple(perfect))
    n_line = skilltree.render_trigger_line("Not ideal for: ", tuple(not_ideal))
    return (
        "---\n"
        f"name: {name}\n"
        "description: |\n"
        f"  {summary}\n"
        f"  {p_line}\n"
        f"  {n_line}\n"
        "---\n\n"
        "## ⚠️ CRITICAL: Data Source Priority\n"
        "- 经 runtime 预取的真实工具产物(`ww_news_live`)是唯一事实源。\n"
        "- 缺源诚实输出空 items + coverage_note。\n"
        + body_extra
    )


def _write_skill(root: Path, skill_id: str, text: str) -> Path:
    d = root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_bytes(text.encode("utf-8"))
    return p


def _make_tree(tmp_path: Path, mapping: dict[str, str]) -> Path:
    root = tmp_path / "skills"
    root.mkdir(parents=True, exist_ok=True)
    for sid, text in mapping.items():
        _write_skill(root, sid, text)
    return root


def _text_material(content_id: str, kind: str, raw: bytes):
    """Resolve one text material with its recomputed content-digest ref."""
    placeholder = "0" * 64
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=content_id, version="1", content_digest=placeholder),
        kind=kind, raw_utf8=raw,
    )
    ref = ContentRef(id=content_id, version="1", content_digest=catalog_material_digest(tmp))
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _minimal_catalog(skill_id: str, raw: bytes, *, source_identity: str):
    """Build a real, sealed single-skill catalog *with its owning final worker*.

    The catalog cross-check joins a tree skill to its catalog entry through the
    OWNING FINAL WORKER (``worker.id == skill_id``, whose ``SkillBinding`` pins the
    skill digest) — never through ``source_identity`` (real P5/P7 catalogs stamp the
    owning worker's LogicalId there, e.g. ``guanlan.datafeed.sentiment``, never
    ``skill.<id>``). ``source_identity`` is therefore a realistic owning-worker id
    here so the fixture can never again fabricate a ``skill.<id>`` convention the
    lint keys off of. The skill id (``skill.<id>``) is just the material ref id, not
    a join key.
    """
    skill_ref, skill_mat = _text_material("skill." + skill_id, "skill", raw)
    prompt_ref, prompt_mat = _text_material(
        "prompt." + skill_id, "prompt", b"you are the owning worker's system prompt\n")
    parsed = parse_skill_v1(raw.decode("utf-8"))
    sm = SkillManifest(
        ref=skill_ref,
        name=parsed.name,
        summary=parsed.summary,
        perfect_for=parsed.perfect_for,
        not_ideal_for=parsed.not_ideal_for,
        critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
        source_identity=source_identity,
    )
    ce = ContentManifestEntry(
        ref=prompt_ref, kind="prompt", name="prompt." + skill_id,
        description="owning worker system prompt", source_identity=source_identity,
    )
    worker = WorkerSpec(
        id=skill_id, catalog_role="final", selection_scope="dynamic_allowed",
        lane="market", persona="demo owning worker", tier=Tier.WRITER,
        execution=ExecutionSpec(
            kind=ExecutionKind.LLM, model_tier="reasoner", thinking_budget=0),
        system_prompt_ref=prompt_ref,
        skills=(SkillBinding(skill_ref=skill_ref),),
        outputs=(OutputBinding(
            name="primary", schema_ref=SchemaRef(name="DemoOut", version="1")),),
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False, decision_authority="none",
    )
    return build_catalog_snapshot(
        catalog_version="skilltree-test-v1",
        content_manifest=(ce,),
        skill_manifest=(sm,),
        capability_manifest=(),
        workers=(worker,),
        resolved_material=(skill_mat, prompt_mat),
    )


def _load_cli(filename: str):
    """Load one of the hyphenated CLIs by file path (never a normal import)."""
    path = _REPO_ROOT / "guanlan_v2" / "orchestration" / "skills" / filename
    spec = importlib.util.spec_from_file_location(f"_skills_cli_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. tree load + ordering                                                      #
# --------------------------------------------------------------------------- #
def test_load_skill_tree_orders_by_skill_id_and_derives_fields(tmp_path):
    root = _make_tree(tmp_path, {
        "text.news": _skill_text(name="news read"),
        "dec.pm": _skill_text(name="pm arbiter"),
        "market.regime": _skill_text(name="regime read"),
    })
    tree = skilltree.load_skill_tree(root)
    assert [s.skill_id for s in tree] == ["dec.pm", "market.regime", "text.news"]
    s0 = tree[0]
    assert s0.source_identity == "skill.dec.pm"
    assert s0.path_label == "dec.pm/SKILL.md"
    assert s0.text.startswith("---\n")


def test_load_skill_tree_ignores_top_level_cli_files(tmp_path):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    (root / "sync-skills.py").write_text("# cli\n", encoding="utf-8")
    (root / "check.py").write_text("# cli\n", encoding="utf-8")
    tree = skilltree.load_skill_tree(root)
    assert [s.skill_id for s in tree] == ["text.news"]


# --------------------------------------------------------------------------- #
# 2. rejection routing                                                         #
# --------------------------------------------------------------------------- #
def test_load_rejects_bom(tmp_path):
    root = tmp_path / "skills"
    d = root / "text.news"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(b"\xef\xbb\xbf" + _skill_text().encode("utf-8"))
    with pytest.raises(SkillFormatError):
        skilltree.load_skill_tree(root)


def test_load_rejects_non_utf8(tmp_path):
    root = tmp_path / "skills"
    d = root / "text.news"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(b"---\nname: x\xff\xfe bad bytes\n")
    with pytest.raises(SkillFormatError):
        skilltree.load_skill_tree(root)


def test_load_rejects_fourth_description_line(tmp_path):
    text = (
        "---\nname: x\ndescription: |\n  a\n  "
        + skilltree.render_trigger_line("Perfect for: ", ("p",))
        + "\n  "
        + skilltree.render_trigger_line("Not ideal for: ", ("n",))
        + "\n  extra fourth line\n---\n\n## ⚠️ CRITICAL: Data Source Priority\n- only block\n"
    )
    root = _make_tree(tmp_path, {"text.news": text})
    with pytest.raises(SkillFormatError):
        skilltree.load_skill_tree(root)


def test_load_rejects_folded_scalar(tmp_path):
    text = _skill_text().replace("description: |", "description: >")
    root = _make_tree(tmp_path, {"text.news": text})
    with pytest.raises(SkillFormatError):
        skilltree.load_skill_tree(root)


def test_load_rejects_missing_critical_heading(tmp_path):
    text = _skill_text().replace("## ⚠️ CRITICAL: Data Source Priority", "## Playbook")
    root = _make_tree(tmp_path, {"text.news": text})
    with pytest.raises(SkillFormatError):
        skilltree.load_skill_tree(root)


def test_load_rejects_directory_without_skill_md(tmp_path):
    root = tmp_path / "skills"
    (root / "text.news").mkdir(parents=True)  # empty dir, no SKILL.md
    with pytest.raises(skilltree.SkillTreeError):
        skilltree.load_skill_tree(root)


def test_load_rejects_bad_directory_name(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "Text.News", _skill_text())  # uppercase not a valid id
    with pytest.raises(skilltree.SkillTreeError):
        skilltree.load_skill_tree(root)


# --------------------------------------------------------------------------- #
# 3. render_trigger_line round-trip                                            #
# --------------------------------------------------------------------------- #
def test_render_trigger_line_roundtrips_through_parser():
    items = ("盘中快讯速读", "全球市场隔夜要闻", "个股公告初筛")
    line = skilltree.render_trigger_line("Perfect for: ", items)
    assert line == 'Perfect for: ["盘中快讯速读","全球市场隔夜要闻","个股公告初筛"]'
    text = _skill_text(perfect=items)
    parsed = parse_skill_v1(text)
    assert parsed.perfect_for == items


def test_render_trigger_line_rejects_empty():
    with pytest.raises(ValueError):
        skilltree.render_trigger_line("Perfect for: ", ())


def test_render_trigger_line_rejects_bad_prefix():
    with pytest.raises(ValueError):
        skilltree.render_trigger_line("Bad prefix: ", ("x",))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 4. mirror plan / apply                                                       #
# --------------------------------------------------------------------------- #
def test_plan_mirror_create_then_apply_then_noop(tmp_path):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    materials = tmp_path / "materials"
    tree = skilltree.load_skill_tree(root)

    plan1 = skilltree.plan_mirror(tree, materials_root=materials)
    assert [a.op for a in plan1] == ["create"]
    assert plan1[0].target_label == "skills/text.news.md"
    assert len(plan1[0].source_digest) == 64

    skilltree.apply_mirror(plan1, tree=tree, materials_root=materials)
    mirror = materials / "skills" / "text.news.md"
    assert mirror.read_bytes() == tree[0].text.encode("utf-8")

    plan2 = skilltree.plan_mirror(tree, materials_root=materials)
    assert [a.op for a in plan2] == ["noop"]
    # apply is idempotent — a second apply changes nothing
    skilltree.apply_mirror(plan2, tree=tree, materials_root=materials)
    assert skilltree.plan_mirror(tree, materials_root=materials)[0].op == "noop"


def test_plan_mirror_update_when_mirror_diverges(tmp_path):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    materials = tmp_path / "materials"
    tree = skilltree.load_skill_tree(root)
    skilltree.apply_mirror(
        skilltree.plan_mirror(tree, materials_root=materials),
        tree=tree, materials_root=materials,
    )
    # hand-edit the mirror
    (materials / "skills" / "text.news.md").write_text("tampered\n", encoding="utf-8")
    plan = skilltree.plan_mirror(tree, materials_root=materials)
    assert plan[0].op == "update"
    skilltree.apply_mirror(plan, tree=tree, materials_root=materials)
    assert skilltree.plan_mirror(tree, materials_root=materials)[0].op == "noop"


# --------------------------------------------------------------------------- #
# 5. drift-lint matrix                                                         #
# --------------------------------------------------------------------------- #
def _seed(tmp_path, mapping):
    root = _make_tree(tmp_path, mapping)
    materials = tmp_path / "materials"
    tree = skilltree.load_skill_tree(root)
    skilltree.apply_mirror(
        skilltree.plan_mirror(tree, materials_root=materials),
        tree=tree, materials_root=materials,
    )
    return root, materials, tree


def test_lint_clean_after_sync(tmp_path):
    _, materials, tree = _seed(tmp_path, {"text.news": _skill_text()})
    assert skilltree.lint_drift(tree, materials_root=materials) == ()


def test_lint_missing_mirror(tmp_path):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    materials = tmp_path / "materials"
    tree = skilltree.load_skill_tree(root)
    issues = skilltree.lint_drift(tree, materials_root=materials)
    assert [i.code for i in issues] == ["missing_mirror"]


def test_lint_orphan_mirror(tmp_path):
    _, materials, tree = _seed(tmp_path, {"text.news": _skill_text()})
    (materials / "skills" / "ghost.skill.md").write_text(_skill_text(), encoding="utf-8")
    codes = {i.code for i in skilltree.lint_drift(tree, materials_root=materials)}
    assert "orphan_mirror" in codes


def test_lint_byte_drift(tmp_path):
    _, materials, tree = _seed(tmp_path, {"text.news": _skill_text()})
    # a valid-but-different mirror (extra playbook prose survives grammar)
    diverged = _skill_text(body_extra="\n## Extra\nmore prose\n")
    (materials / "skills" / "text.news.md").write_text(diverged, encoding="utf-8")
    codes = {i.code for i in skilltree.lint_drift(tree, materials_root=materials)}
    assert "byte_drift" in codes


def test_lint_grammar_error_in_mirror(tmp_path):
    _, materials, tree = _seed(tmp_path, {"text.news": _skill_text()})
    (materials / "skills" / "text.news.md").write_text("not a skill\n", encoding="utf-8")
    codes = {i.code for i in skilltree.lint_drift(tree, materials_root=materials)}
    assert "grammar_error" in codes


def test_lint_manifest_digest_mismatch(tmp_path):
    # catalog built from ORIGINAL bytes; tree edited to different (valid) bytes.
    # The owning final worker (id == "text.sentiment") still binds the original
    # digest, so the edited tree file diverges from what the catalog pins.
    original = _skill_text(name="A-share sentiment read")
    catalog = _minimal_catalog(
        "text.sentiment", original.encode("utf-8"),
        source_identity="guanlan.datafeed.sentiment",
    )
    diverged = _skill_text(name="A-share sentiment read", body_extra="\n## Note\nchanged\n")
    _, materials, tree = _seed(tmp_path, {"text.sentiment": diverged})
    issues = skilltree.lint_drift(tree, materials_root=materials, catalog=catalog)
    codes = {i.code for i in issues}
    assert "manifest_digest_mismatch" in codes


def test_lint_clean_with_matching_catalog(tmp_path):
    text = _skill_text(name="A-share sentiment read")
    catalog = _minimal_catalog(
        "text.sentiment", text.encode("utf-8"),
        source_identity="guanlan.datafeed.sentiment",
    )
    _, materials, tree = _seed(tmp_path, {"text.sentiment": text})
    assert skilltree.lint_drift(tree, materials_root=materials, catalog=catalog) == ()


def test_lint_uncataloged_new_tree_skill_is_legitimate_during_migration(tmp_path):
    """A tree skill with no owning final worker (a new Phase-8 skill that has not
    yet been registered into a catalog batch) must NOT be flagged — cross-check A
    joins tree→catalog through the owning final worker, so an un-owned tree skill
    is a legitimate migration state, not drift."""
    # catalog owns ONLY text.sentiment; the tree additionally carries a brand-new,
    # not-yet-cataloged skill that no worker binds.
    sentiment = _skill_text(name="A-share sentiment read")
    catalog = _minimal_catalog(
        "text.sentiment", sentiment.encode("utf-8"),
        source_identity="guanlan.datafeed.sentiment",
    )
    _, materials, tree = _seed(tmp_path, {
        "text.sentiment": sentiment,
        "text.newsflow": _skill_text(name="brand-new phase-8 skill"),
    })
    assert skilltree.lint_drift(tree, materials_root=materials, catalog=catalog) == ()


# --------------------------------------------------------------------------- #
# 6. relocation digest preservation (real tree)                               #
# --------------------------------------------------------------------------- #
def test_real_tree_relocations_are_digest_preserving():
    tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
    by_id = {s.skill_id: s for s in tree}
    for sid, want in RECORDED_SKILL_DIGESTS.items():
        assert sid in by_id, f"relocated skill {sid} missing from tree"
        got = skilltree._skill_digest(by_id[sid])
        if sid in SUPERSEDED_IN_PHASE8:
            # Task 4/10 installed the 交付物②③ 逐字安装件 wholesale — the tree digest is
            # the NEW install digest (byte-verbatim-checked in test_lane_batch_text.py),
            # NOT the pilot relocation digest `want` (which the frozen Phase-7 catalog
            # still binds). Assert the supersession actually happened.
            assert got != want, f"{sid}: expected a superseded (changed) tree digest"
            continue
        assert got == want, f"{sid}: tree digest {got} != recorded {want}"


def test_real_tree_bytes_equal_legacy_golden_source_bytes():
    tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
    by_id = {s.skill_id: s for s in tree}
    for sid, rel in LEGACY_SOURCE_PATHS.items():
        if sid in SUPERSEDED_IN_PHASE8:
            continue  # tree superseded wholesale by Task 4/10; no longer byte-identical
        legacy = (_REPO_ROOT / rel).read_bytes()
        assert by_id[sid].text.encode("utf-8") == legacy, f"{sid} bytes diverge from {rel}"


def test_real_tree_mirror_is_clean():
    """The seeded mirror must be byte-identical to the tree (no drift at HEAD)."""
    tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
    issues = skilltree.lint_drift(tree, materials_root=skilltree.DEFAULT_MATERIALS_ROOT)
    assert issues == (), f"real mirror drifted: {issues}"


# --------------------------------------------------------------------------- #
# 6b. catalog cross-check against the REAL Phase-7 catalog (join by digest)     #
#                                                                              #
# Guards against the join bug the reviewer caught: the previous cross-check    #
# keyed off ``source_identity.startswith("skill.")``, but real P5/P7 catalogs  #
# stamp the OWNING worker's LogicalId (phase5.task8.lane0 / guanlan.decision.pm#
# / orchestrator.planner / …) there — so both loops were dead code against a   #
# production catalog. These tests run the lint against the real snapshot.      #
# --------------------------------------------------------------------------- #
def _phase7_catalog():
    from guanlan_v2.orchestration import phase7_registry as p7
    return p7.phase7_catalog_snapshot()


def _copy_real_tree(tmp_path: Path) -> Path:
    dst = tmp_path / "skills"
    shutil.copytree(skilltree.DEFAULT_TREE_ROOT, dst)
    return dst


def test_lint_real_tree_clean_against_real_phase7_catalog():
    """Real tree + real mirror + the REAL Phase-7 catalog cross-check.

    Every relocated tree skill's digest equals the digest its owning final worker
    binds; ``compat.skill.mirror`` (bound only by ``compatibility`` workers) and the
    planner skill (materials-without-worker) are correctly NOT flagged. The ONLY
    permitted issues are case-A ``manifest_digest_mismatch`` on the SUPERSEDED_IN_PHASE8
    ids: Task 4/10 install the 交付物②③ tree files wholesale while the FROZEN Phase-7
    pilot catalog still binds the pilot digest (D11), so exactly those ids diverge — the
    sanctioned Phase-8 transitional state. Task 11 re-points this test at the Phase-8
    catalog and empties the set (see SUPERSEDED_IN_PHASE8)."""
    tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
    issues = skilltree.lint_drift(
        tree, materials_root=skilltree.DEFAULT_MATERIALS_ROOT, catalog=_phase7_catalog())
    # every issue must be a sanctioned supersession mismatch — nothing else may drift
    for i in issues:
        assert i.code == "manifest_digest_mismatch" and i.skill_id in SUPERSEDED_IN_PHASE8, (
            f"unexpected real-catalog drift: {i}")
    # and every superseded id must actually produce that mismatch (proof the wholesale
    # install happened and the cross-check still detects it)
    assert {i.skill_id for i in issues} == SUPERSEDED_IN_PHASE8, issues


def test_lint_real_catalog_flags_tampered_relocated_skill(tmp_path):
    """Editing a relocated tree skill (owned by a FINAL worker) so its digest no
    longer matches the catalog binding fires ``manifest_digest_mismatch`` against
    the REAL catalog — the exact divergence the dead-code join never detected."""
    tree_root = _copy_real_tree(tmp_path)
    tampered = tree_root / "market.regime" / "SKILL.md"
    tampered.write_bytes(
        tampered.read_bytes() + b"\n## Extra\nappended prose changes the digest\n")
    tree = skilltree.load_skill_tree(tree_root)
    materials = tmp_path / "materials"
    skilltree.apply_mirror(
        skilltree.plan_mirror(tree, materials_root=materials),
        tree=tree, materials_root=materials,
    )
    issues = skilltree.lint_drift(tree, materials_root=materials, catalog=_phase7_catalog())
    # exclude the sanctioned SUPERSEDED_IN_PHASE8 supersession mismatch(es) — the tampered
    # market.regime is the divergence under test here.
    hits = [
        i for i in issues
        if i.code == "manifest_digest_mismatch" and i.skill_id not in SUPERSEDED_IN_PHASE8
    ]
    assert [i.skill_id for i in hits] == ["market.regime"], issues


def test_lint_real_catalog_flags_bound_skill_missing_from_tree(tmp_path):
    """Removing a tree skill that a FINAL worker still binds fires
    ``bound_skill_missing_from_tree`` (cross-check B, the inverted loop). The lint
    scopes this to FINAL workers, so a deleted compat-only skill would not."""
    tree_root = _copy_real_tree(tmp_path)
    shutil.rmtree(tree_root / "market.regime")
    tree = skilltree.load_skill_tree(tree_root)
    materials = tmp_path / "materials"
    skilltree.apply_mirror(
        skilltree.plan_mirror(tree, materials_root=materials),
        tree=tree, materials_root=materials,
    )
    issues = skilltree.lint_drift(tree, materials_root=materials, catalog=_phase7_catalog())
    hits = [i for i in issues if i.code == "bound_skill_missing_from_tree"]
    assert [i.skill_id for i in hits] == ["market.regime"], issues


# --------------------------------------------------------------------------- #
# 7. structural no-skill-write invariant                                       #
# --------------------------------------------------------------------------- #
def test_no_catalog_capability_can_write_the_skill_tree():
    from guanlan_v2.orchestration import phase7_registry as p7
    snap = p7.phase7_catalog_snapshot()
    # The capability *descriptor* (which carries ``operation``) is deliberately NOT
    # carried by a sealed ``WorkerCatalogSnapshot`` — ``CapabilityManifestEntry``
    # exposes only ``ref`` (id/version/digest), ``capability_kind`` and
    # ``transport``. So the descriptor's ``operation`` surface is unreachable from a
    # sealed snapshot; ``ref.id`` is the reachable proxy for "no capability names a
    # skill write". We sweep every string-valued field the manifest entry does
    # expose so the invariant can never be satisfied by a differently-named field.
    for entry in snap.capability_manifest:
        surface = (
            entry.ref.id,
            entry.ref.version,
            str(entry.capability_kind),
            str(entry.transport),
        )
        for value in surface:
            assert "skill" not in value.lower(), (
                f"capability {entry.ref.id} field {value!r} names 'skill' — "
                "no capability may write the tree"
            )


def test_apply_mirror_never_writes_under_the_tree_root(tmp_path):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    materials = tmp_path / "materials"
    tree = skilltree.load_skill_tree(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    skilltree.apply_mirror(
        skilltree.plan_mirror(tree, materials_root=materials),
        tree=tree, materials_root=materials,
    )
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after, "apply_mirror mutated the human-edited tree"


# --------------------------------------------------------------------------- #
# 8. the two hyphenated CLIs are runnable (never imported as a module)         #
# --------------------------------------------------------------------------- #
def test_sync_and_check_clis_run_against_a_tmp_tree(tmp_path, capsys):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    materials = tmp_path / "materials"

    sync = _load_cli("sync-skills.py")
    rc = sync.main(["--root", str(root), "--materials-root", str(materials)])
    assert rc == 0
    assert (materials / "skills" / "text.news.md").exists()

    check = _load_cli("check.py")
    rc = check.main(["--root", str(root), "--materials-root", str(materials)])
    assert rc == 0

    # drift → nonzero
    (materials / "skills" / "text.news.md").write_text("tampered\n", encoding="utf-8")
    assert check.main(["--root", str(root), "--materials-root", str(materials)]) != 0


def test_sync_cli_dry_run_writes_nothing(tmp_path):
    root = _make_tree(tmp_path, {"text.news": _skill_text()})
    materials = tmp_path / "materials"
    sync = _load_cli("sync-skills.py")
    rc = sync.main(["--root", str(root), "--materials-root", str(materials), "--dry-run"])
    assert rc == 0
    assert not (materials / "skills").exists()
