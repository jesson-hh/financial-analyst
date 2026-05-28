import pytest
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore


def _card(card_id: str, status: str = "draft", score: float = 0.5) -> WisdomCard:
    return WisdomCard(id=card_id, title=f"t-{card_id}", status=status,
                      quality_score=score, body="## 经验\nx")


def test_creates_status_dirs(tmp_path):
    store = WisdomStore(root=tmp_path)
    for s in ("draft", "approved", "rejected"):
        assert (tmp_path / s).is_dir()


def test_save_and_load(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001"))
    loaded = store.load("EV-001")
    assert loaded.id == "EV-001"
    assert loaded.status == "draft"


def test_save_writes_to_status_subdir(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    assert (tmp_path / "draft" / "EV-001.md").exists()


def test_list_by_status(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    store.save(_card("EV-002", status="draft"))
    store.save(_card("EV-003", status="approved"))
    drafts = store.list_by_status("draft")
    assert {c.id for c in drafts} == {"EV-001", "EV-002"}
    assert len(store.list_by_status("approved")) == 1


def test_set_status_moves_file(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    store.set_status("EV-001", "approved", reviewed_by="xuyi")
    assert not (tmp_path / "draft" / "EV-001.md").exists()
    assert (tmp_path / "approved" / "EV-001.md").exists()
    reloaded = store.load("EV-001")
    assert reloaded.status == "approved"
    assert reloaded.reviewed_by == "xuyi"


def test_set_status_invalid_raises(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001"))
    with pytest.raises(ValueError):
        store.set_status("EV-001", "bogus")


def test_load_missing_raises_keyerror(tmp_path):
    store = WisdomStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.load("EV-999")


def test_next_id_sequence(tmp_path):
    store = WisdomStore(root=tmp_path)
    assert store.next_id() == "EV-001"
    store.save(_card("EV-001"))
    store.save(_card("EV-012", status="approved"))
    assert store.next_id() == "EV-013"


def test_default_root_honours_env_override(tmp_path, monkeypatch):
    # 对齐 memory_paths: $FINANCIAL_ANALYST_HOME/wisdom 优先
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path / "fahome"))
    store = WisdomStore()
    assert store.root == tmp_path / "fahome" / "wisdom"
    assert (tmp_path / "fahome" / "wisdom" / "draft").is_dir()


def test_default_root_uses_cwd_wisdom_if_present(tmp_path, monkeypatch):
    # 第二级: <cwd>/wisdom 存在则用 (dev / 源码 checkout)
    monkeypatch.delenv("FINANCIAL_ANALYST_HOME", raising=False)
    (tmp_path / "wisdom").mkdir()
    monkeypatch.chdir(tmp_path)
    store = WisdomStore()
    assert store.root == tmp_path / "wisdom"
