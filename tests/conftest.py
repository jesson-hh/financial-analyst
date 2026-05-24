import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture(autouse=True)
def _isolate_buddy_prefs(tmp_path, monkeypatch):
    """v1.7.5: BuddyApp persists permission_mode + model to
    ~/.financial-analyst/buddy.yaml. Redirect that to a per-test tmp file
    so tests neither read the developer's real prefs nor clobber them."""
    prefs = tmp_path / "buddy.yaml"
    try:
        from financial_analyst.buddy.app import BuddyApp
        monkeypatch.setattr(BuddyApp, "_prefs_path", staticmethod(lambda: prefs))
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _clear_net_caches():
    """v1.9.6: net.py @rate_limited 给每个 source 配了短 TTL 缓存
    (xueqiu 30s, xueqiu_hot 60s, tencent 2s). 同 session 多个 test 用同 args
    调同一 collector 会命中 cache, mock 的 fake response 不生效.
    每个 test 跑前清 source cache 保证独立."""
    try:
        from financial_analyst.data.net import _clear_all_caches
        _clear_all_caches()
    except Exception:
        pass
    yield
