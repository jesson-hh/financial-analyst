import pytest
from financial_analyst.knowledge.base import KnowledgeBase
from financial_analyst.knowledge.local_markdown import LocalMarkdownKB


def test_base_is_abstract():
    with pytest.raises(TypeError):
        KnowledgeBase()


def test_local_kb_query_returns_top_k(tmp_path):
    (tmp_path / "factor_a.md").write_text("# Factor A\nreversal alpha")
    (tmp_path / "factor_b.md").write_text("# Factor B\nmomentum alpha")
    (tmp_path / "factor_c.md").write_text("# Factor C\nvolatility risk")
    kb = LocalMarkdownKB(tmp_path)
    hits = kb.query("alpha", top_k=2)
    assert len(hits) == 2
    assert all("alpha" in h["content"].lower() for h in hits)


def test_local_kb_get_related_empty(tmp_path):
    kb = LocalMarkdownKB(tmp_path)
    assert kb.get_related("SH600519") == []
