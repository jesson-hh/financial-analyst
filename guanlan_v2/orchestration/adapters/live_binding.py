# -*- coding: utf-8 -*-
"""Code-owned activation authority for the orchestration live-data adapter.

The stocks capability manifest is an *observation*.  It may propose a source,
but it can never make that source executable.  This module is the deliberately
small authority boundary between discovery and activation:

* one reviewed row binds one existing Phase-3 method to exactly one facade
  source and exactly one physical provider;
* the row pins the already-frozen method spec and the reviewed adapter
  projection contract;
* resolver/source echo, strict provenance and no-provider-fallback are signed
  semantics, not caller options;
* production accepts only :func:`reviewed_live_binding_snapshot`.  A caller can
  construct a structurally valid snapshot, but
  :func:`verify_code_approved_live_binding_snapshot` rejects it unless it is
  byte-for-byte equal to the code-owned production approval.

Consequently, adding a stocks manifest row never changes an orchestration run.
Activating a compatible provider requires a reviewed code change to the rows
below (and the adapter projection/tests), followed by a process restart.  The
workers continue to use their existing method capability and prefetch grants;
this snapshot contains no capability or worker authority at all.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from guanlan_v2.orchestration.data.errors import RoutingConfigurationError

__all__ = [
    "ReviewedLiveBinding",
    "ReviewedLiveBindingSnapshot",
    "reviewed_live_binding_snapshot",
    "verify_code_approved_live_binding_snapshot",
]


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_SCHEMA_VERSION = "guanlan.reviewed-live-binding-snapshot.v1"
_APPROVAL_DOMAIN = "guanlan.reviewed-live-binding-approval.v1"
_APPROVAL_ID = "approval.live-client.production"
_APPROVAL_VERSION = "1"
_ADAPTER_ID = "guanlan.live-client-source"
_ADAPTER_VERSION = "1"
_ENVELOPE_SCHEMA_VERSION = "guanlan.live-source-result.v2"


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_id(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field}: {value!r}")


def _require_digest(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 digest")


@dataclass(frozen=True, slots=True)
class ReviewedLiveBinding:
    """One reviewed single-provider binding behind an existing method.

    ``dataset_id`` is audit lineage; the executable authority remains the exact
    existing ``method_id``/``method_spec_digest`` pair.  The adapter contract
    digest pins the source-specific projection, including its temporal and
    provenance checks.  All four closed booleans are intentionally part of the
    signed semantic mapping.
    """

    dataset_id: str
    dataset_contract_digest: str
    method_id: str
    method_spec_digest: str
    source_id: str
    provider_id: str
    projection_id: str
    adapter_contract_digest: str
    adapter_id: str = _ADAPTER_ID
    adapter_version: str = _ADAPTER_VERSION
    envelope_schema_version: str = _ENVELOPE_SCHEMA_VERSION
    requires_exact_resolver_match: bool = True
    requires_source_echo: bool = True
    requires_strict_provenance: bool = True
    requires_network_used: bool = True
    requires_closed_circuit: bool = True
    allows_provider_fallback: bool = False

    def __post_init__(self) -> None:
        for field in (
            "dataset_id",
            "method_id",
            "source_id",
            "provider_id",
            "projection_id",
            "adapter_id",
            "adapter_version",
            "envelope_schema_version",
        ):
            _require_id(getattr(self, field), field=field)
        for field in (
            "dataset_contract_digest",
            "method_spec_digest",
            "adapter_contract_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        if self.adapter_id != _ADAPTER_ID or self.adapter_version != _ADAPTER_VERSION:
            raise ValueError("binding must name the reviewed live-client adapter identity")
        if self.envelope_schema_version != _ENVELOPE_SCHEMA_VERSION:
            raise ValueError("binding must pin the reviewed live envelope schema")
        if self.requires_exact_resolver_match is not True:
            raise ValueError("binding must require an exact resolver result")
        if self.requires_source_echo is not True:
            raise ValueError("binding must require an exact source echo")
        if self.requires_strict_provenance is not True:
            raise ValueError("binding must require strict provenance")
        if self.requires_network_used is not True:
            raise ValueError("binding must require a real network observation")
        if self.requires_closed_circuit is not True:
            raise ValueError("binding must require a closed provider circuit")
        if self.allows_provider_fallback is not False:
            raise ValueError("binding must prohibit provider fallback")

    @property
    def key(self) -> tuple[str, str]:
        return (self.dataset_id, self.method_id)

    def semantic_mapping(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_contract_digest": self.dataset_contract_digest,
            "method_id": self.method_id,
            "method_spec_digest": self.method_spec_digest,
            "source_id": self.source_id,
            "provider_id": self.provider_id,
            "projection_id": self.projection_id,
            "adapter_contract_digest": self.adapter_contract_digest,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "envelope_schema_version": self.envelope_schema_version,
            "requires_exact_resolver_match": self.requires_exact_resolver_match,
            "requires_source_echo": self.requires_source_echo,
            "requires_strict_provenance": self.requires_strict_provenance,
            "requires_network_used": self.requires_network_used,
            "requires_closed_circuit": self.requires_closed_circuit,
            "allows_provider_fallback": self.allows_provider_fallback,
        }


@dataclass(frozen=True, slots=True)
class ReviewedLiveBindingSnapshot:
    """Self-sealed, canonically ordered live binding approval.

    The domain-tagged ``approval_signature`` detects any changed signed field.
    Authorization is a second, separate check: production compares the complete
    object with the module-owned approval via
    :func:`verify_code_approved_live_binding_snapshot`.  Self-sealing alone is
    therefore never treated as permission.
    """

    schema_version: str
    approval_id: str
    approval_version: str
    bindings: tuple[ReviewedLiveBinding, ...]
    snapshot_digest: str
    approval_signature: str

    def __post_init__(self) -> None:
        if self.schema_version != _SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported reviewed live binding snapshot schema")
        _require_id(self.approval_id, field="approval_id")
        _require_id(self.approval_version, field="approval_version")
        _require_digest(self.snapshot_digest, field="snapshot_digest")
        _require_digest(self.approval_signature, field="approval_signature")
        if not self.bindings:
            raise ValueError("a reviewed live binding snapshot cannot be empty")
        if any(type(binding) is not ReviewedLiveBinding for binding in self.bindings):
            raise ValueError(
                "bindings must contain only exact ReviewedLiveBinding values"
            )
        keys = tuple(binding.key for binding in self.bindings)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("bindings must be unique and canonically sorted by dataset/method")
        methods = tuple(binding.method_id for binding in self.bindings)
        if len(set(methods)) != len(methods):
            raise ValueError(
                "each method must bind exactly one source/provider; fallback chains are refused"
            )
        expected_digest = _canonical_digest(self.semantic_mapping())
        if self.snapshot_digest != expected_digest:
            raise ValueError("reviewed live binding snapshot digest does not recompute")
        expected_signature = _canonical_digest(
            {
                "domain": _APPROVAL_DOMAIN,
                "approval_id": self.approval_id,
                "approval_version": self.approval_version,
                "snapshot_digest": self.snapshot_digest,
            }
        )
        if self.approval_signature != expected_signature:
            raise ValueError("reviewed live binding approval signature does not recompute")

    def semantic_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "approval_id": self.approval_id,
            "approval_version": self.approval_version,
            "bindings": [binding.semantic_mapping() for binding in self.bindings],
        }

    def binding_for_method(self, method_id: str) -> ReviewedLiveBinding:
        """Return the one signed binding for ``method_id`` or fail closed."""
        for binding in self.bindings:
            if binding.method_id == method_id:
                return binding
        raise RoutingConfigurationError(
            f"method {method_id!r} has no code-approved live source binding"
        )

    def method_source_map(self) -> dict[str, str]:
        """Return scalar method→source selections; never a fallback chain."""
        return {binding.method_id: binding.source_id for binding in self.bindings}


def _adapter_contract_digest(
    *, dataset_id: str, method_id: str, source_id: str, provider_id: str,
    projection_id: str,
) -> str:
    """Match the runtime-catalog attestation's reviewed semantic projection."""
    return _canonical_digest(
        {
            "schema_version": "guanlan.live-adapter-support-contract.v1",
            "adapter_id": _ADAPTER_ID,
            "adapter_version": _ADAPTER_VERSION,
            "dataset_id": dataset_id,
            "method_id": method_id,
            "source_id": source_id,
            "provider_id": provider_id,
            "projection_id": projection_id,
            "envelope_schema_version": _ENVELOPE_SCHEMA_VERSION,
            "requires_source_echo": True,
            "requires_network_used": True,
            "requires_closed_circuit": True,
            "allows_provider_fallback": False,
        }
    )


def _binding(
    *, dataset_id: str, dataset_contract_digest: str, method_id: str,
    method_spec_digest: str, source_id: str, provider_id: str,
    projection_id: str,
) -> ReviewedLiveBinding:
    return ReviewedLiveBinding(
        dataset_id=dataset_id,
        dataset_contract_digest=dataset_contract_digest,
        method_id=method_id,
        method_spec_digest=method_spec_digest,
        source_id=source_id,
        provider_id=provider_id,
        projection_id=projection_id,
        adapter_contract_digest=_adapter_contract_digest(
            dataset_id=dataset_id,
            method_id=method_id,
            source_id=source_id,
            provider_id=provider_id,
            projection_id=projection_id,
        ),
    )


# Code review authority.  These rows are deliberately not read from stocks,
# environment variables or configuration.  Switching one source means editing
# this table and the matching adapter projection/tests.  The method spec digests
# are hard pins: a method/schema refreeze does not silently inherit approval.
_REVIEWED_BINDINGS: tuple[ReviewedLiveBinding, ...] = tuple(sorted((
    _binding(
        dataset_id="market.bar",
        dataset_contract_digest=(
            "a3806bcdc7d765e6a85cd1f13ae42664f"
            "658e134be831c5b578acc73db0d8dec"
        ),
        method_id="ohlcv",
        method_spec_digest=(
            "b5693f417b7506c2125834615a8e800f"
            "7c4855d2f17fca5eeb0da8466e515677"
        ),
        source_id="tdx_kline",
        provider_id="tdx",
        projection_id="daily-ohlcv.v1",
    ),
    _binding(
        dataset_id="market.quote",
        dataset_contract_digest=(
            "b457ca0ce6ad43ea0a9cf47e168be98a6"
            "fa8ad97465fadde5c102b977e2dfdc4"
        ),
        method_id="indicators",
        method_spec_digest=(
            "6d95955b996c5fd699b30ec8b0702b2d"
            "2d58dd11a72190ffa87861e6703dc556"
        ),
        source_id="tdx_realtime_quote",
        provider_id="tdx",
        projection_id="timed-five-level-book.v1",
    ),
    _binding(
        dataset_id="market.quote",
        dataset_contract_digest=(
            "b457ca0ce6ad43ea0a9cf47e168be98a6"
            "fa8ad97465fadde5c102b977e2dfdc4"
        ),
        method_id="verified_snapshot",
        method_spec_digest=(
            "e77bc00479717dfcca33b47e892534fa"
            "e9e21a917e406d53ef6e746c8b1f4989"
        ),
        source_id="tencent_realtime_quote",
        provider_id="tencent",
        projection_id="verified-settled-quote.v1",
    ),
    _binding(
        dataset_id="text.stock_news",
        dataset_contract_digest=(
            "ec8afaf51e68bc3d617c2b6f024373b6"
            "5d2332ee330a748d02424a58d88fe362"
        ),
        method_id="news",
        method_spec_digest=(
            "ecaf9abe1cf29e3b76565e3f6a715b5"
            "a5583bb608a0857c4b1cafe4a6e30e4b3"
        ),
        source_id="eastmoney_stock_news",
        provider_id="eastmoney",
        projection_id="pit-certified-publication.v1",
    ),
), key=lambda item: item.key))


def _seal_snapshot(bindings: tuple[ReviewedLiveBinding, ...]) -> ReviewedLiveBindingSnapshot:
    semantic = {
        "schema_version": _SNAPSHOT_SCHEMA_VERSION,
        "approval_id": _APPROVAL_ID,
        "approval_version": _APPROVAL_VERSION,
        "bindings": [binding.semantic_mapping() for binding in bindings],
    }
    digest = _canonical_digest(semantic)
    signature = _canonical_digest(
        {
            "domain": _APPROVAL_DOMAIN,
            "approval_id": _APPROVAL_ID,
            "approval_version": _APPROVAL_VERSION,
            "snapshot_digest": digest,
        }
    )
    return ReviewedLiveBindingSnapshot(
        schema_version=_SNAPSHOT_SCHEMA_VERSION,
        approval_id=_APPROVAL_ID,
        approval_version=_APPROVAL_VERSION,
        bindings=bindings,
        snapshot_digest=digest,
        approval_signature=signature,
    )


_PRODUCTION_SNAPSHOT = _seal_snapshot(_REVIEWED_BINDINGS)


def reviewed_live_binding_snapshot() -> ReviewedLiveBindingSnapshot:
    """Return the immutable, code-owned production approval (no I/O)."""
    return _PRODUCTION_SNAPSHOT


def verify_code_approved_live_binding_snapshot(
    snapshot: ReviewedLiveBindingSnapshot,
) -> None:
    """Reject a merely self-sealed or externally supplied activation snapshot.

    Equality with the module-owned object is the authorization check.  This is
    intentionally stronger than recomputing ``approval_signature``: an external
    manifest can compute hashes, but it cannot add itself to the production
    approval path without a reviewed code change.
    """
    if type(snapshot) is not ReviewedLiveBindingSnapshot:
        raise RoutingConfigurationError(
            "live binding activation requires the exact reviewed snapshot type"
        )
    if snapshot is not _PRODUCTION_SNAPSHOT:
        raise RoutingConfigurationError(
            "live binding snapshot is not the code-owned production approval"
        )
