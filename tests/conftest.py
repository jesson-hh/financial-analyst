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
