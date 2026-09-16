# -*- coding: utf-8 -*-
"""Controlled activation boundary for reviewed stocks live sources."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from guanlan_v2.datafeed.capability_catalog import _dataset_projection
from guanlan_v2.orchestration.adapters import live_binding as LB
from guanlan_v2.orchestration.adapters.data_world import (
    build_production_capture,
    production_data_adapters,
    production_data_recipe,
)
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
from guanlan_v2.orchestration.data.runtime_catalog import (
    reviewed_live_client_bindings,
    reviewed_live_client_adapter_support,
)
from guanlan_v2.orchestration.digest import content_digest


def test_production_snapshot_is_single_provider_and_code_approved():
    snapshot = LB.reviewed_live_binding_snapshot()
    LB.verify_code_approved_live_binding_snapshot(snapshot)

    assert set(snapshot.method_source_map()) == {
        "verified_snapshot", "indicators", "ohlcv", "news",
    }
    assert len(snapshot.method_source_map()) == len(snapshot.bindings)
    assert all(binding.requires_exact_resolver_match is True
               for binding in snapshot.bindings)
    assert all(binding.requires_source_echo is True for binding in snapshot.bindings)
    assert all(binding.requires_strict_provenance is True
               for binding in snapshot.bindings)
    assert all(binding.requires_network_used is True
               for binding in snapshot.bindings)
    assert all(binding.requires_closed_circuit is True
               for binding in snapshot.bindings)
    assert all(
        binding.envelope_schema_version == "guanlan.live-source-result.v2"
        for binding in snapshot.bindings
    )
    assert all(binding.allows_provider_fallback is False
               for binding in snapshot.bindings)


def test_approval_pins_existing_method_specs_and_mints_no_worker_authority():
    snapshot = LB.reviewed_live_binding_snapshot()
    surface = phase3_data_surface()

    for binding in snapshot.bindings:
        assert surface.spec_by_method[binding.method_id].spec_digest == \
            binding.method_spec_digest

    # This is only a physical-source selection behind existing method ids.
    # Capability / worker / prefetch authority must remain in the sealed catalog.
    forbidden = {
        "capability_ref", "capability_allowlist", "worker_id", "skill_id",
        "prefetch", "method_schema", "result_schema",
    }
    assert forbidden.isdisjoint(snapshot.semantic_mapping())
    for binding in snapshot.bindings:
        assert forbidden.isdisjoint(binding.semantic_mapping())


def test_production_config_and_context_bind_the_exact_approval_digest():
    approved = LB.reviewed_live_binding_snapshot()
    recipe = production_data_recipe()
    assert recipe.live_binding_snapshot is approved

    expected_options = {
        "facade": "guanlan_v2.datafeed.live_client",
        "transport": "subprocess_probe",
        "live_binding_snapshot_digest": approved.snapshot_digest,
        "live_binding_approval_signature": approved.approval_signature,
    }
    assert recipe.source_config.option_digests["guanlan.datafeed"] == \
        content_digest(expected_options)

    class _Clock:
        def now(self):
            return datetime(2026, 7, 16, 7, 0, tzinfo=timezone.utc)

    _, ctx = build_production_capture(
        clock=_Clock(), data_snapshot_id="binding-context-test")
    assert ctx.source_config_digest == recipe.source_config.source_config_digest
    assert ctx.routing_snapshot_digest == recipe.routing.routing_digest

    adapter = production_data_adapters()["guanlan.datafeed"]
    assert adapter.binding_snapshot is approved


def test_activation_contract_matches_observation_only_review_attestations():
    snapshot_rows = {
        (row.dataset_id, row.method_id, row.source_id): row
        for row in LB.reviewed_live_binding_snapshot().bindings
    }
    report_rows = {
        (row.dataset_id, row.method_id, row.source_id): row
        for row in reviewed_live_client_adapter_support()
    }
    assert set(snapshot_rows) == set(report_rows)
    for key, binding in snapshot_rows.items():
        support = report_rows[key]
        assert binding.provider_id == support.provider_id
        assert binding.adapter_id == support.adapter_id
        assert binding.adapter_version == support.adapter_version
        assert binding.adapter_contract_digest == support.adapter_contract_digest
        assert support.requires_source_echo is True
        assert support.requires_network_used is True
        assert support.requires_closed_circuit is True
        assert support.envelope_schema_version == binding.envelope_schema_version
        assert support.allows_provider_fallback is False

    reviewed_contracts = {
        (row.dataset_id, row.method_id): row.contract_digest
        for row in reviewed_live_client_bindings()
    }
    assert {
        (row.dataset_id, row.method_id): row.dataset_contract_digest
        for row in LB.reviewed_live_binding_snapshot().bindings
    } == reviewed_contracts


def test_reviewed_dataset_contracts_match_capability_projection_fixture():
    """A capability-projection change requires an explicit binding refreeze."""
    rows = (
        {
            "dataset_id": "market.quote",
            "category": "market",
            "live_source_ids": ["tdx_realtime_quote", "tencent_realtime_quote"],
            "source_priority": ["tdx_realtime_quote", "tencent_realtime_quote"],
            "canonical_reader": "",
            "natural_keys": ["instrument", "quote_ts"],
            "date_field": "",
            "visible_field": "visible_ts",
            "visible_ts_policy": "quote_time",
            "query_fields": ["code", "start", "end", "as_of", "limit"],
            "bridge_status": "live_only_safe_merge_separate",
            "write_enabled": False,
            "canonical_write": False,
        },
        {
            "dataset_id": "market.bar",
            "category": "market",
            "live_source_ids": ["tdx_kline", "baidu_kline_ma"],
            "source_priority": ["tdx_kline", "baidu_kline_ma"],
            "canonical_reader": "",
            "natural_keys": ["instrument", "datetime", "freq"],
            "date_field": "datetime",
            "visible_field": "visible_ts",
            "visible_ts_policy": "bar_close_time",
            "query_fields": ["code", "start", "end", "as_of", "freq", "limit"],
            "bridge_status": "live_only_safe_merge_separate",
            "write_enabled": False,
            "canonical_write": False,
        },
        {
            "dataset_id": "text.stock_news",
            "category": "text",
            "live_source_ids": ["eastmoney_stock_news"],
            "source_priority": [],
            "canonical_reader": "src.data.text_datasets.load_stock_news",
            "natural_keys": ["provider", "url", "publish_ts"],
            "date_field": "publish_ts",
            "visible_field": "visible_ts",
            "visible_ts_policy": "exact_publish_time_or_date_only_eod",
            "query_fields": ["code", "start", "end", "as_of", "limit"],
            "bridge_status": "text_promotion_ready",
            "write_enabled": False,
            "canonical_write": False,
        },
    )
    expected = {
        "market.quote": "b457ca0ce6ad43ea0a9cf47e168be98a6fa8ad97465fadde5c102b977e2dfdc4",
        "market.bar": "a3806bcdc7d765e6a85cd1f13ae42664f658e134be831c5b578acc73db0d8dec",
        "text.stock_news": "ec8afaf51e68bc3d617c2b6f024373b65d2332ee330a748d02424a58d88fe362",
    }

    assert {
        row["dataset_id"]: row["contract_digest"]
        for row in map(_dataset_projection, rows)
    } == expected
    assert {
        row.dataset_id: row.dataset_contract_digest
        for row in LB.reviewed_live_binding_snapshot().bindings
    } == expected


def test_self_sealed_external_candidate_is_not_code_approval():
    approved = LB.reviewed_live_binding_snapshot()
    original = approved.binding_for_method("ohlcv")
    candidate = replace(
        original,
        source_id="stocks_manifest_injected_source",
        provider_id="manifest_provider",
        adapter_contract_digest=LB._adapter_contract_digest(
            dataset_id=original.dataset_id,
            method_id=original.method_id,
            source_id="stocks_manifest_injected_source",
            provider_id="manifest_provider",
            projection_id=original.projection_id,
        ),
    )
    rows = tuple(
        sorted(
            (candidate if row.method_id == "ohlcv" else row
             for row in approved.bindings),
            key=lambda row: row.key,
        )
    )

    # A manifest can recompute hashes, so structural self-sealing succeeds.
    forged = LB._seal_snapshot(rows)
    assert forged.snapshot_digest != approved.snapshot_digest
    # It still has no activation authority: only exact code-owned equality does.
    with pytest.raises(RoutingConfigurationError, match="not the code-owned"):
        LB.verify_code_approved_live_binding_snapshot(forged)


def test_equal_reconstruction_and_malicious_duck_have_no_authority():
    approved = LB.reviewed_live_binding_snapshot()

    # Even byte-equal reconstructed values are not the module-owned authority.
    reconstructed = LB._seal_snapshot(tuple(approved.bindings))
    assert reconstructed == approved and reconstructed is not approved
    with pytest.raises(RoutingConfigurationError, match="not the code-owned"):
        LB.verify_code_approved_live_binding_snapshot(reconstructed)

    class _MaliciousDuck:
        key = approved.bindings[0].key
        method_id = approved.bindings[0].method_id
        source_id = "evil_source"
        provider_id = "evil_provider"

        def semantic_mapping(self):
            return approved.bindings[0].semantic_mapping()

        def __eq__(self, other):
            return True

    with pytest.raises(ValueError, match="exact ReviewedLiveBinding"):
        replace(
            approved,
            bindings=(_MaliciousDuck(), *approved.bindings[1:]),
        )


def test_fallback_and_multi_source_method_shapes_are_refused():
    approved = LB.reviewed_live_binding_snapshot()
    with pytest.raises(ValueError, match="prohibit provider fallback"):
        replace(approved.bindings[0], allows_provider_fallback=True)

    duplicate_method = replace(
        approved.bindings[0],
        dataset_id="different.dataset",
        source_id="different_source",
    )
    rows = tuple(sorted((*approved.bindings, duplicate_method), key=lambda row: row.key))
    with pytest.raises(ValueError, match="each method must bind exactly one"):
        LB._seal_snapshot(rows)


def test_signature_and_unbound_method_fail_closed():
    approved = LB.reviewed_live_binding_snapshot()
    with pytest.raises(ValueError, match="signature does not recompute"):
        replace(approved, approval_signature="0" * 64)
    with pytest.raises(RoutingConfigurationError, match="no code-approved"):
        approved.binding_for_method("fundamentals")
