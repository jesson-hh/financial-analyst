import asyncio

import financial_analyst.wisdom.cli as cli_mod
from financial_analyst.wisdom.card import WisdomCard


def test_extract_assigns_ids_and_saves_drafts(tmp_path, monkeypatch):
    transcript = tmp_path / "t.txt"
    transcript.write_text("今天大盘跌4200家.", encoding="utf-8")

    async def _fake_extract(text, source, existing=None):
        return [
            WisdomCard(id="", title="c1", status="draft", body="## 经验\nx\n## 反例\ny"),
            WisdomCard(id="", title="c2", status="draft", body="## 经验\nx\n## 反例\ny"),
        ]
    monkeypatch.setattr(cli_mod, "extract_cards", _fake_extract)

    rc = cli_mod.main([
        "extract", str(transcript),
        "--platform", "bilibili", "--up", "老徐",
        "--bvid", "BVxxx", "--date", "2026-05-26",
        "--root", str(tmp_path / "wisdom"),
    ])
    assert rc == 0
    from financial_analyst.wisdom.store import WisdomStore
    store = WisdomStore(root=tmp_path / "wisdom")
    drafts = store.list_by_status("draft")
    assert len(drafts) == 2
    assert {c.id for c in drafts} == {"EV-001", "EV-002"}
    assert all(c.source["bvid"] == "BVxxx" for c in drafts)
    assert all(c.created for c in drafts)
