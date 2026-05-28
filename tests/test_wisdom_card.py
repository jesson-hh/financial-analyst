from financial_analyst.wisdom.card import WisdomCard


def _sample() -> WisdomCard:
    return WisdomCard(
        id="EV-008",
        title="证券板块作为科技兑现/抄底切换信号",
        status="draft",
        quality_score=0.82,
        confidence="高",
        tags=["板块组合", "择时", "证券"],
        source={"platform": "bilibili", "up": "来去由心",
                "bvid": "BV1F3Gy6oEMx", "date": "2026-05-27", "segments": "71-167"},
        body="## 经验\n证券和主流题材高开组合判断兑现/抄底.\n\n"
             "## 适用条件\n日内/隔日, 有明确主流题材抱团时.\n\n"
             "## 操作建议\n科技多日高潮+证券同步走强 → 当日减仓.\n\n"
             "## 反例 / 边界\n证券有独立行情时信号失真.",
        corroborates=["EV-002"],
        conflicts=[],
        created="2026-05-28",
        reviewed_by=None,
    )


def test_roundtrip_preserves_all_fields():
    card = _sample()
    text = card.to_markdown()
    back = WisdomCard.from_markdown(text)
    assert back.id == "EV-008"
    assert back.title == card.title
    assert back.status == "draft"
    assert back.quality_score == 0.82
    assert back.confidence == "高"
    assert back.tags == ["板块组合", "择时", "证券"]
    assert back.source["bvid"] == "BV1F3Gy6oEMx"
    assert back.corroborates == ["EV-002"]
    assert back.conflicts == []
    assert back.reviewed_by is None
    assert "## 经验" in back.body
    assert "## 反例 / 边界" in back.body


def test_to_markdown_starts_with_frontmatter():
    text = _sample().to_markdown()
    assert text.startswith("---\n")
    assert text.count("---") >= 2


def test_from_markdown_missing_frontmatter_raises():
    import pytest
    with pytest.raises(ValueError):
        WisdomCard.from_markdown("no frontmatter here")


def test_from_markdown_tolerates_null_optional_fields():
    text = (
        "---\n"
        "id: EV-001\n"
        "title: t\n"
        "status: draft\n"
        "tags:\n"
        "source:\n"
        "corroborates:\n"
        "conflicts:\n"
        "reviewed_by:\n"
        "---\n\n## 经验\nx"
    )
    card = WisdomCard.from_markdown(text)
    assert card.tags == []
    assert card.source == {}
    assert card.corroborates == []
    assert card.reviewed_by is None
