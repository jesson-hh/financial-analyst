"""Prompt-only navigation for large raw valuation histories.

Acquisition packs and calculation ledgers stay complete. Call only on the
sections already admitted by the caller's evidence/PIT validation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping


_HISTORY_NODES = {"valuation.historical_percentile", "valuation.pb_historical_percentile"}


def _navigation(items: list, raw_ref: str) -> dict[str, Any]:
    body = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    dates = sorted({
        str(item.get("date") or item.get("as_of") or "")[:10]
        for item in items if isinstance(item, Mapping)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("date") or item.get("as_of") or "")[:10])
    })
    result = {
        "omitted_from_prompt": True, "original_count": len(items),
        "raw_ref": raw_ref, "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
        "note": "Prompt navigation only; full records remain in the original evidence artifact.",
    }
    diagnostics = [
        {"index": index, **{key: row[key] for key in ("status", "usable", "gaps", "warnings") if key in row}}
        for index, row in enumerate(items) if isinstance(row, Mapping)
        and any(key in row for key in ("status", "usable", "gaps", "warnings"))
    ]
    if diagnostics:
        result["record_diagnostics"] = diagnostics
    return result


def project_company_history_for_prompt(sections: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy sections and omit only repeated raw valuation-history arrays.

    Calculation ids/results/status/gaps/warnings, sample coverage (including
    missing dates), provenance and all non-history evidence remain unchanged.
    This function performs no I/O, calculations, authentication or PIT claims.
    """
    view = copy.deepcopy(dict(sections)) if isinstance(sections, Mapping) else {}
    valuation = view.get("valuation")
    if isinstance(valuation, dict):
        for key in ("history_series", "history_pb_series"):
            series = valuation.get(key)
            if isinstance(series, dict) and isinstance(series.get("points"), list):
                series["points_omitted_from_prompt"] = _navigation(
                    series.pop("points"), f"pack.sections.valuation.{key}.points",
                )

    dag = view.get("company_valuation_dag")
    if isinstance(dag, dict):
        for index, node in enumerate(dag.get("nodes") or []):
            if not isinstance(node, dict) or node.get("calc_id") not in _HISTORY_NODES:
                continue
            for key in ("inputs", "input_refs"):
                if isinstance(node.get(key), list):
                    node[f"{key}_omitted_from_prompt"] = _navigation(
                        node.pop(key), f"pack.sections.company_valuation_dag.nodes[{index}].{key}",
                    )
        edges = dag.get("edges")
        if isinstance(edges, list):
            omitted = [edge for edge in edges if isinstance(edge, Mapping)
                       and edge.get("to") in _HISTORY_NODES
                       and str(edge.get("from") or "").startswith("input:")]
            if omitted:
                dag["edges"] = [edge for edge in edges if not (
                    isinstance(edge, Mapping) and edge.get("to") in _HISTORY_NODES
                    and str(edge.get("from") or "").startswith("input:")
                )]
                dag["history_input_edges_omitted_from_prompt"] = _navigation(
                    omitted, "pack.sections.company_valuation_dag.edges",
                )

    vibe = view.get("company_vibe_valuation")
    if isinstance(vibe, dict):
        for index, calc in enumerate(vibe.get("calculations") or []):
            if not isinstance(calc, dict) or calc.get("function") != "percentile_rank":
                continue
            prefix = f"pack.sections.company_vibe_valuation.calculations[{index}]"
            inputs = calc.get("inputs")
            if isinstance(inputs, dict) and isinstance(inputs.get("history"), list):
                inputs["history_omitted_from_prompt"] = _navigation(
                    inputs.pop("history"), f"{prefix}.inputs.history",
                )
            refs = calc.get("input_refs")
            if isinstance(refs, list):
                history_refs = [ref for ref in refs if str(ref).startswith(
                    "pack.sections.valuation.history_series.points["
                )]
                if history_refs:
                    omitted_refs = set(history_refs)
                    calc["input_refs"] = [ref for ref in refs if ref not in omitted_refs]
                    calc["history_input_refs_omitted_from_prompt"] = _navigation(
                        history_refs, f"{prefix}.input_refs",
                    )
    return view


__all__ = ["project_company_history_for_prompt"]
