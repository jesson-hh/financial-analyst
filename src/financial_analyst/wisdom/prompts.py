from __future__ import annotations

import json
from typing import Any

from financial_analyst.wisdom.card import WisdomCard

_SYSTEM = """你是 A 股投资经验提炼专家. 输入是某视频博主的口语化盘面复盘转写文本.
你的任务: 提炼出**有信息量、可操作、分析师能看懂**的经验条, 输出严格的 JSON.

# 硬质量门 (不满足就不要产出该条)
每条经验必须同时满足:
1. 含**具体数字/阈值** (如 "占比>15%") 或**明确可操作动作** (如 "压力位附近减仓")
2. 必须有**反例 / 边界** (什么时候这条不成立)
绝不输出"要保持好心态""顺势而为"这类无信息量的水文.

# 输出 JSON 格式 (只输出 JSON, 不要其他文字)
{
  "cards": [
    {
      "title": "一句话标题",
      "quality_score": 0.0-1.0,
      "confidence": "高|中|低",
      "tags": ["标签1", "标签2"],
      "body": "## 经验\\n...\\n\\n## 适用条件\\n...\\n\\n## 操作建议\\n...\\n\\n## 反例 / 边界\\n...",
      "corroborates": ["EV-002"],
      "conflicts": []
    }
  ]
}
body 必须是 4 段式: 经验 / 适用条件 / 操作建议 / 反例 / 边界."""


def _summarize_existing(existing: list[WisdomCard]) -> str:
    if not existing:
        return "(无已有经验)"
    lines = [f"- {c.id} | {c.title} | tags={','.join(c.tags)}" for c in existing]
    return "\n".join(lines)


def build_extraction_messages(
    transcript: str,
    source: dict[str, Any],
    existing: list[WisdomCard],
) -> list[dict[str, str]]:
    """构造抽取用 messages. system=任务+质量门+schema, user=转写+来源+已有经验摘要."""
    src_line = json.dumps(source, ensure_ascii=False)
    user = (
        f"# 视频来源\n{src_line}\n\n"
        f"# 已有经验 (判断 corroborates/conflicts 用, 不要重复产出同一条)\n"
        f"{_summarize_existing(existing)}\n\n"
        f"# 转写文本\n{transcript}\n\n"
        f"请按 system 的 JSON 格式输出经验卡."
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
