"""Curator — periodic skill library maintenance (Hermes pattern).

Runs on an interval (default: 7 days idle-triggered). Performs:

1. **Auto-transitions** (pure, no LLM): Time-based active → stale → archived.
   - active → stale after ``stale_after_days`` (default 30d) without activity
   - stale → archived after ``archive_after_days`` (default 90d)
   - Pinned skills are skipped entirely.

2. **LLM consolidation pass**: Forks an LLM call that scans agent-created
   skills, identifies prefix clusters (skills sharing domain keywords), and
   merges them into umbrella skills or demotes narrow skills to supporting
   files.

Only touches skills with ``created_by: "agent"`` provenance. Never deletes
(max destructive action is archive). Pinned + user-created skills are off-limits.

Usage::

    curator = Curator(skills_root=Path("skills_generation"))
    summary = await curator.run_review()
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .schema import SkillLifecycleState, SkillType


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


# Default: check every 7 days
DEFAULT_CURATOR_INTERVAL_HOURS = 168
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90
DEFAULT_MIN_IDLE_HOURS = 2


CURATOR_STATE_PATH = Path.home() / ".financial-analyst" / "curator_state.json"


class Curator:
    """Periodic skill library maintenance.

    Does NOT run a background thread. Call ``maybe_run()`` from idle-tick hooks
    (CLI, TUI, gateway). It checks the last-run timestamp and only proceeds if
    the configured interval has elapsed.
    """

    def __init__(
        self,
        skills_root: Path = Path("skills_generation"),
        interval_hours: int = DEFAULT_CURATOR_INTERVAL_HOURS,
        stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
        archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
        min_idle_hours: int = DEFAULT_MIN_IDLE_HOURS,
    ):
        self._skills_root = skills_root
        self.interval_hours = interval_hours
        self.stale_after_days = stale_after_days
        self.archive_after_days = archive_after_days
        self.min_idle_hours = min_idle_hours

    # ── gate check ─────────────────────────────────────────────────────

    def should_run(self) -> bool:
        """Check if enough time has passed since the last curator run."""
        if not CURATOR_STATE_PATH.exists():
            return True
        try:
            state = json.loads(CURATOR_STATE_PATH.read_text(encoding="utf-8"))
            last_ts = state.get("last_run_ts", 0)
        except Exception:
            return True
        elapsed = _now_ts() - last_ts
        return elapsed >= self.interval_hours * 3600

    def _save_state(self, summary: dict) -> None:
        CURATOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CURATOR_STATE_PATH.write_text(
            json.dumps({
                "last_run_ts": _now_ts(),
                "last_summary": summary,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── main entry ─────────────────────────────────────────────────────

    async def run_review(self, dry_run: bool = False) -> dict:
        """Run the full curator pass: auto-transitions + LLM consolidation.

        Returns a summary dict suitable for logging / display.
        """
        summary: dict = {
            "auto_transitions": {"staled": [], "archived": []},
            "consolidation": {},
            "errors": [],
        }

        # 1. Auto-transitions (no LLM needed)
        try:
            transitions = self.apply_automatic_transitions(dry_run=dry_run)
            summary["auto_transitions"] = transitions
        except Exception as exc:
            summary["errors"].append(f"auto-transitions failed: {exc}")

        # 2. LLM consolidation pass
        try:
            consolidation = await self._run_llm_consolidation(dry_run=dry_run)
            summary["consolidation"] = consolidation
        except Exception as exc:
            summary["errors"].append(f"LLM consolidation failed: {exc}")

        if not dry_run:
            self._save_state(summary)

        return summary

    # ── auto-transitions (no LLM) ──────────────────────────────────────

    def apply_automatic_transitions(self, dry_run: bool = False) -> dict:
        """Pure time-based state transitions. No LLM calls.

        Returns: {"staled": [...], "archived": [...]}
        """
        result: dict = {"staled": [], "archived": []}
        usage = self._load_usage()

        now = _now_ts()
        stale_cutoff = now - self.stale_after_days * 86400
        archive_cutoff = now - self.archive_after_days * 86400

        for entry in usage.values():
            if not self._is_agent_created(entry):
                continue
            if entry.get("pinned"):
                continue

            name = entry.get("name", "")
            state = entry.get("lifecycle_state", "active")
            last_activity = entry.get("last_activity_at", 0)
            if isinstance(last_activity, str):
                try:
                    last_activity = datetime.fromisoformat(last_activity).timestamp()
                except Exception:
                    last_activity = 0

            if state == "active" and last_activity < stale_cutoff:
                result["staled"].append(name)
                if not dry_run:
                    entry["lifecycle_state"] = SkillLifecycleState.STALE.value
                    entry["state_changed_at"] = _now_iso()

            elif state == "stale" and last_activity < archive_cutoff:
                result["archived"].append(name)
                if not dry_run:
                    self._archive_skill(name)
                    entry["lifecycle_state"] = SkillLifecycleState.ARCHIVED.value
                    entry["state_changed_at"] = _now_iso()

        if not dry_run and (result["staled"] or result["archived"]):
            self._save_usage(usage)

        return result

    # ── LLM consolidation ──────────────────────────────────────────────

    async def _run_llm_consolidation(self, dry_run: bool = False) -> dict:
        """Fork an LLM call to scan agent-created skills and propose merges."""
        agent_skills = self._list_agent_skills()
        if len(agent_skills) < 2:
            return {"skipped": "fewer than 2 agent-created skills"}

        prompt = CURATOR_CONSOLIDATION_PROMPT.format(
            agent_skills=self._format_skills_for_prompt(agent_skills),
        )

        from financial_analyst.llm.client import LLMClient
        client = LLMClient.for_agent("skill-curator")
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Scan the skills above and output your consolidation plan JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])

        if dry_run:
            return {"plan": raw, "dry_run": True}

        # Execute consolidations
        executed = await self._execute_consolidations(raw)
        return {"plan": raw, "executed": executed}

    async def _execute_consolidations(self, plan: dict) -> list[dict]:
        """Execute the consolidation plan returned by the LLM."""
        results: list[dict] = []
        for action in plan.get("actions", []):
            try:
                act = action.get("action", "")
                if act == "merge":
                    r = await self._merge_skills(
                        action.get("sources", []),
                        action.get("target", ""),
                    )
                    results.append({"action": "merge", "result": r})
                elif act == "archive":
                    name = action.get("name", "")
                    r = self._archive_skill(name)
                    results.append({"action": "archive", "name": name, "result": r})
                elif act == "demote":
                    r = self._demote_to_reference(
                        action.get("name", ""),
                        action.get("umbrella", ""),
                    )
                    results.append({"action": "demote", "result": r})
            except Exception as exc:
                results.append({"action": action.get("action", "?"), "error": str(exc)})
        return results

    async def _merge_skills(self, sources: list[str], target: str) -> dict:
        """Merge multiple skills into one umbrella skill."""
        if not sources or not target:
            return {"error": "merge requires sources and target"}
        # For now, archive sources and note them as absorbed_into target
        archived = []
        for name in sources:
            if name != target:
                self._archive_skill(name, absorbed_into=target)
                archived.append(name)
        return {"archived": archived, "target": target}

    def _demote_to_reference(self, name: str, umbrella: str) -> dict:
        """Demote a narrow skill to a reference file under an umbrella."""
        # Find the skill file
        skill_path = self._find_skill_file(name)
        if skill_path is None:
            return {"error": f"skill '{name}' not found"}

        # Determine umbrella directory
        umbrella_dir = self._skills_root / umbrella
        refs_dir = umbrella_dir / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)

        # Copy content as reference
        dest = refs_dir / f"{name}.md"
        content = skill_path.read_text(encoding="utf-8")
        dest.write_text(content, encoding="utf-8")

        # Archive the original
        self._archive_skill(name, absorbed_into=umbrella)
        return {"demoted": name, "umbrella": umbrella, "dest": str(dest)}

    # ── helpers ─────────────────────────────────────────────────────────

    def _archive_skill(self, name: str, absorbed_into: str = "") -> None:
        """Move a skill to the .archive/ directory."""
        archive_dir = self._skills_root / ".archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        skill_path = self._find_skill_file(name)
        if skill_path is None:
            # Try to find as a directory-based skill
            skill_dir = self._skills_root / name
            if skill_dir.is_dir():
                dest = archive_dir / name
                if dest.exists():
                    shutil.rmtree(str(dest))
                shutil.move(str(skill_dir), str(dest))
                if absorbed_into:
                    (dest / ".absorbed_into").write_text(absorbed_into)
            return

        dest = archive_dir / f"{name}{skill_path.suffix}"
        if dest.exists():
            dest.unlink()
        shutil.move(str(skill_path), str(dest))
        if absorbed_into:
            (archive_dir / f".{name}_absorbed_into").write_text(absorbed_into)

    def _find_skill_file(self, name: str) -> Optional[Path]:
        """Find a skill file by name across all deployment locations."""
        # Check tools/
        p = self._skills_root / "tools" / f"{name}.py"
        if p.exists():
            return p
        # Check agents/
        p = Path("src/financial_analyst/agent/tier2") / f"{name}.py"
        if p.exists():
            return p
        # Check presets/
        p = Path("config/swarm") / f"{name}.yaml"
        if p.exists():
            return p
        # Check as directory-based skill
        p = self._skills_root / name / "SKILL.md"
        if p.exists():
            return p
        return None

    def _list_agent_skills(self) -> list[dict]:
        """List all skills with created_by='agent'."""
        usage = self._load_usage()
        return [
            {"name": name, **info}
            for name, info in usage.items()
            if self._is_agent_created(info)
        ]

    def _is_agent_created(self, entry: dict) -> bool:
        return entry.get("created_by") == "agent"

    def _format_skills_for_prompt(self, skills: list[dict]) -> str:
        lines = []
        for s in skills:
            name = s.get("name", "?")
            desc = s.get("description", "")[:120]
            state = s.get("lifecycle_state", "active")
            tags = s.get("tags", [])
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"  {name} ({state}){tag_str}: {desc}")
        return "\n".join(lines) if lines else "(none)"

    def _load_usage(self) -> dict:
        """Load usage sidecar from ~/.financial-analyst/skills/.usage.json."""
        usage_path = Path.home() / ".financial-analyst" / "skills" / ".usage.json"
        if not usage_path.exists():
            return {}
        try:
            return json.loads(usage_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_usage(self, usage: dict) -> None:
        usage_path = Path.home() / ".financial-analyst" / "skills" / ".usage.json"
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(
            json.dumps(usage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


CURATOR_CONSOLIDATION_PROMPT = """\
You are the skill curator for 觀瀾 (financial-analyst), an A-share investment
research system. Your job: maintain a clean, useful skill library.

## Agent-Created Skills

{agent_skills}

## Instructions

1. **Identify prefix clusters**: skills that share a first word or domain keyword
   (e.g. "convertible_bond_*", "biotech_*", "screening_*").

2. **For each cluster**, ask: "What is the UMBRELLA CLASS these skills all serve?"
   - If there's a clear umbrella, MERGE them (archive sources, note absorbed_into).
   - If one skill is narrower than the others, DEMOTE it to a reference file
     under the umbrella.
   - If all skills are independent, do nothing.

3. **Identify stale/outdated skills**: skills whose description suggests they
   cover functionality now available in another skill or built-in tool.

## Output Format

```json
{{
  "actions": [
    {{
      "action": "merge",
      "sources": ["skill-a", "skill-b"],
      "target": "umbrella-skill-name",
      "reason": "Both cover X domain; merging into umbrella"
    }},
    {{
      "action": "archive",
      "name": "outdated-skill",
      "reason": "Superseded by built-in functionality"
    }},
    {{
      "action": "demote",
      "name": "narrow-skill",
      "umbrella": "broader-skill",
      "reason": "Too narrow to stand alone; becomes a reference"
    }}
  ]
}}
```

RULES:
- Only touch agent-created skills. Never touch user-created or built-in skills.
- Never DELETE — max destructive action is ARCHIVE.
- If no consolidation is needed, return {{"actions": []}}.
- Quality over quantity: at most 5 actions.
- Return ONLY JSON. No markdown, no commentary."""
