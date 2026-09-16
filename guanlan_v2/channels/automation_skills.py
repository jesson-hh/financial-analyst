"""Closed, versioned skill blocks available to confirmed automations.

Automation skill references are identifiers, never filesystem locations.  The
registry is deliberately defined in code so a stored schedule cannot cause an
unreviewed file, URL, or user-created instruction bundle to be loaded later.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence


class UnknownAutomationSkill(ValueError):
    """A schedule referenced something outside the reviewed registry."""


@dataclass(frozen=True)
class AutomationSkill:
    """One immutable, reviewed workflow fragment."""

    skill_id: str
    name: str
    description: str
    workflow_text: str
    data_priority: tuple[str, ...]
    citation_rule: str
    failure_rule: str


_SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


_SKILLS: tuple[AutomationSkill, ...] = (
    AutomationSkill(
        skill_id="morning_brief_v1",
        name="每日晨报",
        description="开盘前汇总市场环境、关注标的和当日观察条件。",
        workflow_text=(
            "先确认最新可用交易日和数据时点；再概括大盘、行业与会话关注标的；"
            "最后列出今日需要验证的条件、风险和数据缺口。事实与判断必须分开。"
        ),
        data_priority=(
            "会话研究空间内已确认的最新数据",
            "观澜结构化行情与事件数据",
            "带明确时间戳的公开信息",
        ),
        citation_rule="所有关键数字注明数据日期或时点；外部事实注明来源名称。",
        failure_rule="缺少最新数据时明确写出缺口，不沿用旧数值冒充当日结果。",
    ),
    AutomationSkill(
        skill_id="closing_review_v1",
        name="每日收盘复盘",
        description="收盘后复盘走势、计划验证结果和下一交易日观察项。",
        workflow_text=(
            "核对收盘数据时点；复盘大盘、行业与会话关注标的；逐项检查盘前假设；"
            "记录被证实、被证伪和仍待观察的判断，并形成下一交易日观察清单。"
        ),
        data_priority=(
            "当日正式收盘行情与会话内实时记录",
            "观澜结构化资金、事件与研究结果",
            "带明确发布时间的公开信息",
        ),
        citation_rule="价格、涨跌幅、成交与资金数字注明交易日；新闻注明来源与发布时间。",
        failure_rule="正式收盘数据尚不可用时标为未完成，不把盘中快照写成收盘结论。",
    ),
)

_BY_ID = {skill.skill_id: skill for skill in _SKILLS}


def list_automation_skills() -> tuple[AutomationSkill, ...]:
    """Return the reviewed registry in stable display order."""

    return _SKILLS


def registered_skill_ids() -> tuple[str, ...]:
    """Return stable IDs accepted in ``skill_refs``."""

    return tuple(skill.skill_id for skill in _SKILLS)


def _normalize_refs(refs: Iterable[str]) -> tuple[str, ...]:
    if isinstance(refs, (str, bytes)):
        raise UnknownAutomationSkill("skill_refs must be a sequence of registered IDs")
    result: list[str] = []
    for raw in refs:
        if not isinstance(raw, str):
            raise UnknownAutomationSkill("skill_refs must contain only registered IDs")
        ref = raw
        if not _SKILL_ID_RE.fullmatch(ref) or ref not in _BY_ID:
            raise UnknownAutomationSkill("unknown automation skill")
        # Preserve the exact confirmed sequence.  Repetition is harmless and
        # silently deduplicating would make execution differ from review.
        result.append(ref)
    return tuple(result)


def resolve_skill_refs(refs: Sequence[str]) -> tuple[AutomationSkill, ...]:
    """Resolve only registered IDs; paths and URLs are rejected by shape."""

    return tuple(_BY_ID[ref] for ref in _normalize_refs(refs))


def render_skill_block(refs: Sequence[str]) -> str:
    """Render reviewed skill content for a trusted automation system turn."""

    skills = resolve_skill_refs(refs)
    if not skills:
        return "（未启用固定 Skill；仅执行已确认 Prompt。）"
    blocks: list[str] = []
    for skill in skills:
        priorities = "\n".join(
            f"  {index}. {item}" for index, item in enumerate(skill.data_priority, 1)
        )
        blocks.append(
            f"[Skill {skill.skill_id} · {skill.name}]\n"
            f"固定流程：{skill.workflow_text}\n"
            f"数据优先级：\n{priorities}\n"
            f"引用规则：{skill.citation_rule}\n"
            f"失败降级：{skill.failure_rule}"
        )
    return "\n\n".join(blocks)


# Readable aliases for callers that use resolver/registry vocabulary.
automation_skills = list_automation_skills
resolve_automation_skills = resolve_skill_refs


__all__ = [
    "AutomationSkill",
    "UnknownAutomationSkill",
    "automation_skills",
    "list_automation_skills",
    "registered_skill_ids",
    "render_skill_block",
    "resolve_automation_skills",
    "resolve_skill_refs",
]
