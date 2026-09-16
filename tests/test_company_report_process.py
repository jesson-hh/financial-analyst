from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

from guanlan_v2.research import company_report_process as process


def test_report_process_uses_job_owned_out_dir_and_v2_receipt(
    tmp_path, monkeypatch,
):
    pack = tmp_path / "pack.json"
    pack.write_text("{}", encoding="utf-8")
    result_path = tmp_path / "job" / "result.json"
    out_dir = tmp_path / "job" / "report_out"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        selected = command[command.index("--out-dir") + 1]
        assert os.path.normcase(selected) == os.path.normcase(str(out_dir.resolve()))
        report = out_dir / "SH603986_2026-08-30.md"
        report.write_text("# isolated\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(process.subprocess, "run", run)
    result = process.run_report_process(
        job_id="bg_3434343434",
        code="SH603986",
        asof="2026-08-30",
        evidence_pack=pack,
        result_path=result_path,
        out_dir=out_dir,
        request_sha256="a" * 64,
    )

    assert result["ok"] is True, result
    assert result["schema_version"] == "company-report-process-result@2"
    assert result["report_path"].startswith(str(out_dir.resolve()))
    assert result["report_out_dir_sha256"] == hashlib.sha256(
        os.path.normcase(str(out_dir.resolve())).encode("utf-8")
    ).hexdigest()
    assert result_path.is_file()
    assert len(calls) == 1


def test_report_process_refuses_ambiguous_fresh_artifacts(tmp_path):
    one = tmp_path / "SH603986_one.md"
    two = tmp_path / "SH603986_two.md"
    one.write_text("one", encoding="utf-8")
    two.write_text("two", encoding="utf-8")

    with pytest.raises(RuntimeError, match="multiple fresh"):
        process._freshest_report(tmp_path, "SH603986", 0)
