# tests/test_quote_fetcher_f10.py
import asyncio
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime

_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd  # noqa: E402
from financial_analyst.agent.tier1 import quote_fetcher as qf  # noqa: E402
from financial_analyst.data import f10_corpus as fc  # noqa: E402
from financial_analyst.data.trusted_receipts import (  # noqa: E402
    F10_ACQUISITION_DOMAIN,
    KEY_ENV,
    KEY_ID_ENV,
    sign_payload,
)


class FakeLoader:
    def fetch_quote(self, code, start, end):
        idx = pd.date_range(end=pd.Timestamp(end), periods=70, freq="D")
        frame = pd.DataFrame({"close": [6.0] * 70, "vol": [1e6] * 70}, index=idx)
        frame.attrs["pulled_at"] = f"{end}T15:00:00"
        return frame

    def fetch_daily_basic(self, code, start, end):
        return pd.DataFrame()   # 空 -> 触发 F10 兜底


def test_quote_fetcher_uses_f10_when_daily_basic_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ID_ENV, "pit-test-key-v1")
    monkeypatch.setenv(
        KEY_ENV, "test-only-f10-pit-hmac-key-material-32-bytes-minimum",
    )
    source = (
        pathlib.Path(__file__).resolve().parents[0]
        / "fixtures" / "f10" / "sz000630" / "最新提示_20260601.txt"
    )
    fixt = tmp_path / "f10"
    target_dir = fixt / "sz000630"
    target_dir.mkdir(parents=True)
    target = target_dir / source.name
    target.write_bytes(source.read_bytes())
    content_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    pulled_at = "2026-06-01T15:00:00+08:00"
    receipt = target.with_suffix(target.suffix + ".receipt.json")
    receipt_payload = {
        "schema_version": "f10-acquisition-receipt/v1",
        "receipt_id": f"fixture-{content_sha[:16]}",
        "source": "f10:最新提示",
        "code": "SZ000630",
        "category": "最新提示",
        "snapshot_file": target.name,
        "pulled_at": pulled_at,
        "acquired_at": pulled_at,
        "snapshot_version": f"fixture:{content_sha}",
        "snapshot_asof": "2026-06-01",
        "snapshot_immutable": True,
        "content_sha256": content_sha,
    }
    receipt_payload.update(
        sign_payload(F10_ACQUISITION_DOMAIN, receipt_payload) or {},
    )
    receipt.write_text(
        json.dumps(receipt_payload, ensure_ascii=False), encoding="utf-8",
    )
    stamp = datetime.fromisoformat(pulled_at).timestamp()
    os.utime(target, (stamp, stamp))
    os.utime(receipt, (stamp, stamp))
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)
    agent = qf.QuoteFetcher(memory_root=tmp_path, loader=FakeLoader())
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    # 总股本 13409470000 股 × 6.0 元 / 1e8 = 804.5682 亿
    # (注:计划注释 "8045.7" 系 10x 笔误;134.0947亿股=1.34e10,×6.0/1e8=804.57亿)
    assert out["mv_yi"] is not None and round(out["mv_yi"], 1) == 804.6
    assert out["pb"] is not None and round(out["pb"], 3) == round(6.0 / 2.7954, 3)
    # f10_valuation 透传,供下游真营收/净利/ROE
    assert out["f10_valuation"] is not None
    assert out["f10_valuation"]["total_shares"] == 13409470000.0
