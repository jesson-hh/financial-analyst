"""Deterministic final gate for one company-research report.

The gate is intentionally pure: it does not fetch evidence, call a model, read
the clock, or write artifacts.  A caller supplies the report body, the Evidence
Hub ledger, deterministic calculations, and the upstream pipeline manifest.
The result is a stable audit document that answers a narrow question: may this
report be labelled ``complete``?

Strict mode is the archival contract.  It requires seven canonical Markdown
section markers, resolved citations, source-local numerical support (or an
explicit assumption), issuer/context separation, gap disclosure, an honest
research status, and a non-personalised research disclaimer.  Non-strict mode
accepts heading aliases for migration previews, but it never relaxes evidence,
upstream, or compliance failures into ``complete``.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "company-research-report-gate@1"

SECTION_ORDER: tuple[str, ...] = (
    "company_profile",
    "financials",
    "expectations",
    "valuation",
    "risk_catalysts",
    "scenarios_verdict",
    "sources_gaps",
)

CANONICAL_SECTION_HEADINGS: Mapping[str, str] = {
    "company_profile": "## [section:company_profile] 结论摘要",
    "financials": "## [section:financials] 事实",
    "expectations": "## [section:expectations] 推断",
    "valuation": "## [section:valuation] 估值",
    "risk_catalysts": "## [section:risk_catalysts] 风险与反证",
    "scenarios_verdict": "## [section:scenarios_verdict] 裁决点",
    "sources_gaps": "## [section:sources_gaps] 数据缺口",
}

REPORT_GATE_PROMPT_CONTRACT = """Final company report contract:
- Start with `<!-- company-research-status: STATUS -->`, where STATUS is one of
  complete / incomplete / stale / failed and does not overstate the upstream gate.
- Use these seven level-2 headings exactly once and in this order:
  {headings}
- Cite evidence as `[evidence:EVIDENCE_ID]`, source aliases as
  `[source:SOURCE_ID]`, and deterministic calculations as
  `[calculation:CALCULATION_ID]`.
- Every report number must be supported by a citation on the same claim, or be
  explicitly labelled as an assumption (for example `情景假设：...`).
- A citation at the end of a long line is not blanket support for every number
  on that line.  Keep one logical numeric claim per Markdown line, place its
  citation on that same line, and copy the number exactly from that cited
  source's prompt-facing local payload or from the cited calculation output.
  Do not round, rescale, aggregate, or expose an intermediate/formula constant
  unless that transformed value is itself a registered calculation output.
- Put every analyst-chosen scenario threshold, range, benchmark, or deadline on
  a separate line beginning with `情景假设：` and a stable
  `[assumption:ASSUMPTION_ID]`.  Keep observed baselines on separately cited
  fact lines.  Never attach an evidence citation merely to legitimise a
  model-chosen threshold; if a number cannot meet either rule, omit it.
- In the gap section, prefer qualitative wording such as `无可用数据`; do not
  restate row counts or ranks unless the exact number has its own citation.
- Industry, macro, flow and peripheral-market context must never be stated as
  issuer evidence.  Disclose upstream gaps in the final section using
  `[gap:GAP_ID_OR_CODE]`.
- Never output a target price / price target, implied price, price anchor,
  stop-loss, or a buy / sell / add / reduce-position direction (including
  目标价、隐含价格、价格锚、止损、买入、卖出、加仓、减仓、增持、减持).
- In `风险与反证`, reference every structured strong claim from the final risk
  contract with its exact `[claim:CLAIM_ID]`.  In `裁决点`, reference at least
  three eligible issuer-backed points with exact `[decision:POINT_ID]` markers.
  Never invent either id from prose.
- State that the report is for research/reference only, is not investment
  advice, is not personalised, and has not considered any reader's financial
  situation, risk tolerance, or investment objectives.
""".format(headings="\n  ".join(CANONICAL_SECTION_HEADINGS.values()))


_SECTION_ALIASES: Mapping[str, tuple[str, ...]] = {
    "company_profile": (
        "结论摘要", "公司画像", "公司概况", "公司概览", "公司档案", "业务画像",
        "company profile", "profile",
    ),
    "financials": (
        "事实", "财务", "财务分析", "经营与财务", "财务与经营", "financials",
        "financial analysis",
    ),
    "expectations": (
        "推断", "预期", "一致预期", "盈利预测", "业绩预期", "市场预期",
        "estimates", "expectations",
    ),
    "valuation": ("估值", "估值分析", "valuation"),
    "risk_catalysts": (
        "风险与反证", "风险与催化", "风险及催化", "催化剂与风险", "风险催化",
        "风险/催化", "risk catalysts", "risks and catalysts",
    ),
    "scenarios_verdict": (
        "裁决点", "情景与裁决", "情景及裁决", "情景/裁决", "情景分析",
        "牛熊情景", "综合裁决", "投资结论", "scenarios verdict",
    ),
    "sources_gaps": (
        "数据缺口", "来源与缺口", "来源及缺口", "来源/缺口", "证据与缺口",
        "数据来源", "来源与限制", "证据来源", "sources gaps",
    ),
}

_ISSUER_REQUIRED_SECTIONS = frozenset({
    "company_profile", "financials", "expectations", "valuation",
})

_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*$")
_SECTION_MARKER_RE = re.compile(
    r"(?:\[\s*section\s*:\s*([a-z_]+)\s*\]|"
    r"\{#([a-z_]+)\}|"
    r"<!--\s*section\s*:\s*([a-z_]+)\s*-->)",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"<!--\s*company-research-status\s*:\s*"
    r"(complete|incomplete|stale|failed)\s*-->|"
    r"(?:公司研究|研究|证据链|工作流)?\s*状态\s*[:：]\s*"
    r"(complete|incomplete|stale|failed|完整|不完整|陈旧|过期|失败)",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(
    r"(?:\[|〔)\s*(?:\^\s*)?"
    r"(?:(evidence|source|calculation|calc|证据|来源|计算)\s*[:：]\s*)?"
    r"([A-Za-z][A-Za-z0-9_.#@:/?-]{2,})\s*(?:\]|〕)",
    re.IGNORECASE,
)
_GAP_REF_RE = re.compile(
    r"(?:\[|〔)\s*gap\s*[:：]\s*([^\]〕\s]+)\s*(?:\]|〕)",
    re.IGNORECASE,
)
_ASSUMPTION_REF_RE = re.compile(
    r"(?:\[|〔)\s*(?:assumption|假设)"
    r"(?:\s*[:：]\s*([^\]〕\s]+))?\s*(?:\]|〕)",
    re.IGNORECASE,
)
_RISK_MARKER_RE = re.compile(
    r"(?:\[|〔)\s*(claim|decision)\s*[:：]\s*"
    r"([A-Za-z][A-Za-z0-9_.#@/-]{1,})\s*(?:\]|〕)",
    re.IGNORECASE,
)
_EXPLICIT_ASSUMPTION_RE = re.compile(
    r"(?:情景)?假设\s*[:：]|假定\s*[:：]|assumption\s*[:：]",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    # ``\b`` is not an ASCII boundary in Python: Chinese characters are
    # Unicode word characters, so ``2026-06-30总资产`` used to miss this
    # branch and was audited as three unrelated numeric claims.  Digits are
    # the only relevant token delimiter here.
    r"(?<!\d)(?:19|20)\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?(?!\d)"
    r"|(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"
)
_SECURITY_CODE_RE = re.compile(r"\b(?:SH|SZ|BJ)?\d{6}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(
    # Python's Unicode ``\w`` includes Chinese characters.  Using it as the
    # left boundary therefore rejects a valid number immediately following a
    # Chinese label (``市值360.16``), then incorrectly starts again after the
    # decimal point and audits only ``16``.  Restrict identifier boundaries to
    # ASCII and also block ``.`` so a rejected token can never be recovered as
    # a decimal tail.  The right boundary symmetrically prevents prefixes of
    # identifiers/decimals from being accepted.
    r"(?<![A-Za-z0-9_.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"\s*(?:%|％|bps?|BP|基点|倍|亿元|万元|亿股|万股|亿|万|元|股|美元|港元|人民币)?"
    r"(?![A-Za-z0-9_.,])",
    re.IGNORECASE,
)
_REPORTING_PERIOD_RE = re.compile(
    # The explicit Chinese year suffix terminates a reporting period even
    # when a metric follows without a space (``2026-2028年EPS``).  Apply the
    # ASCII identifier guard only to Q/H/E suffixes, where it is necessary.
    r"(?<!\d)(?:(?:19|20)\d{2}\s*[-–—至]\s*(?:19|20)\d{2}\s*年|"
    r"(?:FY\s*)?(?:19|20)\d{2}\s*(?:全年|年)|"
    r"(?:FY\s*)?(?:19|20)\d{2}\s*(?:Q[1-4]|H[12]|E)(?![A-Za-z0-9_]))",
    re.IGNORECASE,
)
_CAGR_PERIOD_RE = re.compile(
    # An explicitly named CAGR identifies a year interval even without 年.
    # Bare four-digit ranges, financial quantities and identifier fragments
    # retain their numeric audit; only ascending calendar-year intervals close.
    r"(?<![A-Za-z0-9_.,])(?P<start>(?:19|20)\d{2})\s*[-–—至]\s*"
    r"(?P<end>(?:19|20)\d{2})(?=\s+CAGR(?![A-Za-z0-9_]))",
    re.IGNORECASE,
)
_CNY_UNITS = frozenset({"cny", "rmb", "人民币", "人民币元", "元"})
_SHARE_UNITS = frozenset({"share", "shares", "股"})
_RATIO_UNITS = frozenset({"", "ratio", "decimal", "fraction", "小数", "比例"})
_PERCENT_UNITS = frozenset({"%", "％", "percent", "percentage", "pct", "百分比"})
_CNY_VALUE_KEY_RE = re.compile(
    r"(?:value|amount|revenue|income|profit|cost|expense|cash|asset|liabil|"
    r"market[_-]?cap|营业|营收|收入|利润|净利|成本|费用|现金|资产|负债|市值|金额)",
    re.IGNORECASE,
)
_STRUCTURAL_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)")
_ISSUER_CLAIM_RE = re.compile(
    r"(?:公司|本公司|该公司|发行人|营收|收入|利润|毛利|净利|现金流|资产|"
    r"负债|EPS|ROE|ROIC|PE|PB|估值|目标价|市值|股东|产能|销量|订单|"
    r"管理层|业绩|预期|一致预期)",
    re.IGNORECASE,
)
_EXPLICIT_ISSUER_SUBJECT_RE = re.compile(
    r"(?:公司|本公司|该公司|发行人|标的公司|标的)", re.IGNORECASE,
)
_CONTEXT_LABEL_RE = re.compile(
    r"(?:行业|产业|宏观|外围|市场背景|同业|供应链背景|情绪背景|context|"
    r"仅作背景|背景信号)",
    re.IGNORECASE,
)
_FALSE_COMPLETENESS_RE = re.compile(
    r"(?:证据链|数据|上游|工作流)(?:已经|已)?(?:完整|全部接通)|"
    r"无(?:任何|重大|实质)?(?:数据)?缺口|"
    r"全部(?:数据源)?(?:已经|已)?(?:接通|完成)",
    re.IGNORECASE,
)
_PERSONALISED_RE = re.compile(
    r"(?:建议你|你应该|适合你|根据你的|为你(?:配置|推荐)|"
    r"您的持仓|你的持仓|您的仓位|你的仓位|你可(?:以)?(?:买入|卖出|加仓|减仓))",
    re.IGNORECASE,
)
_PROHIBITED_REPORT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("target_price", re.compile(
        r"目标(?:股)?价(?:格)?|(?:price[-\s]+target|target[-\s]+(?:share[-\s]+)?price)",
        re.IGNORECASE,
    )),
    ("implied_price", re.compile(
        r"隐含(?:股价|价格)|implied\s+(?:share\s+)?price",
        re.IGNORECASE,
    )),
    ("price_anchor", re.compile(
        r"(?:股价|价格)锚|price\s+anchor",
        re.IGNORECASE,
    )),
    ("stop_loss", re.compile(
        r"止损|止盈|入场价|退出价|stop[-\s]?loss|take[-\s]?profit|"
        r"(?:entry|exit)[-\s]+price",
        re.IGNORECASE,
    )),
    ("position_direction", re.compile(
        r"仓位建议|建议仓位|持仓比例建议|position[-\s]+sizing|"
        r"(?:portfolio[-\s]+)?allocation",
        re.IGNORECASE,
    )),
    ("trading_direction_zh", re.compile(
        r"(?:建议|评级|维持|给予|操作|应该|应当|可考虑|宜)"
        r".{0,12}(?:买入|卖出|加仓|减仓|增持|减持)|"
        r"(?:买入|卖出|加仓|减仓|增持|减持).{0,8}(?:评级|操作)",
    )),
    ("trading_direction_en", re.compile(
        r"\b(?:recommend|rating|action)\b.{0,24}"
        r"\b(?:buy|buying|sell|selling|hold|avoid|accumulate|overweight|underweight)\b|"
        r"\b(?:buy|sell|hold|avoid|accumulate|overweight|underweight)\b"
        r".{0,12}\b(?:rating|recommendation|action)\b|"
        r"\b(?:add(?:ing)?\s+to|reduce|reducing)\s+(?:the\s+|a\s+|your\s+)?position\b",
        re.IGNORECASE,
    )),
)

_CALCULATION_CLOSING_STATUSES = frozenset({"complete", "stale"})
_CALCULATION_BAD_PAYLOAD_STATUSES = frozenset({
    "error", "failed", "invalid", "incomplete", "missing", "not_available",
    "not-available", "unavailable", "unknown",
})
_SOURCE_CLOSING_STATUSES = frozenset({
    "ok", "partial", "saved", "complete", "fresh", "live", "resolved",
})
_DECISION_POINT_STATUSES = frozenset({
    "pending", "scheduled", "monitoring", "triggered", "resolved", "closed",
    "overdue",
})
_NEGATED_DIRECTION_DISCLOSURE_RE = re.compile(
    r"(?:不|未|无|不会|不得)(?:提供|给出|构成|包含)?.{0,12}"
    r"(?:买卖|交易|仓位|持仓).{0,10}(?:建议|指令|方向|配置)",
)

_STATUS_ALIASES = {
    "complete": "complete", "完整": "complete",
    "incomplete": "incomplete", "不完整": "incomplete",
    "stale": "stale", "陈旧": "stale", "过期": "stale",
    "failed": "failed", "失败": "failed",
}
# Vibe archival precedence: a stale evidence boundary is more conservative
# than an incomplete-but-current one.  The worst state always wins.
_STATUS_RANK = {"complete": 0, "incomplete": 1, "stale": 2, "failed": 3}


@dataclass(frozen=True)
class _Section:
    section_id: str
    title: str
    marker: str | None
    heading_level: int
    heading_line: int
    start_line: int
    end_line: int
    body_lines: tuple[str, ...]


def _text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value:
            return None
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _digest(value: Any) -> str:
    body = json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _finding(
    *, check_id: str, code: str, message: str, severity: str,
    section_id: str | None = None, line: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "check_id": check_id,
        "code": code,
        "message": _text(message, 800),
        "severity": severity,
    }
    if section_id:
        body["section_id"] = section_id
    if line is not None:
        body["line"] = int(line)
    if details:
        body["details"] = _json_safe(dict(details))
    return {"finding_id": "RG-" + _digest(body)[:12].upper(), **body}


def _report_body(report: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(report, str):
        return report.replace("\r\n", "\n").replace("\r", "\n"), {}
    if not isinstance(report, Mapping):
        return "", {}
    for key in ("markdown_body", "report_md", "body", "content"):
        value = report.get(key)
        if isinstance(value, str):
            return value.replace("\r\n", "\n").replace("\r", "\n"), report
    nested = report.get("report")
    if isinstance(nested, Mapping):
        for key in ("markdown_body", "report_md", "body", "content"):
            value = nested.get(key)
            if isinstance(value, str):
                return value.replace("\r\n", "\n").replace("\r", "\n"), report
    return "", report


def _normalise_heading(value: str) -> str:
    value = _SECTION_MARKER_RE.sub("", value)
    value = re.sub(r"^[\s#>*_-]*[一二三四五六七八九十百0-9]+\s*[、.)：:]\s*", "", value)
    value = re.sub(r"[\s　:/／|·—_()（）\[\]{}]+", "", value).lower()
    return value


def _alias_section_id(title: str) -> str | None:
    normalised = _normalise_heading(title)
    exact: list[tuple[int, str]] = []
    contained: list[tuple[int, str]] = []
    for section_id, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            token = _normalise_heading(alias)
            if normalised == token:
                exact.append((len(token), section_id))
            elif token and token in normalised:
                contained.append((len(token), section_id))
    candidates = exact or contained
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], SECTION_ORDER.index(item[1])))
    return candidates[0][1]


def _marker_id(value: str) -> str | None:
    match = _SECTION_MARKER_RE.search(value)
    if not match:
        return None
    return next((part.lower() for part in match.groups() if part), None)


def _parse_sections(
    markdown: str, *, strict: bool,
) -> tuple[list[_Section], list[dict[str, Any]]]:
    lines = markdown.splitlines()
    starts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    pending_marker: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        standalone = _marker_id(line)
        heading = _HEADING_RE.match(line)
        if standalone and not heading:
            pending_marker = (standalone, index + 1)
            continue
        if not heading:
            if line.strip() and not line.lstrip().startswith("<!--"):
                pending_marker = None
            continue
        heading_level = len(heading.group(1))
        marker = _marker_id(heading.group(2))
        if marker is None and pending_marker and pending_marker[1] == index:
            marker = pending_marker[0]
        pending_marker = None
        alias = _alias_section_id(heading.group(2))
        section_id = marker or alias
        if marker and marker not in SECTION_ORDER:
            findings.append(_finding(
                check_id="sections", code="unknown_section_marker",
                message=f"Unknown canonical section marker {marker!r}.",
                severity="high", line=index + 1,
                details={"marker": marker},
            ))
            continue
        if marker and heading_level != 2:
            findings.append(_finding(
                check_id="sections", code="canonical_section_heading_level_invalid",
                message=(f"Canonical section {marker!r} must use a level-2 "
                         "Markdown heading."),
                severity="high", section_id=marker, line=index + 1,
                details={"heading_level": heading_level},
            ))
        if not marker and heading_level != 2:
            # Nested headings may reuse words such as "估值方法" without
            # becoming top-level company-report sections.
            continue
        if section_id not in SECTION_ORDER and strict and heading_level == 2:
            findings.append(_finding(
                check_id="sections", code="unexpected_level2_section",
                message="Strict archival mode permits only the seven canonical level-2 sections.",
                severity="high", line=index + 1,
                details={"title": _text(heading.group(2), 240)},
            ))
        if marker and alias and marker != alias:
            findings.append(_finding(
                check_id="sections", code="section_marker_title_mismatch",
                message=(f"Heading title resolves to {alias!r}, but its marker is "
                         f"{marker!r}."),
                severity="high", section_id=marker, line=index + 1,
                details={"marker": marker, "title_alias": alias},
            ))
        if strict and marker in SECTION_ORDER:
            expected_heading = CANONICAL_SECTION_HEADINGS[marker]
            expected_title = _SECTION_MARKER_RE.sub(
                "", expected_heading.removeprefix("## ")
            ).strip()
            observed_title = _SECTION_MARKER_RE.sub("", heading.group(2)).strip()
            if observed_title != expected_title:
                findings.append(_finding(
                    check_id="sections", code="canonical_section_title_mismatch",
                    message=(f"Canonical section {marker!r} must be titled "
                             f"{expected_title!r}."),
                    severity="high", section_id=marker, line=index + 1,
                    details={"expected": expected_title, "observed": observed_title},
                ))
        if section_id not in SECTION_ORDER:
            continue
        starts.append({
            "section_id": section_id,
            "title": _SECTION_MARKER_RE.sub("", heading.group(2)).strip(),
            "marker": marker,
            "heading_level": heading_level,
            "heading_line": index + 1,
            "body_start": index + 1,
        })

    sections: list[_Section] = []
    for position, item in enumerate(starts):
        body_start = int(item["body_start"])
        body_end = (
            int(starts[position + 1]["heading_line"]) - 1
            if position + 1 < len(starts) else len(lines)
        )
        sections.append(_Section(
            section_id=str(item["section_id"]),
            title=str(item["title"]),
            marker=item["marker"],
            heading_level=int(item["heading_level"]),
            heading_line=int(item["heading_line"]),
            start_line=body_start + 1,
            end_line=body_end,
            body_lines=tuple(lines[body_start:body_end]),
        ))
    return sections, findings


def _walk_mappings(value: Any, *, max_depth: int = 10) -> Iterable[Mapping[str, Any]]:
    seen: set[int] = set()

    def walk(item: Any, depth: int) -> Iterable[Mapping[str, Any]]:
        if depth > max_depth:
            return
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            yield item
            for child in item.values():
                yield from walk(child, depth + 1)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            for child in item:
                yield from walk(child, depth + 1)

    yield from walk(value, 0)


def _hub_from(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    if isinstance(value.get("source_register"), list) \
            and isinstance(value.get("evidence_scope"), Mapping):
        return value
    for row in _walk_mappings(value):
        if isinstance(row.get("source_register"), list) \
                and isinstance(row.get("evidence_scope"), Mapping):
            return row
    return {}


def _normalise_status(value: Any) -> str | None:
    return _STATUS_ALIASES.get(str(value or "").strip().lower())


def _is_research_control(value: Mapping[str, Any]) -> bool:
    """Identify status/gap envelopes, excluding ordinary source payload rows."""
    schema = str(value.get("schema_version") or "").lower()
    if schema.startswith((
        "company-research-hub@",
        "company-research-workspace@",
        "company-research-report-gate@",
    )):
        return True
    if value.get("pipeline") == "company_research":
        return True
    if any(key in value for key in (
        "completion_status", "stage_status", "stage_statuses",
        "known_unconnected_nodes",
    )):
        return True
    stages = value.get("stages")
    return bool(
        isinstance(stages, list)
        and any(
            isinstance(stage, Mapping) and stage.get("stage") in {
                "profile", "financials", "estimates", "valuation", "risk", "report",
            }
            for stage in stages
        )
    )


def _upstream_state(upstream: Any, evidence: Any) -> dict[str, Any]:
    statuses: list[tuple[str, str]] = []
    stage_statuses: list[tuple[str, str]] = []
    freshness: list[str] = []
    for label, value in (("upstream", upstream), ("evidence", evidence)):
        if isinstance(value, str):
            status = _normalise_status(value)
            if status:
                statuses.append((label, status))
            continue
        for index, row in enumerate(_walk_mappings(value)):
            # The direct object is caller-declared upstream state.  Nested
            # objects count only when they carry the company-research control
            # contract.  A failed optional provider row must not turn the
            # whole company workflow into ``failed``.
            if index > 0 and not _is_research_control(row):
                continue
            for key in ("completion_status", "status"):
                status = _normalise_status(row.get(key))
                if status:
                    statuses.append((f"{label}.{key}", status))
            fresh = _normalise_status(row.get("freshness_status"))
            if fresh:
                freshness.append(fresh)
            raw_stages = row.get("stage_status") or row.get("stage_statuses")
            if isinstance(raw_stages, Mapping):
                for stage, raw_status in raw_stages.items():
                    status = _normalise_status(raw_status)
                    if status:
                        stage_statuses.append((str(stage), status))
            if isinstance(row.get("stages"), list):
                for stage in row["stages"]:
                    if not isinstance(stage, Mapping):
                        continue
                    status = _normalise_status(stage.get("status"))
                    if status:
                        stage_statuses.append((str(stage.get("stage") or "unknown"), status))

    # A nested source can have an ordinary "status: ok" which is ignored.  The
    # worst recognised company-research status wins; stage failures are never
    # hidden by a top-level optimistic field.
    status_values = [status for _label, status in statuses]
    status_values.extend(status for _stage, status in stage_statuses)
    status = max(status_values, key=lambda item: _STATUS_RANK[item]) if status_values else "unknown"
    if status == "complete" and "stale" in freshness:
        status = "stale"
    return {
        "status": status,
        "reported_statuses": [
            {"location": label, "status": item} for label, item in statuses
        ],
        "stage_statuses": [
            {"stage": stage, "status": item} for stage, item in stage_statuses
        ],
    }


def _gap_key(row: Mapping[str, Any]) -> str:
    return _text(
        row.get("gap_id") or row.get("code") or row.get("kind")
        or row.get("source_id"),
        160,
    )


def _collect_gaps(upstream: Any, evidence: Any) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def add(raw: Any) -> None:
        if not isinstance(raw, list):
            return
        for gap in raw:
            if not isinstance(gap, Mapping):
                continue
            key = _gap_key(gap)
            if not key:
                continue
            critical = bool(gap.get("critical")) or str(
                gap.get("severity") or ""
            ).lower() in {"required", "critical", "high"}
            details = gap.get("details") if isinstance(gap.get("details"), Mapping) else {}
            # Pipeline projections preserve the original gap ID in details;
            # draft preflight sees those rows even when the raw Hub gap is not
            # separately present. Preserve that exact alias, not prose guesses.
            nested_gap_id = _text(details.get("gap_id"), 160).lower()
            item = {
                "key": key,
                "aliases": sorted({
                    _text(gap.get(field), 160).lower()
                    for field in ("gap_id", "code", "kind")
                    if _text(gap.get(field), 160)
                } | {key.lower()} | ({nested_gap_id} if nested_gap_id else set())),
                "stage": _text(gap.get("stage"), 80) or None,
                "source_id": _text(gap.get("source_id") or gap.get("input_key"), 180) or None,
                "critical": critical,
                "message": _text(gap.get("detail") or gap.get("message"), 500),
            }
            identity = key.lower() + "|" + str(item["stage"] or "") + "|" + str(item["source_id"] or "")
            previous = rows.get(identity)
            if previous is not None:
                item["aliases"] = sorted(set(item["aliases"]) | set(previous["aliases"]))
            if previous is None or (item["critical"] and not previous["critical"]):
                rows[identity] = item
            else:
                previous["aliases"] = item["aliases"]

    for value in (upstream, evidence):
        for index, mapping in enumerate(_walk_mappings(value)):
            if index > 0 and not _is_research_control(mapping):
                continue
            add(mapping.get("gaps"))
            stages = mapping.get("stages")
            if isinstance(stages, list):
                for stage in stages:
                    if isinstance(stage, Mapping):
                        add(stage.get("gaps"))
    return sorted(rows.values(), key=lambda item: (
        not item["critical"], str(item["stage"] or ""), item["key"].lower(),
    ))


def _industry_gap_count_support(
    line: str, number: Mapping[str, Any], gaps: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Close only the registered all-pending industry-candidate count.

    A gap reference is not an evidence reference.  This narrow exception is
    for an operational limitation disclosed in sources_gaps, never an issuer
    fact or an arbitrary number that happens to occur in a gap message.
    """
    if number.get("suffix") or not re.fullmatch(r"\d+", str(number.get("raw") or "")):
        return []
    gap_refs = {token.lower() for token in _GAP_REF_RE.findall(line)}
    cleaned = _strip_non_claim_numbers(line).strip()
    disclosure = re.fullmatch(
        r"行业(?:新闻|资讯)?候选\s*(\d+)\s*条(?:均|全部)(?:未审核|待审核)"
        r"[，,；;]?\s*(?:不能|不可|不得)作为发行人事实[。.]?", cleaned,
    )
    # The production writer also names the source explicitly when saying
    # that no reviewed articles are available.  This is the same operational
    # count, not an issuer claim.  Keep it an anchored, source-specific form
    # and corroborate both the all-pending and zero-confirmed assertions below.
    source_named_disclosure = False
    if not disclosure:
        disclosure = re.fullmatch(
            r"无可用数据[：:]\s*行业新闻雷达[（(]research\.newsradar\.live[）)]"
            r"\s*(\d+)\s*条候选(?:均未评审[，,]\s*无用户确认相关条目|"
            r"均未审核[，,]\s*不能作为发行人事实)\s*[。.]?",
            cleaned,
        )
        source_named_disclosure = disclosure is not None
    if not disclosure or disclosure.group(1) != str(number.get("raw")):
        return []
    supported: list[str] = []
    for gap in gaps:
        aliases = set(gap.get("aliases") or [str(gap.get("key") or "").lower()])
        if "industry_news_review_pending" not in aliases or not gap_refs.intersection(aliases):
            continue
        if source_named_disclosure and str(gap.get("source_id") or "").lower() \
                != "research.newsradar.live":
            continue
        counts = re.match(
            r"^(\d+) industry candidates were fetched; (\d+) remain unreviewed/uncertain,"
            r"(?: (\d+) are unrelated, and (\d+) are user-confirmed related/report-eligible\.)?",
            str(gap.get("message") or ""),
        )
        if source_named_disclosure and (
            not counts or counts.group(3) != "0" or counts.group(4) != "0"
        ):
            continue
        if counts and counts.group(1) == counts.group(2) == disclosure.group(1):
            supported.extend("gap:" + ref for ref in sorted(gap_refs.intersection(aliases)))
    return sorted(set(supported))


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _decimal_key(value: Decimal) -> str:
    if value == 0:
        return "0"
    normal = value.normalize()
    return format(normal, "f")


def _numeric_variants(value: Decimal, *, suffix: str = "") -> set[str]:
    suffix = suffix.strip().lower().replace("％", "%")
    scaled = value
    if suffix in {"亿", "亿元", "亿股"}:
        scaled = value * Decimal("100000000")
    elif suffix in {"万", "万元", "万股"}:
        scaled = value * Decimal("10000")
    elif suffix == "%":
        scaled = value / Decimal("100")
    elif suffix in {"bp", "bps", "基点"}:
        scaled = value / Decimal("10000")

    variants = {_decimal_key(value), _decimal_key(scaled)}
    # Source systems commonly store percentages as either 0.125 or 12.5.  Both
    # representations are accepted, but only after an actual cited value was
    # found.  Do not pre-expand rounded variants: two distinct values could
    # otherwise intersect through an unrelated coarse rounding.
    if abs(value) <= 1:
        variants.add(_decimal_key(value * Decimal("100")))
    if abs(scaled) <= 1:
        variants.add(_decimal_key(scaled * Decimal("100")))
    return variants


def _cny_values_in_value(
    value: Any, *, inherited_cny: bool = False, depth: int = 0,
) -> set[str]:
    """Collect base-yuan values only where structured metadata says CNY."""
    out: set[str] = set()
    if depth > 10 or value is None:
        return out
    if isinstance(value, Mapping):
        unit = str(value.get("unit") or "").strip().lower().replace(" ", "")
        is_cny = inherited_cny or unit in _CNY_UNITS
        if is_cny:
            for key, item in value.items():
                if isinstance(item, bool) or not isinstance(item, (int, float, Decimal)):
                    continue
                if _CNY_VALUE_KEY_RE.search(str(key)):
                    decimal = _decimal(item)
                    if decimal is not None and decimal.is_finite():
                        out.add(_decimal_key(decimal))
        for item in value.values():
            out.update(
                _cny_values_in_value(item, inherited_cny=is_cny, depth=depth + 1),
            )
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            out.update(
                _cny_values_in_value(item, inherited_cny=inherited_cny, depth=depth + 1),
            )
    return out


def _calculation_input_facets(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Collect only typed ``inputs.*.value`` fields; never arbitrary literals."""
    values: list[dict[str, str]] = []
    cny_values: set[str] = set()
    if depth > 10 or value is None:
        return {"values": values, "cny_values": cny_values}
    if isinstance(value, Mapping):
        if "value" in value and not isinstance(value.get("value"), bool):
            decimal = _decimal(value.get("value"))
            if decimal is not None and decimal.is_finite():
                unit = str(value.get("unit") or "").strip().lower().replace(" ", "")
                values.append({"value": _decimal_key(decimal), "unit": unit})
                if unit in _CNY_UNITS:
                    cny_values.add(_decimal_key(decimal))
        for item in value.values():
            nested = _calculation_input_facets(item, depth=depth + 1)
            values.extend(nested["values"])
            cny_values.update(nested["cny_values"])
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            nested = _calculation_input_facets(item, depth=depth + 1)
            values.extend(nested["values"])
            cny_values.update(nested["cny_values"])
    unique_values = {
        (item["value"], item["unit"]): item for item in values
    }
    return {
        "values": list(unique_values.values()),
        "cny_values": cny_values,
    }


def _verified_valuation_share_inputs(evidence: Any) -> dict[str, dict[str, str]]:
    """Return only typed, closed valuation-DAG share inputs keyed by exact ref.

    This is intentionally narrower than walking arbitrary evidence values.  A
    share count may close rounded ``亿股``/``万股`` prose only when the
    canonical valuation input contract and its extractor both closed, and the
    row carries an exact field ref, supported share unit and finite value.
    Lineage resolution still independently corroborates the value below.
    """
    if not isinstance(evidence, Mapping):
        return {}
    contracts = evidence.get("contracts")
    if not isinstance(contracts, Mapping):
        return {}
    contract = contracts.get("valuation_extended_dag")
    if not isinstance(contract, Mapping) \
            or str(contract.get("schema_version") or "") != "company-valuation-dag@1" \
            or str(contract.get("input_schema_version") or "") != "company-valuation-input@1" \
            or str(contract.get("status") or "").lower() != "complete":
        return {}
    extraction = contract.get("extraction")
    if not isinstance(extraction, Mapping) \
            or str(extraction.get("schema_version") or "") \
            != "company-valuation-input-extraction@1" \
            or str(extraction.get("status") or "").lower() != "complete":
        return {}

    out: dict[str, dict[str, str]] = {}
    register = contract.get("input_register")
    if not isinstance(register, list):
        return out
    for row in register:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("key") or "").strip().lower()
        unit = str(row.get("unit") or "").strip().lower().replace(" ", "")
        ref = _text(row.get("ref"), 240)
        decimal = _decimal(row.get("value"))
        if key not in {"shares_outstanding", "total_shares"} \
                or unit not in _SHARE_UNITS or not ref \
                or decimal is None or not decimal.is_finite() or decimal <= 0:
            continue
        out[ref.lower()] = {"value": _decimal_key(decimal), "unit": unit}
    return out


def _calculation_output_values(value: Any, *, inherited_unit: str = "", depth: int = 0) -> list[dict[str, str]]:
    if depth > 10 or value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float, Decimal)):
        decimal = _decimal(value)
        return ([{"value": _decimal_key(decimal), "unit": inherited_unit}]
                if decimal is not None and decimal.is_finite() else [])
    if isinstance(value, Mapping):
        unit = str(value.get("unit") or inherited_unit).strip().lower().replace(" ", "")
        if "value" in value:
            return _calculation_output_values(value.get("value"), inherited_unit=unit, depth=depth + 1)
        out: list[dict[str, str]] = []
        for item in value.values():
            out.extend(_calculation_output_values(item, inherited_unit=unit, depth=depth + 1))
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        out: list[dict[str, str]] = []
        for item in value:
            out.extend(_calculation_output_values(item, inherited_unit=inherited_unit, depth=depth + 1))
        return out
    return []


def _claim_decimal_places(number: Mapping[str, Any]) -> int:
    numeric = re.match(
        r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?",
        str(number.get("raw") or ""),
    )
    return len(numeric.group(1) or "") if numeric else 0


def _rounded_display_matches(actual: Decimal, displayed: Decimal, places: int) -> bool:
    quantum = Decimal(1).scaleb(-places)
    return actual.quantize(quantum, rounding=ROUND_HALF_UP) == displayed


def _number_matches_cny_value(number: Mapping[str, Any], raw_value: str) -> bool:
    suffix = str(number.get("suffix") or "").strip().lower().replace("％", "%")
    divisor = (
        Decimal("100000000") if suffix in {"亿", "亿元"}
        else Decimal("10000") if suffix in {"万", "万元"}
        else None
    )
    actual = _decimal(raw_value)
    displayed = _decimal(number.get("value"))
    return bool(
        divisor is not None and actual is not None and displayed is not None
        and _rounded_display_matches(
            actual / divisor, displayed, _claim_decimal_places(number),
        )
    )


def _number_matches_input_value(number: Mapping[str, Any], item: Mapping[str, str]) -> bool:
    actual = _decimal(item.get("value"))
    displayed = _decimal(number.get("value"))
    if actual is None or displayed is None:
        return False
    suffix = str(number.get("suffix") or "").strip().lower().replace("％", "%")
    if suffix == "%":
        return item.get("unit") in _RATIO_UNITS and _rounded_display_matches(
            actual * Decimal("100"), displayed, _claim_decimal_places(number),
        )
    if suffix in {"bp", "bps", "基点"}:
        return item.get("unit") in _RATIO_UNITS and _rounded_display_matches(
            actual * Decimal("10000"), displayed, _claim_decimal_places(number),
        )
    if suffix in {"亿", "亿元", "万", "万元"}:
        return item.get("unit") in _CNY_UNITS and _number_matches_cny_value(
            number, item["value"],
        )
    if suffix in {"亿股", "万股"}:
        divisor = Decimal("100000000") if suffix == "亿股" else Decimal("10000")
        return item.get("unit") in _SHARE_UNITS and _rounded_display_matches(
            actual / divisor, displayed, _claim_decimal_places(number),
        )
    return actual == displayed


def _number_matches_output_value(number: Mapping[str, Any], item: Mapping[str, str]) -> bool:
    actual = _decimal(item.get("value"))
    displayed = _decimal(number.get("value"))
    if actual is None or displayed is None:
        return False
    suffix = str(number.get("suffix") or "").strip().lower().replace("％", "%")
    if suffix == "%":
        if item.get("unit") in _PERCENT_UNITS:
            return _rounded_display_matches(actual, displayed, _claim_decimal_places(number))
        return item.get("unit") in _RATIO_UNITS and _rounded_display_matches(
            actual * Decimal("100"), displayed, _claim_decimal_places(number),
        )
    if suffix in {"bp", "bps", "基点"}:
        return item.get("unit") in _RATIO_UNITS and _rounded_display_matches(
            actual * Decimal("10000"), displayed, _claim_decimal_places(number),
        )
    if suffix in {"亿", "亿元", "万", "万元"}:
        return item.get("unit") in _CNY_UNITS and _number_matches_cny_value(
            number, item["value"],
        )
    return _rounded_display_matches(actual, displayed, _claim_decimal_places(number))


def _reference_supports_number(
    number: Mapping[str, Any], reference: Mapping[str, Any],
) -> bool:
    if set(number.get("variants") or ()) & set(reference.get("numbers") or ()):
        return True
    if any(
        _number_matches_cny_value(number, value)
        for value in (reference.get("cny_values") or ())
    ):
        return True
    if any(
        _number_matches_output_value(number, item)
        for item in (reference.get("calculation_output_values") or ())
    ):
        return True
    return any(
        _number_matches_input_value(number, item)
        for item in (reference.get("trusted_input_values") or ())
    )


def _strip_non_claim_numbers(line: str) -> str:
    line = _SECTION_MARKER_RE.sub("", line)
    line = _STATUS_RE.sub("", line)
    line = _CITATION_RE.sub("", line)
    line = _GAP_REF_RE.sub("", line)
    line = _ASSUMPTION_REF_RE.sub("", line)
    line = _RISK_MARKER_RE.sub("", line)
    line = _DATE_RE.sub("", line)
    line = _REPORTING_PERIOD_RE.sub("", line)
    line = _CAGR_PERIOD_RE.sub(
        lambda match: "" if int(match["end"]) > int(match["start"]) else match.group(0),
        line,
    )
    line = _SECURITY_CODE_RE.sub("", line)
    return _STRUCTURAL_PREFIX_RE.sub("", line)


def _numbers_in_text(value: str) -> list[dict[str, Any]]:
    cleaned = _strip_non_claim_numbers(value)
    rows: list[dict[str, Any]] = []
    for match in _NUMBER_RE.finditer(cleaned):
        raw = match.group(0).strip()
        numeric = re.match(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", raw)
        if not numeric:
            continue
        value_decimal = _decimal(numeric.group(0))
        if value_decimal is None:
            continue
        suffix = raw[numeric.end():].strip()
        rows.append({
            "raw": raw,
            "value": _decimal_key(value_decimal),
            "suffix": suffix,
            "variants": sorted(_numeric_variants(value_decimal, suffix=suffix)),
        })
    return rows


def _numbers_in_value(value: Any, *, depth: int = 0) -> set[str]:
    if depth > 10:
        return set()
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float, Decimal)):
        decimal = _decimal(value)
        return _numeric_variants(decimal) if decimal is not None else set()
    if isinstance(value, str):
        out: set[str] = set()
        for item in _numbers_in_text(value):
            out.update(item["variants"])
        return out
    if isinstance(value, Mapping):
        out: set[str] = set()
        for item in value.values():
            out.update(_numbers_in_value(item, depth=depth + 1))
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        out: set[str] = set()
        for item in value:
            out.update(_numbers_in_value(item, depth=depth + 1))
        return out
    return set()


def _exact_numeric_values(value: Any, *, depth: int = 0) -> set[str]:
    """Return scalar numeric values without rounding or unit expansion."""
    if depth > 10 or value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float, Decimal)):
        decimal = _decimal(value)
        return (
            {_decimal_key(decimal)}
            if decimal is not None and decimal.is_finite() else set()
        )
    if isinstance(value, Mapping):
        out: set[str] = set()
        for item in value.values():
            out.update(_exact_numeric_values(item, depth=depth + 1))
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        out: set[str] = set()
        for item in value:
            out.update(_exact_numeric_values(item, depth=depth + 1))
        return out
    return set()


def _evidence_payloads(evidence: Any) -> tuple[Mapping[str, Any], list[Any]]:
    pack_sections: Mapping[str, Any] = {}
    snapshots: list[Any] = []
    if not isinstance(evidence, Mapping):
        return pack_sections, snapshots
    for row in _walk_mappings(evidence):
        pack = row.get("evidence_pack")
        if isinstance(pack, Mapping) and isinstance(pack.get("sections"), Mapping):
            pack_sections = pack["sections"]
            break
    if not pack_sections and str(evidence.get("schema_version") or "").startswith("company-research-evidence@") \
            and isinstance(evidence.get("sections"), Mapping):
        pack_sections = evidence["sections"]
    if not pack_sections and isinstance(evidence.get("sections"), Mapping):
        # Engine evidence packs retain the original named company sections.
        pack_sections = evidence["sections"]

    for row in _walk_mappings(evidence):
        raw = row.get("workspace_snapshots")
        if isinstance(raw, list):
            snapshots.extend(raw)
    return pack_sections, snapshots


def _snapshot_identity(raw: Any) -> tuple[Mapping[str, Any], list[str]]:
    if not isinstance(raw, Mapping):
        return {}, []
    snapshot = raw.get("snapshot") if isinstance(raw.get("snapshot"), Mapping) else raw
    metadata = (
        raw.get("asset") if isinstance(raw.get("asset"), Mapping)
        else raw.get("metadata") if isinstance(raw.get("metadata"), Mapping)
        else raw
    )
    aliases = [
        _text(snapshot.get("dataset_id"), 180),
        _text(metadata.get("dataset_id"), 180),
        _text(metadata.get("id") or raw.get("asset_id"), 180),
    ]
    return snapshot, [item for item in aliases if item]


def _payload_top_level_eligibility(payload: Any) -> tuple[bool, str | None]:
    """Validate only the bound payload envelope, never nested providers."""
    if not isinstance(payload, Mapping):
        return True, None
    status = _text(payload.get("status"), 40).lower()
    if payload.get("future") is True:
        return False, "payload_future_source"
    if status and status not in _SOURCE_CLOSING_STATUSES:
        return False, "payload_status_not_closing"
    if "usable" in payload:
        if payload.get("usable") is False:
            return False, "payload_marked_unusable"
        if payload.get("usable") is not True:
            return False, "payload_usable_not_boolean"
    return True, None


_PRUNED_PAYLOAD = object()


def _sanitise_payload_for_numbers(value: Any, *, depth: int = 0) -> Any:
    """Copy an eligible payload while pruning explicitly unusable subtrees."""
    if depth > 10:
        return _PRUNED_PAYLOAD
    if isinstance(value, Mapping):
        eligible, _reason = _payload_top_level_eligibility(value)
        if not eligible:
            return _PRUNED_PAYLOAD
        out: dict[Any, Any] = {}
        for key, child in value.items():
            clean = _sanitise_payload_for_numbers(child, depth=depth + 1)
            if clean is not _PRUNED_PAYLOAD:
                out[key] = clean
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        out: list[Any] = []
        for child in value:
            clean = _sanitise_payload_for_numbers(child, depth=depth + 1)
            if clean is not _PRUNED_PAYLOAD:
                out.append(clean)
        return out
    return value


def _reference_conflict(
    existing: Mapping[str, Any], incoming: Mapping[str, Any], *, reason: str,
) -> dict[str, Any]:
    evidence_only = existing.get("kind") == incoming.get("kind") == "evidence"
    scope = (
        existing.get("scope")
        if existing.get("scope") == incoming.get("scope") else "unknown"
    )
    return {
        "kind": "evidence" if evidence_only else "reference_conflict",
        "scope": scope if evidence_only else "unknown",
        "source_id": None,
        "evidence_id": None,
        "status": "conflict",
        "usable": False,
        "unusable_reason": reason,
        "payload_available": bool(
            existing.get("payload_available") or incoming.get("payload_available")
        ),
        "numbers": [],
        "exact_values": [],
        "cny_values": [],
        "alias_conflicts": sorted({
            reason,
            *[str(item) for item in existing.get("alias_conflicts") or []],
            *[str(item) for item in incoming.get("alias_conflicts") or []],
        }),
    }


def _register_reference(
    refs: dict[str, dict[str, Any]], alias: str, record: dict[str, Any],
) -> None:
    token = alias.lower()
    existing = refs.get(token)
    if existing is None:
        refs[token] = record
        return
    if existing is record or existing == record:
        return
    if existing.get("kind") != "evidence" or record.get("kind") != "evidence":
        refs[token] = _reference_conflict(
            existing, record, reason="alias_kind_conflict",
        )
        return

    scope_conflict = existing.get("scope") != record.get("scope")
    usability_conflict = (
        (existing.get("usable") is True) != (record.get("usable") is True)
    )
    if scope_conflict or usability_conflict:
        refs[token] = _reference_conflict(
            existing,
            record,
            reason=(
                "alias_scope_conflict" if scope_conflict
                else "alias_usability_conflict"
            ),
        )
        return
    refs[token] = _reference_conflict(
        existing, record, reason="alias_record_conflict",
    )


def _calculation_rows(calculations: Any, evidence: Any) -> list[Mapping[str, Any]]:
    raw = calculations
    if raw is None and isinstance(evidence, Mapping):
        raw = evidence.get("calculations")
        if raw is None and isinstance(evidence.get("artifacts"), Mapping):
            raw = evidence["artifacts"].get("calculations")
    if isinstance(raw, Mapping):
        if isinstance(raw.get("nodes"), list):
            raw = raw["nodes"]
        elif isinstance(raw.get("calculations"), list):
            raw = raw["calculations"]
        else:
            raw = [raw]
    return [item for item in (raw or []) if isinstance(item, Mapping)] \
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) else []


def _calculation_input_refs(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return stable lineage refs from supported calculation input shapes.

    Vibe-style calculations evolved through several compatible encodings:
    ``input_refs`` may contain strings or reference objects, while ``inputs``
    may contain strings or objects keyed by ``ref``, ``evidence_id``, or
    ``calculation_id``.  Literal input objects without one of those keys are
    deliberately ignored rather than mistaken for source identifiers.
    """
    refs: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            ref = _text(value, 180)
            if ref:
                refs.append(ref)
            return
        if isinstance(value, Mapping):
            for key in ("ref", "evidence_id", "calculation_id"):
                if key in value:
                    collect(value.get(key))
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                collect(item)

    collect(row.get("input_refs"))
    collect(row.get("inputs"))
    # Formula DAGs encode calculation-to-calculation edges separately from
    # external data refs.  Accept both the current ``depends_on`` field and the
    # more generic ``dependencies`` alias.
    collect(row.get("depends_on"))
    collect(row.get("dependencies"))
    return tuple(dict.fromkeys(refs))


def _payload_has_result(value: Any, *, depth: int = 0) -> bool:
    """Return whether a calculation payload contains an actual output value."""
    if depth > 10 or value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, Decimal)):
        return True
    if isinstance(value, float):
        return value == value and value not in {float("inf"), float("-inf")}
    if isinstance(value, str):
        token = value.strip().lower()
        return bool(token) and token not in {
            "none", "null", "n/a", "na", "unknown", "invalid", "error",
            "failed", "not available", "unavailable",
        }
    if isinstance(value, Mapping):
        payload_status = str(value.get("status") or "").strip().lower()
        if payload_status in _CALCULATION_BAD_PAYLOAD_STATUSES:
            return False
        if "value" in value:
            return _payload_has_result(value.get("value"), depth=depth + 1)
        ignored = {
            "status", "unit", "reason", "message", "error", "errors",
            "warning", "warnings", "as_of", "generated_at",
        }
        return any(
            _payload_has_result(item, depth=depth + 1)
            for key, item in value.items() if str(key).lower() not in ignored
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_payload_has_result(item, depth=depth + 1) for item in value)
    return False


def _calculation_output(row: Mapping[str, Any]) -> dict[str, Any]:
    """Select only a status-qualified result/output; never inspect inputs."""
    status = str(
        row.get("status") or row.get("calculation_status") or ""
    ).strip().lower()
    if status not in _CALCULATION_CLOSING_STATUSES:
        return {
            "status": status or "missing",
            "usable": False,
            "payload_key": None,
            "payload": None,
            "reason": "status_not_complete_or_stale",
        }
    for key in ("result", "output"):
        if key not in row:
            continue
        payload = row.get(key)
        if _payload_has_result(payload):
            return {
                "status": status,
                "usable": True,
                "payload_key": key,
                "payload": payload,
                "reason": None,
            }
    return {
        "status": status,
        "usable": False,
        "payload_key": None,
        "payload": None,
        "reason": "result_or_output_missing",
    }


def _resolve_lineage_ref(
    ref: str, refs: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Resolve an exact id or a field path owned by a registered source/calc."""
    token = _text(ref, 240).lower()
    if not token:
        return None
    exact = refs.get(token)
    if exact is not None:
        return exact

    # Evidence contracts commonly retain field-level paths such as
    # ``pack.sections.quote_live.price``.  Source registers own the containing
    # section, so a boundary-safe prefix match preserves that issuer lineage.
    for alias in sorted(refs, key=len, reverse=True):
        if token.startswith((alias + ".", alias + "[", alias + "/")):
            return refs[alias]
    return None


def _source_inventory(evidence: Any, calculations: Any) -> dict[str, Any]:
    hub = _hub_from(evidence)
    pack_sections, snapshots = _evidence_payloads(evidence)
    verified_share_inputs = _verified_valuation_share_inputs(evidence)
    snapshot_by_alias: dict[str, list[Mapping[str, Any]]] = {}
    for raw in snapshots:
        snapshot, aliases = _snapshot_identity(raw)
        for alias in aliases:
            bucket = snapshot_by_alias.setdefault(alias.lower(), [])
            if not any(item is snapshot or item == snapshot for item in bucket):
                bucket.append(snapshot)

    issuer_ids = {
        str(item).lower() for item in (hub.get("evidence_scope", {}).get("issuer") or [])
    } if isinstance(hub.get("evidence_scope"), Mapping) else set()
    context_ids = {
        str(item).lower() for item in (hub.get("evidence_scope", {}).get("context") or [])
    } if isinstance(hub.get("evidence_scope"), Mapping) else set()

    refs: dict[str, dict[str, Any]] = {}
    source_register = hub.get("source_register") if isinstance(hub.get("source_register"), list) else []
    for row in source_register:
        if not isinstance(row, Mapping):
            continue
        evidence_id = _text(row.get("evidence_id"), 180)
        source_id = _text(row.get("source_id"), 180)
        source_ref = _text(row.get("source_ref"), 240)
        scope = _text(row.get("type"), 40).lower()
        if not scope:
            if evidence_id.lower() in issuer_ids:
                scope = "issuer"
            elif evidence_id.lower() in context_ids:
                scope = "context"
            else:
                scope = "unknown"

        payloads: list[Any] = []
        if source_ref.lower().startswith("section:"):
            section_payload = pack_sections.get(source_ref.split(":", 1)[1])
            if section_payload is not None:
                payloads.append(section_payload)
        if not payloads:
            for alias in (source_ref, source_id):
                for snapshot in snapshot_by_alias.get(alias.lower(), []):
                    if not any(item is snapshot or item == snapshot for item in payloads):
                        payloads.append(snapshot)
        if not payloads and source_id.lower().startswith("pack."):
            section_payload = pack_sections.get(
                source_id.split(".", 1)[1].split("#", 1)[0],
            )
            if section_payload is not None:
                payloads.append(section_payload)
        payload: Any = (
            payloads[0] if len(payloads) == 1 else payloads if payloads else None
        )
        is_industry_news = (
            source_id.lower().split("#", 1)[0] in {
                "research.newsradar.live", "pack.newsradar",
            }
            or source_ref.lower().split("#", 1)[0] in {
                "dataset:research.newsradar.live", "section:newsradar",
            }
        )
        if is_industry_news:
            from guanlan_v2.research.company_industry_news import (
                newsradar_report_projection_is_valid,
            )
            # Do not register an evidence alias at all unless its prompt-facing
            # body is the closed, related-only projection.  Otherwise a bare
            # citation could resolve even though no reviewed article was
            # available behind it.
            if len(payloads) != 1 or not isinstance(payload, Mapping) \
                    or not newsradar_report_projection_is_valid(payload):
                continue
        source_status = _text(row.get("status"), 40).lower() or "unknown"
        record_count = row.get("record_count")
        explicitly_empty = (
            isinstance(record_count, int)
            and not isinstance(record_count, bool)
            and record_count == 0
        )
        payload_checks = [_payload_top_level_eligibility(item) for item in payloads]
        payload_usable = all(item[0] for item in payload_checks)
        payload_unusable_reason = next(
            (item[1] for item in payload_checks if not item[0]), None,
        )
        row_usable = "usable" not in row or row.get("usable") is True
        source_usable = bool(
            (evidence_id or source_id or source_ref)
            and source_status in _SOURCE_CLOSING_STATUSES
            and row_usable
            and row.get("future") is not True
            and not explicitly_empty
            and payload_usable
        )
        if row.get("future") is True:
            unusable_reason = "future_source"
        elif source_status not in _SOURCE_CLOSING_STATUSES:
            unusable_reason = "status_not_closing"
        elif row.get("usable") is False:
            unusable_reason = "source_marked_unusable"
        elif "usable" in row and row.get("usable") is not True:
            unusable_reason = "source_usable_not_boolean"
        elif explicitly_empty:
            unusable_reason = "source_empty"
        elif not (evidence_id or source_id or source_ref):
            unusable_reason = "source_identity_missing"
        elif not payload_usable:
            unusable_reason = payload_unusable_reason
        else:
            unusable_reason = None
        numeric_payload = (
            _sanitise_payload_for_numbers(payload) if source_usable else _PRUNED_PAYLOAD
        )
        if numeric_payload is _PRUNED_PAYLOAD:
            numeric_payload = None
        metadata_numbers = _numbers_in_value(row) if source_usable else set()
        payload_numbers = _numbers_in_value(numeric_payload) if source_usable else set()
        record = {
            "kind": "evidence",
            "scope": scope,
            "source_id": source_id or None,
            "evidence_id": evidence_id or None,
            "status": source_status,
            "usable": source_usable,
            "unusable_reason": unusable_reason,
            "payload_available": payload is not None,
            "numbers": sorted(metadata_numbers | payload_numbers),
            "exact_values": sorted(
                (_exact_numeric_values(row) | _exact_numeric_values(numeric_payload))
                if source_usable else set()
            ),
            "cny_values": sorted(
                (_cny_values_in_value(row) | _cny_values_in_value(numeric_payload))
                if source_usable else set()
            ),
        }
        aliases = [evidence_id, source_id, source_ref]
        if source_ref.lower().startswith("section:"):
            section_name = source_ref.split(":", 1)[1].split("#", 1)[0]
            aliases.extend((
                f"pack.{section_name}",
                f"pack.sections.{section_name}",
            ))
        for alias in aliases:
            if alias:
                _register_reference(refs, alias, record)

    calculation_items = _calculation_rows(calculations, evidence)
    calculation_records: list[dict[str, Any]] = []
    for index, row in enumerate(calculation_items):
        calculation_id = _text(
            row.get("calculation_id") or row.get("calc_id") or row.get("id")
            or f"calculation-{index + 1}",
            180,
        )
        input_refs = _calculation_input_refs(row)
        input_facets = _calculation_input_facets(row.get("inputs"))
        for input_ref in input_refs:
            verified_share = verified_share_inputs.get(input_ref.lower())
            if verified_share is not None \
                    and verified_share not in input_facets["values"]:
                input_facets["values"].append(verified_share)
        output = _calculation_output(row)
        output_unit = ""
        if isinstance(output["payload"], (int, float, Decimal)) \
                and not isinstance(output["payload"], bool):
            # A scalar result cannot carry its own unit.  Preserve the row's
            # explicit unit, but never project it onto a composite payload.
            output_unit = str(row.get("unit") or "").strip().lower().replace(" ", "")
        record = {
            "kind": "calculation",
            "scope": "derived_unknown",
            "calculation_id": calculation_id,
            "status": output["status"],
            "usable": output["usable"],
            "payload_available": output["usable"],
            "payload_key": output["payload_key"],
            "unusable_reason": output["reason"],
            "numbers": sorted(_numbers_in_value(output["payload"])),
            "exact_values": sorted(_exact_numeric_values(output["payload"])),
            "cny_values": sorted(_cny_values_in_value(output["payload"])),
            "calculation_output_values": _calculation_output_values(
                output["payload"], inherited_unit=output_unit,
            ),
            "candidate_input_values": input_facets["values"],
            "candidate_input_cny_values": sorted(input_facets["cny_values"]),
            "trusted_input_values": [],
            "trusted_input_cny_values": [],
            "input_refs": list(input_refs),
            "unresolved_input_refs": list(input_refs),
        }
        _register_reference(refs, calculation_id, record)
        calculation_records.append(record)

    # Resolve the calculation graph to a fixed point.  All calculation ids are
    # registered before propagation, so dependency order in the artifact does
    # not matter.  A cycle or a genuinely missing reference remains
    # ``derived_unknown`` and therefore cannot close an issuer evidence gate.
    for _pass in range(len(calculation_records) + 1):
        changed = False
        for record in calculation_records:
            input_refs = [str(item) for item in record.get("input_refs") or []]
            dependencies = [_resolve_lineage_ref(item, refs) for item in input_refs]
            unresolved = [
                item for item, dependency in zip(input_refs, dependencies)
                if dependency is None or dependency.get("scope") in {
                    None, "unknown", "derived_unknown",
                }
                or dependency.get("usable") is not True
            ]
            input_scopes = {
                str(dependency.get("scope"))
                for dependency in dependencies if dependency is not None
            }
            if not record.get("usable") or not input_refs or unresolved:
                scope = "derived_unknown"
            elif input_scopes.intersection({"issuer", "issuer_derived"}):
                scope = "issuer_derived"
            elif input_scopes == {"context"}:
                scope = "context"
            else:
                scope = "derived_unknown"
            if record.get("scope") != scope \
                    or record.get("unresolved_input_refs") != unresolved:
                record["scope"] = scope
                record["unresolved_input_refs"] = unresolved
                changed = True
        if not changed:
            break

    # A calculation input may support report prose only if it is a typed
    # ``inputs.*.value``, every lineage edge resolved, and the exact value is
    # independently present in a resolved dependency payload/output.
    for record in calculation_records:
        input_refs = [str(item) for item in record.get("input_refs") or []]
        dependencies = [_resolve_lineage_ref(item, refs) for item in input_refs]
        resolved = [item for item in dependencies if item is not None]
        exact_dependency_values = {
            str(value)
            for dependency in resolved
            for value in (dependency.get("exact_values") or [])
        }
        trusted_lineage = bool(
            record.get("usable") and input_refs
            and not record.get("unresolved_input_refs")
            and len(resolved) == len(input_refs)
        )
        if trusted_lineage:
            record["trusted_input_values"] = [
                item for item in (record.get("candidate_input_values") or [])
                if item.get("value") in exact_dependency_values
            ]
            trusted_values = {
                item["value"] for item in record["trusted_input_values"]
            }
            record["trusted_input_cny_values"] = sorted(
                value for value in (record.get("candidate_input_cny_values") or [])
                if value in trusted_values
            )

    calculation_lineage = [
        {
            "calculation_id": record["calculation_id"],
            "status": record["status"],
            "usable": record["usable"],
            "payload_key": record["payload_key"],
            "unusable_reason": record["unusable_reason"],
            "scope": record["scope"],
            "input_refs": list(record["input_refs"]),
            "unresolved_input_refs": list(record["unresolved_input_refs"]),
        }
        for record in calculation_records
    ]
    return {
        "hub": hub,
        "refs": refs,
        "sources": len(source_register),
        "calculations": len(calculation_items),
        "calculation_lineage": calculation_lineage,
    }


def _reference_is_usable(record: Mapping[str, Any]) -> bool:
    if record.get("usable") is not True:
        return False
    if record.get("kind") == "calculation" \
            and record.get("unresolved_input_refs"):
        return False
    return True


def _citation_rows(value: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for match in _CITATION_RE.finditer(value):
        kind = (match.group(1) or "evidence").lower()
        ref = match.group(2)
        # Do not treat ordinary Markdown labels as citations.  Unprefixed refs
        # are accepted only for stable Evidence Hub ids.
        if match.group(1) is None and not ref.upper().startswith("CE-"):
            continue
        if kind in {"calc", "计算"}:
            kind = "calculation"
        elif kind == "证据":
            kind = "evidence"
        elif kind == "来源":
            kind = "source"
        out.append({"kind": kind, "ref": ref, "raw": match.group(0)})
    return out


def _is_issuer_claim(value: str) -> bool:
    claim_text = _CITATION_RE.sub("", value)
    if not _ISSUER_CLAIM_RE.search(claim_text):
        return False
    # "行业收入" is context; "行业背景显示公司收入" is still an issuer
    # claim and therefore requires issuer evidence.
    if _CONTEXT_LABEL_RE.search(claim_text) \
            and not _EXPLICIT_ISSUER_SUBJECT_RE.search(claim_text):
        return False
    return True


def _citation_kind_matches(citation: Mapping[str, str], record: Mapping[str, Any]) -> bool:
    expected = "calculation" if citation.get("kind") == "calculation" else "evidence"
    return record.get("kind") == expected


def _assumption_register(report_meta: Mapping[str, Any], assumptions: Any) -> dict[str, dict[str, Any]]:
    raw = assumptions if assumptions is not None else report_meta.get("assumptions")
    if isinstance(raw, Mapping):
        raw = [raw]
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return out
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            continue
        assumption_id = _text(
            row.get("assumption_id") or row.get("id") or f"assumption-{index + 1}", 120,
        )
        out[assumption_id.lower()] = {
            "assumption_id": assumption_id,
            "section_id": _text(row.get("section_id"), 80) or None,
            "numbers": sorted(_numbers_in_value(row.get("value", row.get("values", row)))),
        }
    return out


def _line_citations(section: _Section) -> dict[int, list[dict[str, str]]]:
    direct: dict[int, list[dict[str, str]]] = {}
    absolute_lines = list(range(section.start_line, section.end_line + 1))
    for line_no, line in zip(absolute_lines, section.body_lines):
        direct[line_no] = _citation_rows(line)

    # A dedicated source line immediately before a claim, or immediately after
    # a contiguous Markdown table, owns that bounded block.  No broader
    # paragraph/section-wide citation inference is made in strict QC.
    resolved = {line_no: list(rows) for line_no, rows in direct.items()}
    for offset, (line_no, line) in enumerate(zip(absolute_lines, section.body_lines)):
        if resolved[line_no]:
            continue
        previous = section.body_lines[offset - 1] if offset > 0 else ""
        previous_no = absolute_lines[offset - 1] if offset > 0 else None
        if previous_no and re.match(r"^\s*(?:来源|证据|依据)\s*[:：]", previous):
            resolved[line_no] = list(direct.get(previous_no, []))
            continue
        if line.lstrip().startswith("|"):
            cursor = offset + 1
            while cursor < len(section.body_lines) and section.body_lines[cursor].lstrip().startswith("|"):
                cursor += 1
            if cursor < len(section.body_lines) and re.match(
                r"^\s*(?:来源|证据|依据)\s*[:：]", section.body_lines[cursor]
            ):
                resolved[line_no] = _citation_rows(section.body_lines[cursor])
    return resolved


def _check_row(
    check_id: str, status: str, *, summary: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "check_id": check_id,
        "status": status,
        "summary": summary,
    }
    if details:
        row["details"] = _json_safe(dict(details))
    return row


def _result_status(upstream_status: str, violations: Sequence[Mapping[str, Any]]) -> str:
    if upstream_status == "failed":
        return "failed"
    if violations or upstream_status in {"unknown", "incomplete"}:
        return "incomplete"
    if upstream_status == "stale":
        return "stale"
    return "complete"


def _risk_contract_candidate(
    report_meta: Mapping[str, Any], upstream: Any, evidence: Any,
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Find the structured final risk contract without interpreting prose."""

    def direct(value: Any, label: str) -> tuple[Mapping[str, Any] | None, str | None]:
        if not isinstance(value, Mapping):
            return None, None
        if str(value.get("schema_version") or "").startswith("company-risk-contract@"):
            return value, label
        for key in ("final_risk_contract", "risk_contract", "company_risk_contract"):
            candidate = value.get(key)
            if isinstance(candidate, Mapping):
                return candidate, f"{label}.{key}"
        contracts = value.get("contracts")
        if isinstance(contracts, Mapping) and isinstance(contracts.get("risk"), Mapping):
            return contracts["risk"], f"{label}.contracts.risk"
        sections = value.get("sections")
        if isinstance(sections, Mapping) \
                and isinstance(sections.get("company_risk_contract"), Mapping):
            return sections["company_risk_contract"], f"{label}.sections.company_risk_contract"
        pack = value.get("evidence_pack")
        if isinstance(pack, Mapping):
            pack_sections = pack.get("sections")
            if isinstance(pack_sections, Mapping) \
                    and isinstance(pack_sections.get("company_risk_contract"), Mapping):
                return (
                    pack_sections["company_risk_contract"],
                    f"{label}.evidence_pack.sections.company_risk_contract",
                )
        return None, None

    for value, label in (
        (report_meta, "report"), (upstream, "upstream"), (evidence, "evidence"),
    ):
        candidate, location = direct(value, label)
        if candidate is not None:
            return candidate, location
    return None, None


def _section_marker_ids(section: _Section | None, kind: str) -> set[str]:
    if section is None:
        return set()
    return {
        match.group(2).lower()
        for line in section.body_lines for match in _RISK_MARKER_RE.finditer(line)
        if match.group(1).lower() == kind
    }


def _complete_counter_group(row: Mapping[str, Any]) -> bool:
    claim = _text(row.get("claim"), 1000)
    issuer_refs = [item for item in row.get("issuer_claim_source_refs") or [] if _text(item)]
    counters = row.get("counter_evidence")
    if not claim or not issuer_refs or str(row.get("status") or "").lower() != "complete" \
            or not isinstance(counters, list):
        return False
    return any(
        isinstance(counter, Mapping)
        and _text(counter.get("statement"), 1000)
        and str(counter.get("relative_direction") or "").lower() == "disconfirming"
        and bool([
            item for item in counter.get("issuer_source_refs") or [] if _text(item)
        ])
        and str(counter.get("freshness") or "").lower() in {"fresh", "stale"}
        for counter in counters
    )


def _complete_decision_point(row: Mapping[str, Any]) -> bool:
    metric_or_event = row.get("metric") is not None or row.get("event") is not None
    threshold_or_resolution = (
        row.get("threshold") is not None or bool(_text(row.get("resolution_condition")))
    )
    deadline = _text(row.get("deadline"), 40)
    return bool(
        _text(row.get("point_id"), 180)
        and _text(row.get("question"), 1000)
        and metric_or_event
        and threshold_or_resolution
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline)
        and str(row.get("status") or "").lower() in _DECISION_POINT_STATUSES
        and [item for item in row.get("issuer_source_refs") or [] if _text(item)]
        and _text(row.get("impact"), 1000)
        and str(row.get("freshness") or "").lower() in {"fresh", "stale"}
        and row.get("eligible") is True
        and not (row.get("ineligibility_reasons") or [])
    )


def _audit_risk_contract(
    *, report_meta: Mapping[str, Any], upstream: Any, evidence: Any,
    grouped: Mapping[str, list[_Section]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract, location = _risk_contract_candidate(report_meta, upstream, evidence)
    findings: list[dict[str, Any]] = []
    if contract is None:
        findings.append(_finding(
            check_id="risk_contract", code="final_risk_contract_missing",
            message=("No final structured risk contract is available; narrative risk text "
                     "cannot prove counter-evidence or decision-point closure."),
            severity="critical",
        ))
        return {
            "location": None, "contract_id": None, "status": "missing",
            "strong_claims": [], "eligible_decision_points": [],
            "claim_markers": [], "decision_markers": [],
        }, findings

    schema = str(contract.get("schema_version") or "")
    status = str(contract.get("status") or "").lower()
    if not schema.startswith("company-risk-contract@"):
        findings.append(_finding(
            check_id="risk_contract", code="final_risk_contract_schema_invalid",
            message="The structured risk contract schema is absent or unsupported.",
            severity="critical", details={"schema_version": schema or None},
        ))
    if status not in {"complete", "stale"}:
        findings.append(_finding(
            check_id="risk_contract", code="final_risk_contract_not_closed",
            message=f"Final structured risk contract status {status or 'missing'!r} is not closed.",
            severity="critical", details={"status": status or None},
        ))

    raw_groups = contract.get("counter_evidence_groups")
    groups = [item for item in raw_groups or [] if isinstance(item, Mapping)] \
        if isinstance(raw_groups, list) else []
    claim_ids: list[str] = []
    for row in groups:
        claim_id = _text(row.get("claim_id"), 180).lower()
        if claim_id:
            claim_ids.append(claim_id)
        if not claim_id or not _complete_counter_group(row):
            findings.append(_finding(
                check_id="risk_contract", code="strong_claim_counter_group_incomplete",
                message=("A strong claim lacks a non-empty issuer-backed claim or an "
                         "explicitly disconfirming issuer counter-evidence row."),
                severity="critical", section_id="risk_catalysts",
                details={"claim_id": claim_id or None},
            ))
    if not groups:
        findings.append(_finding(
            check_id="risk_contract", code="strong_claim_inventory_missing",
            message="The final risk contract contains no structured strong-claim inventory.",
            severity="critical", section_id="risk_catalysts",
        ))

    risk_section = grouped.get("risk_catalysts", [None])[0]
    claim_markers = _section_marker_ids(risk_section, "claim")
    known_claims = set(claim_ids)
    for claim_id in sorted(known_claims - claim_markers):
        findings.append(_finding(
            check_id="risk_contract", code="strong_claim_not_covered",
            message=f"Risk/counter-evidence section does not cover strong claim {claim_id!r}.",
            severity="high", section_id="risk_catalysts",
            details={"claim_id": claim_id},
        ))
    for claim_id in sorted(claim_markers - known_claims):
        findings.append(_finding(
            check_id="risk_contract", code="strong_claim_reference_unknown",
            message=f"Report references unknown strong-claim id {claim_id!r}.",
            severity="high", section_id="risk_catalysts",
            details={"claim_id": claim_id},
        ))

    raw_points = contract.get("decision_points")
    points = [item for item in raw_points or [] if isinstance(item, Mapping)] \
        if isinstance(raw_points, list) else []
    eligible_ids = {
        _text(row.get("point_id"), 180).lower()
        for row in points if _complete_decision_point(row)
    }
    eligible_ids.discard("")
    if len(eligible_ids) < 3:
        findings.append(_finding(
            check_id="risk_contract", code="eligible_decision_points_insufficient",
            message=(f"The final risk contract has {len(eligible_ids)} actual eligible "
                     "issuer decision point(s); at least three are required."),
            severity="critical", section_id="scenarios_verdict",
            details={"required": 3, "eligible": len(eligible_ids)},
        ))
    decision_section = grouped.get("scenarios_verdict", [None])[0]
    decision_markers = _section_marker_ids(decision_section, "decision")
    for point_id in sorted(decision_markers - eligible_ids):
        findings.append(_finding(
            check_id="risk_contract", code="decision_point_reference_unknown",
            message=f"Report references an unknown or ineligible decision point {point_id!r}.",
            severity="high", section_id="scenarios_verdict",
            details={"point_id": point_id},
        ))
    covered_points = decision_markers & eligible_ids
    if len(covered_points) < 3:
        findings.append(_finding(
            check_id="risk_contract", code="decision_points_not_covered",
            message=(f"Scenarios/verdict section covers {len(covered_points)} eligible "
                     "decision point(s); at least three exact ids are required."),
            severity="high", section_id="scenarios_verdict",
            details={"required": 3, "covered": len(covered_points)},
        ))

    critical_gaps = [
        row for row in contract.get("gaps") or []
        if isinstance(row, Mapping) and str(row.get("severity") or "").lower() == "critical"
    ]
    if critical_gaps:
        findings.append(_finding(
            check_id="risk_contract", code="final_risk_contract_has_critical_gaps",
            message="Final structured risk contract still contains critical gaps.",
            severity="critical", details={"critical_gaps": len(critical_gaps)},
        ))

    return {
        "location": location,
        "contract_id": _text(contract.get("contract_id"), 180) or None,
        "status": status or "missing",
        "strong_claims": sorted(known_claims),
        "eligible_decision_points": sorted(eligible_ids),
        "claim_markers": sorted(claim_markers),
        "decision_markers": sorted(decision_markers),
    }, findings


def validate_company_research_report(
    report: Any,
    *,
    evidence: Any = None,
    calculations: Any = None,
    upstream: Any = None,
    assumptions: Any = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Return a deterministic audit result for a final company report.

    ``report`` may be Markdown or a mapping containing ``markdown_body`` (and
    optionally an ``assumptions`` register).  ``evidence`` may be a bare Hub
    ledger, the workspace evidence document, the complete workspace artifact
    mapping, or an engine evidence pack.  ``upstream`` may be a status string or
    any manifest/pipeline mapping.  Unknown input shapes fail closed as
    structured findings; ordinary data absence does not raise.
    """
    markdown, report_meta = _report_body(report)
    inventory = _source_inventory(evidence, calculations)
    upstream_state = _upstream_state(upstream, evidence)
    upstream_status = str(upstream_state["status"])
    gaps = _collect_gaps(upstream, evidence)
    assumption_rows = _assumption_register(report_meta, assumptions)

    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    if not markdown.strip():
        violations.append(_finding(
            check_id="report_body", code="report_body_missing",
            message="No final report Markdown was supplied.", severity="critical",
        ))
        checks.append(_check_row(
            "report_body", "failed", summary="Final report body is missing.",
            details={"characters": 0},
        ))
    else:
        checks.append(_check_row(
            "report_body", "passed", summary="Final report body is present.",
            details={"characters": len(markdown), "sha256": _digest(markdown)},
        ))

    if upstream_status in {"unknown", "incomplete", "failed"}:
        violations.append(_finding(
            check_id="upstream", code=(
                "upstream_status_unknown" if upstream_status == "unknown"
                else "upstream_not_complete"
            ),
            message=(
                "Upstream company-research status is unknown; completion cannot be inferred."
                if upstream_status == "unknown"
                else f"Upstream company-research status is {upstream_status!r}."
            ),
            severity="critical",
            details={"upstream_status": upstream_status},
        ))
        upstream_check = "failed"
    elif upstream_status == "stale":
        warnings.append(_finding(
            check_id="upstream", code="upstream_stale",
            message="Upstream evidence is complete but stale; final status remains stale.",
            severity="high", details={"upstream_status": upstream_status},
        ))
        upstream_check = "warning"
    else:
        upstream_check = "passed"
    checks.append(_check_row(
        "upstream", upstream_check,
        summary=f"Worst upstream status: {upstream_status}.",
        details=upstream_state,
    ))

    if not markdown.strip():
        for check_id in (
            "sections", "risk_contract", "citations", "numeric_fidelity", "issuer_context_boundary",
            "gap_disclosure", "status_disclosure", "compliance",
        ):
            checks.append(_check_row(
                check_id, "not_run", summary="Report body is unavailable.",
            ))
        result_status = _result_status(upstream_status, violations)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": result_status,
            "completion_status": "failed" if result_status == "failed" else "incomplete",
            "freshness_status": "stale" if upstream_status == "stale" else "unknown",
            "strict": bool(strict),
            "checks": checks,
            "violations": violations,
            "warnings": warnings,
            "audit": {
                "report_sha256": None,
                "upstream": upstream_state,
                "sections": [], "citations": [], "numeric_claims": [],
                "risk_contract": None,
                "gaps": gaps, "assumptions": list(assumption_rows.values()),
            },
        }

    sections, section_parse_findings = _parse_sections(markdown, strict=bool(strict))
    violations.extend(section_parse_findings)
    grouped: dict[str, list[_Section]] = defaultdict(list)
    for section in sections:
        grouped[section.section_id].append(section)
    for section_id in SECTION_ORDER:
        rows = grouped.get(section_id, [])
        if not rows:
            violations.append(_finding(
                check_id="sections", code="required_section_missing",
                message=f"Required section {section_id!r} is missing.", severity="high",
                section_id=section_id,
            ))
            continue
        if len(rows) > 1:
            violations.append(_finding(
                check_id="sections", code="duplicate_required_section",
                message=f"Required section {section_id!r} appears {len(rows)} times.",
                severity="high", section_id=section_id,
                details={"heading_lines": [row.heading_line for row in rows]},
            ))
        if strict and rows[0].marker != section_id:
            violations.append(_finding(
                check_id="sections", code="canonical_section_marker_missing",
                message=(f"Strict archival mode requires marker "
                         f"[section:{section_id}] on this heading."),
                severity="high", section_id=section_id, line=rows[0].heading_line,
            ))
    observed_order = [section.section_id for section in sections]
    first_order = list(dict.fromkeys(observed_order))
    if observed_order != list(SECTION_ORDER):
        violations.append(_finding(
            check_id="sections", code="section_order_invalid",
            message="The seven company-report sections are not in canonical order.",
            severity="high",
            details={"expected": list(SECTION_ORDER), "observed": first_order},
        ))
    section_violation_count = sum(
        1 for item in violations if item["check_id"] == "sections"
    )
    checks.append(_check_row(
        "sections", "failed" if section_violation_count else "passed",
        summary=(
            "Seven canonical sections passed."
            if not section_violation_count else
            f"Section contract has {section_violation_count} blocking finding(s)."
        ),
        details={
            "expected_order": list(SECTION_ORDER),
            "observed_order": observed_order,
            "strict_markers": bool(strict),
        },
    ))

    risk_audit, risk_findings = _audit_risk_contract(
        report_meta=report_meta, upstream=upstream, evidence=evidence,
        grouped=grouped,
    )
    violations.extend(risk_findings)
    checks.append(_check_row(
        "risk_contract", "failed" if risk_findings else "passed",
        summary=(
            "Structured risk contract and report coverage passed."
            if not risk_findings else
            f"Structured risk closure has {len(risk_findings)} blocking finding(s)."
        ),
        details=risk_audit,
    ))

    refs = inventory["refs"]
    citation_audit: list[dict[str, Any]] = []
    section_resolved_refs: dict[str, set[str]] = defaultdict(set)
    line_refs_by_section: dict[str, dict[int, list[dict[str, str]]]] = {}
    all_citations = []
    for section in sections:
        line_refs = _line_citations(section)
        line_refs_by_section[section.section_id] = line_refs
        for line_no, line in zip(
            range(section.start_line, section.end_line + 1), section.body_lines,
        ):
            for citation in _citation_rows(line):
                normal_ref = citation["ref"].lower()
                resolved = refs.get(normal_ref)
                kind_matches = bool(resolved and _citation_kind_matches(citation, resolved))
                usable = bool(resolved and _reference_is_usable(resolved))
                row = {
                    "section_id": section.section_id,
                    "line": line_no,
                    "kind": citation["kind"],
                    "ref": citation["ref"],
                    "resolved": bool(resolved),
                    "kind_matches": kind_matches,
                    "usable": usable,
                    "scope": resolved.get("scope") if resolved else None,
                    "calculation_status": (
                        resolved.get("status")
                        if resolved and resolved.get("kind") == "calculation" else None
                    ),
                }
                citation_audit.append(row)
                all_citations.append(row)
                if resolved:
                    if kind_matches and usable:
                        section_resolved_refs[section.section_id].add(normal_ref)
                    elif not kind_matches:
                        violations.append(_finding(
                            check_id="citations", code="citation_kind_mismatch",
                            message=(f"Citation {citation['ref']!r} is tagged as "
                                     f"{citation['kind']!r}, but resolves to "
                                     f"{resolved.get('kind')!r}."),
                            severity="high", section_id=section.section_id, line=line_no,
                            details={
                                "ref": citation["ref"],
                                "declared_kind": citation["kind"],
                                "resolved_kind": resolved.get("kind"),
                            },
                        ))
                    elif resolved.get("kind") == "calculation":
                        violations.append(_finding(
                            check_id="citations", code="calculation_output_unusable",
                            message=(f"Calculation {citation['ref']!r} cannot close a report "
                                     "claim because its status/result output is unusable."),
                            severity="critical", section_id=section.section_id, line=line_no,
                            details={
                                "ref": citation["ref"],
                                "status": resolved.get("status"),
                                "reason": resolved.get("unusable_reason"),
                                "unresolved_input_refs": resolved.get("unresolved_input_refs") or [],
                            },
                        ))
                    else:
                        violations.append(_finding(
                            check_id="citations", code="evidence_source_unusable",
                            message=(f"Evidence source {citation['ref']!r} cannot close a report "
                                     "claim because its source status/usability is not eligible."),
                            severity="critical", section_id=section.section_id, line=line_no,
                            details={
                                "ref": citation["ref"],
                                "status": resolved.get("status"),
                                "reason": resolved.get("unusable_reason"),
                            },
                        ))
                else:
                    violations.append(_finding(
                        check_id="citations", code="citation_unresolved",
                        message=f"Citation {citation['ref']!r} is not in the evidence/calculation register.",
                        severity="high", section_id=section.section_id, line=line_no,
                        details={"ref": citation["ref"], "kind": citation["kind"]},
                    ))

    if not refs:
        violations.append(_finding(
            check_id="citations", code="source_register_missing",
            message="No Evidence Hub source register or calculation register is available.",
            severity="critical",
        ))
    for section_id in SECTION_ORDER:
        if section_id not in grouped:
            continue
        resolved = section_resolved_refs.get(section_id, set())
        gap_refs = _GAP_REF_RE.findall("\n".join(grouped[section_id][0].body_lines))
        if not resolved and not (section_id == "sources_gaps" and gap_refs):
            violations.append(_finding(
                check_id="citations", code="section_citation_missing",
                message=f"Section {section_id!r} has no resolved evidence/calculation citation.",
                severity="high", section_id=section_id,
            ))
    citation_violation_count = sum(
        1 for item in violations if item["check_id"] == "citations"
    )
    checks.append(_check_row(
        "citations", "failed" if citation_violation_count else "passed",
        summary=(
            f"Resolved {sum(1 for row in citation_audit if row['resolved'])} citation(s)."
            if not citation_violation_count else
            f"Citation contract has {citation_violation_count} blocking finding(s)."
        ),
        details={
            "known_references": len(refs),
            "citations": len(citation_audit),
            "resolved": sum(1 for row in citation_audit if row["resolved"]),
            "usable": sum(
                1 for row in citation_audit
                if row["resolved"] and row["kind_matches"] and row["usable"]
            ),
        },
    ))

    numeric_audit: list[dict[str, Any]] = []
    numeric_seen: set[tuple[str, int, str]] = set()
    for section in sections:
        line_refs = line_refs_by_section.get(section.section_id, {})
        for line_no, line in zip(
            range(section.start_line, section.end_line + 1), section.body_lines,
        ):
            numbers = _numbers_in_text(line)
            if not numbers:
                continue
            citations = line_refs.get(line_no, [])
            matched_refs = [
                citation["ref"].lower() for citation in citations
                if citation["ref"].lower() in refs
                and _citation_kind_matches(citation, refs[citation["ref"].lower()])
            ]
            resolved_refs = [
                ref for ref in matched_refs if _reference_is_usable(refs[ref])
            ]
            issuer_claim = _is_issuer_claim(line)
            assumption_matches = list(_ASSUMPTION_REF_RE.finditer(line))
            explicit_assumption = bool(assumption_matches or _EXPLICIT_ASSUMPTION_RE.search(line))
            assumption_ids = [
                match.group(1).lower() for match in assumption_matches if match.group(1)
            ]
            for number in numbers:
                identity = (section.section_id, line_no, number["raw"])
                if identity in numeric_seen:
                    continue
                numeric_seen.add(identity)
                supported = False
                support_kind = None
                supporting_refs: list[str] = []
                if explicit_assumption:
                    if assumption_ids:
                        registered = [
                            assumption_rows[item] for item in assumption_ids
                            if item in assumption_rows
                        ]
                        if registered:
                            supported = any(
                                set(number["variants"]) & set(item["numbers"])
                                and (not item.get("section_id") or item["section_id"] == section.section_id)
                                for item in registered
                            )
                            if not supported:
                                violations.append(_finding(
                                    check_id="numeric_fidelity",
                                    code="assumption_value_mismatch",
                                    message=(f"Assumption-labelled number {number['raw']!r} does not "
                                             "match its registered assumption value/section."),
                                    severity="high", section_id=section.section_id, line=line_no,
                                    details={"assumption_ids": assumption_ids, "number": number["raw"]},
                                ))
                        else:
                            supported = True
                            warnings.append(_finding(
                                check_id="numeric_fidelity", code="assumption_not_registered",
                                message="Assumption is explicit but has no structured register entry.",
                                severity="medium", section_id=section.section_id, line=line_no,
                                details={"assumption_ids": assumption_ids},
                            ))
                    else:
                        supported = True
                        warnings.append(_finding(
                            check_id="numeric_fidelity", code="assumption_not_registered",
                            message="Assumption is explicit but has no stable assumption id.",
                            severity="medium", section_id=section.section_id, line=line_no,
                        ))
                    support_kind = "assumption"
                else:
                    for ref in resolved_refs:
                        scope_allowed = (
                            not issuer_claim
                            or refs[ref].get("scope") in {"issuer", "issuer_derived"}
                        )
                        if scope_allowed and _reference_supports_number(number, refs[ref]):
                            supported = True
                            supporting_refs.append(ref)
                    if supported:
                        support_kind = "evidence_or_calculation"
                    elif section.section_id == "sources_gaps":
                        supporting_refs = _industry_gap_count_support(line, number, gaps)
                        if supporting_refs:
                            supported = True
                            support_kind = "gap_disclosure"
                    if not supported and not matched_refs:
                        violations.append(_finding(
                            check_id="numeric_fidelity", code="numeric_claim_uncited",
                            message=f"Numeric claim {number['raw']!r} has no resolved citation.",
                            severity="high", section_id=section.section_id, line=line_no,
                            details={"number": number["raw"]},
                        ))
                    elif not supported:
                        violations.append(_finding(
                            check_id="numeric_fidelity", code="unsupported_numeric_claim",
                            message=(f"Numeric claim {number['raw']!r} is absent from the "
                                     "cited evidence/calculation payload."),
                            severity="critical", section_id=section.section_id, line=line_no,
                            details={"number": number["raw"], "cited_refs": matched_refs},
                        ))
                numeric_audit.append({
                    "section_id": section.section_id,
                    "line": line_no,
                    "number": number["raw"],
                    "supported": supported,
                    "support_kind": support_kind,
                    "supporting_refs": supporting_refs,
                    "assumption_ids": assumption_ids,
                    "issuer_claim": issuer_claim,
                })
    numeric_violation_count = sum(
        1 for item in violations if item["check_id"] == "numeric_fidelity"
    )
    numeric_warning_count = sum(
        1 for item in warnings if item["check_id"] == "numeric_fidelity"
    )
    checks.append(_check_row(
        "numeric_fidelity",
        "failed" if numeric_violation_count else "warning" if numeric_warning_count else "passed",
        summary=(
            f"Checked {len(numeric_audit)} numeric claim(s); "
            f"{numeric_violation_count} unsupported."
        ),
        details={
            "claims": len(numeric_audit),
            "supported": sum(1 for row in numeric_audit if row["supported"]),
            "assumptions": sum(1 for row in numeric_audit if row["support_kind"] == "assumption"),
        },
    ))

    for section_id in _ISSUER_REQUIRED_SECTIONS:
        if section_id not in grouped:
            continue
        scopes = {
            refs[ref]["scope"] for ref in section_resolved_refs.get(section_id, set())
            if ref in refs
        }
        if not scopes.intersection({"issuer", "issuer_derived"}):
            violations.append(_finding(
                check_id="issuer_context_boundary", code="issuer_evidence_missing",
                message=f"Section {section_id!r} has no issuer or issuer-derived evidence.",
                severity="critical", section_id=section_id,
                details={"observed_scopes": sorted(scopes)},
            ))
    for section in sections:
        line_refs = line_refs_by_section.get(section.section_id, {})
        for line_no, line in zip(
            range(section.start_line, section.end_line + 1), section.body_lines,
        ):
            if not _is_issuer_claim(line):
                continue
            resolved = [
                refs[item["ref"].lower()] for item in line_refs.get(line_no, [])
                if item["ref"].lower() in refs
                and _citation_kind_matches(item, refs[item["ref"].lower()])
                and _reference_is_usable(refs[item["ref"].lower()])
            ]
            if resolved and all(item.get("scope") == "context" for item in resolved):
                violations.append(_finding(
                    check_id="issuer_context_boundary",
                    code="context_used_as_issuer_evidence",
                    message="An issuer-specific claim is supported only by context evidence.",
                    severity="critical", section_id=section.section_id, line=line_no,
                    details={
                        "refs": [item.get("source_id") or item.get("evidence_id")
                                 or item.get("calculation_id") for item in resolved],
                    },
                ))
    boundary_violation_count = sum(
        1 for item in violations if item["check_id"] == "issuer_context_boundary"
    )
    checks.append(_check_row(
        "issuer_context_boundary",
        "failed" if boundary_violation_count else "passed",
        summary=(
            "Issuer/context evidence boundary passed."
            if not boundary_violation_count else
            f"Issuer/context boundary has {boundary_violation_count} blocking finding(s)."
        ),
        details={
            "issuer_references": sum(1 for row in refs.values() if row.get("scope") == "issuer"),
            "context_references": sum(1 for row in refs.values() if row.get("scope") == "context"),
        },
    ))

    sources_gap_text = (
        "\n".join(grouped["sources_gaps"][0].body_lines)
        if grouped.get("sources_gaps") else ""
    )
    gap_ref_tokens = {item.lower() for item in _GAP_REF_RE.findall(sources_gap_text)}
    directly_disclosed_gaps = [
        gap for gap in gaps
        if gap_ref_tokens.intersection(gap.get("aliases") or [gap["key"].lower()])
    ]
    for gap in gaps:
        key = gap["key"].lower()
        alternative = str(gap.get("source_id") or "").lower()
        disclosed = (
            bool(gap_ref_tokens.intersection(gap.get("aliases") or [key]))
            or key in sources_gap_text.lower()
            or bool(alternative and alternative in sources_gap_text.lower())
            # Hub IDs and stage-level codes describe the same limitation.
            # Require the same source, message and a common alias; a generic
            # code cannot hide another source or a changed limitation.
            or any(
                alternative
                and alternative == str(other.get("source_id") or "").lower()
                and gap.get("message")
                and gap.get("message") == other.get("message")
                and set(gap.get("aliases") or [key]).intersection(other.get("aliases") or [])
                for other in directly_disclosed_gaps
            )
        )
        if disclosed:
            continue
        target = violations if gap["critical"] else warnings
        target.append(_finding(
            check_id="gap_disclosure",
            code=("required_gap_undisclosed" if gap["critical"] else "optional_gap_undisclosed"),
            message=f"Upstream gap {gap['key']!r} is not disclosed in sources_gaps.",
            severity="high" if gap["critical"] else "medium",
            section_id="sources_gaps",
            details={"gap": gap},
        ))
    if upstream_status in {"incomplete", "failed", "unknown"} and not gaps:
        if not re.search(r"缺口|未接通|不完整|失败|unknown|incomplete|failed", sources_gap_text, re.IGNORECASE):
            violations.append(_finding(
                check_id="gap_disclosure", code="upstream_limitation_undisclosed",
                message="Non-complete upstream status is not disclosed in sources_gaps.",
                severity="high", section_id="sources_gaps",
            ))
    if upstream_status in {"incomplete", "failed", "unknown"} \
            and _FALSE_COMPLETENESS_RE.search(markdown):
        violations.append(_finding(
            check_id="gap_disclosure", code="false_completeness_claim",
            message="Report claims completeness while upstream status is not complete.",
            severity="critical",
            details={"upstream_status": upstream_status},
        ))
    gap_violation_count = sum(
        1 for item in violations if item["check_id"] == "gap_disclosure"
    )
    gap_warning_count = sum(
        1 for item in warnings if item["check_id"] == "gap_disclosure"
    )
    checks.append(_check_row(
        "gap_disclosure",
        "failed" if gap_violation_count else "warning" if gap_warning_count else "passed",
        summary=(
            f"Checked {len(gaps)} upstream gap(s); {gap_violation_count} required gap(s) undisclosed."
        ),
        details={
            "required_gaps": sum(1 for gap in gaps if gap["critical"]),
            "optional_gaps": sum(1 for gap in gaps if not gap["critical"]),
            "gap_references": sorted(gap_ref_tokens),
        },
    ))

    reported_statuses = [
        _normalise_status(next(part for part in match.groups() if part))
        for match in _STATUS_RE.finditer(markdown)
    ]
    reported_statuses = [item for item in reported_statuses if item]
    unique_reported_statuses = list(dict.fromkeys(reported_statuses))
    if not unique_reported_statuses:
        violations.append(_finding(
            check_id="status_disclosure", code="research_status_missing",
            message="Report has no canonical company-research status disclosure.",
            severity="high",
        ))
    elif len(unique_reported_statuses) > 1:
        violations.append(_finding(
            check_id="status_disclosure", code="research_status_conflict",
            message="Report contains conflicting company-research statuses.",
            severity="high", details={"statuses": unique_reported_statuses},
        ))
    else:
        declared = unique_reported_statuses[0]
        if upstream_status != "unknown" and _STATUS_RANK[declared] < _STATUS_RANK[upstream_status]:
            violations.append(_finding(
                check_id="status_disclosure", code="research_status_overstated",
                message=(f"Report declares {declared!r}, which is more optimistic than "
                         f"upstream {upstream_status!r}."),
                severity="critical",
                details={"declared": declared, "upstream": upstream_status},
            ))
        if declared in {"incomplete", "failed"}:
            violations.append(_finding(
                check_id="status_disclosure", code="report_self_declared_not_complete",
                message=f"Report self-declares status {declared!r}; it cannot pass as complete.",
                severity="high", details={"declared": declared},
            ))
        elif declared == "stale" and upstream_status == "complete":
            warnings.append(_finding(
                check_id="status_disclosure", code="report_self_declared_stale",
                message="Report is more conservative than upstream and declares stale evidence.",
                severity="medium",
            ))
    status_violation_count = sum(
        1 for item in violations if item["check_id"] == "status_disclosure"
    )
    checks.append(_check_row(
        "status_disclosure", "failed" if status_violation_count else "passed",
        summary=(
            "Research status disclosure is consistent."
            if not status_violation_count else
            f"Status disclosure has {status_violation_count} blocking finding(s)."
        ),
        details={"reported_statuses": unique_reported_statuses, "upstream_status": upstream_status},
    ))

    research_only = bool(re.search(
        r"仅供(?:研究|参考)|研究用途|研究\s*[/／]\s*参考用途",
        markdown,
    ))
    no_advice = bool(re.search(
        r"(?:不构成|并非).{0,18}(?:投资|交易).{0,8}(?:建议|意见)", markdown,
        re.IGNORECASE,
    ))
    non_personalised = bool(re.search(
        r"非个性化(?:投资)?建议|不针对任何(?:个人|特定投资者)|"
        r"未考虑.{0,60}(?:个人|读者|您的|你的).{0,40}"
        r"(?:财务状况|风险承受|投资目标)",
        markdown, re.IGNORECASE,
    ))
    prohibited_matches: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(markdown.splitlines(), start=1):
        line = _CITATION_RE.sub("", raw_line)
        line = _GAP_REF_RE.sub("", line)
        line = _ASSUMPTION_REF_RE.sub("", line)
        line = _RISK_MARKER_RE.sub("", line)
        negated_direction_disclosure = bool(
            _NEGATED_DIRECTION_DISCLOSURE_RE.search(line)
        )
        for category, pattern in _PROHIBITED_REPORT_PATTERNS:
            if negated_direction_disclosure and category in {
                "position_direction", "trading_direction_zh",
                "trading_direction_en",
            }:
                continue
            for match in pattern.finditer(line):
                prohibited_matches.append({
                    "category": category,
                    "text": _text(match.group(0), 120),
                    "line": line_no,
                })
    prohibited_matches = list({
        (row["category"], row["text"].lower(), row["line"]): row
        for row in prohibited_matches
    }.values())
    prohibited_matches.sort(key=lambda row: (
        int(row["line"]), str(row["category"]), str(row["text"]).lower(),
    ))
    if prohibited_matches:
        violations.append(_finding(
            check_id="compliance", code="prohibited_trading_or_price_anchor",
            message=("Final company research must not contain target/implied prices, "
                     "price anchors, stop-losses, or buy/sell/position directions."),
            severity="critical", details={"matches": prohibited_matches[:24]},
        ))
    for code, passed, message in (
        ("research_only_disclaimer_missing", research_only,
         "Research/reference-only disclaimer is missing."),
        ("investment_advice_disclaimer_missing", no_advice,
         "The report does not state that it is not investment advice."),
        ("non_personalised_disclaimer_missing", non_personalised,
         "The report does not state that it is non-personalised."),
    ):
        if not passed:
            violations.append(_finding(
                check_id="compliance", code=code, message=message, severity="high",
            ))
    personalised_matches = sorted(set(match.group(0) for match in _PERSONALISED_RE.finditer(markdown)))
    if personalised_matches:
        violations.append(_finding(
            check_id="compliance", code="personalised_investment_direction",
            message="Report contains reader-specific investment direction.",
            severity="critical", details={"matches": personalised_matches[:12]},
        ))
    compliance_violation_count = sum(
        1 for item in violations if item["check_id"] == "compliance"
    )
    checks.append(_check_row(
        "compliance", "failed" if compliance_violation_count else "passed",
        summary=(
            "Research-only, non-advice, and non-personalised compliance passed."
            if not compliance_violation_count else
            f"Compliance has {compliance_violation_count} blocking finding(s)."
        ),
        details={
            "research_only": research_only,
            "not_investment_advice": no_advice,
            "non_personalised": non_personalised,
            "personalised_matches": personalised_matches,
            "prohibited_matches": prohibited_matches,
        },
    ))

    check_order = {
        key: index for index, key in enumerate((
            "report_body", "upstream", "sections", "risk_contract", "citations",
            "numeric_fidelity", "issuer_context_boundary", "gap_disclosure",
            "status_disclosure", "compliance",
        ))
    }
    def sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            check_order.get(str(item.get("check_id")), 99),
            str(item.get("section_id") or ""), int(item.get("line") or 0),
            str(item.get("code") or ""), str(item.get("finding_id") or ""),
        )
    violations = sorted(
        {item["finding_id"]: item for item in violations}.values(), key=sort_key,
    )
    warnings = sorted(
        {item["finding_id"]: item for item in warnings}.values(), key=sort_key,
    )
    status_basis = upstream_status
    if len(unique_reported_statuses) == 1:
        declared_status = unique_reported_statuses[0]
        if status_basis == "unknown" or _STATUS_RANK[declared_status] > _STATUS_RANK[status_basis]:
            status_basis = declared_status if status_basis != "unknown" else "unknown"
    result_status = _result_status(status_basis, violations)
    completion_status = (
        "failed" if result_status == "failed" else
        "complete" if result_status in {"complete", "stale"} else "incomplete"
    )
    freshness_status = (
        "stale" if result_status == "stale" or upstream_status == "stale"
        else "fresh" if upstream_status == "complete" else "unknown"
    )
    section_audit = [
        {
            "section_id": section.section_id,
            "title": section.title,
            "canonical_marker": section.marker,
            "heading_level": section.heading_level,
            "heading_line": section.heading_line,
            "start_line": section.start_line,
            "end_line": section.end_line,
        }
        for section in sections
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": result_status,
        "completion_status": completion_status,
        "freshness_status": freshness_status,
        "strict": bool(strict),
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
        "audit": {
            "report_sha256": _digest(markdown),
            "upstream": upstream_state,
            "sections": section_audit,
            "risk_contract": risk_audit,
            "citations": citation_audit,
            "numeric_claims": numeric_audit,
            "gaps": gaps,
            "assumptions": list(assumption_rows.values()),
            "inventory": {
                "sources": inventory["sources"],
                "calculations": inventory["calculations"],
                "known_references": len(refs),
                "calculation_lineage": inventory["calculation_lineage"],
            },
        },
    }


__all__ = [
    "CANONICAL_SECTION_HEADINGS",
    "REPORT_GATE_PROMPT_CONTRACT",
    "SCHEMA_VERSION",
    "SECTION_ORDER",
    "validate_company_research_report",
]
