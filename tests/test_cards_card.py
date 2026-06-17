# guanlan_v2.cards.card · Card 模型(UI 卡超集)往返测试
# 形状刻意区别于引擎 wisdom WisdomCard:本卡有 cat/verdict/conf/ic/expr/insight/src/refs,
# 无 quality_score/body/corroborates/conflicts。引擎卡逐字复制过不了这些断言。
from guanlan_v2.cards.card import Card


def _sample() -> Card:
    return Card(
        id="EV-008",
        title="缩量企稳反转",
        status="approved",
        cat="价量",
        tags=["反转", "缩量", "周频"],
        verdict="通过",
        conf=76,
        ic="0.043",
        expr="-rank(ts_sum(ret,5)) · (vol_ratio < 0.7)",
        insight="超跌后缩量企稳,3 日内反转概率显著抬升;震荡市、周频最有效,但信号衰减快。",
        src="研报",
        refs=["rs_reversal", "fa_reversal"],
        created="2026-06-04",
        reviewed_by=None,
    )


def test_roundtrip_preserves_all_fields():
    card = _sample()
    back = Card.from_markdown(card.to_markdown())
    assert back.id == "EV-008"
    assert back.title == "缩量企稳反转"
    assert back.status == "approved"
    assert back.cat == "价量"
    assert back.tags == ["反转", "缩量", "周频"]
    assert back.verdict == "通过"
    assert back.conf == 76
    assert back.ic == "0.043"
    assert back.expr == "-rank(ts_sum(ret,5)) · (vol_ratio < 0.7)"
    assert "缩量企稳" in back.insight
    assert back.src == "研报"
    assert back.refs == ["rs_reversal", "fa_reversal"]
    assert back.created == "2026-06-04"
    assert back.reviewed_by is None


def test_conf_stays_int():
    # UI 置信度是 0-100 整数, 往返不得变成字符串
    back = Card.from_markdown(_sample().to_markdown())
    assert isinstance(back.conf, int)
    assert back.conf == 76


def test_ic_stays_string():
    # ic 保形为字符串(如 "0.043"), 不被 yaml 解析成 float
    back = Card.from_markdown(_sample().to_markdown())
    assert isinstance(back.ic, str)
    assert back.ic == "0.043"


def test_insight_maps_to_body():
    # insight = frontmatter 之后的正文
    text = _sample().to_markdown()
    assert text.startswith("---\n")
    assert "超跌后缩量企稳" in text.split("---", 2)[2]


def test_to_markdown_starts_with_frontmatter():
    text = _sample().to_markdown()
    assert text.startswith("---\n")
    assert text.count("---") >= 2


def test_from_markdown_missing_frontmatter_raises():
    import pytest
    with pytest.raises(ValueError):
        Card.from_markdown("no frontmatter here")


def test_from_markdown_tolerates_null_optional_fields():
    text = (
        "---\n"
        "id: EV-001\n"
        "title: t\n"
        "status: draft\n"
        "cat: 其他\n"
        "tags:\n"
        "verdict: 存疑\n"
        "conf: 0\n"
        "refs:\n"
        "reviewed_by:\n"
        "---\n\nx"
    )
    card = Card.from_markdown(text)
    assert card.tags == []
    assert card.refs == []
    assert card.reviewed_by is None
    assert card.insight == "x"
