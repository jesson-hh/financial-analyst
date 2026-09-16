# -*- coding: utf-8 -*-
"""Deterministic, lossless migrations for saved workflow graphs.

The migration layer is intentionally conservative.  It repairs only graph
shapes whose old and new meanings are equivalent, and reports every legacy
shape that requires a human choice instead of inventing a replacement.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple


WORKFLOW_SCHEMA_VERSION = 3
_DIRECT_FACTOR_SOURCES = {"formula", "factorlib"}
_DIRECT_FACTOR_TERMINALS = {"analysis", "iccalc"}
_LEGACY_NODE_TYPES = {"model", "validate", "screen"}


def legacy_recovery_info(graph: Any) -> Dict[str, Any] | None:
    """Describe legacy intent without making an old display board executable.

    Model IDs are references, not training recipes or proof that an estimator
    still exists. Registry/file existence checks belong to the read-only recovery
    audit, not this pure migration function. Historical decisions stay in place.
    """
    if not isinstance(graph, dict):
        return None
    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict)] \
        if isinstance(graph.get("nodes"), list) else []
    legacy = [n for n in nodes if n.get("type") in _LEGACY_NODE_TYPES]
    if not legacy:
        return None
    edges = graph.get("edges", [])
    edges = edges if isinstance(edges, list) else []
    by_id = {str(n.get("id")): n for n in nodes}

    def params(node):
        value = node.get("params")
        return value if isinstance(value, dict) else {}

    allowed_board_types = {"model", "validate", "source", "formula", "factorlib"}
    model_board = any(n.get("type") == "model" for n in nodes) \
        and all(n.get("type") in allowed_board_types for n in nodes)
    reference_mismatch = []
    for edge in edges:
        if not isinstance(edge, dict):
            model_board = False
            continue
        frm, to = edge.get("from"), edge.get("to")
        if not isinstance(frm, (list, tuple)) or len(frm) != 2 \
                or not isinstance(to, (list, tuple)) or len(to) != 2:
            model_board = False
            continue
        src, dst = by_id.get(str(frm[0]), {}), by_id.get(str(to[0]), {})
        if src.get("type") != "model" or dst.get("type") != "validate" \
                or frm[1] != "out" or to[1] != "model":
            model_board = False
            continue
        if params(src).get("model_id") != params(dst).get("model_id"):
            model_board = False
            reference_mismatch.append({"model_node_id": str(frm[0]), "validation_node_id": str(to[0])})

    references = []
    decisions = []
    for node in legacy:
        p = params(node)
        if node.get("type") in {"model", "validate"}:
            references.append({"node_id": str(node.get("id") or ""),
                               "node_type": node["type"], "model_id": p.get("model_id"),
                               "model_name": p.get("model_name"),
                               "registry_resolution": "not_checked"})
        if "decision" in p:
            decisions.append({"node_id": str(node.get("id") or ""), "decision": p["decision"]})
    blockers = [{"code": "legacy_execution_contract_missing",
                 "node_ids": [str(n.get("id") or "") for n in legacy],
                 "reason": "旧节点没有现行执行契约，不能仅改类型或补连线冒充恢复"}]
    if references:
        blockers.append({"code": "model_reference_not_training_recipe",
                         "reason": "model_id 只指向历史模型；须核验原训练器、数据与验证版本，重新训练属于新实验"})
    if reference_mismatch:
        blockers.append({"code": "model_validation_reference_mismatch", "edges": reference_mismatch,
                         "reason": "模型节点与验证节点引用不同模型，禁止自动选择其中一个"})
    if any(n.get("type") == "screen" for n in legacy):
        blockers.append({"code": "legacy_screen_requires_explicit_spec",
                         "reason": "旧 screen 展示参数不等于 data_screen 的筛选规则，须明确规则、数据上下文与评估目标"})
    return {
        "classification": "historical_model_board" if model_board else "legacy_rebuild_required",
        "auto_repairable": False, "execution_supported": False,
        "original_graph_preserved": True, "model_references": references,
        "historical_decisions": decisions, "blockers": blockers,
        "suggested_actions": (
            ["保留历史图和原验证结论", "按原 model_id 核验模型档案", "如需重训，显式确认配方与时序验证设置后另建研究工作流"]
            if model_board else
            ["保留原研究草图", "明确每个节点的可执行规则与评估目标", "将可支持的分支另建为严格校验通过的新工作流"]
        ),
    }


def context_source_for_node(nid: str, nodes: list, edges: list) -> str | None:
    """Find one nearest source, honoring explicit references without order guesses.

    ``params.context_source_id`` is a semantic graph reference, not a copied
    parameter snapshot. A dangling/wrong-type reference or equally near distinct
    sources fails closed, so reordering nodes/edges cannot change research scope.
    """
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict)}
    seen = {str(nid)}
    frontier = [str(nid)]
    while frontier:
        candidates: set[str] = set()
        for current in frontier:
            node = by_id.get(current) or {}
            params = node.get("params") or {}
            if "context_source_id" in params:
                ref = params["context_source_id"]
                if not isinstance(ref, str) or not ref.strip():
                    raise ValueError(f"节点 {current} context_source_id 必须是非空 source ID")
                target = by_id.get(ref)
                if not target or target.get("type") != "source":
                    raise ValueError(f"节点 {current} context_source_id '{ref}' 未指向现存 source")
                candidates.add(ref)
            elif node.get("type") == "source":
                candidates.add(current)
        if len(candidates) > 1:
            raise ValueError("数据上下文存在多个等距 source，需显式指定 context_source_id: "
                             + ", ".join(sorted(candidates)))
        if candidates:
            return next(iter(candidates))
        next_frontier = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            frm, to = edge.get("from"), edge.get("to")
            if not isinstance(frm, (list, tuple)) or len(frm) != 2 \
                    or not isinstance(to, (list, tuple)) or len(to) != 2:
                continue
            upstream = str(frm[0])
            if str(to[0]) in frontier and upstream not in seen:
                seen.add(upstream)
                next_frontier.append(upstream)
        frontier = next_frontier
    return None


def migrate_graph(
    graph: Any, *, previous_migration: Any = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return a migrated copy and a machine-readable migration report.

    v1 canvases commonly contained this single-factor path::

        formula.out -> feature.feat -> feature.fe -> analysis.factor

    ``feature.fe`` is a feature matrix, whereas ``analysis.factor`` accepts a
    factor artifact.  With exactly one formula/factor-library input, bypassing
    the feature node is lossless: both paths materialise the same expression.
    The old feature node is retained and the terminal explicitly references its
    source. v2 already-bypassed graphs can recover this reference only from the
    saved migration report, not from a guess based on nearby nodes.
    """
    source = graph if isinstance(graph, dict) else {}
    migrated: Dict[str, Any] = deepcopy(source)
    nodes = migrated.get("nodes")
    edges = migrated.get("edges")
    if not isinstance(nodes, list):
        nodes = []
        migrated["nodes"] = nodes
    if not isinstance(edges, list):
        edges = []
        migrated["edges"] = edges

    by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and str(node.get("id") or "").strip()
    }
    changes: List[Dict[str, Any]] = []
    needs_review: List[Dict[str, Any]] = []
    remove_indexes: set[int] = set()
    additions: List[Dict[str, List[str]]] = []

    def endpoints(edge: Any) -> tuple[Any, Any]:
        if not isinstance(edge, dict):
            return None, None
        frm, to = edge.get("from"), edge.get("to")
        if (not isinstance(frm, (list, tuple)) or len(frm) != 2
                or not isinstance(to, (list, tuple)) or len(to) != 2):
            return None, None
        return frm, to

    def feature_inputs(feature_id: str) -> list:
        return [endpoints(edge)[0] for edge in edges
                if endpoints(edge)[0] is not None
                and str(endpoints(edge)[1][0]) == feature_id
                and str(endpoints(edge)[1][1]) == "feat"]

    def bind_context(feature_id: str, terminal_id: str, code: str) -> bool:
        terminal = by_id[terminal_id]
        params = terminal.get("params") or {}
        try:
            # A user's explicit target choice is authoritative; validate but
            # never overwrite it with historical migration evidence.
            if "context_source_id" in params:
                context_source_for_node(terminal_id, nodes, [])
                return True
            source_id = context_source_for_node(feature_id, nodes, edges)
        except ValueError as exc:
            needs_review.append({"code": "ambiguous_source_context",
                                 "feature_node_id": feature_id,
                                 "terminal_node_id": terminal_id, "reason": str(exc)})
            return False
        if source_id is not None:
            terminal["params"] = {**params, "context_source_id": source_id}
            changes.append({"code": code, "feature_node_id": feature_id,
                            "terminal_node_id": terminal_id,
                            "context_source_id": source_id})
        return True

    for index, edge in enumerate(edges):
        frm, to = endpoints(edge)
        if frm is None:
            continue
        src = by_id.get(str(frm[0])) or {}
        dst = by_id.get(str(to[0])) or {}
        if not (
            src.get("type") == "feature"
            and str(frm[1]) == "fe"
            and dst.get("type") in _DIRECT_FACTOR_TERMINALS
            and str(to[1]) == "factor"
        ):
            continue

        inbound = []
        for candidate in edges:
            cfrm, cto = endpoints(candidate)
            if cfrm is None or str(cto[0]) != str(frm[0]) or str(cto[1]) != "feat":
                continue
            producer = by_id.get(str(cfrm[0])) or {}
            if producer.get("type") in _DIRECT_FACTOR_SOURCES and str(cfrm[1]) == "out":
                inbound.append((str(cfrm[0]), str(cfrm[1]), producer.get("type")))

        if len(inbound) != 1 or len(feature_inputs(str(frm[0]))) != 1:
            needs_review.append({
                "code": "ambiguous_feature_to_factor",
                "feature_node_id": str(frm[0]),
                "terminal_node_id": str(to[0]),
                "candidate_inputs": len(inbound),
                "reason": "特征矩阵不是单一公式输入，无法无损推断合成方法",
            })
            continue

        producer_id, producer_port, producer_type = inbound[0]
        if not bind_context(str(frm[0]), str(to[0]), "preserve_source_context"):
            continue
        replacement = {
            "from": [producer_id, producer_port],
            "to": [str(to[0]), "factor"],
        }
        duplicate = any(
            isinstance(existing, dict)
            and existing.get("from") == replacement["from"]
            and existing.get("to") == replacement["to"]
            for existing in edges
        ) or any(
            existing.get("from") == replacement["from"]
            and existing.get("to") == replacement["to"]
            for existing in additions
        )
        remove_indexes.add(index)
        if not duplicate:
            additions.append(replacement)
        changes.append({
            "code": "bypass_single_feature_matrix",
            "feature_node_id": str(frm[0]),
            "terminal_node_id": str(to[0]),
            "producer_node_id": producer_id,
            "producer_type": producer_type,
        })

    if remove_indexes:
        migrated["edges"] = [
            edge for index, edge in enumerate(edges) if index not in remove_indexes
        ] + additions

    prior_changes = previous_migration.get("changes", []) \
        if isinstance(previous_migration, dict) else []
    for prior in prior_changes:
        if not isinstance(prior, dict) or prior.get("code") != "bypass_single_feature_matrix":
            continue
        feature_id = str(prior.get("feature_node_id") or "")
        terminal_id = str(prior.get("terminal_node_id") or "")
        producer_id = str(prior.get("producer_node_id") or "")
        feature = by_id.get(feature_id) or {}
        terminal = by_id.get(terminal_id) or {}
        producer = by_id.get(producer_id) or {}
        inputs = feature_inputs(feature_id)
        expected_edge = {"from": [producer_id, "out"], "to": [terminal_id, "factor"]}
        if not (feature.get("type") == "feature"
                and terminal.get("type") in _DIRECT_FACTOR_TERMINALS
                and producer.get("type") in _DIRECT_FACTOR_SOURCES
                and len(inputs) == 1 and list(inputs[0]) == [producer_id, "out"]
                and expected_edge in migrated["edges"]):
            needs_review.append({"code": "migration_context_provenance_mismatch",
                                 "feature_node_id": feature_id,
                                 "terminal_node_id": terminal_id,
                                 "reason": "旧迁移记录与当前连线不一致，保留用户修改、不猜测数据源"})
            continue
        bind_context(feature_id, terminal_id, "restore_v2_source_context")

    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in _LEGACY_NODE_TYPES:
            continue
        needs_review.append({
            "code": "legacy_node_type",
            "node_id": str(node.get("id") or ""),
            "node_type": str(node.get("type") or ""),
            "reason": "旧版展示/验证节点没有现行可执行契约，已保留原图等待重建",
        })

    report = {
        "from_version": int(source.get("schema_version") or 1)
        if isinstance(source.get("schema_version"), (int, float)) else 1,
        "to_version": WORKFLOW_SCHEMA_VERSION,
        "changed": bool(changes),
        "changes": changes,
        "needs_review": needs_review,
        "runnable_candidate": not needs_review,
    }
    recovery = legacy_recovery_info(source)
    if recovery is not None:
        # A partly recognizable branch does not authorize rewriting a legacy
        # board's other semantics. Preserve the entire graph, including schema
        # and unknown metadata, until its intended executable contract is known.
        report["changed"] = False
        report["changes"] = []
        report["runnable_candidate"] = False
        report["legacy_recovery"] = recovery
        return deepcopy(source), report
    migrated["schema_version"] = WORKFLOW_SCHEMA_VERSION
    return migrated, report
