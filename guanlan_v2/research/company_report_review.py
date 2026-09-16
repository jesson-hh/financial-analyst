"""Deterministic, bounded human review notes; never an evidence or status input."""
from __future__ import annotations

from collections import Counter
import html
import re
from typing import Any


def _cell(value: Any, limit: int = 360) -> str:
    """Keep report/model text inert in a Markdown attachment."""
    if not isinstance(value, (str, int, float, bool)):
        return "—"
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > limit:
        text = text[:limit] + "…（截断）"
    text = html.escape(text, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text) or "—"


def render_company_report_review(
    markdown: str,
    report_gate: dict[str, Any],
    risk_contract: dict[str, Any],
) -> str:
    """Render exact gate findings without changing acceptance or fetching data.

    Input comes from the finalizer's computed gates. Original JSON and Markdown
    remain authoritative; excerpts and long finding lists are explicitly bounded.
    The renderer does not read paths, invoke a model, or infer missing evidence.
    """
    lines = markdown.splitlines()
    violations = [r for r in report_gate.get("violations", []) if isinstance(r, dict)]
    warnings = [r for r in report_gate.get("warnings", []) if isinstance(r, dict)]
    semantic = risk_contract.get("semantic_review")
    semantic = semantic if isinstance(semantic, dict) else {}
    out = [
        "# 公司报告审查清单",
        "",
        "本附件由最终 Gate 确定性生成，仅用于定位问题，不是证据，也不改变报告状态。",
        "",
        "| 检查 | 当前结果 |",
        "|---|---|",
        f"| 报告 Gate | {_cell(report_gate.get('status'))} |",
        f"| 完整性 | {_cell(report_gate.get('completion_status'))} |",
        f"| 新鲜度 | {_cell(report_gate.get('freshness_status'))} |",
        f"| 风险结构 Gate | {_cell(risk_contract.get('status'))} |",
        f"| 独立语义核验 | {_cell(semantic.get('status', 'unknown'))} |",
        f"| 阻断 / 警告 | {len(violations)} / {len(warnings)} |",
        "",
        "风险结构通过不等于反证方向正确或引用内容足以支持结论；新鲜度是本次冻结输入的校验结果，不是持续实时保证。",
        "",
        "行号对应本资产的 report_final.md；完整结果见 report_gate.json、risk_contract_final.json。"
        "源接口失败、覆盖不足及 empty 不能解释为不存在事件；以证据清单中的明确缺口为准。",
        "",
    ]
    for title, findings, limit in (
        ("阻断项", violations, 200), ("警告项", warnings, 100),
    ):
        out.extend([f"## {title}", ""])
        if not findings:
            out.extend(["未登记此类问题；不代表独立语义核验或全部数据源已完成。", ""])
            continue
        counts = Counter(str(row.get("code", "unknown")) for row in findings)
        out.extend([
            "；".join(f"{_cell(code, 120)}：{count}" for code, count in sorted(counts.items())[:100])
            + ("（错误码分类仅显示前 100 类）" if len(counts) > 100 else ""),
            "",
            "| 位置 | 错误码 / 检查 | 原始诊断 | 数字 / 引用 |",
            "|---|---|---|---|",
        ])
        shown_lines: set[int] = set()
        for row in findings[:limit]:
            line = row.get("line")
            valid_line = isinstance(line, int) and not isinstance(line, bool) and 1 <= line <= len(lines)
            location = f"L{line}" if valid_line else "无有效行号"
            details = row.get("details")
            details = details if isinstance(details, dict) else {}
            refs = details.get("cited_refs")
            refs = refs if isinstance(refs, list) else []
            number_refs = _cell(details.get("number"))
            if refs:
                number_refs += " / " + ", ".join(_cell(ref, 100) for ref in refs[:8])
                if len(refs) > 8:
                    number_refs += "（引用截断）"
            out.append(
                f"| {location} · {_cell(row.get('section_id'), 100)} "
                f"| {_cell(row.get('code'), 120)} / {_cell(row.get('check_id'), 100)} "
                f"| {_cell(row.get('message'))} | {number_refs} |"
            )
            if valid_line:
                shown_lines.add(line)
        if len(findings) > limit:
            out.extend(["", f"仅显示前 {limit} / {len(findings)} 项，剩余见完整 Gate JSON。"])
        if shown_lines:
            out.extend(["", "### 对应原文摘录（仅诊断，不作证据）", ""])
            for line in sorted(shown_lines):
                out.extend([f"- L{line}：{_cell(lines[line - 1])}", ""])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


__all__ = ["render_company_report_review"]
