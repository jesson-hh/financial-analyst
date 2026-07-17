"""Write Introspector proposals to memories/_proposed/<agent>/<date>_<slug>.md.

Two entry points:

* :func:`write_proposals` — the LEGACY dream-loop writer (unchanged).
* :func:`write_pending_proposal` — the Task-9 safe/idempotent pending-proposal
  API: receives a service-assigned proposal ID + effective date, validates
  target agent/slug/root containment, creates one deterministic ``_proposed``
  path with create-if-absent/atomic-replace semantics and returns a logical
  relative locator plus the pending content digest. A crash after file creation
  is recovered by the same ID/digest; no second file/date is ever created. The
  absolute physical path remains audit/runtime-only and is never returned.
"""
from __future__ import annotations
import hashlib
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import List, Optional
import yaml
from financial_analyst.dream.introspector import Proposal
from financial_analyst.memory_paths import default_memory_root

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def write_pending_proposal(
    *,
    memory_root: Path,
    target_agent: str,
    topic_slug: str,
    proposal_id: str,
    effective_date: str,
    content: str,
) -> dict:
    """Create (or idempotently recover) one pending proposal file.

    Returns ``{"relative_locator", "content_digest", "created"}``. Same
    ID/date/content retries succeed without a second file; an existing pending
    file with DIFFERENT content is a conflict (``{"error": ...}``) — the caller
    decides, this writer never overwrites a pending proposal.
    """
    root = Path(memory_root).resolve()
    for label, value in (("target_agent", target_agent), ("topic_slug", topic_slug),
                         ("proposal_id", proposal_id)):
        if not _NAME_RE.fullmatch(value) or ".." in value:
            return {"error": f"{label} {value!r} is outside the closed name form"}
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_date):
        return {"error": f"effective_date {effective_date!r} must be YYYY-MM-DD"}
    proposed_dir = (root / "_proposed" / target_agent).resolve()
    if root not in proposed_dir.parents:
        return {"error": "pending path escapes the memory root"}
    filename = f"{effective_date}_{topic_slug}.md"
    out_path = proposed_dir / filename
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    relative = f"_proposed/{target_agent}/{filename}"
    if out_path.exists():
        existing = hashlib.sha256(out_path.read_bytes()).hexdigest()
        if existing != digest:
            return {"error": f"pending proposal {relative} exists with different content"}
        return {"relative_locator": relative, "content_digest": digest, "created": False}
    proposed_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(proposed_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"relative_locator": relative, "content_digest": digest, "created": True}


def write_proposals(proposals: List[Proposal], memory_root: Optional[Path] = None) -> List[Path]:
    """Write each proposal to memories/_proposed/<agent>/<date>_<slug>.md with frontmatter.
    Returns list of written file paths.
    """
    if memory_root is None:
        memory_root = default_memory_root()
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
