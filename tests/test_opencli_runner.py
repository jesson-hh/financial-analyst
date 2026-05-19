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
    """runner now reads raw bytes from subprocess, decodes utf-8 itself."""
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.is_opencli_available",
        lambda: True,
    )
    fake_proc = MagicMock(returncode=0, stdout=b'[{"a": 1}, {"a": 2}]', stderr=b"")
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
                          stdout=b'(node:36780) warning\n[{"a": 1}]', stderr=b"")
    with patch("subprocess.run", return_value=fake_proc):
        result = run_opencli("eastmoney", "kuaixun")
    assert result == [{"a": 1}]


def test_run_opencli_nonzero_raises(monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.is_opencli_available",
        lambda: True,
    )
    fake_proc = MagicMock(returncode=2, stdout=b"", stderr=b"bad command")
    with patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(RuntimeError, match="exit 2"):
            run_opencli("bogus")


def test_run_opencli_decodes_utf8_chinese(monkeypatch):
    """Regression: cmd.exe shell=True used to mangle utf-8 → GBK. We now read
    bytes directly so Chinese characters round-trip correctly."""
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.runner.is_opencli_available",
        lambda: True,
    )
    payload = '[{"title": "影视院线概念震荡拉升", "summary": "幸福蓝海涨超14%"}]'
    fake_proc = MagicMock(returncode=0, stdout=payload.encode("utf-8"), stderr=b"")
    with patch("subprocess.run", return_value=fake_proc):
        result = run_opencli("eastmoney", "kuaixun")
    assert result[0]["title"] == "影视院线概念震荡拉升"
    assert result[0]["summary"] == "幸福蓝海涨超14%"


def test_resolve_npm_shim_parses_main_js(tmp_path):
    """The .CMD shim parser should extract the main.js path from a real
    npm-generated wrapper so we can call node directly and avoid cmd.exe
    transcoding stdout under a GBK console code page."""
    from financial_analyst.data.collectors.opencli.runner import _resolve_npm_shim

    main_js = tmp_path / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
    main_js.parent.mkdir(parents=True)
    main_js.write_text("// stub\n", encoding="utf-8")

    cmd = tmp_path / "opencli.CMD"
    cmd.write_text(
        '@ECHO off\r\n'
        'GOTO start\r\n'
        ':find_dp0\r\n'
        'SET dp0=%~dp0\r\n'
        'EXIT /b\r\n'
        ':start\r\n'
        'SETLOCAL\r\n'
        'CALL :find_dp0\r\n'
        'IF EXIST "%dp0%\\node.exe" (\r\n'
        '  SET "_prog=%dp0%\\node.exe"\r\n'
        ') ELSE (\r\n'
        '  SET "_prog=node"\r\n'
        '  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n'
        ')\r\n'
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
        '"%_prog%"  "%dp0%\\node_modules\\@jackwener\\opencli\\dist\\src\\main.js" %*\r\n',
        encoding="utf-8",
    )

    result = _resolve_npm_shim(str(cmd))
    assert result is not None
    node_exe, js_path = result
    assert js_path.endswith("main.js")
    assert "@jackwener" in js_path
    # node_exe should at least be a string ("node" if not on PATH, else absolute path)
    assert isinstance(node_exe, str) and node_exe
