import asyncio
import json

import financial_analyst.wisdom.extractor as extractor_mod
from financial_analyst.wisdom.extractor import extract_cards


class _FakeClient:
    """Stand-in for LLMClient: returns a canned OpenAI-compat response."""
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls = 0

    async def chat(self, messages, tools=None, response_format=None, temperature=0.2):
        self.calls += 1
        return {"choices": [{"message": {"content": json.dumps(self._payload, ensure_ascii=False)}}]}


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(extractor_mod.LLMClient, "for_agent",
                        classmethod(lambda cls, name: fake))


def test_extracts_cards_with_draft_status(monkeypatch):
    payload = {"cards": [{
        "title": "证券组合判断兑现",
        "quality_score": 0.8,
        "confidence": "高",
        "tags": ["择时"],
        "body": "## 经验\n证券+科技高开看兑现.\n\n## 适用条件\n抱团时.\n\n"
                "## 操作建议\n减仓.\n\n## 反例 / 边界\n证券独立行情失真.",
        "corroborates": ["EV-002"],
        "conflicts": [],
    }]}
    fake = _FakeClient(payload)
    _patch_client(monkeypatch, fake)
    cards = asyncio.run(extract_cards("转写x", {"up": "来去由心"}, existing=None))
    assert len(cards) == 1
    c = cards[0]
    assert c.status == "draft"
    assert c.title == "证券组合判断兑现"
    assert c.quality_score == 0.8
    assert c.corroborates == ["EV-002"]
    assert c.source["up"] == "来去由心"
    assert c.id == ""   # id 由 store.next_id() 落盘时分配, extractor 不分配


def test_quality_gate_drops_card_without_counterexample(monkeypatch):
    payload = {"cards": [{
        "title": "水文条",
        "quality_score": 0.9,
        "confidence": "高",
        "tags": [],
        "body": "## 经验\n保持好心态.\n\n## 适用条件\n随时.\n\n## 操作建议\n顺势.",
        "corroborates": [],
        "conflicts": [],
    }]}
    fake = _FakeClient(payload)
    _patch_client(monkeypatch, fake)
    cards = asyncio.run(extract_cards("x", {}, None))
    assert cards == []


def test_retries_once_on_bad_json_then_raises(monkeypatch):
    class _BadClient:
        def __init__(self):
            self.calls = 0
        async def chat(self, messages, tools=None, response_format=None, temperature=0.2):
            self.calls += 1
            return {"choices": [{"message": {"content": "NOT JSON"}}]}
    bad = _BadClient()
    _patch_client(monkeypatch, bad)
    import pytest
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(extract_cards("x", {}, None))
    assert bad.calls == 2   # 1 try + 1 retry
