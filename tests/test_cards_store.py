# guanlan_v2.cards.store · CardStore 落盘/检索测试
# 镜像引擎 test_wisdom_store.py,但用 Card(UI 形状)+ GUANLAN_WISDOM_ROOT。
import pytest

from guanlan_v2.cards.card import Card
from guanlan_v2.cards.store import CardStore, _default_root


def _card(card_id: str, status: str = "approved", title: str | None = None) -> Card:
    return Card(id=card_id, title=title or f"t-{card_id}", status=status,
                cat="价量", verdict="通过", conf=70, ic="0.040")


def test_creates_status_dirs(tmp_path):
    CardStore(root=tmp_path)
    for s in ("draft", "approved", "rejected"):
        assert (tmp_path / s).is_dir()


def test_save_and_load(tmp_path):
    store = CardStore(root=tmp_path)
    store.save(_card("EV-001"))
    loaded = store.load("EV-001")
    assert loaded.id == "EV-001"
    assert loaded.status == "approved"
    assert loaded.cat == "价量"
    assert loaded.conf == 70


def test_save_writes_to_status_subdir(tmp_path):
    store = CardStore(root=tmp_path)
    store.save(_card("EV-002", status="draft"))
    assert (tmp_path / "draft" / "EV-002.md").exists()


def test_list_by_status(tmp_path):
    store = CardStore(root=tmp_path)
    store.save(_card("EV-001", status="approved"))
    store.save(_card("EV-002", status="approved"))
    store.save(_card("EV-003", status="draft"))
    approved = store.list_by_status("approved")
    assert {c.id for c in approved} == {"EV-001", "EV-002"}
    assert len(store.list_by_status("draft")) == 1


def test_list_all_spans_statuses(tmp_path):
    store = CardStore(root=tmp_path)
    store.save(_card("EV-001", status="approved"))
    store.save(_card("EV-002", status="draft"))
    store.save(_card("EV-003", status="rejected"))
    assert {c.id for c in store.list_all()} == {"EV-001", "EV-002", "EV-003"}


def test_set_status_moves_file(tmp_path):
    store = CardStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    store.set_status("EV-001", "approved", reviewed_by="xuyi")
    assert not (tmp_path / "draft" / "EV-001.md").exists()
    assert (tmp_path / "approved" / "EV-001.md").exists()
    reloaded = store.load("EV-001")
    assert reloaded.status == "approved"
    assert reloaded.reviewed_by == "xuyi"


def test_set_status_invalid_raises(tmp_path):
    store = CardStore(root=tmp_path)
    store.save(_card("EV-001"))
    with pytest.raises(ValueError):
        store.set_status("EV-001", "bogus")


def test_load_missing_raises_keyerror(tmp_path):
    store = CardStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.load("EV-999")


def test_next_id_sequence(tmp_path):
    store = CardStore(root=tmp_path)
    assert store.next_id() == "EV-001"
    store.save(_card("EV-001"))
    store.save(_card("EV-012", status="draft"))
    assert store.next_id() == "EV-013"


def test_root_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_WISDOM_ROOT", str(tmp_path / "wroot"))
    store = CardStore()
    assert store.root == tmp_path / "wroot"
    assert (tmp_path / "wroot" / "draft").is_dir()


def test_default_root_falls_back_to_repo_data(monkeypatch):
    # 无 env 时默认 guanlan-v2/.data/wisdom(仅校验路径, 不实例化以免在仓库里建目录)
    monkeypatch.delenv("GUANLAN_WISDOM_ROOT", raising=False)
    root = _default_root()
    assert root.name == "wisdom"
    assert root.parent.name == ".data"
