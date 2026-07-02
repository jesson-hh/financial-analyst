"""picks 档案纯函数单测(P0 §1)。全部经 monkeypatch PICKS_PATH 指 tmp,零生产污染。"""
import json


def test_append_and_read_roundtrip(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "p.jsonl")
    assert picks.append_pick({"ts": "t1", "snapshot": False, "note": None}) is True
    assert picks.append_pick({"ts": "t2", "snapshot": True, "note": "官方"}) is True
    rows = picks.read_picks(limit=10)
    assert [r["ts"] for r in rows] == ["t2", "t1"]          # 新在前


def test_snapshot_only_filter(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "p.jsonl")
    picks.append_pick({"ts": "a", "snapshot": False})
    picks.append_pick({"ts": "b", "snapshot": True})
    rows = picks.read_picks(snapshot_only=True, limit=10)
    assert len(rows) == 1 and rows[0]["ts"] == "b"


def test_dirty_line_skipped_and_limit(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    p = tmp_path / "p.jsonl"
    monkeypatch.setattr(picks, "PICKS_PATH", p)
    picks.append_pick({"ts": "a"})
    with p.open("a", encoding="utf-8") as f:
        f.write("{oops 不是JSON\n")
    picks.append_pick({"ts": "b"})
    picks.append_pick({"ts": "c"})
    rows = picks.read_picks(limit=2)
    assert [r["ts"] for r in rows] == ["c", "b"]            # 坏行跳过,limit 生效


def test_missing_file_returns_empty(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "nope" / "p.jsonl")
    assert picks.read_picks() == []


def test_append_failure_returns_false(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")               # 父路径是文件 → mkdir 必炸
    monkeypatch.setattr(picks, "PICKS_PATH", blocker / "p.jsonl")
    assert picks.append_pick({"ts": "a"}) is False          # 吞异常回 False,绝不抛


def test_chinese_not_escaped(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    p = tmp_path / "p.jsonl"
    monkeypatch.setattr(picks, "PICKS_PATH", p)
    picks.append_pick({"name": "宁德时代"})
    assert "宁德时代" in p.read_text(encoding="utf-8")       # ensure_ascii=False
