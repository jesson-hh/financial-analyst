"""Deterministic six-stage company-research gate.

This module owns only the state transition and artifact contracts.  It does not
fetch data, call a model, or write to a workspace.  Callers feed it the evidence
and gaps already resolved by the Evidence Hub, then optionally attach pure stage
handlers for calculations or report assembly.

The fixed stage order follows the Vibe Research company workflow::

    profile -> financials -> estimates -> valuation -> risk -> report

A critical gap closes the current stage and skips every downstream stage.  This
is deliberate: a missing required input must never be dressed up by running a
calculation that depends on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


STAGE_ORDER: tuple[str, ...] = (
    "profile",
    "financials",
    "estimates",
    "valuation",
    "risk",
    "report",
)

# Required slots mirror the primary Vibe contract.  Risk sources such as
# announcements and K-lines are useful but optional; absence is still recorded
# as a non-critical gap by the Evidence Hub instead of blocking the report.
REQUIRED_INPUTS: Mapping[str, tuple[str, ...]] = {
    "profile": ("profile", "quote", "trade_calendar"),
    "financials": ("financials",),
    "estimates": ("estimates",),
    "valuation": ("quote",),
    "risk": (),
    "report": (),
}


class StageStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    SKIPPED = "skipped"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class OverallStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


_UNAVAILABLE_EVIDENCE_STATUSES = {
    "failed",
    "missing",
    "not_found",
    "unavailable",
}


@dataclass(frozen=True)
class EvidenceItem:
    """One opaque Evidence Hub item used to satisfy an input slot."""

    input_key: str
    evidence_id: str = ""
    available: bool = True
    stale: bool = False
    payload: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        default_input_key: str = "",
    ) -> "EvidenceItem":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            input_key = _text(
                value.get("input_key")
                or value.get("slot")
                or value.get("kind")
                or default_input_key
            )
            status = _text(value.get("status")).lower()
            available = bool(value.get("available", True))
            if status in _UNAVAILABLE_EVIDENCE_STATUSES:
                available = False
            return cls(
                input_key=input_key,
                evidence_id=_text(value.get("evidence_id") or value.get("id")),
                available=available,
                stale=bool(value.get("stale", False)) or status == "stale",
                payload=value.get("payload", value),
            )
        return cls(
            input_key=_text(default_input_key),
            available=value is not None,
            payload=value,
        )


@dataclass(frozen=True)
class EvidenceGap:
    """A structured and reportable absence; never a fabricated fact."""

    code: str
    message: str
    stage: str = ""
    input_key: str = ""
    critical: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any, *, stage: str) -> "EvidenceGap":
        if isinstance(value, cls):
            if value.stage:
                return value
            return cls(
                code=value.code,
                message=value.message,
                stage=stage,
                input_key=value.input_key,
                critical=value.critical,
                details=dict(value.details),
            )
        if isinstance(value, Mapping):
            known = {
                "code",
                "message",
                "reason",
                "stage",
                "input_key",
                "slot",
                "critical",
                "details",
            }
            details = value.get("details")
            if not isinstance(details, Mapping):
                details = {key: raw for key, raw in value.items() if key not in known}
            return cls(
                code=_text(value.get("code")) or "evidence_gap",
                message=_text(value.get("message") or value.get("reason"))
                or "Evidence Hub reported a data gap.",
                stage=_text(value.get("stage")) or stage,
                input_key=_text(value.get("input_key") or value.get("slot")),
                critical=bool(value.get("critical", True)),
                details=dict(details),
            )
        return cls(
            code="evidence_gap",
            message=_text(value) or "Evidence Hub reported a data gap.",
            stage=stage,
        )

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "critical": self.critical,
        }
        if self.input_key:
            row["input_key"] = self.input_key
        if self.details:
            row["details"] = dict(self.details)
        return row


@dataclass(frozen=True)
class StageEvidence:
    """Evidence Hub response for one research stage."""

    evidence: tuple[EvidenceItem, ...] = ()
    gaps: tuple[EvidenceGap, ...] = ()
    stale: bool = False
    failure: str = ""

    @classmethod
    def from_value(cls, value: Any, *, stage: str) -> "StageEvidence":
        if isinstance(value, cls):
            return cls(
                evidence=tuple(value.evidence),
                gaps=tuple(EvidenceGap.from_value(gap, stage=stage) for gap in value.gaps),
                stale=value.stale,
                failure=value.failure,
            )
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise TypeError(f"stage evidence for {stage!r} must be a mapping or StageEvidence")

        reserved = {"evidence", "gaps", "stale", "failure", "error", "status"}
        raw_evidence = value.get("evidence")
        if raw_evidence is None:
            raw_evidence = {key: raw for key, raw in value.items() if key not in reserved}

        evidence = tuple(_normalise_evidence(raw_evidence))
        raw_gaps = value.get("gaps", ())
        if isinstance(raw_gaps, (str, bytes, Mapping, EvidenceGap)):
            raw_gaps = (raw_gaps,)
        gaps = tuple(EvidenceGap.from_value(gap, stage=stage) for gap in raw_gaps or ())

        status = _text(value.get("status")).lower()
        failure = _text(value.get("failure"))
        if not failure and status == "failed":
            failure = _text(value.get("error")) or "Evidence Hub stage failed."
        return cls(
            evidence=evidence,
            gaps=gaps,
            stale=bool(value.get("stale", False)) or status == "stale",
            failure=failure,
        )


@dataclass(frozen=True)
class StageComputation:
    """Optional in-memory artifacts returned by a successful stage handler."""

    calculations: tuple[Mapping[str, Any], ...] = ()
    events: tuple[Mapping[str, Any], ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    report: Any = None

    @classmethod
    def from_value(cls, value: Any) -> "StageComputation":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("a stage handler must return a mapping, StageComputation, or None")
        return cls(
            calculations=_mapping_rows(value.get("calculations")),
            events=_mapping_rows(value.get("events")),
            conflicts=_mapping_rows(value.get("conflicts")),
            report=value.get("report"),
        )


@dataclass(frozen=True)
class StageContext:
    stage: str
    index: int
    evidence: StageEvidence
    prior_results: tuple["StageResult", ...]


StageHandler = Callable[[StageContext], StageComputation | Mapping[str, Any] | None]


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: StageStatus
    required_inputs: tuple[str, ...]
    observed_inputs: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    gaps: tuple[EvidenceGap, ...]
    stale: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "stage": self.stage,
            "status": self.status.value,
            "required_inputs": list(self.required_inputs),
            "observed_inputs": list(self.observed_inputs),
            "evidence_ids": list(self.evidence_ids),
            "gaps": [gap.as_dict() for gap in self.gaps],
            "stale": self.stale,
        }
        if self.error:
            row["error"] = self.error
        return row


@dataclass(frozen=True)
class PipelineArtifacts:
    manifest: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]
    calculations: tuple[Mapping[str, Any], ...]
    conflicts: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": dict(self.manifest),
            "events": [dict(row) for row in self.events],
            "calculations": [dict(row) for row in self.calculations],
            "conflicts": [dict(row) for row in self.conflicts],
            "report": dict(self.report),
        }


@dataclass(frozen=True)
class CompanyResearchResult:
    status: OverallStatus
    stages: tuple[StageResult, ...]
    artifacts: PipelineArtifacts

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "stages": [stage.as_dict() for stage in self.stages],
            "artifacts": self.artifacts.as_dict(),
        }


class CompanyResearchPipeline:
    """Run the fixed company-research state machine without side effects."""

    def __init__(self, handlers: Mapping[str, StageHandler] | None = None) -> None:
        unknown = set(handlers or ()) - set(STAGE_ORDER)
        if unknown:
            raise ValueError(f"unknown company research handlers: {sorted(unknown)!r}")
        self._handlers = dict(handlers or {})

    def run(self, stage_evidence: Mapping[str, Any] | None = None) -> CompanyResearchResult:
        supplied = dict(stage_evidence or {})
        unknown = set(supplied) - set(STAGE_ORDER)
        if unknown:
            raise ValueError(f"unknown company research stages: {sorted(unknown)!r}")

        results: list[StageResult] = []
        events: list[dict[str, Any]] = []
        calculations: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        report_payload: Any = None

        for index, stage in enumerate(STAGE_ORDER):
            required = REQUIRED_INPUTS[stage]
            bundle = StageEvidence.from_value(supplied.get(stage), stage=stage)
            upstream = next(
                (result for result in results if result.status is not StageStatus.COMPLETE),
                None,
            )
            if upstream is not None:
                gap = EvidenceGap(
                    code="upstream_stage_not_complete",
                    message=(
                        f"Stage {stage!r} was not run because upstream stage "
                        f"{upstream.stage!r} is {upstream.status.value}."
                    ),
                    stage=stage,
                    input_key=upstream.stage,
                    critical=True,
                    details={"upstream_status": upstream.status.value},
                )
                result = StageResult(
                    stage=stage,
                    status=StageStatus.SKIPPED,
                    required_inputs=required,
                    observed_inputs=(),
                    evidence_ids=(),
                    gaps=(gap,),
                    stale=bundle.stale or any(item.stale for item in bundle.evidence),
                )
                results.append(result)
                events.append(_stage_event(result))
                continue

            observed = tuple(sorted({
                item.input_key
                for item in bundle.evidence
                if item.available and item.input_key
            }))
            evidence_ids = tuple(dict.fromkeys(
                item.evidence_id
                for item in bundle.evidence
                if item.available and item.evidence_id
            ))
            stale = bundle.stale or any(item.stale for item in bundle.evidence if item.available)

            if bundle.failure:
                gap = EvidenceGap(
                    code="evidence_hub_failed",
                    message=bundle.failure,
                    stage=stage,
                    critical=True,
                )
                result = StageResult(
                    stage=stage,
                    status=StageStatus.FAILED,
                    required_inputs=required,
                    observed_inputs=observed,
                    evidence_ids=evidence_ids,
                    gaps=(*bundle.gaps, gap),
                    stale=stale,
                    error=bundle.failure,
                )
                results.append(result)
                events.append(_stage_event(result))
                continue

            stage_gaps = list(bundle.gaps)
            critical_gap_slots = {gap.input_key for gap in stage_gaps if gap.critical}
            missing = [
                input_key
                for input_key in required
                if input_key not in observed and input_key not in critical_gap_slots
            ]
            for input_key in missing:
                stage_gaps.append(EvidenceGap(
                    code="missing_required_input",
                    message=f"Required input {input_key!r} is unavailable.",
                    stage=stage,
                    input_key=input_key,
                    critical=True,
                ))

            if any(gap.critical for gap in stage_gaps):
                result = StageResult(
                    stage=stage,
                    status=StageStatus.INCOMPLETE,
                    required_inputs=required,
                    observed_inputs=observed,
                    evidence_ids=evidence_ids,
                    gaps=tuple(stage_gaps),
                    stale=stale,
                )
                results.append(result)
                events.append(_stage_event(result))
                continue

            handler = self._handlers.get(stage)
            if stage == "report" and handler is None:
                gap = EvidenceGap(
                    code="report_handler_missing",
                    message="No report handler was supplied; no report was fabricated.",
                    stage=stage,
                    input_key="report",
                    critical=True,
                )
                result = StageResult(
                    stage=stage,
                    status=StageStatus.INCOMPLETE,
                    required_inputs=required,
                    observed_inputs=observed,
                    evidence_ids=evidence_ids,
                    gaps=(*stage_gaps, gap),
                    stale=stale,
                )
                results.append(result)
                events.append(_stage_event(result))
                continue

            try:
                computation = StageComputation.from_value(
                    handler(StageContext(stage, index, bundle, tuple(results)))
                    if handler is not None
                    else None
                )
            except Exception as exc:  # a stage failure is data, not a pipeline crash
                error = _exception_text(exc)
                gap = EvidenceGap(
                    code="stage_handler_failed",
                    message=error,
                    stage=stage,
                    critical=True,
                    details={"error_class": type(exc).__name__},
                )
                result = StageResult(
                    stage=stage,
                    status=StageStatus.FAILED,
                    required_inputs=required,
                    observed_inputs=observed,
                    evidence_ids=evidence_ids,
                    gaps=(*stage_gaps, gap),
                    stale=stale,
                    error=error,
                )
                results.append(result)
                events.append(_stage_event(result))
                continue

            if stage == "report" and computation.report is None:
                gap = EvidenceGap(
                    code="report_content_missing",
                    message="The report handler returned no report content.",
                    stage=stage,
                    input_key="report",
                    critical=True,
                )
                result = StageResult(
                    stage=stage,
                    status=StageStatus.INCOMPLETE,
                    required_inputs=required,
                    observed_inputs=observed,
                    evidence_ids=evidence_ids,
                    gaps=(*stage_gaps, gap),
                    stale=stale,
                )
                results.append(result)
                events.append(_stage_event(result))
                continue

            for row in computation.calculations:
                calculations.append(_stage_row(row, stage))
            for row in computation.conflicts:
                conflicts.append(_stage_row(row, stage))
            for row in computation.events:
                events.append(_stage_row(row, stage))
            if stage == "report" and computation.report is not None:
                report_payload = computation.report

            result = StageResult(
                stage=stage,
                status=StageStatus.COMPLETE,
                required_inputs=required,
                observed_inputs=observed,
                evidence_ids=evidence_ids,
                gaps=tuple(stage_gaps),
                stale=stale,
            )
            results.append(result)
            events.append(_stage_event(result))

        overall = _overall_status(results)
        all_gaps = [gap.as_dict() for result in results for gap in result.gaps]
        stage_rows = [result.as_dict() for result in results]
        for sequence, event in enumerate(events, start=1):
            event["sequence"] = sequence

        manifest = {
            "schema_version": "1",
            "pipeline": "company_research",
            "stage_order": list(STAGE_ORDER),
            "status": overall.value,
            "stale": any(result.stale for result in results),
            "stages": stage_rows,
            "gaps": all_gaps,
        }
        report = {
            "schema_version": "1",
            "status": overall.value,
            "stage_statuses": {result.stage: result.status.value for result in results},
            "gaps": all_gaps,
            "content": report_payload,
        }
        artifacts = PipelineArtifacts(
            manifest=manifest,
            events=tuple(events),
            calculations=tuple(calculations),
            conflicts=tuple(conflicts),
            report=report,
        )
        return CompanyResearchResult(overall, tuple(results), artifacts)


def run_company_research(
    stage_evidence: Mapping[str, Any] | None = None,
    *,
    handlers: Mapping[str, StageHandler] | None = None,
) -> CompanyResearchResult:
    """Convenience entry point for Evidence Hub and workspace adapters."""
    return CompanyResearchPipeline(handlers=handlers).run(stage_evidence)


def _normalise_evidence(value: Any) -> Iterable[EvidenceItem]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        rows: list[EvidenceItem] = []
        for input_key, raw in value.items():
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                rows.extend(
                    EvidenceItem.from_value(item, default_input_key=_text(input_key))
                    for item in raw
                )
            else:
                rows.append(EvidenceItem.from_value(raw, default_input_key=_text(input_key)))
        return tuple(rows)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(EvidenceItem.from_value(item) for item in value)
    return (EvidenceItem.from_value(value),)


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (dict(value),)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows: list[Mapping[str, Any]] = []
        for row in value:
            if not isinstance(row, Mapping):
                raise TypeError("stage artifact rows must be mappings")
            rows.append(dict(row))
        return tuple(rows)
    raise TypeError("stage artifacts must be a mapping, a sequence of mappings, or None")


def _stage_row(row: Mapping[str, Any], stage: str) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("stage", stage)
    return out


def _stage_event(result: StageResult) -> dict[str, Any]:
    return {
        "event": "stage_finished",
        "stage": result.stage,
        "status": result.status.value,
        "stale": result.stale,
        "gap_codes": [gap.code for gap in result.gaps],
    }


def _overall_status(results: Iterable[StageResult]) -> OverallStatus:
    rows = tuple(results)
    if any(result.status is StageStatus.FAILED for result in rows):
        return OverallStatus.FAILED
    if any(result.stale for result in rows):
        return OverallStatus.STALE
    if any(result.status in {StageStatus.INCOMPLETE, StageStatus.SKIPPED} for result in rows):
        return OverallStatus.INCOMPLETE
    return OverallStatus.COMPLETE


def _exception_text(exc: Exception) -> str:
    message = " ".join(str(exc).replace("\x00", " ").split())[:500]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


__all__ = [
    "CompanyResearchPipeline",
    "CompanyResearchResult",
    "EvidenceGap",
    "EvidenceItem",
    "OverallStatus",
    "PipelineArtifacts",
    "REQUIRED_INPUTS",
    "STAGE_ORDER",
    "StageComputation",
    "StageContext",
    "StageEvidence",
    "StageHandler",
    "StageResult",
    "StageStatus",
    "run_company_research",
]
