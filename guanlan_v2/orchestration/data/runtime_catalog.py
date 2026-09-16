# -*- coding: utf-8 -*-
"""Pure, read-only classification of an observed runtime data catalog.

The input is the mapping produced by
``guanlan_v2.datafeed.capability_catalog.build_data_capability_manifest``.  This
module deliberately does not import that producer, call a loader, inspect
``G:\\stocks`` or resolve a process-global "latest" catalog.  A caller must pass
one already-frozen observation explicitly.

The report is diagnostic evidence only.  It has no operation that creates an
orchestration capability, changes a worker grant, adds a prefetch row, binds a
handler or edits a route.  In particular, an observed provider becomes only a
``compatible_candidate`` only when both sides carry the same published
result-schema digest *and* code-owned review material attests the exact
dataset/method/source/adapter projection.  The current stocks manifest does not
yet publish that identity, so a new provider remains ``requires_review`` and a
new dataset/method remains ``requires_contract``.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "ReviewedAdapterSupport",
    "ReviewedDataBinding",
    "RuntimeDataCatalogClassification",
    "RuntimeDataCatalogFinding",
    "RuntimeDataCatalogReport",
    "build_runtime_data_catalog_report",
    "runtime_data_catalog_support_summary",
    "render_runtime_data_catalog_support",
    "reviewed_live_client_adapter_support",
    "reviewed_live_client_bindings",
]


RuntimeDataCatalogClassification = Literal[
    "reviewed_current_binding",
    "compatible_candidate",
    "requires_review",
    "requires_contract",
    "unsupported",
]

_CLASSIFICATIONS: tuple[RuntimeDataCatalogClassification, ...] = (
    "reviewed_current_binding",
    "compatible_candidate",
    "requires_review",
    "requires_contract",
    "unsupported",
)
_REPORT_SCHEMA_VERSION = "runtime-data-catalog-report.v1"
_INPUT_SCHEMA_VERSION = "guanlan.data-capability-manifest.v1"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LIVE_CLIENT_ADAPTER_ID = "guanlan.live-client-source"
_LIVE_CLIENT_ADAPTER_VERSION = "1"
_LIVE_SOURCE_ENVELOPE_SCHEMA_VERSION = "guanlan.live-source-result.v2"


class _ManifestShapeError(ValueError):
    """Internal marker for an untrusted manifest that cannot be classified."""


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"invalid {field}: {text!r}")
    return text


def _optional_id(value: Any, *, field: str) -> str:
    if value in (None, ""):
        return ""
    return _require_id(value, field=field)


def _require_digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 digest")
    return value


def _optional_digest(value: Any, *, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_digest(value, field=field)


def _sorted_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(reasons)))


@dataclass(frozen=True, slots=True)
class ReviewedAdapterSupport:
    """Code-reviewed proof that one adapter can project one exact live source.

    A dataset-level result schema cannot prove that a source's *raw* probe shape
    satisfies a method-specific adapter projection.  This attestation therefore
    binds all four identities explicitly: dataset, method, source and adapter.
    ``adapter_contract_digest`` pins the reviewed projection/temporal/provenance
    contract.  A candidate additionally needs ``result_schema_digest`` equal to
    the published dataset identity before it can be called compatible.

    The two boolean fields are deliberately closed: the current live adapter
    requires a source echo and admits no provider fallback inside one Phase-3
    source attempt.  They are evidence, never an activation or worker grant.
    """

    dataset_id: str
    method_id: str
    source_id: str
    adapter_id: str
    adapter_version: str
    provider_id: str
    adapter_contract_digest: str
    result_schema_digest: str | None = None
    envelope_schema_version: str = _LIVE_SOURCE_ENVELOPE_SCHEMA_VERSION
    requires_source_echo: bool = True
    requires_network_used: bool = True
    requires_closed_circuit: bool = True
    allows_provider_fallback: bool = False

    def __post_init__(self) -> None:
        for field, value in (
            ("dataset_id", self.dataset_id),
            ("method_id", self.method_id),
            ("source_id", self.source_id),
            ("adapter_id", self.adapter_id),
            ("adapter_version", self.adapter_version),
            ("provider_id", self.provider_id),
            ("envelope_schema_version", self.envelope_schema_version),
        ):
            _require_id(value, field=field)
        _require_digest(
            self.adapter_contract_digest, field="adapter_contract_digest"
        )
        if self.result_schema_digest is not None:
            _require_digest(
                self.result_schema_digest, field="result_schema_digest"
            )
        if self.requires_source_echo is not True:
            raise ValueError("reviewed adapter support must require source echo")
        if self.envelope_schema_version != _LIVE_SOURCE_ENVELOPE_SCHEMA_VERSION:
            raise ValueError("reviewed adapter support must pin the live envelope schema")
        if self.requires_network_used is not True:
            raise ValueError("reviewed adapter support must require network_used=true")
        if self.requires_closed_circuit is not True:
            raise ValueError("reviewed adapter support must require a closed circuit")
        if self.allows_provider_fallback is not False:
            raise ValueError(
                "reviewed adapter support must not allow provider fallback"
            )

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.dataset_id,
            self.method_id,
            self.source_id,
            self.adapter_id,
            self.adapter_version,
        )

    def semantic_mapping(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "method_id": self.method_id,
            "source_id": self.source_id,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "provider_id": self.provider_id,
            "adapter_contract_digest": self.adapter_contract_digest,
            "result_schema_digest": self.result_schema_digest,
            "envelope_schema_version": self.envelope_schema_version,
            "requires_source_echo": self.requires_source_echo,
            "requires_network_used": self.requires_network_used,
            "requires_closed_circuit": self.requires_closed_circuit,
            "allows_provider_fallback": self.allows_provider_fallback,
        }


@dataclass(frozen=True, slots=True)
class ReviewedDataBinding:
    """A service-reviewed current dataset→method→source binding.

    ``contract_digest`` can pin today's manually reviewed stocks contract even
    while stocks has no published per-dataset result schema.  That pin is enough
    to recognise the already-reviewed sources, but never enough to auto-classify
    a *new* provider as compatible.  Candidate compatibility additionally requires
    ``result_schema_digest`` on this binding and an equal published digest in the
    observed manifest.
    """

    dataset_id: str
    method_id: str
    source_ids: tuple[str, ...]
    contract_digest: str
    result_schema_digest: str | None = None
    adapter_id: str = _LIVE_CLIENT_ADAPTER_ID
    adapter_version: str = _LIVE_CLIENT_ADAPTER_VERSION

    def __post_init__(self) -> None:
        _require_id(self.dataset_id, field="dataset_id")
        _require_id(self.method_id, field="method_id")
        _require_digest(self.contract_digest, field="contract_digest")
        _require_id(self.adapter_id, field="adapter_id")
        _require_id(self.adapter_version, field="adapter_version")
        if self.result_schema_digest is not None:
            _require_digest(
                self.result_schema_digest, field="result_schema_digest"
            )
        for source_id in self.source_ids:
            _require_id(source_id, field="source_id")
        if self.source_ids != tuple(sorted(set(self.source_ids))):
            raise ValueError("source_ids must be unique and canonically sorted")

    @classmethod
    def build(
        cls,
        *,
        dataset_id: str,
        method_id: str,
        source_ids: Iterable[str],
        contract_digest: str,
        result_schema_digest: str | None = None,
        adapter_id: str = _LIVE_CLIENT_ADAPTER_ID,
        adapter_version: str = _LIVE_CLIENT_ADAPTER_VERSION,
    ) -> "ReviewedDataBinding":
        return cls(
            dataset_id=dataset_id,
            method_id=method_id,
            source_ids=tuple(sorted(set(source_ids))),
            contract_digest=contract_digest,
            result_schema_digest=result_schema_digest,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
        )

    def semantic_mapping(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "method_id": self.method_id,
            "source_ids": list(self.source_ids),
            "contract_digest": self.contract_digest,
            "result_schema_digest": self.result_schema_digest,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
        }


# These are reviewed adapter facts, not runtime discovery.  The contract digests
# pin the current `guanlan.data-capability-manifest.v1` dataset contracts.  They
# must be changed only in code review alongside the corresponding adapter and
# orchestration contract review.  Constructing this tuple performs no I/O.
_REVIEWED_LIVE_CLIENT_BINDINGS: tuple[ReviewedDataBinding, ...] = (
    ReviewedDataBinding(
        dataset_id="market.quote",
        method_id="verified_snapshot",
        source_ids=("tencent_realtime_quote",),
        contract_digest=(
            "b457ca0ce6ad43ea0a9cf47e168be98a6"
            "fa8ad97465fadde5c102b977e2dfdc4"
        ),
        result_schema_digest=None,
    ),
    ReviewedDataBinding(
        dataset_id="market.quote",
        method_id="indicators",
        source_ids=("tdx_realtime_quote",),
        contract_digest=(
            "b457ca0ce6ad43ea0a9cf47e168be98a6"
            "fa8ad97465fadde5c102b977e2dfdc4"
        ),
        result_schema_digest=None,
    ),
    ReviewedDataBinding(
        dataset_id="market.bar",
        method_id="ohlcv",
        source_ids=("tdx_kline",),
        contract_digest=(
            "a3806bcdc7d765e6a85cd1f13ae42664f"
            "658e134be831c5b578acc73db0d8dec"
        ),
        result_schema_digest=None,
    ),
    ReviewedDataBinding(
        dataset_id="text.stock_news",
        method_id="news",
        source_ids=("eastmoney_stock_news",),
        contract_digest=(
            "ec8afaf51e68bc3d617c2b6f024373b6"
            "5d2332ee330a748d02424a58d88fe362"
        ),
        result_schema_digest=None,
    ),
)


def _live_client_support(
    *,
    dataset_id: str,
    method_id: str,
    source_id: str,
    provider_id: str,
    projection_id: str,
    result_schema_digest: str | None = None,
) -> ReviewedAdapterSupport:
    contract = {
        "schema_version": "guanlan.live-adapter-support-contract.v1",
        "adapter_id": _LIVE_CLIENT_ADAPTER_ID,
        "adapter_version": _LIVE_CLIENT_ADAPTER_VERSION,
        "dataset_id": dataset_id,
        "method_id": method_id,
        "source_id": source_id,
        "provider_id": provider_id,
        "projection_id": projection_id,
        "envelope_schema_version": _LIVE_SOURCE_ENVELOPE_SCHEMA_VERSION,
        "requires_source_echo": True,
        "requires_network_used": True,
        "requires_closed_circuit": True,
        "allows_provider_fallback": False,
    }
    return ReviewedAdapterSupport(
        dataset_id=dataset_id,
        method_id=method_id,
        source_id=source_id,
        adapter_id=_LIVE_CLIENT_ADAPTER_ID,
        adapter_version=_LIVE_CLIENT_ADAPTER_VERSION,
        provider_id=provider_id,
        adapter_contract_digest=_canonical_digest(contract),
        result_schema_digest=result_schema_digest,
    )


# Unlike the observed stocks manifest, this table is executable service-owned
# review material.  Adding a source row here is a code review; a manifest field
# with the same spelling is ignored by ``build_runtime_data_catalog_report``.
_REVIEWED_LIVE_CLIENT_ADAPTER_SUPPORT: tuple[ReviewedAdapterSupport, ...] = (
    _live_client_support(
        dataset_id="market.quote",
        method_id="verified_snapshot",
        source_id="tencent_realtime_quote",
        provider_id="tencent",
        projection_id="verified-settled-quote.v1",
    ),
    _live_client_support(
        dataset_id="market.quote",
        method_id="indicators",
        source_id="tdx_realtime_quote",
        provider_id="tdx",
        projection_id="timed-five-level-book.v1",
    ),
    _live_client_support(
        dataset_id="market.bar",
        method_id="ohlcv",
        source_id="tdx_kline",
        provider_id="tdx",
        projection_id="daily-ohlcv.v1",
    ),
    _live_client_support(
        dataset_id="text.stock_news",
        method_id="news",
        source_id="eastmoney_stock_news",
        provider_id="eastmoney",
        projection_id="pit-certified-publication.v1",
    ),
)


def reviewed_live_client_bindings() -> tuple[ReviewedDataBinding, ...]:
    """Return the code-reviewed current LiveClientSource adapter bindings.

    External manifest fields cannot add to or replace this frozen baseline.
    """
    return _REVIEWED_LIVE_CLIENT_BINDINGS


def reviewed_live_client_adapter_support() -> tuple[ReviewedAdapterSupport, ...]:
    """Return the immutable source×method support baseline for LiveClientSource."""
    return _REVIEWED_LIVE_CLIENT_ADAPTER_SUPPORT


@dataclass(frozen=True, slots=True)
class RuntimeDataCatalogFinding:
    """One immutable dataset/source classification; never an authority grant."""

    dataset_id: str
    source_id: str | None
    method_id: str | None
    adapter_id: str | None
    adapter_version: str | None
    adapter_support_digest: str | None
    classification: RuntimeDataCatalogClassification
    reason_codes: tuple[str, ...]
    contract_digest: str
    result_schema_digest: str | None

    def __post_init__(self) -> None:
        _require_id(self.dataset_id, field="dataset_id")
        if self.source_id is not None:
            _require_id(self.source_id, field="source_id")
        if self.method_id is not None:
            _require_id(self.method_id, field="method_id")
        if self.adapter_id is not None:
            _require_id(self.adapter_id, field="adapter_id")
        if self.adapter_version is not None:
            _require_id(self.adapter_version, field="adapter_version")
        if (self.adapter_id is None) != (self.adapter_version is None):
            raise ValueError("adapter id/version must be present or absent together")
        if self.adapter_support_digest is not None:
            _require_digest(
                self.adapter_support_digest, field="adapter_support_digest"
            )
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError(f"unknown classification: {self.classification!r}")
        if not self.reason_codes:
            raise ValueError("a finding requires at least one reason code")
        if self.reason_codes != _sorted_reasons(self.reason_codes):
            raise ValueError("reason_codes must be unique and canonically sorted")
        if self.contract_digest:
            _require_digest(self.contract_digest, field="contract_digest")
        if self.result_schema_digest is not None:
            _require_digest(
                self.result_schema_digest, field="result_schema_digest"
            )
        if self.classification in {
            "reviewed_current_binding",
            "compatible_candidate",
        } and (
            self.adapter_id is None
            or self.adapter_version is None
            or self.adapter_support_digest is None
        ):
            raise ValueError(
                f"{self.classification} requires an exact reviewed adapter "
                "support identity"
            )
        if (
            self.classification == "compatible_candidate"
            and self.result_schema_digest is None
        ):
            raise ValueError(
                "compatible_candidate requires published result-schema and "
                "reviewed adapter-support identities"
            )

    @property
    def sort_key(
        self,
    ) -> tuple[str, str, str, str, str, tuple[str, ...]]:
        return (
            self.dataset_id,
            self.source_id or "",
            self.method_id or "",
            self.adapter_id or "",
            self.classification,
            self.reason_codes,
        )

    def semantic_mapping(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "source_id": self.source_id,
            "method_id": self.method_id,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "adapter_support_digest": self.adapter_support_digest,
            "classification": self.classification,
            "reason_codes": list(self.reason_codes),
            "contract_digest": self.contract_digest,
            "result_schema_digest": self.result_schema_digest,
        }


@dataclass(frozen=True, slots=True)
class RuntimeDataCatalogReport:
    """Self-sealed read-only assessment of one explicit manifest mapping.

    ``sealed_catalog_changed`` and ``authority_changes`` are closed at false/empty
    so a serialized report is explicit about its non-authoritative effect.
    """

    schema_version: str
    manifest_schema_version: str
    manifest_catalog_digest: str
    reviewed_bindings_digest: str
    reviewed_adapter_support_digest: str
    authoritative: bool
    stale: bool
    findings: tuple[RuntimeDataCatalogFinding, ...]
    classification_counts: tuple[tuple[str, int], ...]
    sealed_catalog_changed: bool
    authority_changes: tuple[str, ...]
    report_digest: str

    def _semantic_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_schema_version": self.manifest_schema_version,
            "manifest_catalog_digest": self.manifest_catalog_digest,
            "reviewed_bindings_digest": self.reviewed_bindings_digest,
            "reviewed_adapter_support_digest": self.reviewed_adapter_support_digest,
            "authoritative": self.authoritative,
            "stale": self.stale,
            "findings": [finding.semantic_mapping() for finding in self.findings],
            "classification_counts": [list(row) for row in self.classification_counts],
            "sealed_catalog_changed": self.sealed_catalog_changed,
            "authority_changes": list(self.authority_changes),
        }

    def __post_init__(self) -> None:
        if self.schema_version != _REPORT_SCHEMA_VERSION:
            raise ValueError("unsupported RuntimeDataCatalogReport schema version")
        if self.manifest_catalog_digest:
            _require_digest(
                self.manifest_catalog_digest, field="manifest_catalog_digest"
            )
        _require_digest(
            self.reviewed_bindings_digest, field="reviewed_bindings_digest"
        )
        _require_digest(
            self.reviewed_adapter_support_digest,
            field="reviewed_adapter_support_digest",
        )
        keys = [finding.sort_key for finding in self.findings]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("findings must be unique and canonically sorted")
        expected_counts = tuple(
            (classification, sum(
                finding.classification == classification
                for finding in self.findings
            ))
            for classification in _CLASSIFICATIONS
        )
        if self.classification_counts != expected_counts:
            raise ValueError("classification_counts do not match findings")
        if self.sealed_catalog_changed or self.authority_changes:
            raise ValueError(
                "RuntimeDataCatalogReport is read-only and cannot carry authority changes"
            )
        if self.report_digest != _canonical_digest(self._semantic_mapping()):
            raise ValueError("report_digest does not match the canonical report")

    def count(self, classification: RuntimeDataCatalogClassification) -> int:
        return dict(self.classification_counts)[classification]

    def to_mapping(self) -> dict[str, Any]:
        return {**self._semantic_mapping(), "report_digest": self.report_digest}


@dataclass(frozen=True, slots=True)
class _ObservedDataset:
    dataset_id: str
    source_ids: tuple[str, ...]
    contract_digest: str
    read_only: bool
    result_schema_status: str
    result_schema_digest: str | None


@dataclass(frozen=True, slots=True)
class _ObservedSource:
    source_id: str
    dataset_ids: tuple[str, ...]
    implementation_status: str
    provider_id: str


def _manifest_ids(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _ManifestShapeError(f"{field} must be an array")
    try:
        out = tuple(_require_id(item, field=field) for item in value)
    except ValueError as exc:
        raise _ManifestShapeError(str(exc)) from exc
    if len(set(out)) != len(out):
        raise _ManifestShapeError(f"{field} must be duplicate-free")
    return out


def _normalise_manifest(
    manifest: Mapping[str, Any],
) -> tuple[tuple[_ObservedDataset, ...], dict[str, _ObservedSource]]:
    raw_datasets = manifest.get("datasets")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_datasets, (list, tuple)) or not raw_datasets:
        raise _ManifestShapeError("manifest must contain at least one dataset")
    if not isinstance(raw_sources, (list, tuple)):
        raise _ManifestShapeError("manifest sources must be an array")

    datasets: list[_ObservedDataset] = []
    dataset_ids: set[str] = set()
    for raw in raw_datasets:
        if not isinstance(raw, Mapping):
            raise _ManifestShapeError("dataset row must be an object")
        try:
            dataset_id = _require_id(raw.get("dataset_id"), field="dataset_id")
            contract_digest = _require_digest(
                raw.get("contract_digest"), field="contract_digest"
            )
            schema_digest = _optional_digest(
                raw.get("result_schema_digest"), field="result_schema_digest"
            )
        except ValueError as exc:
            raise _ManifestShapeError(str(exc)) from exc
        if dataset_id in dataset_ids:
            raise _ManifestShapeError(f"duplicate dataset_id: {dataset_id}")
        dataset_ids.add(dataset_id)
        source_ids = _manifest_ids(
            raw.get("live_source_ids") or (), field="live_source_ids"
        )
        read_only = raw.get("read_only")
        if not isinstance(read_only, bool):
            raise _ManifestShapeError("dataset read_only must be a boolean")
        schema_status = str(raw.get("result_schema_status") or "unpublished")
        if schema_status not in {"published", "unpublished", "invalid"}:
            raise _ManifestShapeError("unknown result_schema_status")
        if schema_status == "published" and schema_digest is None:
            raise _ManifestShapeError(
                "published result_schema_status requires result_schema_digest"
            )
        if schema_status != "published" and schema_digest is not None:
            raise _ManifestShapeError(
                "an unpublished/invalid result schema must not carry a digest"
            )
        datasets.append(_ObservedDataset(
            dataset_id=dataset_id,
            source_ids=source_ids,
            contract_digest=contract_digest,
            read_only=read_only,
            result_schema_status=schema_status,
            result_schema_digest=schema_digest,
        ))

    sources: dict[str, _ObservedSource] = {}
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise _ManifestShapeError("source row must be an object")
        try:
            source_id = _require_id(raw.get("source_id"), field="source_id")
            implementation_status = _require_id(
                raw.get("implementation_status") or "unknown",
                field="implementation_status",
            )
            provider_id = _require_id(
                raw.get("provider") or "unknown", field="provider"
            )
        except ValueError as exc:
            raise _ManifestShapeError(str(exc)) from exc
        if "dataset_ids" in raw:
            dataset_ids = _manifest_ids(
                raw.get("dataset_ids"), field="dataset_ids"
            )
            try:
                legacy_dataset_id = _optional_id(
                    raw.get("dataset_id"), field="dataset_id"
                )
            except ValueError as exc:
                raise _ManifestShapeError(str(exc)) from exc
            if legacy_dataset_id and legacy_dataset_id not in dataset_ids:
                raise _ManifestShapeError(
                    "legacy dataset_id must be a member of canonical dataset_ids"
                )
        else:
            try:
                legacy_dataset_id = _optional_id(
                    raw.get("dataset_id"), field="dataset_id"
                )
            except ValueError as exc:
                raise _ManifestShapeError(str(exc)) from exc
            dataset_ids = (
                (legacy_dataset_id,) if legacy_dataset_id else ()
            )
        if source_id in sources:
            raise _ManifestShapeError(f"duplicate source_id: {source_id}")
        sources[source_id] = _ObservedSource(
            source_id=source_id,
            dataset_ids=dataset_ids,
            implementation_status=implementation_status,
            provider_id=provider_id,
        )

    for dataset in datasets:
        for source_id in dataset.source_ids:
            source = sources.get(source_id)
            if source is None:
                raise _ManifestShapeError(
                    f"dataset {dataset.dataset_id!r} references an absent source"
                )
            if dataset.dataset_id not in source.dataset_ids:
                raise _ManifestShapeError(
                    f"source {source_id!r} does not bind its declaring dataset"
                )
    return tuple(sorted(datasets, key=lambda row: row.dataset_id)), sources


def _finding(
    *,
    dataset_id: str,
    source_id: str | None,
    method_id: str | None,
    classification: RuntimeDataCatalogClassification,
    reasons: Iterable[str],
    adapter_id: str | None = None,
    adapter_version: str | None = None,
    adapter_support_digest: str | None = None,
    contract_digest: str = "",
    result_schema_digest: str | None = None,
) -> RuntimeDataCatalogFinding:
    return RuntimeDataCatalogFinding(
        dataset_id=dataset_id,
        source_id=source_id,
        method_id=method_id,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        adapter_support_digest=adapter_support_digest,
        classification=classification,
        reason_codes=_sorted_reasons(reasons),
        contract_digest=contract_digest,
        result_schema_digest=result_schema_digest,
    )


def _make_report(
    *,
    manifest_schema_version: str,
    manifest_catalog_digest: str,
    reviewed_bindings_digest: str,
    reviewed_adapter_support_digest: str,
    authoritative: bool,
    stale: bool,
    findings: Iterable[RuntimeDataCatalogFinding],
) -> RuntimeDataCatalogReport:
    ordered = tuple(sorted(findings, key=lambda finding: finding.sort_key))
    counts = tuple(
        (classification, sum(
            finding.classification == classification for finding in ordered
        ))
        for classification in _CLASSIFICATIONS
    )
    fields = {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "manifest_schema_version": manifest_schema_version,
        "manifest_catalog_digest": manifest_catalog_digest,
        "reviewed_bindings_digest": reviewed_bindings_digest,
        "reviewed_adapter_support_digest": reviewed_adapter_support_digest,
        "authoritative": authoritative,
        "stale": stale,
        "findings": ordered,
        "classification_counts": counts,
        "sealed_catalog_changed": False,
        "authority_changes": (),
    }
    semantic = {
        "schema_version": fields["schema_version"],
        "manifest_schema_version": fields["manifest_schema_version"],
        "manifest_catalog_digest": fields["manifest_catalog_digest"],
        "reviewed_bindings_digest": fields["reviewed_bindings_digest"],
        "reviewed_adapter_support_digest": fields[
            "reviewed_adapter_support_digest"
        ],
        "authoritative": fields["authoritative"],
        "stale": fields["stale"],
        "findings": [finding.semantic_mapping() for finding in ordered],
        "classification_counts": [list(row) for row in counts],
        "sealed_catalog_changed": False,
        "authority_changes": [],
    }
    digest = _canonical_digest(semantic)
    return RuntimeDataCatalogReport(**fields, report_digest=digest)


def _failure_report(
    manifest: Mapping[str, Any],
    *,
    reviewed_bindings_digest: str,
    reviewed_adapter_support_digest: str,
    reasons: Iterable[str],
) -> RuntimeDataCatalogReport:
    raw_digest = manifest.get("catalog_digest")
    manifest_digest = raw_digest if (
        isinstance(raw_digest, str) and _DIGEST_RE.fullmatch(raw_digest)
    ) else ""
    return _make_report(
        manifest_schema_version=str(manifest.get("schema_version") or "")[:128],
        manifest_catalog_digest=manifest_digest,
        reviewed_bindings_digest=reviewed_bindings_digest,
        reviewed_adapter_support_digest=reviewed_adapter_support_digest,
        authoritative=manifest.get("authoritative") is True,
        stale=manifest.get("stale") is True,
        findings=(_finding(
            dataset_id="manifest",
            source_id=None,
            method_id=None,
            classification="unsupported",
            reasons=reasons,
        ),),
    )


def _dataset_problem(
    dataset: _ObservedDataset,
    binding: ReviewedDataBinding,
) -> tuple[RuntimeDataCatalogClassification | None, tuple[str, ...]]:
    if not dataset.read_only:
        return "unsupported", ("write_enabled_dataset",)
    if dataset.result_schema_status == "invalid":
        return "unsupported", ("invalid_result_schema_identity",)
    if (
        binding.result_schema_digest is not None
        and dataset.result_schema_status == "published"
        and dataset.result_schema_digest != binding.result_schema_digest
    ):
        return "requires_contract", ("result_schema_identity_mismatch",)
    if dataset.contract_digest != binding.contract_digest:
        return "requires_review", ("dataset_contract_drift",)
    return None, ()


def build_runtime_data_catalog_report(
    manifest: Mapping[str, Any],
    *,
    reviewed_bindings: Iterable[ReviewedDataBinding] = (),
    reviewed_adapter_support: Iterable[ReviewedAdapterSupport] | None = None,
) -> RuntimeDataCatalogReport:
    """Classify one explicit capability-manifest mapping without side effects.

    The function neither loads nor caches a manifest.  ``reviewed_bindings`` is
    service-owned evidence describing what is already approved; observed manifest
    fields can only lower confidence or create a candidate/review finding.  When
    ``reviewed_adapter_support`` is omitted the code-owned LiveClientSource
    baseline is used.  No similarly named manifest field is consulted.
    """
    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a Mapping")

    bindings = tuple(sorted(
        reviewed_bindings,
        key=lambda row: (row.dataset_id, row.method_id),
    ))
    binding_keys = {
        (binding.dataset_id, binding.method_id) for binding in bindings
    }
    if len(binding_keys) != len(bindings):
        raise ValueError(
            "reviewed_bindings must be unique by (dataset_id, method_id)"
        )
    reviewed_digest = _canonical_digest([
        binding.semantic_mapping() for binding in bindings
    ])
    binding_adapter_keys = {
        (
            binding.dataset_id,
            binding.method_id,
            binding.adapter_id,
            binding.adapter_version,
        )
        for binding in bindings
    }
    raw_supports = (
        reviewed_live_client_adapter_support()
        if reviewed_adapter_support is None
        else tuple(reviewed_adapter_support)
    )
    if reviewed_adapter_support is None:
        # The module baseline covers all current LiveClientSource methods.  A
        # caller assessing only a subset of bindings receives only that subset;
        # unused reviewed evidence is not an orphan and cannot affect the report.
        raw_supports = tuple(
            support
            for support in raw_supports
            if (
                support.dataset_id,
                support.method_id,
                support.adapter_id,
                support.adapter_version,
            ) in binding_adapter_keys
        )
    supports = tuple(sorted(raw_supports, key=lambda row: row.key))
    support_keys = [support.key for support in supports]
    if len(set(support_keys)) != len(support_keys):
        raise ValueError(
            "reviewed_adapter_support must be unique by "
            "(dataset_id, method_id, source_id, adapter_id, adapter_version)"
        )
    orphan_support = [
        support.key
        for support in supports
        if (
            support.dataset_id,
            support.method_id,
            support.adapter_id,
            support.adapter_version,
        ) not in binding_adapter_keys
    ]
    if orphan_support:
        raise ValueError(
            "reviewed_adapter_support contains an entry without a reviewed "
            f"dataset/method/adapter binding: {orphan_support!r}"
        )
    support_by_key = {support.key: support for support in supports}
    reviewed_support_digest = _canonical_digest([
        support.semantic_mapping() for support in supports
    ])

    top_reasons: list[str] = []
    if manifest.get("ok") is not True:
        top_reasons.append("manifest_not_ok")
    if manifest.get("authoritative") is not True:
        top_reasons.append("manifest_not_authoritative")
    if manifest.get("stale") is True:
        top_reasons.append("manifest_stale")
    elif manifest.get("stale") is not False:
        top_reasons.append("manifest_stale_invalid")
    if manifest.get("schema_version") != _INPUT_SCHEMA_VERSION:
        top_reasons.append("manifest_schema_unsupported")
    raw_manifest_digest = manifest.get("catalog_digest")
    if not isinstance(raw_manifest_digest, str) or not _DIGEST_RE.fullmatch(
        raw_manifest_digest
    ):
        top_reasons.append("manifest_catalog_digest_invalid")
    elif manifest.get("schema_version") == _INPUT_SCHEMA_VERSION:
        try:
            observed_digest = _canonical_digest({
                "schema_version": manifest.get("schema_version"),
                "datasets": manifest.get("datasets"),
                "sources": manifest.get("sources"),
            })
        except (TypeError, ValueError):
            top_reasons.append("manifest_shape_invalid")
        else:
            if observed_digest != raw_manifest_digest:
                top_reasons.append("manifest_catalog_digest_mismatch")
    if top_reasons:
        return _failure_report(
            manifest,
            reviewed_bindings_digest=reviewed_digest,
            reviewed_adapter_support_digest=reviewed_support_digest,
            reasons=top_reasons,
        )

    try:
        datasets, sources = _normalise_manifest(manifest)
    except _ManifestShapeError:
        return _failure_report(
            manifest,
            reviewed_bindings_digest=reviewed_digest,
            reviewed_adapter_support_digest=reviewed_support_digest,
            reasons=("manifest_shape_invalid",),
        )

    bindings_by_dataset: dict[str, list[ReviewedDataBinding]] = {}
    for binding in bindings:
        bindings_by_dataset.setdefault(binding.dataset_id, []).append(binding)
    datasets_by_id = {dataset.dataset_id: dataset for dataset in datasets}
    handled_source_bindings: set[tuple[str, str]] = set()
    findings: list[RuntimeDataCatalogFinding] = []

    for dataset in datasets:
        dataset_bindings = bindings_by_dataset.get(dataset.dataset_id, ())
        source_ids: tuple[str | None, ...] = (
            tuple(dataset.source_ids) if dataset.source_ids else (None,)
        )
        handled_source_bindings.update(
            (dataset.dataset_id, source_id)
            for source_id in dataset.source_ids
        )

        if not dataset_bindings:
            for source_id in source_ids:
                source = sources.get(source_id) if source_id is not None else None
                if not dataset.read_only:
                    classification: RuntimeDataCatalogClassification = "unsupported"
                    reasons = ("write_enabled_dataset",)
                elif dataset.result_schema_status == "invalid":
                    classification = "unsupported"
                    reasons = ("invalid_result_schema_identity",)
                elif source is not None and source.implementation_status != "implemented":
                    classification = "unsupported"
                    reasons = ("source_not_implemented",)
                else:
                    # A schema digest alone cannot decide which reviewed Guanlan
                    # method this new dataset means.  A versioned method/schema
                    # contract is required before any authority can exist.
                    classification = "requires_contract"
                    reasons = ("dataset_not_mapped_to_reviewed_method",)
                findings.append(_finding(
                    dataset_id=dataset.dataset_id,
                    source_id=source_id,
                    method_id=None,
                    classification=classification,
                    reasons=reasons,
                    contract_digest=dataset.contract_digest,
                    result_schema_digest=dataset.result_schema_digest,
                ))
            continue

        for binding in dataset_bindings:
            problem, problem_reasons = _dataset_problem(dataset, binding)
            for source_id in source_ids:
                source = sources.get(source_id) if source_id is not None else None
                support_key = (
                    dataset.dataset_id,
                    binding.method_id,
                    source_id,
                    binding.adapter_id,
                    binding.adapter_version,
                ) if source_id is not None else None
                support = (
                    support_by_key.get(support_key)
                    if support_key is not None else None
                )
                finding_support_digest = (
                    support.adapter_contract_digest
                    if support is not None else None
                )
                if problem is not None:
                    classification = problem
                    reasons = problem_reasons
                elif source_id is None:
                    classification = "unsupported"
                    reasons = ("dataset_has_no_live_source",)
                elif (
                    source is not None
                    and source.implementation_status != "implemented"
                ):
                    classification = "unsupported"
                    reasons = ("source_not_implemented",)
                else:
                    is_current = source_id in binding.source_ids
                    reasons_list: list[str] = []
                    if not is_current:
                        reasons_list.append("new_provider")
                        if dataset.result_schema_status != "published":
                            reasons_list.append(
                                "result_schema_identity_unpublished"
                            )
                        if binding.result_schema_digest is None:
                            reasons_list.append(
                                "reviewed_result_schema_identity_absent"
                            )

                    if support is None:
                        # A manifest schema says nothing about whether this exact
                        # facade source can satisfy this exact adapter method.
                        # Absence of code-reviewed support therefore closes both
                        # the current and candidate paths.
                        classification = "requires_review"
                        reasons_list.append(
                            "adapter_support_attestation_missing"
                        )
                    elif source is None:
                        classification = "unsupported"
                        reasons_list.append("source_catalog_entry_missing")
                    elif source.provider_id != support.provider_id:
                        classification = "requires_review"
                        reasons_list.append(
                            "adapter_support_provider_mismatch"
                        )
                    elif is_current:
                        classification = "reviewed_current_binding"
                        reasons_list.extend((
                            "exact_reviewed_adapter_support",
                            "exact_reviewed_binding",
                            "source_provider_matches_reviewed_support",
                        ))
                    else:
                        schema_compatible = (
                            binding.result_schema_digest is not None
                            and dataset.result_schema_status == "published"
                            and dataset.result_schema_digest
                            == binding.result_schema_digest
                        )
                        support_schema_compatible = (
                            support.result_schema_digest is not None
                            and support.result_schema_digest
                            == binding.result_schema_digest
                        )
                        if schema_compatible and support_schema_compatible:
                            classification = "compatible_candidate"
                            reasons_list.extend((
                                "exact_reviewed_adapter_support",
                                "result_schema_identity_matches",
                                "source_provider_matches_reviewed_support",
                            ))
                        else:
                            classification = "requires_review"
                            if dataset.result_schema_status != "published":
                                reasons_list.append(
                                    "result_schema_identity_unpublished"
                                )
                            if binding.result_schema_digest is None:
                                reasons_list.append(
                                    "reviewed_result_schema_identity_absent"
                                )
                            if support.result_schema_digest is None:
                                reasons_list.append(
                                    "adapter_result_schema_identity_absent"
                                )
                            elif (
                                support.result_schema_digest
                                != binding.result_schema_digest
                            ):
                                reasons_list.append(
                                    "adapter_result_schema_identity_mismatch"
                                )
                    reasons = tuple(reasons_list)
                findings.append(_finding(
                    dataset_id=dataset.dataset_id,
                    source_id=source_id,
                    method_id=binding.method_id,
                    adapter_id=binding.adapter_id,
                    adapter_version=binding.adapter_version,
                    adapter_support_digest=finding_support_digest,
                    classification=classification,
                    reasons=reasons,
                    contract_digest=dataset.contract_digest,
                    result_schema_digest=dataset.result_schema_digest,
                ))

            for reviewed_source_id in binding.source_ids:
                if reviewed_source_id in dataset.source_ids:
                    continue
                handled_source_bindings.add((
                    dataset.dataset_id, reviewed_source_id
                ))
                support = support_by_key.get((
                    dataset.dataset_id,
                    binding.method_id,
                    reviewed_source_id,
                    binding.adapter_id,
                    binding.adapter_version,
                ))
                findings.append(_finding(
                    dataset_id=dataset.dataset_id,
                    source_id=reviewed_source_id,
                    method_id=binding.method_id,
                    adapter_id=binding.adapter_id,
                    adapter_version=binding.adapter_version,
                    adapter_support_digest=(
                        support.adapter_contract_digest
                        if support is not None else None
                    ),
                    classification="unsupported",
                    reasons=("reviewed_source_missing",),
                    contract_digest=dataset.contract_digest,
                    result_schema_digest=dataset.result_schema_digest,
                ))

    for binding in bindings:
        if binding.dataset_id in datasets_by_id:
            continue
        source_ids: tuple[str | None, ...] = (
            tuple(binding.source_ids) if binding.source_ids else (None,)
        )
        handled_source_bindings.update(
            (binding.dataset_id, source_id)
            for source_id in binding.source_ids
        )
        for source_id in source_ids:
            support = (
                support_by_key.get((
                    binding.dataset_id,
                    binding.method_id,
                    source_id,
                    binding.adapter_id,
                    binding.adapter_version,
                ))
                if source_id is not None else None
            )
            findings.append(_finding(
                dataset_id=binding.dataset_id,
                source_id=source_id,
                method_id=binding.method_id,
                adapter_id=binding.adapter_id,
                adapter_version=binding.adapter_version,
                adapter_support_digest=(
                    support.adapter_contract_digest
                    if support is not None else None
                ),
                classification="unsupported",
                reasons=("reviewed_dataset_missing",),
                contract_digest=binding.contract_digest,
                result_schema_digest=binding.result_schema_digest,
            ))

    # The datafeed source catalog can contain a source not yet owned by a dataset
    # contract.  It cannot be mapped to a method from source/provider names alone.
    for source_id, source in sorted(sources.items()):
        owner_ids = source.dataset_ids or ("unbound",)
        for dataset_id in owner_ids:
            if (dataset_id, source_id) in handled_source_bindings:
                continue
            classification = (
                "requires_contract"
                if source.implementation_status == "implemented"
                else "unsupported"
            )
            reasons = (
                ("source_has_no_dataset_contract",)
                if classification == "requires_contract"
                else (
                    "source_not_implemented",
                    "source_has_no_dataset_contract",
                )
            )
            findings.append(_finding(
                dataset_id=dataset_id,
                source_id=source_id,
                method_id=None,
                classification=classification,
                reasons=reasons,
            ))

    return _make_report(
        manifest_schema_version=_INPUT_SCHEMA_VERSION,
        manifest_catalog_digest=raw_manifest_digest,
        reviewed_bindings_digest=reviewed_digest,
        reviewed_adapter_support_digest=reviewed_support_digest,
        authoritative=True,
        stale=False,
        findings=findings,
    )


def runtime_data_catalog_support_summary(
    report: RuntimeDataCatalogReport,
) -> dict[str, Any]:
    """Return the small agent-facing projection of a diagnostic report.

    ``compatible_candidate`` deliberately remains outside ``active_bindings``:
    schema compatibility is evidence for a later reviewed route/catalog
    rebuild, not authority to mutate the current sealed runtime.
    """
    active = [
        {
            "dataset_id": finding.dataset_id,
            "method_id": finding.method_id,
            "source_id": finding.source_id,
        }
        for finding in report.findings
        if finding.classification == "reviewed_current_binding"
    ]
    candidates = [
        {
            "dataset_id": finding.dataset_id,
            "method_id": finding.method_id,
            "source_id": finding.source_id,
        }
        for finding in report.findings
        if finding.classification == "compatible_candidate"
    ]
    return {
        "schema_version": "runtime-data-catalog-support-summary.v1",
        "report_digest": report.report_digest,
        "manifest_catalog_digest": report.manifest_catalog_digest,
        "effect": "observation_only",
        "active_bindings": active,
        "compatible_candidates_not_activated": candidates,
        "classification_counts": dict(report.classification_counts),
        "sealed_catalog_changed": False,
        "authority_changes": [],
    }


def render_runtime_data_catalog_support(report: RuntimeDataCatalogReport) -> str:
    """Render an identifier-only, bounded summary for a trusted Agent prefix."""
    summary = runtime_data_catalog_support_summary(report)
    active = summary["active_bindings"]
    active_text = ", ".join(
        f"{row['dataset_id']}/{row['method_id']}={row['source_id']}"
        for row in active
    ) or "无"
    counts = summary["classification_counts"]
    return (
        "[系统·编排数据支持（只读报告，不授予权限）]\n"
        f"report={report.report_digest}；当前已审阅绑定: {active_text}。\n"
        "未激活观察项: "
        f"compatible_candidate={counts['compatible_candidate']}；"
        f"requires_review={counts['requires_review']}；"
        f"requires_contract={counts['requires_contract']}；"
        f"unsupported={counts['unsupported']}。"
        "只有 sealed catalog/Worker grant/prefetch 已有的绑定才能由子 Agent 调用。\n\n"
    )
