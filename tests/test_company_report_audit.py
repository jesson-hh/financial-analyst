from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from guanlan_v2.research import company_report_audit as audit


def test_same_stem_diagnostics_are_loaded_verbatim_with_hash_receipts(tmp_path):
    report = tmp_path / "SH600667.md"
    draft = b'{"status":"incomplete","findings":["missing citation"]}\n'
    sidecar = '{"company_risk_input":{},"note":"原始诊断"}\n'.encode("utf-8")
    report.with_suffix(".draft-review.json").write_bytes(draft)
    report.with_suffix(".json").write_bytes(sidecar)

    result = audit.load_company_report_diagnostics(report)

    assert result["files"] == {
        "writer_draft_review.json": draft, "report_input.json": sidecar,
    }
    for name, raw in result["files"].items():
        receipt = result["receipts"][name]
        assert receipt["status"] == "loaded"
        assert receipt["diagnostic_only"] is True
        assert receipt["bytes"] == len(raw)
        assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    assert not report.exists()


def test_missing_diagnostics_have_explicit_receipts_and_create_nothing(tmp_path):
    result = audit.load_company_report_diagnostics(tmp_path / "absent.md")

    assert result["files"] == {}
    assert all(receipt["status"] == "missing" for receipt in result["receipts"].values())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw,reason", [
    (b"", "empty_file"),
    (b"\xff", "not_utf8"),
    (b'{"x":\x00}', "nul_byte"),
    (b"{", "invalid_json"),
    (b"[]", "root_not_object"),
    (b'{"x":NaN}', "invalid_json"),
])
def test_invalid_diagnostic_is_recorded_without_blocking_other_valid_file(tmp_path, raw, reason):
    report = tmp_path / "company.md"
    report.with_suffix(".json").write_bytes(raw)
    report.with_suffix(".draft-review.json").write_bytes(b"{}")

    result = audit.load_company_report_diagnostics(report)

    assert result["files"] == {"writer_draft_review.json": b"{}"}
    assert result["receipts"]["report_input.json"]["status"] == "invalid"
    assert result["receipts"]["report_input.json"]["reason_code"] == reason


def test_oversized_diagnostic_is_rejected_before_open(tmp_path, monkeypatch):
    report = tmp_path / "company.md"
    report.with_suffix(".json").write_bytes(b'{"long":"1234567890"}')

    def do_not_open(*args, **kwargs):
        pytest.fail("oversized diagnostic must not be opened")

    monkeypatch.setattr(audit.os, "open", do_not_open)
    result = audit.load_company_report_diagnostics(report, max_bytes=8)
    assert result["files"] == {}
    assert result["receipts"]["report_input.json"]["reason_code"] == "too_large"


def test_does_not_follow_model_authored_artifact_paths(tmp_path, monkeypatch):
    report = tmp_path / "company.md"
    foreign = tmp_path / "unrelated-secret.json"
    foreign.write_text('{"secret":"must not be read"}', encoding="utf-8")
    body = json.dumps({
        "artifact_path": str(foreign),
        "draft_review_path": str(foreign),
        "files": {"writer_draft_review.json": str(foreign)},
    }).encode("utf-8")
    report.with_suffix(".json").write_bytes(body)
    opened = []
    real_open = audit.os.open

    def record_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(audit.os, "open", record_open)
    result = audit.load_company_report_diagnostics(report)

    assert opened == [report.with_suffix(".json")]
    assert result["files"] == {"report_input.json": body}
    assert result["receipts"]["writer_draft_review.json"]["status"] == "missing"


def test_directory_diagnostic_is_not_read(tmp_path):
    report = tmp_path / "company.md"
    report.with_suffix(".json").mkdir()

    result = audit.load_company_report_diagnostics(report)
    assert result["files"] == {}
    assert result["receipts"]["report_input.json"]["reason_code"] == "not_regular_file"


def test_reparse_marked_diagnostic_is_rejected_without_open(tmp_path, monkeypatch):
    report = tmp_path / "company.md"
    sidecar = report.with_suffix(".json")
    sidecar.write_bytes(b"{}")
    sidecar_id = sidecar.lstat().st_ino
    actual_reparse = audit._is_reparse
    monkeypatch.setattr(audit, "_is_reparse", lambda info: (
        info.st_ino == sidecar_id or actual_reparse(info)
    ))

    def do_not_open(*args, **kwargs):
        pytest.fail("reparse diagnostic must not be opened")

    monkeypatch.setattr(audit.os, "open", do_not_open)
    result = audit.load_company_report_diagnostics(report)
    assert result["files"] == {}
    assert result["receipts"]["report_input.json"]["reason_code"] == "reparse_point"


@pytest.mark.parametrize("limit", [0, -1, True, "9MiB"])
def test_invalid_read_limit_fails_closed_without_raising(tmp_path, limit):
    result = audit.load_company_report_diagnostics(tmp_path / "company.md", max_bytes=limit)
    assert result["files"] == {}
    assert all(
        row["reason_code"] == "invalid_read_limit" for row in result["receipts"].values()
    )
