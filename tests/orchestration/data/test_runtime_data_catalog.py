# -*- coding: utf-8 -*-
"""Read-only runtime data-catalog compatibility reporting."""
from __future__ import annotations

import builtins
import hashlib
import json
import subprocess
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from guanlan_v2.datafeed.capability_catalog import build_data_capability_manifest
from guanlan_v2.orchestration.data.runtime_catalog import (
    ReviewedAdapterSupport,
    ReviewedDataBinding,
    build_runtime_data_catalog_report,
    render_runtime_data_catalog_support,
    reviewed_live_client_adapter_support,
    reviewed_live_client_bindings,
    runtime_data_catalog_support_summary,
)


_CONTRACT_A = "a" * 64
_CONTRACT_B = "b" * 64
_SCHEMA_A = "c" * 64
_SCHEMA_B = "d" * 64


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _dataset(
    *,
    dataset_id: str = "market.quote",
    source_ids: tuple[str, ...] = ("tencent_realtime_quote",),
    contract_digest: str = _CONTRACT_A,
    read_only: bool = True,
    schema_status: str = "unpublished",
    schema_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "live_source_ids": list(source_ids),
        "contract_digest": contract_digest,
        "read_only": read_only,
        "result_schema_status": schema_status,
        "result_schema_digest": schema_digest,
    }


def _source(
    source_id: str = "tencent_realtime_quote",
    *,
    dataset_id: str = "market.quote",
    dataset_ids: tuple[str, ...] | None = None,
    implementation_status: str = "implemented",
    provider: str | None = None,
) -> dict[str, Any]:
    if provider is None:
        provider = (
            "tencent" if source_id.startswith("tencent_")
            else "tdx" if source_id.startswith("tdx_")
            else "eastmoney" if source_id.startswith("eastmoney_")
            else "unknown"
        )
    return {
        "source_id": source_id,
        "dataset_ids": list(
            dataset_ids if dataset_ids is not None else (dataset_id,)
        ),
        "implementation_status": implementation_status,
        "provider": provider,
    }


def _manifest(
    *,
    datasets: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    ok: bool = True,
    authoritative: bool = True,
    stale: bool = False,
) -> dict[str, Any]:
    datasets = datasets if datasets is not None else [_dataset()]
    sources = sources if sources is not None else [_source()]
    semantic = {
        "schema_version": "guanlan.data-capability-manifest.v1",
        "datasets": datasets,
        "sources": sources,
    }
    return {
        "ok": ok,
        "authoritative": authoritative,
        "stale": stale,
        **semantic,
        "catalog_digest": _digest(semantic),
    }


def _binding(
    *,
    schema_digest: str | None = None,
    method_id: str = "verified_snapshot",
    source_ids: tuple[str, ...] = ("tencent_realtime_quote",),
) -> ReviewedDataBinding:
    return ReviewedDataBinding.build(
        dataset_id="market.quote",
        method_id=method_id,
        source_ids=source_ids,
        contract_digest=_CONTRACT_A,
        result_schema_digest=schema_digest,
    )


def _support(
    *,
    source_id: str,
    method_id: str = "verified_snapshot",
    provider: str,
    schema_digest: str | None = None,
) -> ReviewedAdapterSupport:
    return ReviewedAdapterSupport(
        dataset_id="market.quote",
        method_id=method_id,
        source_id=source_id,
        adapter_id="guanlan.live-client-source",
        adapter_version="1",
        provider_id=provider,
        adapter_contract_digest=_digest({
            "reviewed_test_adapter": "live-client-source",
            "method_id": method_id,
            "source_id": source_id,
            "provider": provider,
        }),
        result_schema_digest=schema_digest,
    )


def _by_source(report: Any, source_id: str) -> Any:
    return next(row for row in report.findings if row.source_id == source_id)


def _by_source_method(report: Any, source_id: str, method_id: str) -> Any:
    return next(
        row for row in report.findings
        if row.source_id == source_id and row.method_id == method_id
    )


def test_exact_reviewed_binding_does_not_require_external_schema_identity() -> None:
    report = build_runtime_data_catalog_report(
        _manifest(), reviewed_bindings=(_binding(),)
    )

    finding = _by_source(report, "tencent_realtime_quote")
    assert finding.classification == "reviewed_current_binding"
    assert finding.method_id == "verified_snapshot"
    assert finding.adapter_id == "guanlan.live-client-source"
    assert finding.adapter_version == "1"
    assert finding.adapter_support_digest is not None
    assert report.count("reviewed_current_binding") == 1


def test_current_binding_without_exact_adapter_attestation_requires_review() -> None:
    report = build_runtime_data_catalog_report(
        _manifest(),
        reviewed_bindings=(_binding(),),
        reviewed_adapter_support=(),
    )

    finding = _by_source(report, "tencent_realtime_quote")
    assert finding.classification == "requires_review"
    assert finding.reason_codes == ("adapter_support_attestation_missing",)
    assert finding.adapter_support_digest is None
    assert report.count("reviewed_current_binding") == 0


def test_accepts_the_datafeed_capability_catalog_manifest_shape() -> None:
    dataset_catalog = {
        "ok": True,
        "items": [{
            "dataset_id": "market.quote",
            "category": "market",
            "live_source_ids": ["tencent_realtime_quote", "eastmoney_quote"],
            "source_priority": ["tencent", "eastmoney"],
            "query_fields": ["code", "as_of"],
            "natural_keys": ["instrument", "trade_date"],
            "date_field": "trade_date",
            "visible_field": "visible_ts",
            "visible_ts_policy": "fetch_time",
            "bridge_status": "query_bridge_ready",
            "write_enabled": False,
            "canonical_write": False,
        }],
    }
    source_catalog = {
        "ok": True,
        "sources": [
            {
                "source_id": "tencent_realtime_quote",
                "provider": "tencent",
                "implementation_status": "implemented",
            },
            {
                "source_id": "eastmoney_quote",
                "provider": "eastmoney",
                "implementation_status": "implemented",
            },
        ],
    }
    manifest = build_data_capability_manifest(dataset_catalog, source_catalog)
    binding = ReviewedDataBinding.build(
        dataset_id="market.quote",
        method_id="verified_snapshot",
        source_ids=("tencent_realtime_quote",),
        contract_digest=manifest["datasets"][0]["contract_digest"],
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(binding,)
    )

    assert _by_source(report, "tencent_realtime_quote").classification == (
        "reviewed_current_binding"
    )
    assert _by_source(report, "eastmoney_quote").classification == (
        "requires_review"
    )


def test_one_source_can_canonically_belong_to_multiple_datasets() -> None:
    manifest = _manifest(
        datasets=[
            _dataset(source_ids=("shared_market",)),
            _dataset(
                dataset_id="market.bar",
                source_ids=("shared_market",),
                contract_digest=_CONTRACT_B,
            ),
        ],
        sources=[_source(
            "shared_market",
            dataset_ids=("market.bar", "market.quote"),
        )],
    )

    report = build_runtime_data_catalog_report(manifest)

    assert {
        (finding.dataset_id, finding.source_id, finding.classification)
        for finding in report.findings
    } == {
        ("market.bar", "shared_market", "requires_contract"),
        ("market.quote", "shared_market", "requires_contract"),
    }


def test_legacy_singular_source_dataset_id_is_accepted_during_transition() -> None:
    legacy_source = {
        "source_id": "tencent_realtime_quote",
        "dataset_id": "market.quote",
        "implementation_status": "implemented",
        "provider": "tencent",
    }

    report = build_runtime_data_catalog_report(
        _manifest(sources=[legacy_source]),
        reviewed_bindings=(_binding(),),
    )

    assert _by_source(report, "tencent_realtime_quote").classification == (
        "reviewed_current_binding"
    )


def test_source_must_list_every_dataset_that_declares_it() -> None:
    manifest = _manifest(sources=[_source(
        "tencent_realtime_quote", dataset_ids=("market.bar",)
    )])

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    assert report.findings[0].classification == "unsupported"
    assert report.findings[0].reason_codes == ("manifest_shape_invalid",)


@pytest.mark.parametrize(
    ("schema_status", "schema_digest"),
    [
        ("unpublished", None),
        ("published", _SCHEMA_A),
    ],
)
def test_two_methods_on_one_dataset_are_classified_independently(
    schema_status: str,
    schema_digest: str | None,
) -> None:
    manifest = _manifest(
        datasets=[_dataset(
            source_ids=("tencent_realtime_quote", "tdx_realtime_quote"),
            schema_status=schema_status,
            schema_digest=schema_digest,
        )],
        sources=[
            _source("tencent_realtime_quote"),
            _source("tdx_realtime_quote"),
        ],
    )
    bindings = (
        _binding(
            schema_digest=schema_digest,
            method_id="verified_snapshot",
            source_ids=("tencent_realtime_quote",),
        ),
        _binding(
            schema_digest=schema_digest,
            method_id="indicators",
            source_ids=("tdx_realtime_quote",),
        ),
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=bindings
    )

    assert _by_source_method(
        report, "tencent_realtime_quote", "verified_snapshot"
    ).classification == "reviewed_current_binding"
    assert _by_source_method(
        report, "tdx_realtime_quote", "indicators"
    ).classification == "reviewed_current_binding"
    assert _by_source_method(
        report, "tdx_realtime_quote", "verified_snapshot"
    ).classification == "requires_review"
    assert _by_source_method(
        report, "tencent_realtime_quote", "indicators"
    ).classification == "requires_review"
    assert "adapter_support_attestation_missing" in _by_source_method(
        report, "tdx_realtime_quote", "verified_snapshot"
    ).reason_codes
    assert "adapter_support_attestation_missing" in _by_source_method(
        report, "tencent_realtime_quote", "indicators"
    ).reason_codes
    assert report.count("reviewed_current_binding") == 2
    assert report.count("requires_review") == 2
    assert report.count("compatible_candidate") == 0


def test_new_provider_without_schema_identity_requires_review() -> None:
    manifest = _manifest(
        datasets=[_dataset(source_ids=("tencent_realtime_quote", "eastmoney_quote"))],
        sources=[_source(), _source("eastmoney_quote")],
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    candidate = _by_source(report, "eastmoney_quote")
    assert candidate.classification == "requires_review"
    assert "result_schema_identity_unpublished" in candidate.reason_codes
    assert report.count("compatible_candidate") == 0


def test_candidate_schema_identity_cannot_replace_exact_adapter_attestation() -> None:
    manifest = _manifest(
        datasets=[_dataset(
            source_ids=("tencent_realtime_quote", "eastmoney_quote"),
            schema_status="published",
            schema_digest=_SCHEMA_A,
        )],
        sources=[_source(), _source("eastmoney_quote")],
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(schema_digest=_SCHEMA_A),)
    )

    candidate = _by_source(report, "eastmoney_quote")
    assert candidate.classification == "requires_review"
    assert "adapter_support_attestation_missing" in candidate.reason_codes
    assert report.count("compatible_candidate") == 0


def test_source_manifest_cannot_self_attest_adapter_support() -> None:
    candidate_source = _source("eastmoney_quote")
    candidate_source["adapter_support"] = {
        "adapter_id": "guanlan.live-client-source",
        "adapter_version": "1",
        "method_id": "verified_snapshot",
        "result_schema_digest": _SCHEMA_A,
        "reviewed": True,
    }
    manifest = _manifest(
        datasets=[_dataset(
            source_ids=("tencent_realtime_quote", "eastmoney_quote"),
            schema_status="published",
            schema_digest=_SCHEMA_A,
        )],
        sources=[_source(), candidate_source],
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(schema_digest=_SCHEMA_A),)
    )

    candidate = _by_source(report, "eastmoney_quote")
    assert candidate.classification == "requires_review"
    assert "adapter_support_attestation_missing" in candidate.reason_codes
    assert candidate.adapter_support_digest is None


def test_new_provider_is_compatible_only_when_schema_identity_matches() -> None:
    manifest = _manifest(
        datasets=[_dataset(
            source_ids=("tencent_realtime_quote", "eastmoney_quote"),
            schema_status="published",
            schema_digest=_SCHEMA_A,
        )],
        sources=[_source(), _source("eastmoney_quote")],
    )

    current_support = next(
        row for row in reviewed_live_client_adapter_support()
        if row.dataset_id == "market.quote"
        and row.method_id == "verified_snapshot"
    )
    report = build_runtime_data_catalog_report(
        manifest,
        reviewed_bindings=(_binding(schema_digest=_SCHEMA_A),),
        reviewed_adapter_support=(
            current_support,
            _support(
                source_id="eastmoney_quote",
                provider="eastmoney",
                schema_digest=_SCHEMA_A,
            ),
        ),
    )

    candidate = _by_source(report, "eastmoney_quote")
    assert candidate.classification == "compatible_candidate"
    assert candidate.result_schema_digest == _SCHEMA_A
    assert candidate.adapter_support_digest is not None


def test_observed_provider_must_match_exact_reviewed_attestation() -> None:
    manifest = _manifest(sources=[_source(provider="eastmoney")])

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    finding = _by_source(report, "tencent_realtime_quote")
    assert finding.classification == "requires_review"
    assert finding.reason_codes == ("adapter_support_provider_mismatch",)
    assert finding.adapter_support_digest is not None


def test_schema_identity_mismatch_requires_a_contract_change() -> None:
    manifest = _manifest(
        datasets=[_dataset(
            schema_status="published",
            schema_digest=_SCHEMA_B,
        )]
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(schema_digest=_SCHEMA_A),)
    )

    finding = _by_source(report, "tencent_realtime_quote")
    assert finding.classification == "requires_contract"
    assert finding.reason_codes == ("result_schema_identity_mismatch",)


def test_new_dataset_requires_contract_even_with_published_schema() -> None:
    manifest = _manifest(
        datasets=[_dataset(
            dataset_id="fundamentals.balance_sheet",
            source_ids=("stock_finance",),
            schema_status="published",
            schema_digest=_SCHEMA_A,
        )],
        sources=[_source(
            "stock_finance", dataset_id="fundamentals.balance_sheet"
        )],
    )

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    finding = _by_source(report, "stock_finance")
    assert finding.classification == "requires_contract"
    assert finding.method_id is None
    assert "dataset_not_mapped_to_reviewed_method" in finding.reason_codes


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"ok": False}, "manifest_not_ok"),
        ({"authoritative": False}, "manifest_not_authoritative"),
        ({"stale": True}, "manifest_stale"),
    ],
)
def test_untrusted_manifest_is_fail_closed(
    overrides: dict[str, bool], reason: str
) -> None:
    manifest = _manifest()
    manifest.update(overrides)

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    assert len(report.findings) == 1
    assert report.findings[0].classification == "unsupported"
    assert reason in report.findings[0].reason_codes


@pytest.mark.parametrize("stale", [None, 0, 1, "false", "true"])
def test_manifest_freshness_requires_exact_false(stale: Any) -> None:
    manifest = _manifest()
    manifest["stale"] = stale

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    assert report.findings[0].classification == "unsupported"
    assert "manifest_stale_invalid" in report.findings[0].reason_codes


def test_manifest_missing_freshness_is_fail_closed() -> None:
    manifest = _manifest()
    del manifest["stale"]

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    assert report.findings[0].classification == "unsupported"
    assert "manifest_stale_invalid" in report.findings[0].reason_codes


def test_catalog_digest_mismatch_is_fail_closed() -> None:
    manifest = _manifest()
    manifest["datasets"][0]["read_only"] = False

    report = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )

    assert report.findings[0].classification == "unsupported"
    assert "manifest_catalog_digest_mismatch" in report.findings[0].reason_codes


@pytest.mark.parametrize(
    ("dataset", "source", "reason"),
    [
        (_dataset(read_only=False), _source(), "write_enabled_dataset"),
        (
            _dataset(),
            _source(implementation_status="planned"),
            "source_not_implemented",
        ),
    ],
)
def test_unsupported_runtime_surfaces_are_never_candidates(
    dataset: dict[str, Any], source: dict[str, Any], reason: str
) -> None:
    report = build_runtime_data_catalog_report(
        _manifest(datasets=[dataset], sources=[source]),
        reviewed_bindings=(_binding(),),
    )

    finding = _by_source(report, "tencent_realtime_quote")
    assert finding.classification == "unsupported"
    assert reason in finding.reason_codes


def test_missing_reviewed_source_is_unsupported() -> None:
    report = build_runtime_data_catalog_report(
        _manifest(datasets=[_dataset(source_ids=())], sources=[]),
        reviewed_bindings=(_binding(),),
    )

    finding = _by_source(report, "tencent_realtime_quote")
    assert finding.classification == "unsupported"
    assert finding.reason_codes == ("reviewed_source_missing",)
    assert len(report.reviewed_adapter_support_digest) == 64


def test_report_is_frozen_canonical_and_explicitly_non_authoritative() -> None:
    report = build_runtime_data_catalog_report(
        _manifest(), reviewed_bindings=(_binding(),)
    )

    with pytest.raises(FrozenInstanceError):
        report.sealed_catalog_changed = True  # type: ignore[misc]
    assert report.sealed_catalog_changed is False
    assert report.authority_changes == ()
    assert report.to_mapping()["authority_changes"] == []
    assert tuple(key for key, _ in report.classification_counts) == (
        "reviewed_current_binding",
        "compatible_candidate",
        "requires_review",
        "requires_contract",
        "unsupported",
    )
    assert len(report.report_digest) == 64


def test_agent_support_summary_never_activates_a_compatible_candidate() -> None:
    manifest = _manifest(
        datasets=[_dataset(
            source_ids=("tencent_realtime_quote", "eastmoney_quote"),
            schema_status="published",
            schema_digest=_SCHEMA_A,
        )],
        sources=[_source(), _source("eastmoney_quote")],
    )
    current_support = next(
        row for row in reviewed_live_client_adapter_support()
        if row.dataset_id == "market.quote"
        and row.method_id == "verified_snapshot"
    )
    report = build_runtime_data_catalog_report(
        manifest,
        reviewed_bindings=(_binding(schema_digest=_SCHEMA_A),),
        reviewed_adapter_support=(
            current_support,
            _support(
                source_id="eastmoney_quote",
                provider="eastmoney",
                schema_digest=_SCHEMA_A,
            ),
        ),
    )

    summary = runtime_data_catalog_support_summary(report)
    assert summary["effect"] == "observation_only"
    assert summary["active_bindings"] == [{
        "dataset_id": "market.quote",
        "method_id": "verified_snapshot",
        "source_id": "tencent_realtime_quote",
    }]
    assert summary["compatible_candidates_not_activated"] == [{
        "dataset_id": "market.quote",
        "method_id": "verified_snapshot",
        "source_id": "eastmoney_quote",
    }]
    assert summary["sealed_catalog_changed"] is False
    assert summary["authority_changes"] == []
    rendered = render_runtime_data_catalog_support(report)
    assert "只读报告，不授予权限" in rendered
    assert "market.quote/verified_snapshot=tencent_realtime_quote" in rendered


def test_authority_looking_manifest_fields_are_ignored() -> None:
    manifest = _manifest()
    baseline = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=(_binding(),)
    )
    injected = {
        **manifest,
        "capability_ref": {"id": "invented"},
        "adapter_support": [{"source_id": "invented", "approved": True}],
        "reviewed_adapter_support": [{"source_id": "invented"}],
        "worker_grant": ["invented"],
        "prefetch": [{"invented": True}],
        "handler": "invented",
        "route": [{"invented": True}],
    }

    observed = build_runtime_data_catalog_report(
        injected, reviewed_bindings=(_binding(),)
    )

    assert observed == baseline
    serialized = observed.to_mapping()
    for forbidden in (
        "capability_ref",
        "adapter_support",
        "reviewed_adapter_support",
        "worker_grant",
        "prefetch",
        "handler",
        "route",
    ):
        assert forbidden not in serialized


def test_external_manifest_cannot_change_reviewed_live_client_baseline() -> None:
    quote_contract = (
        "b457ca0ce6ad43ea0a9cf47e168be98a6"
        "fa8ad97465fadde5c102b977e2dfdc4"
    )
    bar_contract = (
        "a3806bcdc7d765e6a85cd1f13ae42664f"
        "658e134be831c5b578acc73db0d8dec"
    )
    news_contract = (
        "ec8afaf51e68bc3d617c2b6f024373b6"
        "5d2332ee330a748d02424a58d88fe362"
    )
    baseline = reviewed_live_client_bindings()
    support_baseline = reviewed_live_client_adapter_support()
    assert tuple(
        (
            row.dataset_id,
            row.method_id,
            row.source_id,
            row.provider_id,
            row.requires_source_echo,
            row.allows_provider_fallback,
        )
        for row in support_baseline
    ) == (
        (
            "market.quote",
            "verified_snapshot",
            "tencent_realtime_quote",
            "tencent",
            True,
            False,
        ),
        (
            "market.quote",
            "indicators",
            "tdx_realtime_quote",
            "tdx",
            True,
            False,
        ),
        ("market.bar", "ohlcv", "tdx_kline", "tdx", True, False),
        (
            "text.stock_news",
            "news",
            "eastmoney_stock_news",
            "eastmoney",
            True,
            False,
        ),
    )
    assert tuple(
        (
            row.dataset_id,
            row.method_id,
            row.source_ids,
            row.contract_digest,
            row.result_schema_digest,
        )
        for row in baseline
    ) == (
        (
            "market.quote",
            "verified_snapshot",
            ("tencent_realtime_quote",),
            quote_contract,
            None,
        ),
        (
            "market.quote",
            "indicators",
            ("tdx_realtime_quote",),
            quote_contract,
            None,
        ),
        ("market.bar", "ohlcv", ("tdx_kline",), bar_contract, None),
        (
            "text.stock_news",
            "news",
            ("eastmoney_stock_news",),
            news_contract,
            None,
        ),
    )
    manifest = _manifest(
        datasets=[
            _dataset(
                source_ids=("tdx_realtime_quote", "tencent_realtime_quote"),
                contract_digest=quote_contract,
            ),
            _dataset(
                dataset_id="market.bar",
                source_ids=("tdx_kline",),
                contract_digest=bar_contract,
            ),
            _dataset(
                dataset_id="text.stock_news",
                source_ids=("eastmoney_stock_news",),
                contract_digest=news_contract,
            ),
        ],
        sources=[
            _source("tdx_realtime_quote"),
            _source("tencent_realtime_quote"),
            _source("tdx_kline", dataset_id="market.bar"),
            _source("eastmoney_stock_news", dataset_id="text.stock_news"),
        ],
    )
    clean = build_runtime_data_catalog_report(
        manifest, reviewed_bindings=baseline
    )
    injected = {
        **manifest,
        "authority_changes": ["replace reviewed bindings"],
        "reviewed_bindings": [{"method_id": "invented"}],
        "reviewed_adapter_support": [{"source_id": "invented"}],
        "worker_grant": ["invented"],
        "route": [{"invented": True}],
    }
    observed = build_runtime_data_catalog_report(
        injected, reviewed_bindings=reviewed_live_client_bindings()
    )

    assert reviewed_live_client_bindings() == baseline
    assert reviewed_live_client_adapter_support() == support_baseline
    assert observed == clean
    assert observed.sealed_catalog_changed is False
    assert observed.authority_changes == ()


def test_report_build_does_not_perform_file_or_process_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("runtime report attempted external I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    report = build_runtime_data_catalog_report(
        _manifest(), reviewed_bindings=(_binding(),)
    )

    assert report.count("reviewed_current_binding") == 1


def test_reviewed_binding_builder_canonicalises_sources() -> None:
    binding = ReviewedDataBinding.build(
        dataset_id="market.quote",
        method_id="verified_snapshot",
        source_ids=("tencent_quote", "eastmoney_quote", "tencent_quote"),
        contract_digest=_CONTRACT_A,
    )
    assert binding.source_ids == ("eastmoney_quote", "tencent_quote")

    with pytest.raises(ValueError, match="canonically sorted"):
        ReviewedDataBinding(
            dataset_id="market.quote",
            method_id="verified_snapshot",
            source_ids=("tencent_quote", "eastmoney_quote"),
            contract_digest=_CONTRACT_A,
        )


def test_reviewed_binding_identity_is_dataset_and_method() -> None:
    first = _binding(method_id="verified_snapshot")
    second = _binding(method_id="indicators")
    report = build_runtime_data_catalog_report(
        _manifest(), reviewed_bindings=(first, second)
    )
    assert {finding.method_id for finding in report.findings} == {
        "verified_snapshot",
        "indicators",
    }

    with pytest.raises(ValueError, match="dataset_id, method_id"):
        build_runtime_data_catalog_report(
            _manifest(), reviewed_bindings=(first, first)
        )


def test_manifest_must_be_a_mapping() -> None:
    with pytest.raises(TypeError, match="Mapping"):
        build_runtime_data_catalog_report([])  # type: ignore[arg-type]
