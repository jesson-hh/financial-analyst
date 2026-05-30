"""Background skill reviewer — Hermes-style LLM-driven skill management.

After every N conversation turns, a forked LLM call evaluates the conversation
and autonomously decides whether to create, patch, or delete skills.

Two modes:
  - ``auto``:  deploy immediately, user sees a notification
  - ``manual``: write proposal to _proposed/, user reviews before accepting
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .schema import SkillProposal, SkillType


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list_existing_skills(skills_root: Path) -> str:
    """Build a compact listing of existing skills for the review prompt."""
    lines: list[str] = []
    proposed_dir = skills_root / "_proposed"
    tools_dir = skills_root / "tools"

    # Deployed tools
    if tools_dir.exists():
        for f in sorted(tools_dir.glob("*.py")):
            lines.append(f"  [tool] {f.stem} (deployed)")

    # Pending proposals
    if proposed_dir.exists():
        for st_dir in sorted(proposed_dir.iterdir()):
            if not st_dir.is_dir():
                continue
            for f in sorted(st_dir.glob("*.md")):
                lines.append(f"  [{st_dir.name}] {f.stem} (proposed)")

    # Agents from registry
    try:
        from financial_analyst.agent.registry import SubAgentRegistry
        for name in sorted(SubAgentRegistry.names()):
            lines.append(f"  [agent] {name} (deployed)")
    except Exception:
        pass

    # Swarm presets
    preset_dir = Path("config") / "swarm"
    if preset_dir.exists():
        for f in sorted(preset_dir.glob("*.yaml")):
            lines.append(f"  [preset] {f.stem} (deployed)")

    return "\n".join(lines) if lines else "(no existing skills)"


REVIEW_PROMPT = """\
You are a skill-management agent for 觀瀾 (financial-analyst), an A-share investment
research system. Your job: review the conversation below and decide whether any
skills should be created, patched, or deleted.

## Current Skills

{existing_skills}

## Signals that warrant a NEW skill

Any ONE of these is enough:
- A **non-trivial technique, analysis method, or tool-usage pattern** emerged that
  can be reused across future conversations.
- The user asked for something the system **could not do** or did poorly, and the
  gap is not a one-off query.
- A **recurring workflow** was applied successfully (multi-step analysis chain,
  data pipeline, screening flow) that would benefit from being templated.
- The agent had to **figure out a workaround** or multi-step reasoning that
  would be worth capturing as a reusable procedure.
- A tool was called **repeatedly (3+ times)** for a specific domain, asset class,
  or data category and consistently returned empty or irrelevant results. The
  tool itself is working — the underlying data source just doesn't cover that
  domain. A new skill using a different data source or API is warranted.

## Signals that warrant PATCHING an existing skill

- A skill that was loaded or referenced turned out to be **wrong, incomplete,
  or outdated**.
- A **better approach** was discovered for an existing skill's procedure.
- A skill's scope is too narrow or too broad — it needs refinement.

## Signals that warrant DELETING a skill

- A skill is **completely superseded** by a newer one.
- A skill has been proven **harmful or consistently wrong** across multiple turns.
- ONLY delete when confidence is HIGH. When in doubt, patch instead.

## What NOT to capture

- **Environment-dependent failures**: missing API keys, network errors, missing
  binaries, unconfigured credentials.
- **One-off queries**: "what's the price of X" — specific to a single stock.
- **User preferences or facts about the user**: those go to memory, not skills.
- **Session-specific transient errors** that resolved before the conversation ended.
- **Genuine tool malfunctions**: the tool crashed, threw an exception, or hit an
  auth/network/permission error. These are ops issues, not capability gaps.
- **Isolated empty results**: a single query came back empty. The tool may just
  need a better query. Only treat it as a gap when the same domain fails across
  3+ distinct queries with different phrasings — that's a data-coverage gap.

## Preference Order (most preferred first)

1. **Patch an existing skill** that was in play during this turn.
2. **Patch an existing umbrella skill** that covers this domain.
3. **Create a new skill** at the right level of generality.
4. **Do nothing** — if nothing meets the signal bar, output an empty actions list.

## Conversation

{conversation_snapshot}

## Output Format

Return a JSON object with an ``actions`` array. Each action has:

```json
{{
  "actions": [
    {{
      "action": "create" | "patch" | "delete",
      "skill_type": "agent" | "tool" | "preset",
      "name": "kebab-case-name",
      "reason": "One-line explanation of WHY this action is warranted",
      "description": "For create: what this skill does and why it's needed. For patch: what to change.",
      "confidence": "low" | "med" | "high"
    }}
  ]
}}
```

RULES:
- Return at most 3 actions. Quality over quantity.
- For patch: ``name`` must match an existing skill exactly.
- For delete: only with HIGH confidence.
- If nothing meets the signal bar, return ``{{"actions": []}}``.
- Return ONLY JSON. No markdown, no commentary."""


class BackgroundSkillReviewer:
    """Hermes-style background skill reviewer.

    Called after every N conversation turns. Forks an LLM call to evaluate
    the conversation and decide on skill actions. In ``auto`` mode, actions
    are deployed immediately; in ``manual`` mode, they're written to
    ``skills_generation/_proposed/`` for user review.
    """

    def __init__(
        self,
        memory_root: Path = Path("memories"),
        skills_root: Path = Path("skills_generation"),
        mode: str = "manual",  # "auto" | "manual"
    ):
        self._memory_root = memory_root
        self._skills_root = skills_root
        self.mode = mode

    # ── public API ──────────────────────────────────────────────────────

    async def review(
        self,
        conversation_snapshot: str,
        loaded_skills: Optional[list[str]] = None,
    ) -> dict:
        """Review a conversation turn and return a summary of actions taken.

        Args:
            conversation_snapshot: recent conversation turns (user + assistant)
            loaded_skills: names of skills that were loaded during this turn

        Returns:
            dict with keys: actions_taken (list), proposals_written (list),
            errors (list), summary (str)
        """
        result: dict = {
            "actions_taken": [],
            "proposals_written": [],
            "errors": [],
            "summary": "",
        }

        # Build prompt
        existing = _list_existing_skills(self._skills_root)
        prompt = REVIEW_PROMPT.format(
            existing_skills=existing,
            conversation_snapshot=conversation_snapshot[:8000],
        )

        # Call LLM
        try:
            actions = await self._call_llm(prompt)
        except Exception as exc:
            result["errors"].append(f"LLM call failed: {exc}")
            result["summary"] = "Background skill review failed (LLM error)"
            return result

        if not actions:
            result["summary"] = "Background skill review: no actions needed"
            return result

        # Execute each action
        for action in actions:
            try:
                self._validate_action(action)
                outcome = await self._execute_action(action)
                result["actions_taken"].append({
                    "action": action["action"],
                    "name": action.get("name", "?"),
                    "skill_type": action.get("skill_type", "?"),
                    "outcome": outcome,
                })
                if outcome.get("proposal_written"):
                    result["proposals_written"].append(outcome["proposal_written"])
            except Exception as exc:
                result["errors"].append(
                    f"Action {action.get('action', '?')} "
                    f"{action.get('name', '?')}: {exc}"
                )

        # Build summary
        parts = ["Background skill review:"]
        for a in result["actions_taken"]:
            act = a["action"]
            name = a["name"]
            st = a.get("skill_type", "?")
            if act == "create":
                mode_label = "auto-deployed" if self.mode == "auto" else "proposed"
                parts.append(f"  + [{st}] {name} ({mode_label})")
            elif act == "patch":
                parts.append(f"  ~ [{st}] {name} (patched)")
            elif act == "delete":
                parts.append(f"  - [{st}] {name} (deleted)")
        if result["errors"]:
            parts.append(f"  ! {len(result['errors'])} errors")
        result["summary"] = "\n".join(parts)

        return result

    # ── internal ────────────────────────────────────────────────────────

    def _validate_action(self, action: dict) -> None:
        act = action.get("action", "")
        if act not in ("create", "patch", "delete"):
            raise ValueError(f"Unknown action: {act}")
        if not action.get("name"):
            raise ValueError("Action missing 'name'")
        st = action.get("skill_type", "tool")
        if st not in ("agent", "tool", "preset"):
            raise ValueError(f"Unknown skill_type: {st}")
        if act == "delete" and action.get("confidence", "") != "high":
            raise ValueError("Delete requires HIGH confidence")

    async def _execute_action(self, action: dict) -> dict:
        act = action["action"]
        if act == "create":
            return await self._handle_create(action)
        if act == "patch":
            return await self._handle_patch(action)
        if act == "delete":
            return await self._handle_delete(action)
        raise ValueError(f"Unknown action: {act}")

    async def _handle_create(self, action: dict) -> dict:
        """Generate a new skill from the action description."""
        from .generator import SkillGenerator
        from .lifecycle import save_proposal, auto_accept_proposal

        st = SkillType(action.get("skill_type", "tool"))
        name = action["name"]
        description = action.get("description", action.get("reason", ""))

        generator = SkillGenerator(memory_root=self._memory_root)
        proposal = await generator.generate_from_gap(
            gap_description=description,
            skill_type=st,
            suggested_name=name,
            since_days=30,
        )
        proposal.name = name
        proposal.trigger_source = "background_review"
        proposal.created_by = "agent"
        proposal.created_at = _now_iso()
        proposal.supporting_cases = [action.get("reason", "")]

        if self.mode == "auto":
            result = auto_accept_proposal(proposal, skills_root=self._skills_root)
            if "error" in result:
                # Fall back to proposal if auto-accept fails (e.g. file exists)
                dest = save_proposal(proposal, skills_root=self._skills_root)
                return {"status": "proposed (auto-accept failed)", "proposal_written": str(dest)}
            return {"status": "deployed", "dst": result.get("dst", "")}
        else:
            dest = save_proposal(proposal, skills_root=self._skills_root)
            return {"status": "proposed", "proposal_written": str(dest)}

    async def _handle_patch(self, action: dict) -> dict:
        """Patch an existing skill."""
        from .generator import SkillGenerator
        from .lifecycle import load_proposal, save_proposal, auto_accept_proposal

        st = SkillType(action.get("skill_type", "tool"))
        name = action["name"]
        patch_desc = action.get("description", action.get("reason", ""))

        # Try to load the existing skill
        existing = load_proposal(name, st, skills_root=self._skills_root)
        if existing is None:
            # Might be a deployed skill, not a proposal — try to find and read it
            deployed_path = self._find_deployed_skill(name, st)
            if deployed_path is None:
                raise ValueError(f"Skill not found: {st.value}/{name}")
            existing_code = deployed_path.read_text(encoding="utf-8")
        else:
            existing_code = existing.generated_code

        generator = SkillGenerator(memory_root=self._memory_root)
        patched_code = await generator.patch_skill(
            skill_type=st,
            skill_name=name,
            current_code=existing_code,
            patch_description=patch_desc,
        )

        # Create a proposal for the patched version
        proposal = SkillProposal(
            skill_type=st,
            name=name,
            title=f"[PATCHED] {name}",
            description=f"Patched: {patch_desc}",
            generated_code=patched_code,
            confidence=action.get("confidence", "med"),
            trigger_source="background_review",
            created_by="agent",
            created_at=_now_iso(),
            supporting_cases=[patch_desc],
        )

        if self.mode == "auto":
            result = auto_accept_proposal(proposal, skills_root=self._skills_root)
            if "error" in result:
                dest = save_proposal(proposal, skills_root=self._skills_root)
                return {"status": "patch proposed (auto-accept failed)", "proposal_written": str(dest)}
            return {"status": "patched + deployed", "dst": result.get("dst", "")}
        else:
            dest = save_proposal(proposal, skills_root=self._skills_root)
            return {"status": "patch proposed", "proposal_written": str(dest)}

    async def _handle_delete(self, action: dict) -> dict:
        """Delete an existing skill (auto mode only)."""
        from .lifecycle import reject_proposal

        if self.mode != "auto":
            return {"status": "skipped — delete requires auto mode"}

        st = SkillType(action.get("skill_type", "tool"))
        name = action["name"]
        result = reject_proposal(name, st, skills_root=self._skills_root)
        if "error" in result:
            raise ValueError(f"Delete failed: {result['error']}")
        return {"status": "deleted"}

    def _find_deployed_skill(self, name: str, skill_type: SkillType) -> Optional[Path]:
        """Find a deployed skill file by name and type."""
        if skill_type == SkillType.TOOL:
            p = self._skills_root / "tools" / f"{name}.py"
            return p if p.exists() else None
        if skill_type == SkillType.AGENT:
            p = Path("src/financial_analyst/agent/tier2") / f"{name}.py"
            return p if p.exists() else None
        if skill_type == SkillType.PRESET:
            p = Path("config/swarm") / f"{name}.yaml"
            return p if p.exists() else None
        return None

    async def _call_llm(self, prompt: str) -> list[dict]:
        """Call the LLM with the review prompt, parse the actions."""
        from financial_analyst.llm.client import LLMClient

        client = LLMClient.for_agent("skill-reviewer")
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Review the conversation above and output your actions JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])
        return raw.get("actions", [])
