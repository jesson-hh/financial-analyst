import json
import pytest
from unittest.mock import patch, MagicMock
from financial_analyst.data.collectors.opencli.runner import (
    run_opencli, is_opencli_available,
)


def test_is_opencli_available_returns_bool():
    # We don't care if it's installed in CI — just that it returns a bool
    assert isinstance(is_opencli_available(), bool)


def test_run_opencli_missing_raises(monkeypatch):
    """When shutil.which finds no opencli, run_opencli must raise RuntimeError."""
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.shutil.which",
        lambda name: None,
    )
    with pytest.raises(RuntimeError, match="not found"):
        run_opencli("eastmoney", "kuaixun")


def test_run_opencli_parses_json(monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.is_opencli_available",
        lambda: True,
    )
    fake_proc = MagicMock(returncode=0, stdout='[{"a": 1}, {"a": 2}]', stderr="")
    with patch("subprocess.run", return_value=fake_proc):
        result = run_opencli("eastmoney", "kuaixun")
    assert result == [{"a": 1}, {"a": 2}]


def test_run_opencli_strips_prefix(monkeypatch):
    """Some opencli output starts with undici warnings — strip them."""
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.is_opencli_available",
        lambda: True,
    )
    fake_proc = MagicMock(returncode=0,
                          stdout='(node:36780) warning\n[{"a": 1}]', stderr="")
    with patch("subprocess.run", return_value=fake_proc):
        result = run_opencli("eastmoney", "kuaixun")
    assert result == [{"a": 1}]


def test_run_opencli_nonzero_raises(monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.is_opencli_available",
        lambda: True,
    )
    fake_proc = MagicMock(returncode=2, stdout="", stderr="bad command")
    with patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(RuntimeError, match="exit 2"):
            run_opencli("bogus")
