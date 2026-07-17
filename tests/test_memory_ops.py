# -*- coding: utf-8 -*-
"""Task 9 — decision-backed exact agent apply + legacy lifecycle coordination.

The EXISTING owner (``financial_analyst.memory_ops``) gains the
``AgentMemoryFileCoordinator``: primitive exact apply under the process-shared
root lease with the closed marker/CAS matrix and a DURABLE ``OwnerApplyResult``;
legacy accept/reject/revert share the coordinator and can neither create exact
markers nor lose one.

Run: ``pytest tests/test_memory_ops.py -v``
"""
from __future__ import annotations

import hashlib
import json

import pytest

import memory_coordination as mc
from financial_analyst import memory_ops


MARKER_ID = "apply." + "a" * 64
REQ = "b" * 64


def _payload(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _marker_line(payload: str) -> str:
    payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return (
        f"<!-- guanlan-memory-apply-v1 marker_id={MARKER_ID} "
        f"request_digest={REQ} payload_digest={payload_digest} -->"
    )


def _command(*, op="op-1", agent="dec.pm", slug="lesson-x", payload="the exact lesson",
             expected_before="absent"):
    payload = _payload(payload)
    marker = _marker_line(payload)
    unit = marker + "\n" + payload
    return memory_ops.OwnerApplyCommand(
        operation_id=op, target_agent=agent, slug=slug, marker_line=marker,
        payload_text=payload,
        expected_before_target_store_digest=expected_before,
        intended_after_target_store_digest=hashlib.sha256(
            unit.encode("utf-8")).hexdigest())


@pytest.fixture()
def root(tmp_path):
    d = tmp_path / "memories"
    (d / "dec.pm").mkdir(parents=True)
    return d


@pytest.fixture()
def coordinator(root):
    return memory_ops.AgentMemoryFileCoordinator(root)


# --------------------------------------------------------------------------- #
# exact apply                                                                  #
# --------------------------------------------------------------------------- #
def test_exact_apply_creates_the_marker_first_unit_and_a_durable_result(root, coordinator):
    cmd = _command()
    result = coordinator.apply_exact(cmd)
    assert result.state == "applied"
    assert result.actual_before_target_store_digest == "absent"
    assert result.actual_after_target_store_digest == cmd.intended_after_target_store_digest
    written = (root / "dec.pm" / "lesson-x.md").read_text(encoding="utf-8")
    first, _, rest = written.partition("\n")
    assert memory_ops.APPLY_MARKER_RE.match(first)
    assert rest == cmd.payload_text
    # the owner result is DURABLE in the reserved coordination namespace.
    results = (root / mc.COORDINATION_DIRNAME / "apply_results.jsonl")
    assert results.exists()
    entry = json.loads(results.read_text(encoding="utf-8").splitlines()[0])
    assert entry["operation_id"] == "op-1" and entry["state"] == "applied"


def test_same_operation_retry_recovers_the_recorded_result(root, coordinator):
    cmd = _command()
    first = coordinator.apply_exact(cmd)
    before = (root / "dec.pm" / "lesson-x.md").read_bytes()
    again = coordinator.apply_exact(cmd)  # crash-after-write retry
    assert again == first
    assert (root / "dec.pm" / "lesson-x.md").read_bytes() == before
    # a fresh coordinator instance (post-crash process) recovers the SAME result.
    recovered = memory_ops.AgentMemoryFileCoordinator(root).apply_exact(cmd)
    assert recovered == first


def test_same_operation_with_different_semantics_conflicts(coordinator):
    coordinator.apply_exact(_command())
    with pytest.raises(memory_ops.OwnerApplyError, match="different"):
        coordinator.apply_exact(_command(payload="another lesson entirely"))


def test_target_cas_failure_leaves_no_side_effect(root, coordinator):
    target = root / "dec.pm" / "lesson-x.md"
    target.write_text("pre-existing content", encoding="utf-8")
    with pytest.raises(memory_ops.OwnerApplyError, match="CAS"):
        coordinator.apply_exact(_command(expected_before="absent"))
    assert target.read_text(encoding="utf-8") == "pre-existing content"
    assert not (root / mc.COORDINATION_DIRNAME / "apply_results.jsonl").exists()


def test_update_requires_the_exact_before_digest(root, coordinator):
    target = root / "dec.pm" / "lesson-x.md"
    target.write_text("old unit\n", encoding="utf-8")
    old_digest = hashlib.sha256(b"old unit\n").hexdigest()
    updated = coordinator.apply_exact(_command(op="op-2", expected_before=old_digest))
    assert updated.actual_before_target_store_digest == old_digest
    assert updated.state == "applied"


def test_intended_digest_must_cover_the_complete_marker_unit(coordinator):
    cmd = _command()
    bad = memory_ops.OwnerApplyCommand(
        **{**cmd.__dict__, "intended_after_target_store_digest": "9" * 64})
    with pytest.raises(memory_ops.OwnerApplyError, match="intended"):
        coordinator.apply_exact(bad)


def test_closed_name_form_blocks_traversal(coordinator):
    cmd = _command()
    with pytest.raises(memory_ops.OwnerApplyError):
        coordinator.apply_exact(memory_ops.OwnerApplyCommand(
            **{**cmd.__dict__, "slug": "../escape"}))
    with pytest.raises(memory_ops.OwnerApplyError):
        coordinator.apply_exact(memory_ops.OwnerApplyCommand(
            **{**cmd.__dict__, "marker_line": "not a marker"}))


def test_exact_apply_excludes_a_concurrent_coordinator(root, coordinator):
    held = mc.ProcessSharedRootLease(root, owner="other", operation="hold")
    held.acquire()
    try:
        with pytest.raises(mc.LeaseHeldError):
            coordinator.apply_exact(_command())
    finally:
        held.release()


# --------------------------------------------------------------------------- #
# legacy lifecycle under the same coordinator                                  #
# --------------------------------------------------------------------------- #
def _seed_pending(root, slug="old-lesson", content="legacy body"):
    proposed = root / "_proposed" / "dec.pm"
    proposed.mkdir(parents=True, exist_ok=True)
    (proposed / f"{slug}.md").write_text(content, encoding="utf-8")


def test_legacy_accept_reject_revert_still_work_under_the_lease(root, coordinator):
    _seed_pending(root)
    out = coordinator.accept_legacy("dec.pm/old-lesson", source="cli")
    assert out.get("action") == "accept", out
    accepted = root / "dec.pm" / "old-lesson.md"
    assert accepted.exists()
    # legacy accept CANNOT create an exact marker.
    assert not memory_ops._is_marker_bound(accepted)
    out = coordinator.revert_legacy("dec.pm/old-lesson", source="cli")
    assert out.get("action") == "revert", out
    out = coordinator.reject_legacy("dec.pm/old-lesson", source="cli")
    assert out.get("action") == "reject", out


def test_marker_bound_target_blocks_legacy_revert(root, coordinator):
    result = coordinator.apply_exact(_command())
    assert result.state == "applied"
    out = memory_ops.revert_proposal(
        "dec.pm/lesson-x", source="cli", project_root=root.parent)
    assert "error" in out and "marker" in out["error"]
    # the marker was NOT lost.
    assert memory_ops._is_marker_bound(root / "dec.pm" / "lesson-x.md")


def test_legacy_accept_refuses_to_overwrite_a_marker_bound_target(root, coordinator):
    coordinator.apply_exact(_command())
    _seed_pending(root, slug="lesson-x", content="competing legacy proposal")
    out = coordinator.accept_legacy("dec.pm/lesson-x", source="cli")
    assert "error" in out and "overwrite" in out["error"]
    assert memory_ops._is_marker_bound(root / "dec.pm" / "lesson-x.md")


def test_owner_module_never_imports_orchestration():
    import inspect
    import re as _re

    source = inspect.getsource(memory_ops)
    imports = [ln for ln in source.splitlines() if _re.match(r"\s*(from|import)\s", ln)]
    joined = "\n".join(imports)
    assert "guanlan_v2" not in joined  # never imports Phase-2/orchestration
    assert "MemoryOwnerApplySemanticReceipt" not in source  # owner never writes main
