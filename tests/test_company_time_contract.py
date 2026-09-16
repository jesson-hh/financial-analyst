from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from guanlan_v2.research.company_workspace import (
    build_company_time_contract,
    build_workspace_company_artifacts,
)


ASOF = date.today().isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def test_time_contract_accepts_only_provider_attested_quote_date():
    quote = {
        "price": 17.22,
        "market_data_as_of": ASOF,
        "retrieved_at": f"{ASOF}T13:10:34+08:00",
        "market_data_attestation": {
            "market_data_as_of": ASOF,
            "source": "tdx_kline",
            "validation": "latest_exact_code_daily_close_equals_quote",
        },
    }
    result = build_company_time_contract({
        "information_cutoff": f"{ASOF}T13:00:00+08:00",
        "sections": {"quote_live": quote},
    }, research_asof=ASOF)

    assert result == {
        "research_asof": ASOF,
        "market_data_asof": ASOF,
        "market_data_source_ref": "pack.sections.quote_live.market_data_as_of",
        "retrieved_at": f"{ASOF}T13:10:34+08:00",
        "information_cutoff": f"{ASOF}T13:00:00+08:00",
        "status": "complete",
    }


def test_time_contract_accepts_content_bound_quote_receipt_date():
    market_day = (date.today() - timedelta(days=1)).isoformat()
    quote = {
        "price": 17.22,
        "retrieved_at": f"{ASOF}T09:15:00+08:00",
    }
    receipt = {
        "source": "quote_live",
        "status": "available",
        "requested_asof": ASOF,
        "data_asof": market_day,
        "pulled_at": f"{ASOF}T09:16:00+08:00",
        "content_sha256": _digest(quote),
    }

    result = build_company_time_contract({
        "sections": {"quote_live": quote},
        "receipts": {"quote_live": receipt},
    }, research_asof=ASOF)

    assert result["market_data_asof"] == market_day
    assert result["market_data_source_ref"] == \
        "pack.receipts.quote_live.data_asof"
    assert result["retrieved_at"] == f"{ASOF}T09:15:00+08:00"
    assert result["information_cutoff"] is None
    assert result["status"] == "partial"


def test_time_contract_never_substitutes_research_date_for_missing_lineage():
    result = build_company_time_contract({
        "asof": ASOF,
        "cutoff_as_of": ASOF,
        "generated_at": f"{ASOF}T14:00:00+08:00",
        "sections": {
            "quote_live": {
                "price": 17.22,
                "as_of": ASOF,
                "retrieved_at": f"{ASOF}T13:10:34+08:00",
            },
        },
    }, research_asof=ASOF)

    assert result["research_asof"] == ASOF
    assert result["market_data_asof"] is None
    assert result["market_data_source_ref"] is None
    assert result["information_cutoff"] is None
    assert result["status"] == "incomplete"


def test_time_contract_fails_closed_when_two_verified_market_dates_disagree():
    receipt_day = (date.today() - timedelta(days=1)).isoformat()
    quote = {
        "price": 17.22,
        "market_data_as_of": ASOF,
        "retrieved_at": f"{ASOF}T13:10:34+08:00",
        "market_data_attestation": {
            "market_data_as_of": ASOF,
            "source": "tdx_kline",
            "validation": "latest_exact_code_daily_close_equals_quote",
        },
    }
    receipt = {
        "source": "quote_live",
        "status": "available",
        "requested_asof": ASOF,
        "data_asof": receipt_day,
        "content_sha256": _digest(quote),
    }

    result = build_company_time_contract({
        "sections": {"quote_live": quote},
        "receipts": {"quote_live": receipt},
        # A date-only value is not a proven information *timestamp*.
        "information_cutoff": ASOF,
    }, research_asof=ASOF)

    assert result["market_data_asof"] is None
    assert result["market_data_source_ref"] is None
    assert result["information_cutoff"] is None
    assert result["status"] == "incomplete"


def test_prepared_artifacts_share_one_top_level_time_contract():
    quote = {
        "price": 17.22,
        "market_data_as_of": ASOF,
        "retrieved_at": f"{ASOF}T13:10:34+08:00",
        "market_data_attestation": {
            "market_data_as_of": ASOF,
            "source": "tdx_kline",
            "validation": "latest_exact_code_daily_close_equals_quote",
        },
    }
    pack = {
        "schema_version": "company-research-evidence@2",
        "code": "SH600667",
        "information_cutoff": f"{ASOF}T13:00:00+08:00",
        "sections": {"quote_live": quote},
        "coverage": {},
        "errors": {},
    }

    artifacts = build_workspace_company_artifacts(
        pack, [],
        subject={"kind": "stock", "code": "SH600667", "name": "太极实业"},
        source_plan=[], as_of=ASOF,
    )
    expected = artifacts["manifest"]["time_contract"]

    assert expected["status"] == "complete"
    assert artifacts["evidence"]["time_contract"] == expected
    assert artifacts["engine_evidence_pack"]["time_contract"] == expected
    assert artifacts["engine_evidence_pack"]["sections"] \
        ["company_research_manifest"]["time_contract"] == expected
