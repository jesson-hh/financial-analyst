from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path

from financial_analyst.data import f10_corpus
from financial_analyst.data.updaters.f10 import write_f10_acquisition_receipt
from financial_analyst.data.trusted_receipts import (
    F10_ACQUISITION_DOMAIN,
    KEY_ENV,
    KEY_ID_ENV,
    VERIFY_KEYS_ENV,
    sign_payload,
    verify_payload,
)


_TEST_KEY_ID = "pit-test-key-v1"
_TEST_KEY = "test-only-f10-pit-hmac-key-material-32-bytes-minimum"


def _configure_trust(monkeypatch, *, key: str = _TEST_KEY, key_id: str = _TEST_KEY_ID):
    monkeypatch.setenv(KEY_ID_ENV, key_id)
    monkeypatch.setenv(KEY_ENV, key)


def _snapshot_path(root: Path, day: date) -> Path:
    directory = root / "sh600000"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"最新提示_{day.strftime('%Y%m%d')}.txt"


def _seal_acquisition(path: Path, *, pulled_at: str, backdate_files: bool = True) -> Path:
    receipt_path = path.with_suffix(path.suffix + ".receipt.json")
    content_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {
        "schema_version": "f10-acquisition-receipt/v1",
        "receipt_id": f"fixture-{content_sha[:16]}",
        "source": "f10:最新提示",
        "code": "SH600000",
        "category": "最新提示",
        "snapshot_file": path.name,
        "pulled_at": pulled_at,
        "acquired_at": pulled_at,
        "snapshot_version": f"fixture:{content_sha}",
        "snapshot_asof": pulled_at[:10],
        "snapshot_immutable": True,
        "content_sha256": content_sha,
    }
    auth = sign_payload(F10_ACQUISITION_DOMAIN, receipt)
    if auth is not None:
        receipt.update(auth)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    if backdate_files:
        stamp = datetime.fromisoformat(pulled_at.replace("Z", "+00:00")).timestamp()
        os.utime(path, (stamp, stamp))
        os.utime(receipt_path, (stamp, stamp))
    return receipt_path


def test_future_only_inventory_is_indistinguishable_from_absent(tmp_path):
    asof = (date.today() - timedelta(days=2)).isoformat()
    absent = f10_corpus.load_facts("SH600000", asof=asof, root=tmp_path / "absent").to_dict()
    future_root = tmp_path / "future"
    _snapshot_path(future_root, date.today() + timedelta(days=1)).write_text(
        "future secret", encoding="utf-8",
    )
    future = f10_corpus.load_facts("SH600000", asof=asof, root=future_root).to_dict()
    assert future == absent


def test_only_latest_visible_snapshot_is_read_and_receipted(monkeypatch, tmp_path):
    _configure_trust(monkeypatch)
    asof_day = date.today() - timedelta(days=2)
    old_day = asof_day - timedelta(days=1)
    future_day = asof_day + timedelta(days=2)
    old = _snapshot_path(tmp_path, old_day)
    future = _snapshot_path(tmp_path, future_day)
    old.write_text("最新主要指标｜2025-12-31\n每股收益｜1.0", encoding="utf-8")
    future.write_text("future secret", encoding="utf-8")
    _seal_acquisition(
        old, pulled_at=f"{asof_day.isoformat()}T15:00:00+08:00",
    )

    opened = []
    original = Path.read_bytes

    def recording_read(path):
        opened.append(path.name)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", recording_read)
    facts = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )
    assert opened == [old.name]
    receipt = next(item for item in facts.snapshot_receipts
                   if item["source"] == "f10:最新提示")
    assert receipt["point_in_time"] is True
    assert receipt["snapshot_immutable"] is True
    assert receipt["snapshot_version"]
    assert receipt["content_sha256"]
    assert "snapshot_path" not in receipt
    assert receipt["snapshot_file"] == old.name
    assert str(tmp_path) not in json.dumps(receipt, ensure_ascii=False)
    assert future.name not in opened


def test_dated_filename_without_prior_acquisition_receipt_is_unavailable(tmp_path):
    asof_day = date.today() - timedelta(days=2)
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜9.9", encoding="utf-8")

    facts = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )

    assert facts.valuation is None
    assert not any(item.get("historical_version_proven") is True
                   for item in facts.snapshot_receipts)


def test_sidecar_created_today_cannot_backdate_an_old_filename(monkeypatch, tmp_path):
    _configure_trust(monkeypatch)
    asof_day = date.today() - timedelta(days=2)
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜9.9", encoding="utf-8")
    _seal_acquisition(
        path,
        pulled_at=f"{asof_day.isoformat()}T15:00:00+08:00",
        backdate_files=False,
    )

    facts = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )

    assert facts.valuation is None
    assert not any(item.get("historical_version_proven") is True
                   for item in facts.snapshot_receipts)


def test_backdated_unsigned_self_receipt_is_not_a_trust_anchor(monkeypatch, tmp_path):
    monkeypatch.delenv(KEY_ID_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    asof_day = date.today() - timedelta(days=2)
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜9.9", encoding="utf-8")
    _seal_acquisition(
        path, pulled_at=f"{asof_day.isoformat()}T15:00:00+08:00",
    )
    _configure_trust(monkeypatch)

    facts = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )

    assert facts.valuation is None
    assert not any(item.get("historical_version_proven") is True
                   for item in facts.snapshot_receipts)


def test_signed_receipt_requires_matching_external_key(monkeypatch, tmp_path):
    asof_day = date.today() - timedelta(days=2)
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜1.0", encoding="utf-8")
    _configure_trust(monkeypatch)
    _seal_acquisition(
        path, pulled_at=f"{asof_day.isoformat()}T15:00:00+08:00",
    )

    monkeypatch.setenv(KEY_ENV, "different-test-key-material-that-is-at-least-32-bytes")
    wrong_key = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )
    assert wrong_key.valuation is None

    monkeypatch.delenv(KEY_ENV)
    no_key = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )
    assert no_key.valuation is None


def test_retired_verification_key_keeps_old_signed_snapshot_readable(
    monkeypatch, tmp_path,
):
    asof_day = date.today() - timedelta(days=2)
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜1.0", encoding="utf-8")
    _configure_trust(monkeypatch, key_id="pit-old-key")
    _seal_acquisition(
        path, pulled_at=f"{asof_day.isoformat()}T15:00:00+08:00",
    )

    _configure_trust(
        monkeypatch,
        key="new-active-test-key-material-that-is-at-least-32-bytes",
        key_id="pit-new-key",
    )
    monkeypatch.setenv(VERIFY_KEYS_ENV, json.dumps({"pit-old-key": _TEST_KEY}))
    facts = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )

    assert facts.valuation is not None
    assert any(item.get("historical_version_proven") is True
               for item in facts.snapshot_receipts)


def test_signed_receipt_rejects_payload_tamper_even_with_backdated_mtime(
    monkeypatch, tmp_path,
):
    asof_day = date.today() - timedelta(days=2)
    pulled_at = f"{asof_day.isoformat()}T15:00:00+08:00"
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜1.0", encoding="utf-8")
    _configure_trust(monkeypatch)
    receipt_path = _seal_acquisition(path, pulled_at=pulled_at)
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜999.0", encoding="utf-8")
    stamp = datetime.fromisoformat(pulled_at).timestamp()
    os.utime(path, (stamp, stamp))
    os.utime(receipt_path, (stamp, stamp))

    facts = f10_corpus.load_facts(
        "SH600000", asof=asof_day.isoformat(), root=tmp_path,
    )
    assert facts.valuation is None


def test_production_updater_writes_receipt_only_with_external_signing_key(
    monkeypatch, tmp_path,
):
    asof_day = date.today() - timedelta(days=2)
    pulled_at = f"{asof_day.isoformat()}T15:00:00+08:00"
    path = _snapshot_path(tmp_path, asof_day - timedelta(days=1))
    path.write_text("最新主要指标｜2025-12-31\n每股收益｜1.0", encoding="utf-8")
    monkeypatch.delenv(KEY_ID_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert write_f10_acquisition_receipt(
        path, code="SH600000", category="最新提示", pulled_at=pulled_at,
    ) is None

    _configure_trust(monkeypatch)
    sidecar = write_f10_acquisition_receipt(
        path, code="SH600000", category="最新提示", pulled_at=pulled_at,
    )
    assert sidecar is not None
    receipt = json.loads(sidecar.read_text(encoding="utf-8"))
    assert receipt["pulled_at"].startswith(asof_day.isoformat())
    assert receipt["acquired_at"].startswith(date.today().isoformat())
    auth = {name: receipt.pop(name) for name in ("algorithm", "key_id", "signature")}
    assert verify_payload(F10_ACQUISITION_DOMAIN, receipt, auth) is True
