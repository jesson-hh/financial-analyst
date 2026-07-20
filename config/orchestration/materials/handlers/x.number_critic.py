# -*- coding: utf-8 -*-
"""handler.x.number_critic — deterministic number/honesty critic (Phase 8, Task 7).

Branch-1 clause (h): the legacy honesty spine ``guanlan_v2.orchestration.honesty`` IS an
import-safe, side-effect-free pure module (no I/O, no LLM, no wall-clock), so this handler
binds it DIRECTLY (exactly like ``handler.pv.price_action`` binds ``compute_pa_features``)
— it never re-implements the anchor↔leaf reconciliation or the classify rule matrix. Each
wired input Artifact is a SUBJECT; :func:`critique_subject` delegates to
``honesty.classify_worker`` verbatim, emitting one ``HonestyReport`` per subject. The
数字溯源门: a fabricated number (anchor value != payload leaf) is a HARD integrity failure
→ the subject's report is ``incomplete`` REGARDLESS of any unsourced allowance; an
unsourced number forces the ``[UNSOURCED]`` badge that propagates to the offending
artifact's consumers.

CONTROLLER RULING (a) — carried: ``require_number_anchors`` on EvidencePolicy is
INTENTIONALLY SUBSUMED by ``allow_unsourced_numbers`` in @1 (a future rev may wire it);
this seat sets ``require_number_anchors=False`` (it PRODUCES the anchor verdicts) and must
never configure the contradictory ``require_number_anchors=True`` +
``allow_unsourced_numbers=True`` combo.
CONTROLLER RULING (b) — carried: rendered-markdown / string-embedded numbers are OUTSIDE
the scan boundary (a known accepted limit); the scan reads the typed payload's semantic
canonical JSON only, never ``rendered_md`` prose.

Forward-compatible attribution surface: the emitted ``HonestyReport`` tuple is EXACTLY the
input ``honesty.attribution_candidates`` consumes (spec §6.3) — together with ``input_refs``
chains and Task 9's ``DebateTranscript`` (which join here later, additively). This handler
couples to NEITHER a premature transcript NOR any Lane-D debate type.

Pure + import-safe: binds ONLY the pure honesty spine (no engine / datafeed / network / LLM).
"""
from __future__ import annotations

from guanlan_v2.orchestration.honesty import (
    attribution_candidates,
    classify_worker,
    scan_unsourced_numbers,
)


def scan(artifact):
    """``honesty.scan_unsourced_numbers`` verbatim — the pure anchor↔leaf reconciliation."""
    return scan_unsourced_numbers(artifact)


def critique_subject(*, worker, node_run, artifact):
    """One subject Artifact → its ``HonestyReport`` (``honesty.classify_worker`` verbatim).

    A fabricated number (anchor != leaf) or an anchor pointing nowhere yields verdict
    ``incomplete`` unconditionally (编数 is a hard integrity failure regardless of the
    unsourced flag); a disallowed unsourced number yields ``incomplete`` + ``[UNSOURCED]``.
    """
    return classify_worker(worker=worker, node_run=node_run, artifact=artifact)


def critique(subjects):
    """Order-stable multi-subject critique → ``(primary, reports)``.

    ``subjects``: an iterable of ``(input_name, worker, node_run, artifact)`` tuples
    (``input_name`` = the wired-input name). Subjects are processed in ``input_name`` order,
    so the result is BYTE-STABLE under input-artifact reordering. ``reports`` is one
    ``HonestyReport`` per subject (in ``input_name`` order); ``primary`` is the report of
    the FIRST (by input name) subject whose verdict != ``ok`` — the offending subject whose
    数字溯源门 ``[UNSOURCED]`` badge propagates — else the first subject's report. ``primary``
    is ``None`` iff there are no subjects.
    """
    ordered = sorted(subjects, key=lambda s: s[0])
    reports = tuple(
        classify_worker(worker=w, node_run=nr, artifact=a)
        for _name, w, nr, a in ordered
    )
    primary = next((r for r in reports if r.verdict != "ok"), None)
    if primary is None and reports:
        primary = reports[0]
    return primary, reports


def attribution(reports):
    """The forward-compatible §6.3 attribution subset (``honesty.attribution_candidates``).

    The verdict≠ok, ``node_id``-sorted subset the Phase-4 evaluator consumes BEFORE any LLM
    judging — coupled to NO Task-9 ``DebateTranscript`` (that + ``input_refs`` join here
    later, additively; the closed issue-code vocabulary keeps the surface stable).
    """
    return attribution_candidates(tuple(reports))
