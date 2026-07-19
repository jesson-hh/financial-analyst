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
from pathlib import Path

import pytest

from guanlan_v2.orchestration import skilltree
from guanlan_v2.orchestration.catalog import (
    SkillFormatError,
    SkillManifest,
    ResolvedTextMaterial,
    build_catalog_snapshot,
    catalog_material_digest,
    parse_skill_v1,
)
from guanlan_v2.orchestration.refs import ContentRef

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


def _minimal_catalog(skill_id: str, raw: bytes, *, source_identity: str):
    """Build a real, sealed single-skill WorkerCatalogSnapshot (no workers)."""
    placeholder = "0" * 64
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id="skill." + skill_id, version="1", content_digest=placeholder),
        kind="skill",
        raw_utf8=raw,
    )
    dig = catalog_material_digest(tmp)
    ref = ContentRef(id="skill." + skill_id, version="1", content_digest=dig)
    mat = ResolvedTextMaterial(ref=ref, kind="skill", raw_utf8=raw)
    parsed = parse_skill_v1(raw.decode("utf-8"))
    sm = SkillManifest(
        ref=ref,
        name=parsed.name,
        summary=parsed.summary,
        perfect_for=parsed.perfect_for,
        not_ideal_for=parsed.not_ideal_for,
        critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
        source_identity=source_identity,
    )
    return build_catalog_snapshot(
        catalog_version="skilltree-test-v1",
        content_manifest=(),
        skill_manifest=(sm,),
        capability_manifest=(),
        workers=(),
        resolved_material=(mat,),
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
    # catalog built from ORIGINAL bytes; tree edited to different (valid) bytes
    original = _skill_text(name="A-share sentiment read")
    catalog = _minimal_catalog(
        "text.sentiment", original.encode("utf-8"), source_identity="skill.text.sentiment"
    )
    diverged = _skill_text(name="A-share sentiment read", body_extra="\n## Note\nchanged\n")
    _, materials, tree = _seed(tmp_path, {"text.sentiment": diverged})
    issues = skilltree.lint_drift(tree, materials_root=materials, catalog=catalog)
    codes = {i.code for i in issues}
    assert "manifest_digest_mismatch" in codes


def test_lint_clean_with_matching_catalog(tmp_path):
    text = _skill_text(name="A-share sentiment read")
    catalog = _minimal_catalog(
        "text.sentiment", text.encode("utf-8"), source_identity="skill.text.sentiment"
    )
    _, materials, tree = _seed(tmp_path, {"text.sentiment": text})
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
        assert got == want, f"{sid}: tree digest {got} != recorded {want}"


def test_real_tree_bytes_equal_legacy_golden_source_bytes():
    tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
    by_id = {s.skill_id: s for s in tree}
    for sid, rel in LEGACY_SOURCE_PATHS.items():
        legacy = (_REPO_ROOT / rel).read_bytes()
        assert by_id[sid].text.encode("utf-8") == legacy, f"{sid} bytes diverge from {rel}"


def test_real_tree_mirror_is_clean():
    """The seeded mirror must be byte-identical to the tree (no drift at HEAD)."""
    tree = skilltree.load_skill_tree(skilltree.DEFAULT_TREE_ROOT)
    issues = skilltree.lint_drift(tree, materials_root=skilltree.DEFAULT_MATERIALS_ROOT)
    assert issues == (), f"real mirror drifted: {issues}"


# --------------------------------------------------------------------------- #
# 7. structural no-skill-write invariant                                       #
# --------------------------------------------------------------------------- #
def test_no_catalog_capability_can_write_the_skill_tree():
    from guanlan_v2.orchestration import phase7_registry as p7
    snap = p7.phase7_catalog_snapshot()
    for entry in snap.capability_manifest:
        assert "skill" not in entry.ref.id.lower(), (
            f"capability {entry.ref.id} names 'skill' — no capability may write the tree"
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
