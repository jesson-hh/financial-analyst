# -*- coding: utf-8 -*-
"""The reviewed strict-JSON decode recipe for raw LLM completions — ONE copy.

D2/D3 (2026-07-29, the first live Lane-0 run) established the only discipline
under which a real model's JSON can lawfully become a typed report:

1. **Strict JSON-mode decoding.** ``DigestModel`` is ``strict=True`` and strict
   PYTHON-mode validation refuses every JSON-expressible conversion (a ``str``
   is not an enum member, a ``list`` is not a ``tuple``, an ISO string is not a
   ``datetime``) — so no model completion, however correct, could ever pass.
   Strict JSON mode allows exactly the conversions JSON can express and nothing
   more.
2. **Runtime-owned fields.** Fields a model cannot know (self-seals, full
   digests, the run's own timestamps) are never demanded from it: the model's
   value is dropped and the runtime stamps what it measured.
3. **A NARROW named envelope unwrap.** Exactly one key, named exactly the
   schema's snake_case name, wrapping a mapping — never "the first dict that
   fits".

That treatment was implemented as a Lane-0 gateway decorator in ``bootstrap``;
the deep lane's ``pipeline.assembly.WorkerSeatModelGateway`` never received it,
and on 2026-07-30 the live deep run ``deep-f1f0031d521bea3a`` died of the same
disease (``'Neutral' is not an instance of SentimentBand``). The machinery was
therefore EXTRACTED here (2026-07-31) so ``bootstrap`` (Lane 0) and
``pipeline.assembly`` (the worker seats) consume one recipe and cannot drift.
Both callers invoke :func:`decode_llm_output_json` THROUGH this module object —
the drift guard in ``tests/orchestration/test_seat_output_decode.py`` pins it.

The honesty red line, unchanged from the reviewed D2 decorator: this layer
NEVER repairs a judgement. It drops no unknown field, fills no missing one and
coerces nothing JSON cannot express; anything it cannot decode comes back
byte-identical (the ORIGINAL payload object) so the executor's own
``output_schema_invalid`` refusal stands with the true errors.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Callable

__all__ = [
    "DEEP_SEAT_RUNTIME_OWNED_FIELDS",
    "decode_field",
    "decode_llm_output_json",
    "envelope_key",
]

#: The deep/worker seats' runtime-owned field set. ``as_of`` is the run's own
#: clock: the committed ``RunSubject@1``'s stamp, echoed by the
#: ``SubjectPromptAssembler`` into the canonical channel's ``trusted_subject``
#: section. Asking a model for it is asking it to invent (or at best transcribe)
#: a timestamp the runtime already owns — the live ``bull-r1`` refusal recorded
#: exactly that demand. Declared here (not in ``assembly``) so the assembler's
#: derived output-schema section and the seat gateway read the SAME tuple.
DEEP_SEAT_RUNTIME_OWNED_FIELDS: tuple[str, ...] = ("as_of",)


def envelope_key(model: type) -> str:
    """``RotationReport`` → ``rotation_report`` (the ONLY unwrappable key)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", model.__name__).lower()


def decode_field(annotation: Any, value: Any) -> Any:
    """One field, decoded from JSON under STRICT discipline (never lax coercion)."""
    from pydantic import TypeAdapter

    return TypeAdapter(annotation).validate_json(
        json.dumps(value, ensure_ascii=False), strict=True)


def decode_llm_output_json(
    payload: Any,
    *,
    model: type,
    runtime_owned_fields: tuple[str, ...] = (),
    prepare: Callable[[dict], dict | None] | None = None,
    construct: Callable[[dict], Any] | None = None,
    refusal_sink: Callable[[str], None] | None = None,
) -> Any:
    """Decode one raw LLM JSON answer into a typed ``model``, or refuse.

    Returns a constructed instance when — and only when — the model's own
    answer decodes cleanly; otherwise returns ``payload`` **unchanged** (the
    same object) so the caller's executor refuses honestly
    (``output_schema_invalid``) with the real errors.

    In order: a single-key envelope whose key is exactly the schema's
    snake_case name is unwrapped; every ``runtime_owned_fields`` entry the
    model wrote is dropped (the runtime re-supplies its own measurement via
    ``construct``); the optional ``prepare`` hook may adjust the raw mapping
    (Lane 0's empty-evidence licence), refuse by returning ``None``, or raise a
    wiring-guard error that PROPAGATES; every remaining field is decoded from
    JSON in strict mode against the model's own declared annotation; finally
    ``construct(fields)`` builds the instance — every model validator still
    runs, and a rule the answer breaks is a refusal, never a repair.

    ``construct`` defaults to plain construction (``model(**fields)``); Lane 0
    passes its sealing ``model.build(...)`` with the runtime stamps closed over.

    ``refusal_sink`` receives the TRUE error text whenever this recipe refuses
    — the exact rule or type error measured under the full discipline (stamps
    included), which the executor cannot re-derive from the raw payload alone.
    The refusing caller forwards it as ``ModelResult.decode_refusal`` so the
    recorded ``output_schema_invalid`` reason is the real defect, never a
    python-mode artifact and never missing-runtime-field noise.
    """
    def _refuse(reason: str) -> Any:
        if refusal_sink is not None:
            refusal_sink(reason)
        return payload

    if not isinstance(payload, Mapping):
        return payload
    raw = dict(payload)
    key = envelope_key(model)
    if len(raw) == 1 and key in raw and isinstance(raw[key], Mapping):
        raw = dict(raw[key])
    for field in runtime_owned_fields:
        raw.pop(field, None)
    if prepare is not None:
        prepared = prepare(raw)     # a wiring-guard raise propagates untouched
        if prepared is None:
            return payload          # the caller sinks its own named reason
        raw = prepared

    fields: dict[str, Any] = {}
    model_fields = model.model_fields
    for name, value in raw.items():
        spec = model_fields.get(name)
        if spec is None:            # an undeclared field is the model's error
            return _refuse(
                f"{model.__name__} does not declare a field {name!r}; an "
                "undeclared field is refused, never silently dropped")
        try:
            fields[name] = decode_field(spec.annotation, value)
        except Exception as exc:    # noqa: BLE001 — an undecodable field is a refusal
            return _refuse(f"{model.__name__}.{name}: {exc}")
    build = construct if construct is not None else (lambda f: model(**f))
    try:
        return build(fields)
    except Exception as exc:        # noqa: BLE001 — a rule the answer breaks is a refusal
        return _refuse(str(exc))
