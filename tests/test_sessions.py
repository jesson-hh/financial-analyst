import pytest
from pathlib import Path
from financial_analyst.sessions import SessionManager, SessionEvent, DEFAULT_SESSION


def test_session_manager_creates_default(tmp_path):
    mgr = SessionManager(root=tmp_path)
    assert mgr.active_name == "default"
    assert (tmp_path / "default").exists()
    assert (tmp_path / "default" / "meta.json").exists()


def test_create_new_session(tmp_path):
    mgr = SessionManager(root=tmp_path)
    meta = mgr.create("project-a", description="my project")
    assert meta.name == "project-a"
    assert (tmp_path / "project-a" / "log.jsonl").exists()


def test_create_duplicate_raises(tmp_path):
    mgr = SessionManager(root=tmp_path)
    mgr.create("foo")
    with pytest.raises(ValueError):
        mgr.create("foo")


def test_switch_session(tmp_path):
    mgr = SessionManager(root=tmp_path)
    mgr.create("foo")
    mgr.switch("foo")
    assert mgr.active_name == "foo"


def test_switch_to_nonexistent_creates_it(tmp_path):
    """_ensure_session is called inside switch, so missing session auto-creates."""
    mgr = SessionManager(root=tmp_path)
    mgr.switch("brand-new")
    assert mgr.active_name == "brand-new"
    assert (tmp_path / "brand-new").exists()


def test_delete_session(tmp_path):
    mgr = SessionManager(root=tmp_path)
    mgr.create("foo")
    mgr.delete("foo")
    assert not (tmp_path / "foo").exists()


def test_delete_default_raises(tmp_path):
    mgr = SessionManager(root=tmp_path)
    with pytest.raises(ValueError, match="default"):
        mgr.delete("default")


def test_append_and_history(tmp_path):
    mgr = SessionManager(root=tmp_path)
    mgr.append(SessionEvent(kind="ask", input="hello"))
    mgr.append(SessionEvent(kind="report", input="SH600519"))
    events = mgr.history()
    assert len(events) == 2
    assert events[0].input == "hello"
    assert events[1].kind == "report"


def test_meta_n_messages_increments(tmp_path):
    mgr = SessionManager(root=tmp_path)
    mgr.append(SessionEvent(kind="ask", input="x"))
    mgr.append(SessionEvent(kind="ask", input="y"))
    metas = mgr.list()
    default_meta = next(m for m in metas if m.name == "default")
    assert default_meta.n_messages == 2


def test_list_returns_all_sessions(tmp_path):
    mgr = SessionManager(root=tmp_path)
    mgr.create("a")
    mgr.create("b")
    metas = mgr.list()
    names = [m.name for m in metas]
    assert "default" in names
    assert "a" in names
    assert "b" in names


def test_delete_nonexistent_raises(tmp_path):
    mgr = SessionManager(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        mgr.delete("nonexistent")
