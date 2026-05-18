import yaml
from pathlib import Path
from financial_analyst.dream.introspector import Proposal
from financial_analyst.dream.proposal_writer import write_proposals


def test_write_proposal_creates_file(tmp_path):
    p = Proposal(
        target_agent="bull-advocate",
        topic_slug="vol-neutral-bias",
        title="Test",
        lesson_md="# Title\nbody",
        confidence="med",
        supporting_cases=["case1", "case2", "case3"],
        reasoning="3 cases share pattern X",
    )
    written = write_proposals([p], memory_root=tmp_path)
    assert len(written) == 1
    f = written[0]
    assert f.parent.name == "bull-advocate"
    assert f.parent.parent.name == "_proposed"
    assert "vol-neutral-bias" in f.name


def test_proposal_has_frontmatter(tmp_path):
    p = Proposal(target_agent="bear-advocate", topic_slug="t", title="Tt",
                 lesson_md="body", confidence="low", supporting_cases=[],
                 reasoning="")
    written = write_proposals([p], memory_root=tmp_path)
    content = written[0].read_text(encoding="utf-8")
    assert content.startswith("---\n")
    end = content.find("---", 3)
    fm = yaml.safe_load(content[3:end])
    assert fm["confidence"] == "low"
    assert fm["target_agent"] == "bear-advocate"


def test_multiple_proposals_separate_files(tmp_path):
    proposals = [
        Proposal(target_agent="bull-advocate", topic_slug="a", title="A",
                 lesson_md="a", confidence="low"),
        Proposal(target_agent="bear-advocate", topic_slug="b", title="B",
                 lesson_md="b", confidence="med"),
    ]
    written = write_proposals(proposals, memory_root=tmp_path)
    assert len(written) == 2
    assert (tmp_path / "_proposed" / "bull-advocate").exists()
    assert (tmp_path / "_proposed" / "bear-advocate").exists()


def test_proposal_with_chinese_content(tmp_path):
    p = Proposal(target_agent="risk-officer", topic_slug="game-capital",
                 title="游资博弈票硬规则细化",
                 lesson_md="# 中文内容测试\n\nPE>100且换手率>10%的游资票特别小心",
                 confidence="med",
                 supporting_cases=["SH600666 2026-05-15 卖出后实际跌 5%"])
    written = write_proposals([p], memory_root=tmp_path)
    content = written[0].read_text(encoding="utf-8")
    assert "游资博弈票" in content
    assert "中文内容测试" in content
