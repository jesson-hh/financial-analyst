import json
from pathlib import Path
from unittest.mock import patch, AsyncMock
import pandas as pd
import pytest
from typer.testing import CliRunner
from financial_analyst.cli import app


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    """Redirect memory_ops.AUDIT_PATH to tmp dir — these tests exercise CLI
    accept/reject which after retrofit writes audit; without isolation the
    user's real ~/.financial-analyst/audit.jsonl would be polluted."""
    from financial_analyst import memory_ops
    monkeypatch.setattr(
        memory_ops, "AUDIT_PATH", tmp_path / "_isolated_audit.jsonl",
    )


def test_cli_dream_no_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "--since", "30"])
    assert result.exit_code == 0
    assert "no reports" in result.stdout.lower() or "found 0" in result.stdout.lower()


def test_cli_dream_dry_run_no_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600519", "rating_overall": -1, "action": "sell",
           "target_price": 1500, "stop_loss": 1700, "position_pct": 0}
    (out_dir / "SH600519_2026-05-01.json").write_text(json.dumps(rpt), encoding="utf-8")

    fake_proposals = {
        "proposals": [{
            "target_agent": "bull-advocate", "topic_slug": "test", "title": "Test",
            "lesson_md": "body", "confidence": "low",
            "supporting_cases": [], "reasoning": "",
        }],
        "summary": "test",
    }
    fake_llm = {"choices": [{"message": {"content": json.dumps(fake_proposals)}}]}

    class FakeLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            return pd.DataFrame({
                "trade_date": pd.date_range("2026-05-02", periods=20, freq="B"),
                "open": [1700]*20, "high": [1750]*20, "low": [1650]*20,
                "close": [1690 - i*5 for i in range(20)],
                "vol": [1e6]*20, "amount": [1e8]*20,
            })

    with patch("financial_analyst.data.loader_factory.get_default_loader", return_value=FakeLoader()):
        with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_llm:
            client = AsyncMock(); client.chat = AsyncMock(return_value=fake_llm)
            mock_llm.return_value = client
            runner = CliRunner()
            result = runner.invoke(app, ["dream", "--since", "365", "--dry-run"])
    assert result.exit_code == 0
    assert "dry-run" in result.stdout.lower()
    assert not (tmp_path / "memories" / "_proposed").exists()


def test_cli_dream_writes_proposals(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Isolate the memory root to this tmp dir: post-fix, default_memory_root()
    # falls back to ~/.financial-analyst/memories when cwd has no ./memories,
    # which would otherwise read the developer's real proposals.
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600519", "rating_overall": -1, "action": "sell",
           "target_price": 1500, "stop_loss": 1700, "position_pct": 0}
    (out_dir / "SH600519_2026-05-01.json").write_text(json.dumps(rpt), encoding="utf-8")

    fake_proposals = {
        "proposals": [{
            "target_agent": "bear-advocate", "topic_slug": "test-rule", "title": "T",
            "lesson_md": "body", "confidence": "low",
            "supporting_cases": [], "reasoning": "",
        }],
        "summary": "test",
    }
    fake_llm = {"choices": [{"message": {"content": json.dumps(fake_proposals)}}]}

    class FakeLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            return pd.DataFrame({
                "trade_date": pd.date_range("2026-05-02", periods=20, freq="B"),
                "open": [1700]*20, "high": [1750]*20, "low": [1650]*20,
                "close": [1690 - i*5 for i in range(20)],
                "vol": [1e6]*20, "amount": [1e8]*20,
            })

    with patch("financial_analyst.data.loader_factory.get_default_loader", return_value=FakeLoader()):
        with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_llm:
            client = AsyncMock(); client.chat = AsyncMock(return_value=fake_llm)
            mock_llm.return_value = client
            runner = CliRunner()
            result = runner.invoke(app, ["dream", "--since", "365"])
    assert result.exit_code == 0
    proposed = tmp_path / "memories" / "_proposed" / "bear-advocate"
    assert proposed.exists()
    md_files = list(proposed.glob("*.md"))
    assert len(md_files) == 1


def test_cli_dream_help():
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "--help"])
    assert result.exit_code == 0
    assert "since" in result.stdout.lower()
    assert "dry-run" in result.stdout.lower()


# ---- v1.4.3: dream review / accept / reject subcommands -------------------


def _make_synthetic_proposal(root: Path, agent: str, slug: str, confidence: str = "med") -> Path:
    """Write a fake proposal markdown under memories/_proposed/<agent>/."""
    proposed_dir = root / "memories" / "_proposed" / agent
    proposed_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"topic: {slug}\n"
        f"title: Test rule {slug}\n"
        f"target_agent: {agent}\n"
        f"confidence: {confidence}\n"
        "generated_at: '2024-12-31'\n"
        "supporting_cases:\n"
        "  - 'case 1'\n"
        "  - 'case 2'\n"
        "  - 'case 3'\n"
        "reasoning: synthetic test\n"
        "---\n\n"
        f"# Test rule {slug}\n\nbody body body\n"
    )
    out = proposed_dir / f"2024-12-31_{slug}.md"
    out.write_text(body, encoding="utf-8")
    return out


def test_cli_dream_review_empty(tmp_path, monkeypatch):
    """`dream review` with no proposals dir should report it clearly."""
    monkeypatch.chdir(tmp_path)
    # Isolate memory root to tmp: post-fix the resolver falls back to
    # ~/.financial-analyst/memories when cwd has no ./memories, which on a dev
    # machine holds real proposals and would make "empty" assertions flaky.
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "review"])
    assert result.exit_code == 0
    assert "no proposals" in result.stdout.lower() or "empty" in result.stdout.lower()


def test_cli_dream_review_lists_proposals(tmp_path, monkeypatch):
    """`dream review` should list each proposal with confidence + title."""
    monkeypatch.chdir(tmp_path)
    _make_synthetic_proposal(tmp_path, "whale-analyst", "rule-a", confidence="med")
    _make_synthetic_proposal(tmp_path, "bear-advocate", "rule-b", confidence="high")
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "review"])
    assert result.exit_code == 0
    assert "whale-analyst/rule-a" in result.stdout
    assert "bear-advocate/rule-b" in result.stdout
    # Both confidence labels rendered
    assert "med" in result.stdout
    assert "high" in result.stdout


def test_cli_dream_accept_promotes_proposal(tmp_path, monkeypatch):
    """`dream accept` moves the proposal from _proposed/ to the agent's
    permanent memory dir."""
    monkeypatch.chdir(tmp_path)
    src = _make_synthetic_proposal(tmp_path, "whale-analyst", "no-vr-without-obv")
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "accept", "whale-analyst/no-vr-without-obv"])
    assert result.exit_code == 0, result.stdout
    # Source file gone
    assert not src.exists()
    # Destination present under memories/<agent>/<slug>.md
    dst = tmp_path / "memories" / "whale-analyst" / "no-vr-without-obv.md"
    assert dst.exists()
    # Content preserved (frontmatter + body)
    text = dst.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "no-vr-without-obv" in text


def test_cli_dream_accept_refuses_overwrite(tmp_path, monkeypatch):
    """`dream accept` should refuse to clobber an existing memory file."""
    monkeypatch.chdir(tmp_path)
    _make_synthetic_proposal(tmp_path, "whale-analyst", "existing-rule")
    # Pre-create the destination
    dst_dir = tmp_path / "memories" / "whale-analyst"
    dst_dir.mkdir(parents=True)
    (dst_dir / "existing-rule.md").write_text("preexisting content", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["dream", "accept", "whale-analyst/existing-rule"])
    assert result.exit_code != 0
    assert "refusing to overwrite" in result.stdout.lower()
    # Original preserved
    assert (dst_dir / "existing-rule.md").read_text(encoding="utf-8") == "preexisting content"


def test_cli_dream_reject_deletes_proposal(tmp_path, monkeypatch):
    """`dream reject` should delete the proposal file."""
    monkeypatch.chdir(tmp_path)
    src = _make_synthetic_proposal(tmp_path, "whale-analyst", "bad-idea")
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "reject", "whale-analyst/bad-idea"])
    assert result.exit_code == 0, result.stdout
    assert not src.exists()
    # Not promoted either
    assert not (tmp_path / "memories" / "whale-analyst" / "bad-idea.md").exists()


def test_cli_dream_accept_unknown_proposal(tmp_path, monkeypatch):
    """`dream accept` with a missing slug should error with a helpful list."""
    monkeypatch.chdir(tmp_path)
    _make_synthetic_proposal(tmp_path, "whale-analyst", "real-slug")
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "accept", "whale-analyst/nonexistent"])
    assert result.exit_code != 0
    assert "no proposal matching" in result.stdout.lower()


def test_cli_dream_accept_bad_target_format(tmp_path, monkeypatch):
    """`dream accept` with target missing slash should error early."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "accept", "no-slash"])
    assert result.exit_code != 0
    assert "<agent>/<slug>" in result.stdout


def test_cli_dream_accept_writes_audit_with_source_cli(tmp_path, monkeypatch):
    """After retrofit: CLI accept must write an audit entry with source='cli'."""
    from financial_analyst import memory_ops
    monkeypatch.chdir(tmp_path)
    _make_synthetic_proposal(tmp_path, "whale-advocate", "audit-test")
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "accept", "whale-advocate/audit-test"])
    assert result.exit_code == 0, result.stdout
    assert "Audit id:" in result.stdout
    # AUDIT_PATH was redirected by the autouse _isolate_audit fixture
    assert memory_ops.AUDIT_PATH.exists(), "audit log was not written"
    entries = [json.loads(line) for line in memory_ops.AUDIT_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["source"] == "cli"
    assert entries[0]["action"] == "accept"
    assert entries[0]["target"] == "whale-advocate/audit-test"
