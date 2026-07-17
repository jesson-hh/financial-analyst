# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — the frozen :class:`RenderedDataBlock` contract + its pure,
deterministic builder.

A rendered data block is what a prompt assembler injects as an *untrusted-data*
envelope: it never carries authority, always binds the exact typed result it was
rendered from, and its text is a pure deterministic function of that result. The
renderer identity comes only from the owning
:class:`~guanlan_v2.orchestration.data.source.DataMethodSpec` — caller/model input
can never select a renderer. Task 7 implements richer renderer *behavior* over
this already-registered contract; it does not introduce a late unregistered model.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import model_validator

from guanlan_v2.orchestration.data.result import DataResult
from guanlan_v2.orchestration.data.source import DataMethodSpec
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    canonical_json,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataStatus
from guanlan_v2.orchestration.refs import ContentRef, TypedPayloadRef

__all__ = [
    "RenderedDataBlock",
    "build_rendered_data_block",
    "RENDER_PUBLIC_MODELS",
    "RENDER_INTERNAL_MODELS",
]

_DIGEST_PLACEHOLDER = "0" * 64


class RenderedDataBlock(DigestModel):
    """One deterministic, untrusted-data rendering of a typed DataResult.

    Binds the exact ``renderer_ref`` (from the method spec — never caller input),
    the exact main ``result_ref`` (:class:`TypedPayloadRef`), the source result
    content / PIT-audit digests, the status/provenance fields, the closed
    ``trust="untrusted_data"`` marker, the deterministic media type/text and the
    ``rendered_from_payload_digest``. ``block_digest`` self-seals the block.
    """

    schema_version: Literal["1"] = "1"
    renderer_ref: ContentRef
    result_ref: TypedPayloadRef
    result_content_digest: DigestHex
    pit_audit_digest: DigestHex
    data_content_digest: DigestHex | None = None
    status: DataStatus
    method: NonEmptyStr
    request_digest: DigestHex
    trust: Literal["untrusted_data"] = "untrusted_data"
    media_type: Literal["application/json"] = "application/json"
    rendered_text: NonEmptyStr
    rendered_length: NonNegativeInt
    rendered_from_payload_digest: DigestHex
    block_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"block_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RenderedDataBlock":
        if self.result_ref.payload_ref.namespace != "main":
            raise ValueError("RenderedDataBlock.result_ref must be main-namespace")
        if self.result_ref.payload_ref.content_digest != self.result_content_digest:
            raise ValueError("result_ref content digest must equal result_content_digest")
        if self.rendered_from_payload_digest != self.result_content_digest:
            raise ValueError(
                "rendered_from_payload_digest must equal the source result content digest"
            )
        if self.rendered_length != len(self.rendered_text):
            raise ValueError("rendered_length must equal len(rendered_text)")
        if self.block_digest != self.semantic_digest():
            raise ValueError("declared block_digest does not match canonical digest")
        return self


def _deterministic_render(result: DataResult) -> str:
    """The v1 deterministic rendering: canonical JSON of a stable projection.

    A pure function of the result's semantic content — no wall clock, no locale,
    no randomness. Rendering the same result twice yields byte-identical text.
    """
    rows = ()
    if result.data is not None:
        rows = getattr(result.data, "rows", ())
    projection = {
        "method": result.method,
        "status": result.status.value,
        "row_count": len(rows),
        "data_content_digest": result.data_content_digest,
        "content_digest": result.content_digest,
        "coverage": result.coverage,
        "degradation_reason": result.degradation_reason,
        "warnings": list(result.warnings),
        "badges": list(result.badges),
        "trust": "untrusted_data",
    }
    return canonical_json(projection)


def build_rendered_data_block(
    *,
    method_spec: DataMethodSpec,
    result: DataResult,
    result_ref: TypedPayloadRef,
    registry: Any,
) -> RenderedDataBlock:
    """Render one typed result into a sealed :class:`RenderedDataBlock`.

    Derives and verifies the result SchemaRef from the typed ref against the
    method spec *and* the loaded named result — a detached schema/ref pair is
    never accepted:

    * ``result_ref.schema_ref`` must equal ``method_spec.result_schema_ref``;
    * the registry must resolve that ref to the exact loaded result's class;
    * ``result_ref.payload_ref.content_digest`` must equal the loaded result's
      ``content_digest``.

    The renderer is ``method_spec.renderer_ref`` — there is deliberately no
    caller-supplied renderer parameter.
    """
    if result_ref.schema_ref != method_spec.result_schema_ref:
        raise ValueError(
            "result_ref schema does not equal the method spec's named result schema"
        )
    resolved = registry.resolve(result_ref.schema_ref)
    if type(result) is not resolved:
        raise ValueError(
            f"loaded result type {type(result).__name__} is not the registered "
            f"{result_ref.schema_ref.key} model"
        )
    if result_ref.payload_ref.content_digest != result.content_digest:
        raise ValueError("result_ref content digest does not equal the loaded result digest")
    if result.method != method_spec.method_id:
        raise ValueError("loaded result method does not equal the method spec id")

    text = _deterministic_render(result)
    fields = dict(
        renderer_ref=method_spec.renderer_ref,
        result_ref=result_ref,
        result_content_digest=result.content_digest,
        pit_audit_digest=content_digest(result.pit_audit),
        data_content_digest=result.data_content_digest,
        status=result.status,
        method=result.method,
        request_digest=result.request_digest,
        trust="untrusted_data",
        media_type="application/json",
        rendered_text=text,
        rendered_length=len(text),
        rendered_from_payload_digest=result.content_digest,
    )
    try:
        digest = RenderedDataBlock.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    return RenderedDataBlock(**fields, block_digest=digest)


#: reviewed Task-5 data-only partition contribution of this module.
RENDER_PUBLIC_MODELS: tuple[type, ...] = (RenderedDataBlock,)
RENDER_INTERNAL_MODELS: dict[type, str] = {}
