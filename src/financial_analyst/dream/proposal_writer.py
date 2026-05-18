"""Write Introspector proposals to memories/_proposed/<agent>/<date>_<slug>.md."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import List
import yaml
from financial_analyst.dream.introspector import Proposal


def write_proposals(proposals: List[Proposal], memory_root: Path = Path("memories")) -> List[Path]:
    """Write each proposal to memories/_proposed/<agent>/<date>_<slug>.md with frontmatter.
    Returns list of written file paths.
    """
    written: List[Path] = []
    today = date.today().isoformat()
    for p in proposals:
        proposed_dir = memory_root / "_proposed" / p.target_agent
        proposed_dir.mkdir(parents=True, exist_ok=True)
        out_path = proposed_dir / f"{today}_{p.topic_slug}.md"

        frontmatter = {
            "topic": p.topic_slug,
            "title": p.title,
            "target_agent": p.target_agent,
            "confidence": p.confidence,
            "generated_at": today,
            "supporting_cases": p.supporting_cases,
            "reasoning": p.reasoning,
        }
        fm_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
        content = f"---\n{fm_yaml}---\n\n{p.lesson_md}\n"
        out_path.write_text(content, encoding="utf-8")
        written.append(out_path)
    return written
