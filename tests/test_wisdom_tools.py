from financial_analyst.buddy.tools import _tool_wisdom_review, _tool_wisdom_search
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore


def _seed(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(WisdomCard(id="EV-001", title="低分草稿", status="draft",
                          quality_score=0.3, body="## 经验\nx\n## 反例\ny"))
    store.save(WisdomCard(id="EV-002", title="高分草稿", status="draft",
                          quality_score=0.9, body="## 经验\nx\n## 反例\ny"))
    store.save(WisdomCard(id="EV-003", title="已批准的证券组合经验", status="approved",
                          quality_score=0.8,
                          body="## 经验\n证券板块判断兑现.\n## 反例\n独立行情失真"))
    return store


def test_review_list_sorted_by_quality_desc(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_review(action="list")
    assert not res.is_error
    assert res.content.index("EV-002") < res.content.index("EV-001")
    assert "EV-003" not in res.content


def test_review_approve_moves_to_approved(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_review(action="approve", card_id="EV-002", reviewed_by="xuyi")
    assert not res.is_error
    assert (tmp_path / "approved" / "EV-002.md").exists()
    assert not (tmp_path / "draft" / "EV-002.md").exists()


def test_review_approve_missing_card_is_error(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_review(action="approve", card_id="EV-999")
    assert res.is_error


def test_search_only_hits_approved(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_search(query="证券")
    assert not res.is_error
    assert "证券" in res.content
    assert "低分草稿" not in res.content


def test_search_no_hit_returns_message(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_search(query="不存在的关键词zzz")
    assert not res.is_error
    assert "未找到" in res.content or "no" in res.content.lower()
