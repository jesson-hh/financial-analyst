import subprocess
from pathlib import Path

import financial_analyst.buddy.tools as tools


def test_etf_report_tool_returns_md_path(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    md = out / "SH510300_2026-05-31.md"
    md.write_text("## 一、综合评级\n总评 1/10\n\n## 二、持仓\n...\n", encoding="utf-8")
    monkeypatch.setattr(tools, "_project_root", lambda: tmp_path)

    def fake_run(cmd, **kw):
        assert cmd[:3] == ["financial-analyst", "etf-report", "SH510300"]
        class P:
            returncode = 0
            stderr = ""
            stdout = ""
        return P()
    monkeypatch.setattr(subprocess, "run", fake_run)

    res = tools._tool_etf_report("SH510300")
    assert res.is_error is False
    assert res.side_effect == {"md_path": str(md)}
    assert "一、综合评级" in res.content


def test_etf_report_tool_surfaces_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_project_root", lambda: tmp_path)

    def fake_run(cmd, **kw):
        class P:
            returncode = 2
            stderr = "boom"
            stdout = ""
        return P()
    monkeypatch.setattr(subprocess, "run", fake_run)

    res = tools._tool_etf_report("SH510300")
    assert res.is_error is True
    assert "exit 2" in res.content


def test_run_etf_report_registered():
    from financial_analyst.buddy.tools import get_tool
    t = get_tool("run_etf_report")
    assert t is not None
    assert t.cost_hint == "minutes"
    assert t.confirm_required is True
