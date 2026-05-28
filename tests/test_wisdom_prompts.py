from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.prompts import build_extraction_messages


def test_messages_shape():
    msgs = build_extraction_messages("今天大盘跌了4200家.", {"up": "老徐"}, [])
    assert isinstance(msgs, list)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


def test_system_prompt_has_quality_gate_and_schema():
    msgs = build_extraction_messages("x", {}, [])
    system = msgs[0]["content"]
    assert "反例" in system
    assert "数字" in system or "阈值" in system
    assert "cards" in system
    assert "quality_score" in system
    assert "corroborates" in system


def test_user_prompt_embeds_transcript_and_source():
    msgs = build_extraction_messages("特定转写内容ABC", {"up": "来去由心", "bvid": "BVxxx"}, [])
    user = msgs[-1]["content"]
    assert "特定转写内容ABC" in user
    assert "来去由心" in user


def test_existing_cards_summarized_for_corroboration():
    existing = [WisdomCard(id="EV-002", title="单板块虹吸警示", tags=["流动性"])]
    msgs = build_extraction_messages("x", {}, existing)
    joined = "\n".join(m["content"] for m in msgs)
    assert "EV-002" in joined
    assert "单板块虹吸警示" in joined
