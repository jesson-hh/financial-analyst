"""Unit tests for memory_ops — proposal lifecycle (accept/reject/revert/list_audit).

CRITICAL: every test monkeypatches ``memory_ops.AUDIT_PATH`` to a tmp file.
A leaked test would write to the user's real ``~/.financial-analyst/audit.jsonl``,
polluting their history. The ``audit_path`` fixture below enforces this.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from financial_analyst import memory_ops


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    """Redirect AUDIT_PATH to tmp dir. NEVER pollutes ~/.financial-analyst/."""
    p = tmp_path / "audit.jsonl"
    monkeypatch.setattr(memory_ops, "AUDIT_PATH", p)
    return p


@pytest.fixture
def proj(tmp_path):
    """Minimal project layout with memories/ + a fresh git repo."""
    (tmp_path / "memories" / "_proposed" / "bear-advocate").mkdir(parents=True)
    (tmp_path / "memories" / "bear-advocate").mkdir(parents=True)
    subprocess.run(
        ["git", "init", str(tmp_path)],
        capture_output=True, check=True,
    )
    # git add needs *some* identity in case it ever wanders into commit territory
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "test"],
        capture_output=True,
    )
    return tmp_path


def _make_proposal(proj_root: Path, agent: str, slug: str,
                   content: str = "# rule\nbody text\n") -> Path:
    """Place a proposal file under memories/_proposed/<agent>/."""
    p = proj_root / "memories" / "_proposed" / agent / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# accept_proposal
# ---------------------------------------------------------------------------

def test_accept_happy_path(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "F15_new_pitfall")
    result = memory_ops.accept_proposal(
        "bear-advocate/F15_new_pitfall", source="test", project_root=proj,
    )
    assert "error" not in result, result
    assert result["action"] == "accept"
    assert result["target"] == "bear-advocate/F15_new_pitfall"
    assert result["id"] == "a-0001"
    assert result["git_staged"] is True
    # File moved
    src = proj / "memories" / "_proposed" / "bear-advocate" / "F15_new_pitfall.md"
    dst = proj / "memories" / "bear-advocate" / "F15_new_pitfall.md"
    assert not src.exists()
    assert dst.exists()
    # Audit written
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["action"] == "accept"
    assert entries[0]["source"] == "test"
    assert entries[0]["target"] == "bear-advocate/F15_new_pitfall"
    assert entries[0]["git_staged"] is True


def test_accept_refuse_overwrite(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "existing")
    (proj / "memories" / "bear-advocate" / "existing.md").write_text(
        "already here", encoding="utf-8",
    )
    result = memory_ops.accept_proposal(
        "bear-advocate/existing", source="test", project_root=proj,
    )
    assert "error" in result
    assert "overwrite" in result["error"]
    # Source file untouched
    assert (proj / "memories" / "_proposed" / "bear-advocate" / "existing.md").exists()
    # No audit
    assert not audit_path.exists() or audit_path.read_text(encoding="utf-8") == ""


def test_accept_dry_run_has_no_side_effects(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "dry_run_test")
    result = memory_ops.accept_proposal(
        "bear-advocate/dry_run_test", source="test", dry_run=True, project_root=proj,
    )
    assert result.get("dry_run") is True
    assert "would_move" in result
    assert "src" in result["would_move"] and "dst" in result["would_move"]
    # No file moved
    assert (proj / "memories" / "_proposed" / "bear-advocate" / "dry_run_test.md").exists()
    assert not (proj / "memories" / "bear-advocate" / "dry_run_test.md").exists()
    # No audit
    assert not audit_path.exists() or audit_path.read_text(encoding="utf-8") == ""


def test_accept_malformed_target(proj, audit_path):
    result = memory_ops.accept_proposal(
        "noslash", source="test", project_root=proj,
    )
    assert "error" in result
    assert "<agent>/<slug>" in result["error"]


def test_accept_missing_proposal(proj, audit_path):
    result = memory_ops.accept_proposal(
        "bear-advocate/nonexistent", source="test", project_root=proj,
    )
    assert "error" in result
    assert "no proposal" in result["error"]


# ---------------------------------------------------------------------------
# reject_proposal
# ---------------------------------------------------------------------------

def test_reject_deletes_and_audits(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "bad_rule")
    result = memory_ops.reject_proposal(
        "bear-advocate/bad_rule", source="test", project_root=proj,
    )
    assert "error" not in result, result
    assert result["action"] == "reject"
    assert not (proj / "memories" / "_proposed" / "bear-advocate" / "bad_rule.md").exists()
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["action"] == "reject"
    assert entries[0]["source"] == "test"


def test_reject_missing_proposal(proj, audit_path):
    result = memory_ops.reject_proposal(
        "bear-advocate/missing", source="test", project_root=proj,
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# revert_proposal
# ---------------------------------------------------------------------------

def test_revert_round_trip(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "to_revert")
    accept_result = memory_ops.accept_proposal(
        "bear-advocate/to_revert", source="test", project_root=proj,
    )
    assert accept_result["id"] == "a-0001"
    revert_result = memory_ops.revert_proposal(
        "bear-advocate/to_revert", source="test", project_root=proj,
    )
    assert "error" not in revert_result, revert_result
    assert revert_result["id"] == "a-0002"
    assert revert_result["action"] == "revert"
    assert revert_result["reverted_id"] == "a-0001"
    # File is back in _proposed/
    assert (proj / "memories" / "_proposed" / "bear-advocate" / "to_revert.md").exists()
    assert not (proj / "memories" / "bear-advocate" / "to_revert.md").exists()
    # Two audit entries
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2
    assert entries[0]["action"] == "accept"
    assert entries[1]["action"] == "revert"


def test_revert_without_prior_accept(proj, audit_path):
    result = memory_ops.revert_proposal(
        "bear-advocate/never_accepted", source="test", project_root=proj,
    )
    assert "error" in result
    assert "nothing to revert" in result["error"]


# ---------------------------------------------------------------------------
# Audit format
# ---------------------------------------------------------------------------

def test_audit_jsonl_has_required_fields(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "fmt_test")
    memory_ops.accept_proposal(
        "bear-advocate/fmt_test", source="mcp", project_root=proj,
    )
    line = audit_path.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    required = {"id", "ts", "action", "source", "target", "src", "dst", "project_root", "git_staged"}
    missing = required - set(entry.keys())
    assert not missing, f"audit entry missing fields: {missing}"
    assert entry["id"].startswith("a-")
    assert "T" in entry["ts"]  # ISO 8601 marker


def test_audit_id_monotonic(proj, audit_path):
    _make_proposal(proj, "bear-advocate", "one")
    _make_proposal(proj, "bear-advocate", "two")
    r1 = memory_ops.accept_proposal("bear-advocate/one", source="t", project_root=proj)
    r2 = memory_ops.accept_proposal("bear-advocate/two", source="t", project_root=proj)
    assert r1["id"] == "a-0001"
    assert r2["id"] == "a-0002"


# ---------------------------------------------------------------------------
# Git stage failure fallback
# ---------------------------------------------------------------------------

def test_git_stage_failure_does_not_block_accept(tmp_path, audit_path):
    """Non-git directory — accept still succeeds with git_staged=False."""
    (tmp_path / "memories" / "_proposed" / "bear-advocate").mkdir(parents=True)
    (tmp_path / "memories" / "bear-advocate").mkdir(parents=True)
    proposal = tmp_path / "memories" / "_proposed" / "bear-advocate" / "no_git.md"
    proposal.write_text("# rule\nbody\n", encoding="utf-8")
    result = memory_ops.accept_proposal(
        "bear-advocate/no_git", source="test", project_root=tmp_path,
    )
    assert "error" not in result, result
    assert result["git_staged"] is False
    # File still moved (accept succeeded)
    assert (tmp_path / "memories" / "bear-advocate" / "no_git.md").exists()
    # Audit entry has git_error populated
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["git_staged"] is False
    assert "git_error" in entries[0]


# ---------------------------------------------------------------------------
# list_audit
# ---------------------------------------------------------------------------

def test_list_audit_newest_first(proj, audit_path):
    for i in range(5):
        _make_proposal(proj, "bear-advocate", f"item_{i}")
        memory_ops.accept_proposal(
            f"bear-advocate/item_{i}", source="test", project_root=proj,
        )
    audits = memory_ops.list_audit(limit=3)
    assert len(audits) == 3
    assert audits[0]["id"] == "a-0005"
    assert audits[1]["id"] == "a-0004"
    assert audits[2]["id"] == "a-0003"


def test_list_audit_empty_when_no_file(audit_path):
    audits = memory_ops.list_audit()
    assert audits == []
