# -*- coding: utf-8 -*-
"""Trusted deterministic handlers for the two price-volume evidence seats.

The sealed Phase-3 binding grants ``pv.price_action`` typed OHLCV and grants
``pv.microstructure`` a verified quote anchor plus typed L1 indicators.  This
wrapper consumes only those persisted ``DataResult`` references: price action
delegates to the shared ``seats.price_action.compute_pa_features`` spine, while
microstructure projects the exact-code L1 spread and displayed-depth imbalance.
No handler performs network I/O or executes sealed material bytes.

Evidence is fail-closed.  Missing refs, an unbound payload reader, fewer than 20
PIT bars, a missing quote anchor, or an incomplete L1 pair raises a typed
refusal; nothing is imputed.  Tick-tape and fund-flow remain optional and their
absence is carried as named degradation warnings even when the complete L1 pair
is usable by the ten-seat quality gate.
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "AUX_DATA_UNGRANTED_CODE",
    "AUX_COMPUTE_NOT_WIRED_CODE",
    "PV_AUX_HANDLER_IDS",
    "PvAuxHandlerError",
    "AuxDataUngranted",
    "AuxComputeNotWired",
    "AuxEvidenceInsufficient",
    "build_pv_aux_handler_factory",
    "pv_aux_handler_registry",
]

#: the typed refusal codes.  ``aux_data_ungranted`` leads the reason string the
#: executor seals, so the node's outcome is readable without unpacking the
#: exception type (``worker.py``'s deterministic branch stamps a fixed
#: ``reason_code="handler_error"`` for ANY handler exception and carries the
#: typed identity in ``error_type`` + ``reason`` — the two channels this uses).
AUX_DATA_UNGRANTED_CODE = "aux_data_ungranted"
AUX_COMPUTE_NOT_WIRED_CODE = "aux_compute_not_wired"

#: worker id -> its sealed catalog handler material id (``lane_catalog._PV_ROWS``).
#: Registration is keyed by the SNAPSHOT's own ``ContentRef`` for these ids —
#: never by an id string alone and never by a digest computed here.
PV_AUX_HANDLER_IDS: dict[str, str] = {
    "pv.price_action": "handler.pv.price_action",
    "pv.microstructure": "handler.pv.microstructure",
}

Factory = Callable[..., Any]


# =========================================================================== #
# typed refusals                                                               #
# =========================================================================== #
class PvAuxHandlerError(RuntimeError):
    """Base of every typed refusal a pv aux handler may raise."""

    reason_code = "pv_aux_handler_error"

    def __init__(self, message: str, *, worker_id: str = "",
                 capability_ids: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.worker_id = worker_id
        self.capability_ids = tuple(capability_ids)


class AuxDataUngranted(PvAuxHandlerError):
    """No data reached the seat, because no data was ever granted to it.

    The L2-b end state for both pv aux nodes: the seat holds data capabilities,
    the sealed prefetch binding grants it no row, its bridge completed the
    licensed EMPTY — so there is nothing to compute over and the handler refuses
    rather than emitting a report over nothing.
    """

    reason_code = AUX_DATA_UNGRANTED_CODE


class AuxComputeNotWired(PvAuxHandlerError):
    """Data DID arrive — and this L2-b handler has no code to read it.

    The L3 tripwire (see the module docstring's exit gate).  Refusing here is
    the only honest answer: a report that ignored the granted rows would claim a
    reading that was never performed.
    """

    reason_code = AUX_COMPUTE_NOT_WIRED_CODE


class AuxEvidenceInsufficient(PvAuxHandlerError):
    """The granted typed rows cannot honestly satisfy the output contract."""

    reason_code = "aux_evidence_insufficient"


# =========================================================================== #
# the trusted factories (the two-stage handler ABI)                            #
# =========================================================================== #
def _held_capability_ids(worker: Any, worker_id: str) -> tuple[str, ...]:
    """The capability ids the SEALED WorkerSpec grants this seat, sorted.

    Read off the catalog's own ``capability_allowlist`` so the refusal can never
    drift from what the seat actually holds.  A factory invoked without its
    WorkerSpec (or with another seat's) refuses at BIND time — naming an
    invented allowlist would be exactly the fabrication this task exists to
    forbid.

    A bind-time refusal is DELIBERATELY not the aux-node outcome: it fires only
    on catalog drift or a misused factory, i.e. a wiring failure, and it is
    raised where ``worker.py`` resolves the factory (outside the deterministic
    branch's ``try``), so it propagates and ``dag.run_plan`` records it as
    ``executor_exception``.  That is the right shape — a broken wire must not
    masquerade as a statement about the market.
    """
    if worker is None:
        raise PvAuxHandlerError(
            f"{worker_id} handler factory was invoked without its WorkerSpec; "
            "the refusal it exists to raise must name the capabilities the "
            "sealed seat actually holds, and this wrapper will not guess them",
            worker_id=worker_id)
    seen_id = getattr(worker, "id", None)
    if seen_id != worker_id:
        raise PvAuxHandlerError(
            f"the {PV_AUX_HANDLER_IDS[worker_id]!r} factory was invoked for "
            f"worker {seen_id!r}, not {worker_id!r}; a handler bound to one "
            "sealed seat never serves another",
            worker_id=worker_id)
    allowlist = tuple(getattr(worker, "capability_allowlist", ()) or ())
    if not allowlist:
        raise PvAuxHandlerError(
            f"{worker_id}'s WorkerSpec carries an EMPTY capability allowlist; "
            "the sealed roster gives both pv aux seats data capabilities, so "
            "this is catalog drift, not a runnable seat",
            worker_id=worker_id)
    return tuple(sorted(str(cap.id) for cap in allowlist))


def _load_data_results(payload_reader: Any, refs: tuple[Any, ...], *,
                       worker_id: str, capability_ids: tuple[str, ...]) -> tuple[Any, ...]:
    if payload_reader is None:
        raise AuxComputeNotWired(
            f"{AUX_COMPUTE_NOT_WIRED_CODE}: {worker_id} received typed data refs "
            "but its handler factory has no trusted payload reader bound",
            worker_id=worker_id, capability_ids=capability_ids)
    out = []
    for typed in refs:
        try:
            out.append(payload_reader.get(
                typed.payload_ref, expected_schema_ref=typed.schema_ref))
        except Exception as exc:
            raise AuxEvidenceInsufficient(
                f"{worker_id} could not resolve a granted typed data result: {exc}",
                worker_id=worker_id, capability_ids=capability_ids) from exc
    return tuple(out)


def _price_action_result(results: tuple[Any, ...], *, worker_id: str,
                         capability_ids: tuple[str, ...]):
    from guanlan_v2.orchestration.data.source import OHLCVDataResult
    from guanlan_v2.orchestration.lane_payloads import (
        PA_FEATURE_SET_KEYS,
        PriceActionFeatureReport,
    )
    from guanlan_v2.orchestration.worker import ModelResult
    from guanlan_v2.seats.price_action import compute_pa_features

    rows = []
    for result in results:
        if not isinstance(result, OHLCVDataResult):
            continue
        rows.extend(tuple(getattr(getattr(result, "data", None), "rows", ()) or ()))
    rows = sorted(rows, key=lambda row: row.effective_at)
    if len(rows) < 20:
        raise AuxEvidenceInsufficient(
            f"{worker_id} needs at least 20 PIT OHLCV bars for pa-15key-v1; "
            f"the granted result contains {len(rows)}",
            worker_id=worker_id, capability_ids=capability_ids)

    # The reviewed compute spine is already the Seats/UI source of truth.  The
    # dataframe is a transient adapter over schema-validated PIT rows; it is not
    # another market-data read.
    import pandas as pd

    frame = pd.DataFrame([{
        "trade_date": row.effective_at,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "vol": row.volume,
    } for row in rows])
    latest = rows[-1]
    raw = compute_pa_features(frame, latest.symbol.dotted, "")
    keys = PA_FEATURE_SET_KEYS["pa-15key-v1"]
    missing = tuple(key for key in keys if raw.get(key) is None)
    if missing:
        raise AuxEvidenceInsufficient(
            f"{worker_id} cannot form pa-15key-v1; missing finite features "
            f"{missing!r}", worker_id=worker_id, capability_ids=capability_ids)
    report = PriceActionFeatureReport(
        symbol=latest.symbol,
        as_of=latest.effective_at,
        feature_set_version="pa-15key-v1",
        features={key: float(raw[key]) for key in keys},
        patterns=(),
    )
    return ModelResult(
        payload=report,
        rendered_text=(
            f"{worker_id}: pa-15key-v1 computed from {len(rows)} committed "
            f"OHLCV bars; patterns=0"),
        provider="deterministic", model="compute_pa_features")


def _microstructure_result(results: tuple[Any, ...], *, worker_id: str,
                           capability_ids: tuple[str, ...]):
    from guanlan_v2.orchestration.adapters.live_data import (
        POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX,
    )
    from guanlan_v2.orchestration.data.source import (
        IndicatorDataResult,
        VerifiedSnapshotDataResult,
    )
    from guanlan_v2.orchestration.lane_payloads import MicrostructureReport
    from guanlan_v2.orchestration.worker import ModelResult

    anchors = []
    indicators = []
    for result in results:
        if isinstance(result, VerifiedSnapshotDataResult):
            anchors.extend(tuple(
                getattr(getattr(result, "data", None), "rows", ()) or ()))
        elif isinstance(result, IndicatorDataResult):
            indicators.extend(tuple(
                getattr(getattr(result, "data", None), "rows", ()) or ()))
    anchors = sorted(anchors, key=lambda row: row.available_at)
    if not anchors:
        raise AuxEvidenceInsufficient(
            f"{worker_id} received no verified snapshot row",
            worker_id=worker_id, capability_ids=capability_ids)
    latest = anchors[-1]
    latest_by_id: dict[str, Any] = {}
    for row in sorted(indicators, key=lambda row: row.available_at):
        if row.symbol != latest.symbol:
            raise AuxEvidenceInsufficient(
                f"{worker_id} indicator symbol {row.symbol.dotted} does not match "
                f"verified anchor {latest.symbol.dotted}",
                worker_id=worker_id, capability_ids=capability_ids)
        if row.indicator_id in {"l1_spread_bp", "bid_ask_imbalance"}:
            latest_by_id[row.indicator_id] = row
    missing = tuple(
        metric for metric in ("l1_spread_bp", "bid_ask_imbalance")
        if metric not in latest_by_id)
    if missing:
        raise AuxEvidenceInsufficient(
            f"{worker_id} has no complete typed L1 book projection; missing {missing!r}",
            worker_id=worker_id, capability_ids=capability_ids)
    l1_spread = latest_by_id["l1_spread_bp"]
    imbalance = latest_by_id["bid_ask_imbalance"]
    revisions = (l1_spread.revision_id, imbalance.revision_id)
    retained = tuple(
        isinstance(revision, str)
        and revision.startswith(POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX)
        for revision in revisions
    )
    if retained[0] != retained[1] or (retained[0] and revisions[0] != revisions[1]):
        raise AuxEvidenceInsufficient(
            f"{worker_id} received L1 metrics from inconsistent observation "
            "revisions",
            worker_id=worker_id, capability_ids=capability_ids)
    is_retained_close_book = retained[0]
    as_of = max(l1_spread.available_at, imbalance.available_at)
    # Tick tape and whale-flow are independent optional feeds.  Their named
    # absences stay visible, but a real complete L1 book remains sufficient for
    # the ten-seat quality gate.
    degradation = (
        "tick_tape_unavailable",
        "fundflow_unavailable",
    ) + (("post_close_retained_book",) if is_retained_close_book else ())
    narrative = (
        "已绑定真实 verified_snapshot 锚点与同标的 TDX 五档买卖盘；"
        "L1 价差和五档挂单不平衡由真实挂单计算，逐笔和主力流未接入。"
    )
    rendered_suffix = "optional feeds unavailable"
    if is_retained_close_book:
        narrative += (
            " 当前盘口是供应商时间标记的盘后保留快照，并非实时盘口，"
            "不可用于下单。"
        )
        rendered_suffix += "; post-close retained snapshot, not live, not for orders"
    report = MicrostructureReport(
        symbol=latest.symbol,
        as_of=as_of,
        l1_spread_bp=l1_spread.value,
        bid_ask_imbalance=imbalance.value,
        break_ratio=None,
        whale_net_inflow=None,
        degradation=degradation,
        narrative=narrative,
    )
    return ModelResult(
        payload=report,
        rendered_text=(
            f"{worker_id}: verified anchor + exact-code five-level book bound; "
            f"l1_spread_bp={l1_spread.value:.6f}; "
            f"bid_ask_imbalance={imbalance.value:.6f}; {rendered_suffix}"),
        provider="deterministic", model="tdx_orderbook_projection")


def _bind_handler(*, worker_id: str, capability_ids: tuple[str, ...],
                  payload_reader: Any = None):
    """The inner executor-shaped handler (stage two of the handler ABI).

    ``worker.py`` calls it ``handler(node=…, input_snapshot=…, contributions=…,
    data_result_refs=…)``; ``data_result_refs`` is the executor's own merge over
    every bridge contribution, so it is the single honest answer to "did any
    data reach this node".
    """
    held = ", ".join(capability_ids)

    def handler(*, node=None, input_snapshot=None, contributions=(),
                data_result_refs=()):
        rows = tuple(data_result_refs or ())
        if not rows:
            raise AuxDataUngranted(
                f"{AUX_DATA_UNGRANTED_CODE}: {worker_id} holds {held} but no "
                "typed data result reached the handler; refusing to compute",
                worker_id=worker_id, capability_ids=capability_ids)
        results = _load_data_results(
            payload_reader, rows, worker_id=worker_id,
            capability_ids=capability_ids)
        if worker_id == "pv.price_action":
            return _price_action_result(
                results, worker_id=worker_id, capability_ids=capability_ids)
        if worker_id == "pv.microstructure":
            return _microstructure_result(
                results, worker_id=worker_id, capability_ids=capability_ids)
        raise AuxComputeNotWired(
            f"{AUX_COMPUTE_NOT_WIRED_CODE}: no reviewed compute spine for "
            f"{worker_id}", worker_id=worker_id,
            capability_ids=capability_ids)

    return handler


def build_pv_aux_handler_factory(worker_id: str, *, payload_reader: Any = None) -> Factory:
    """The trusted factory for ONE pv aux seat, in the reviewed two-stage shape.

    ``TrustedFactoryRegistry`` calls it ``factory(worker=…, resolved=…)``
    (``worker.py``'s deterministic branch) and the returned inner callable is
    the handler.  Nothing is captured from the runtime: the refusal is built
    from the invoked seat's OWN sealed WorkerSpec.
    """
    if worker_id not in PV_AUX_HANDLER_IDS:
        raise PvAuxHandlerError(
            f"{worker_id!r} is not one of the two reviewed pv aux seats "
            f"{sorted(PV_AUX_HANDLER_IDS)}; this wrapper binds no other seat",
            worker_id=worker_id)

    def factory(*, worker=None, resolved=None):
        return _bind_handler(
            worker_id=worker_id,
            capability_ids=_held_capability_ids(worker, worker_id),
            payload_reader=payload_reader)

    return factory


def pv_aux_handler_registry(*, payload_reader: Any = None) -> dict[str, Factory]:
    """``handler material id -> trusted factory`` for both pv aux seats.

    The mapping ``build_production_catalog_runtime`` merges into its
    ``handler_registry`` — the Task-11 ``cand.*`` idiom, one level up.
    """
    return {
        handler_id: build_pv_aux_handler_factory(
            worker_id, payload_reader=payload_reader)
        for worker_id, handler_id in PV_AUX_HANDLER_IDS.items()
    }
