"""Aggregator: Tier-4 introspector backlog → dream _proposed/.

每次 ``fa report`` 跑完, Tier-4 introspector 写一份
``memories/_pending_introspections/<date>_<code>.json``. 单份样本不足以下结论;
跨多份样本**重复出现的 pattern** 才值得升级成 memory rule.

这里做 Jaccard token-based clustering:
  1. 扫所有 _pending_introspections/*.json, 提取 proposals
  2. 按 target_agent 分桶, 桶内 token Jaccard 相似度 >= ``threshold`` 视为同 cluster
  3. cluster size >= ``min_count`` (默认 3) → 写 _proposed/<agent>/<date>_<slug>.md
  4. confidence by cluster size: 3-5=med, 6+=high (matches introspector_rules.md)

调用入口: ``aggregate_pending(memory_root)`` 或 CLI ``fa dream aggregate``.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date as _date
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from financial_analyst.dream.introspector import Proposal
from financial_analyst.dream.proposal_writer import write_proposals
from financial_analyst.memory_paths import default_memory_root


# ──────────────────────── 关键词白名单 (boost) ────────────────────────


# 这些是 A 股 quant 语境里有判定意义的 keyword. clustering 时同时出现这些
# 词的 pattern 视为更相关. 不在白名单内的 token 仍然算, 只是权重 1.0.
BOOST_KEYWORDS = {
    # market cap tiers
    "mv_tier", "large", "mega", "mid", "small", "micro",
    # vol regime
    "vol_regime", "super_distr", "distr", "tail_surge", "bounce", "neutral",
    # technical signals
    "rsi", "oversold", "overbought", "macd", "ma_state", "rev_20", "vol_20",
    # board / first-board v5
    "board_total_score", "board_score", "seal_at_close", "seal_bar",
    # whale signals
    "obv", "vr", "mfi", "chip_judge", "whale_score", "accumulating", "dispersed",
    # quant
    "lgb", "fm", "tsfm", "conviction", "model_consensus",
    # risk
    "veto", "veto_flags", "position_pct", "stop_loss", "target_price",
    # action types
    "buy", "sell", "hold", "avoid", "accumulate",
    # rule anchors
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10",
    "f11", "f12", "f13", "f14",
    "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10",
    # event / news
    "negative", "positive", "severity", "lhb", "game_capital",
    # factor anchors
    "factor_neutralized", "ic_decay", "ic_inverse",
}


STOPWORDS = {
    "and", "or", "not", "the", "a", "an", "is", "be", "with", "to", "of",
    "in", "for", "on", "by", "as", "at", "from", "this", "that", "if",
    "but", "than", "null", "none", "true", "false", "may", "should", "would",
    "could", "must", "其", "在", "或", "和", "与", "等", "时", "的",
}


def _tokenize(text: str) -> Set[str]:
    """Pattern / rule 字符串 → token set. 含中英文 word + 关键标识符."""
    if not text:
        return set()
    text = text.lower()
    # 抽 word: 拉丁字母数字 + 下划线 + 中文连续段
    tokens = re.findall(r"[a-z0-9_]+|[一-鿿]+", text)
    return {t for t in tokens if len(t) >= 2 and t not in STOPWORDS}


def _weighted_jaccard(a: Set[str], b: Set[str], boost_only: bool = True) -> float:
    """Jaccard 相似度.

    boost_only=True (默认): 只看 BOOST_KEYWORDS 范围内的 token 计算 Jaccard.
        LLM 在每份 introspection 里用**不同 prose 描述同一 pattern** —
        非关键词 token 80%+ 是噪声 (例如 'automatically' / 'institutional'
        / 'liquidity'), 会把同模式 cluster 稀释开. Boost-only 直接抓"判定意义"
        关键词, 共同概念主导.
    boost_only=False: 全 token Jaccard, BOOST_KEYWORDS 权重 2x.
    """
    if not a or not b:
        return 0.0

    if boost_only:
        a_b = a & BOOST_KEYWORDS
        b_b = b & BOOST_KEYWORDS
        # 双方都没命中任何 boost keyword → 退回全 token Jaccard 防 false zero
        if not a_b and not b_b:
            inter = len(a & b)
            union = len(a | b)
            return inter / union if union > 0 else 0.0
        inter = len(a_b & b_b)
        union = len(a_b | b_b)
        return inter / union if union > 0 else 0.0

    def _w(t: str) -> float:
        return 2.0 if t in BOOST_KEYWORDS else 1.0

    inter = sum(_w(t) for t in a & b)
    union = sum(_w(t) for t in a | b)
    return inter / union if union > 0 else 0.0


# ──────────────────────── 聚类 ────────────────────────


def _cluster_proposals(proposals: List[dict], threshold: float = 0.4) -> List[List[int]]:
    """单 agent 桶内做 Jaccard 阈值聚类. 返回 cluster 索引列表.

    Algorithm: simple greedy single-link — 每条新 proposal 与已有 cluster 的
    representative 比 Jaccard, 第一个超阈值的吸进去; 都没匹配开新 cluster.
    """
    if not proposals:
        return []

    tokens_per = [_tokenize(p["pattern"] + " " + p["proposed_rule"]) for p in proposals]

    clusters: List[List[int]] = []
    cluster_reps: List[Set[str]] = []  # representative token set per cluster

    for i, toks in enumerate(tokens_per):
        placed = False
        for ci, rep in enumerate(cluster_reps):
            if _weighted_jaccard(toks, rep) >= threshold:
                clusters[ci].append(i)
                cluster_reps[ci] = rep | toks   # expand rep with new tokens
                placed = True
                break
        if not placed:
            clusters.append([i])
            cluster_reps.append(toks)

    return clusters


# ──────────────────────── 入口 ────────────────────────


def aggregate_pending(
    memory_root: Optional[Path] = None,
    min_count: int = 3,
    threshold: float = 0.4,
    dry_run: bool = False,
) -> Tuple[List[Path], dict]:
    """扫 _pending_introspections/, 聚类, 把重复 >= min_count 的 promote 到 _proposed/.

    Args:
        memory_root: ``memories/`` 根目录
        min_count: cluster 内最少出现次数才升级 (默认 3, 匹配 introspector_rules.md)
        threshold: Jaccard 相似度阈值
        dry_run: True 只打印, 不写盘

    Returns:
        (written_paths, stats_dict)
    """
    if memory_root is None:
        memory_root = default_memory_root()
    pending_dir = memory_root / "_pending_introspections"
    if not pending_dir.exists():
        return [], {"reason": "no _pending_introspections dir"}

    # 收所有 proposals
    all_props: List[dict] = []
    by_file: Dict[str, List[int]] = defaultdict(list)
    for jf in sorted(pending_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        # source case id: e.g. "2026-05-23_SH600519"
        case_id = jf.stem
        for prop in data.get("proposals", []):
            if not prop.get("target_agent") or not prop.get("pattern"):
                continue
            all_props.append({**prop, "_case_id": case_id})
            by_file[case_id].append(len(all_props) - 1)

    stats = {
        "n_pending_files": sum(1 for _ in pending_dir.glob("*.json")),
        "n_proposals_total": len(all_props),
        "min_count": min_count,
        "threshold": threshold,
        "clusters_total": 0,
        "clusters_promoted": 0,
        "promoted_breakdown": {},
    }

    if not all_props:
        return [], stats

    # 按 target_agent 分桶
    by_agent: Dict[str, List[int]] = defaultdict(list)
    for idx, p in enumerate(all_props):
        by_agent[p["target_agent"]].append(idx)

    # 每桶聚类 + promote
    today = _date.today().isoformat()
    to_write: List[Proposal] = []
    stats["skipped_unchanged"] = 0    # 已经 promote 过且 cases 没变 → 跳过

    for agent, idxs in by_agent.items():
        agent_props = [all_props[i] for i in idxs]
        clusters = _cluster_proposals(agent_props, threshold=threshold)
        stats["clusters_total"] += len(clusters)

        for ci, member_idxs in enumerate(clusters):
            if len(member_idxs) < min_count:
                continue

            members = [agent_props[mi] for mi in member_idxs]
            # representative: 出现频次最高的 (target_agent, pattern_phrase)
            rep = members[0]   # 简化: first member

            # confidence
            n = len(members)
            confidence = "high" if n >= 6 else "med" if n >= 3 else "low"

            # slug from pattern keywords
            common_tokens = (_tokenize(rep["pattern"]) & BOOST_KEYWORDS) or _tokenize(rep["pattern"])
            slug_parts = sorted(common_tokens)[:4] or ["pattern"]
            slug = f"{agent}-" + "-".join(slug_parts)
            slug = re.sub(r"[^a-z0-9_-]+", "", slug)[:80]
            if not slug:
                slug = f"{agent}-cluster-{ci}"

            # supporting cases
            cases = sorted({p["_case_id"] for p in members})

            # ─── Idempotency: 检测 _proposed/<agent>/*_<slug>.md 已存在 ───
            # 如果 supporting_cases 完全一致 → skip (无变化)
            # 如果有新 cases → 删 stale, 写新的 (替换)
            proposed_dir = memory_root / "_proposed" / agent
            stale_files = list(proposed_dir.glob(f"*_{slug}.md")) if proposed_dir.exists() else []

            unchanged = False
            for stale in stale_files:
                try:
                    text = stale.read_text(encoding="utf-8")
                    if text.startswith("---\n"):
                        end = text.find("\n---\n", 4)
                        if end > 0:
                            import yaml as _yaml
                            fm = _yaml.safe_load(text[4:end]) or {}
                            existing_cases = sorted(fm.get("supporting_cases", []))
                            if existing_cases == cases:
                                unchanged = True
                                break
                except Exception:
                    continue

            if unchanged:
                stats["skipped_unchanged"] += 1
                continue   # 跳过, stale file 保留

            # 删 stale (slug 同名但 cases 不同 → 升级)
            for stale in stale_files:
                try:
                    stale.unlink()
                except Exception:
                    pass

            title = (rep.get("proposed_rule") or rep.get("pattern", ""))[:120]
            lesson_md = _build_lesson_md(agent, members, n)

            to_write.append(Proposal(
                target_agent=agent,
                topic_slug=slug,
                title=title,
                lesson_md=lesson_md,
                confidence=confidence,
                supporting_cases=cases,
                reasoning=f"Aggregated from {n} pending introspections via "
                         f"Jaccard clustering (threshold={threshold}). "
                         f"All in {agent} bucket.",
            ))
            stats["clusters_promoted"] += 1
            stats["promoted_breakdown"][slug] = {
                "n_cases": n, "confidence": confidence,
                "agent": agent,
            }

    if dry_run:
        return [], stats

    # write
    written = write_proposals(to_write, memory_root=memory_root) if to_write else []
    return written, stats


def _build_lesson_md(agent: str, members: List[dict], n: int) -> str:
    """渲染 _proposed/ markdown body."""
    lines = [
        f"# {agent} — aggregated rule proposal",
        "",
        f"Auto-aggregated from **{n} pending introspections** (cluster threshold 0.4, ",
        f"Jaccard with BOOST_KEYWORDS 2x).",
        "",
        "## Cluster member proposals",
        "",
    ]
    for i, p in enumerate(members, 1):
        case_id = p.get("_case_id", "?")
        lines.append(f"### {i}. case `{case_id}` (confidence={p.get('confidence', '?')})")
        lines.append("")
        lines.append(f"**Pattern**: {p.get('pattern', '')}")
        lines.append("")
        lines.append(f"**Proposed rule**: {p.get('proposed_rule', '')}")
        lines.append("")
        rationale = p.get("rationale", "").strip()
        if rationale:
            lines.append(f"**Rationale**: {rationale}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## Recommendation",
        "",
        f"Above {n} cases all point to the same pattern under `{agent}`.",
        "Review the proposed rules + pick the cleanest formulation. After "
        "manually crafting the final rule, run `fa dream accept "
        f"{agent}/<slug>` to promote into permanent memory.",
        "",
        "If false-pattern (e.g., all from one stock or one date), reject with",
        f"`fa dream reject {agent}/<slug>`.",
    ])
    return "\n".join(lines)


# ──────────────────────── Capability Gap → Skill Proposal ────────────────────────
# Hermes-style: LLM reviews collected gaps and decides what skills to create,
# rather than deterministic Jaccard clustering with keyword whitelists.


def _collect_capability_gaps(memory_root: Path) -> List[dict]:
    """Scan _pending_introspections/*.json for capability_gaps entries.

    Returns list of dicts: {gap_description, skill_type, evidence, suggested_name, _case_ids}.
    """
    pending_dir = memory_root / "_pending_introspections"
    if not pending_dir.exists():
        return []

    gap_map: Dict[str, dict] = {}
    for jf in sorted(pending_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        case_id = jf.stem
        for gap in data.get("capability_gaps", []):
            desc = gap.get("gap_description", "")
            if not desc:
                continue
            st = gap.get("skill_type", "tool")
            sname = gap.get("suggested_name", "")
            key = (st, sname) if sname else (st, desc[:40])
            if key in gap_map:
                gap_map[key]["evidence"].extend(gap.get("evidence", []))
                gap_map[key]["_case_ids"].append(case_id)
            else:
                gap_map[key] = {
                    "gap_description": desc,
                    "skill_type": st,
                    "suggested_name": sname,
                    "evidence": list(gap.get("evidence", [])),
                    "_case_ids": [case_id],
                }
    return list(gap_map.values())


def _gaps_to_snapshot(gaps: List[dict], min_count: int) -> str:
    """Build a conversation-like snapshot from capability gaps for the LLM reviewer."""
    if not gaps:
        return "(no capability gaps detected)"

    # Count occurrences — gaps with more cases are more significant
    qualified = [g for g in gaps if len(g["_case_ids"]) >= min_count]
    others = [g for g in gaps if len(g["_case_ids"]) < min_count]

    lines = [f"## Capability Gaps from Dream Loop Introspections\n"]
    lines.append(f"### Qualified (≥{min_count} occurrences)\n")
    if qualified:
        for g in qualified:
            st = g.get("skill_type", "tool")
            sname = g.get("suggested_name", "")
            n = len(g["_case_ids"])
            desc = g["gap_description"][:300]
            evidence = g.get("evidence", [])[:3]
            name_hint = f" (suggested: {sname})" if sname else ""
            lines.append(f"- [{st}]{name_hint} ({n} cases): {desc}")
            for e in evidence:
                lines.append(f"    Evidence: {e[:120]}")
    else:
        lines.append("  (none)")

    if others:
        lines.append(f"\n### Below Threshold ({len(others)} gaps with <{min_count} cases)\n")
        for g in others:
            st = g.get("skill_type", "tool")
            desc = g["gap_description"][:150]
            lines.append(f"  - [{st}] ({len(g['_case_ids'])} cases): {desc}")

    return "\n".join(lines)


async def aggregate_capability_gaps(
    memory_root: Path = Path("memories"),
    skills_root: Path = Path("skills_generation"),
    min_count: int = 3,
    dry_run: bool = False,
) -> Tuple[List[Path], dict]:
    """Hermes-style LLM-driven gap → skill pipeline.

    Collects capability gaps from pending introspections, builds a context
    snapshot, and asks the BackgroundSkillReviewer's LLM to decide what
    skills to create/patch. No Jaccard clustering or keyword whitelists —
    the LLM does all the reasoning.

    Args:
        memory_root: ``memories/`` root
        skills_root: ``skills/`` root
        min_count: minimum occurrences for a gap to be included in the prompt
        dry_run: preview only

    Returns:
        (written_paths, stats_dict)
    """
    gaps = _collect_capability_gaps(memory_root)

    stats = {
        "n_gaps_total": len(gaps),
        "n_gaps_qualified": sum(1 for g in gaps if len(g["_case_ids"]) >= min_count),
        "n_proposals_generated": 0,
        "skipped": 0,
        "generated": [],
    }

    qualified = [g for g in gaps if len(g["_case_ids"]) >= min_count]
    if not qualified:
        return [], stats

    if dry_run:
        for g in qualified:
            stats["generated"].append({
                "name": g.get("suggested_name", "?"),
                "skill_type": g.get("skill_type", "tool"),
                "n_cases": len(g["_case_ids"]),
                "gap": g["gap_description"][:120],
            })
        return [], stats

    # Build snapshot and run LLM-driven review
    snapshot = _gaps_to_snapshot(gaps, min_count)

    from financial_analyst.skill_gen.review import BackgroundSkillReviewer
    from financial_analyst.skill_gen.lifecycle import get_skill_mode

    mode = get_skill_mode()
    reviewer = BackgroundSkillReviewer(
        memory_root=memory_root,
        skills_root=skills_root,
        mode=mode,
    )

    result = await reviewer.review(conversation_snapshot=snapshot)
    written: List[Path] = []

    for action in result.get("actions_taken", []):
        outcome = action.get("outcome", {})
        stats["n_proposals_generated"] += 1
        stats["generated"].append({
            "name": action.get("name", "?"),
            "skill_type": action.get("skill_type", "?"),
            "action": action.get("action", "?"),
            "status": outcome.get("status", "?"),
        })
        if outcome.get("proposal_written"):
            written.append(Path(outcome["proposal_written"]))

    if result.get("errors"):
        stats.setdefault("errors", []).extend(result["errors"])

    return written, stats
