"""Skill proposal lifecycle — review, accept, reject, deploy.

Mirrors ``memory_ops.py`` pattern: _proposed/<type>/<name>.md → active location.
Every mutation appends to ``~/.financial-analyst/audit.jsonl``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from .schema import SkillProposal, SkillType

AUDIT_PATH = Path.home() / ".financial-analyst" / "audit.jsonl"
DEFAULT_SKILLS_ROOT = Path("skills_generation")


# ---------------------------------------------------------------------------
# Internal helpers (same pattern as memory_ops.py)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_audit_dir() -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _next_audit_id() -> str:
    if not AUDIT_PATH.exists():
        return "a-0001"
    try:
        with AUDIT_PATH.open("r", encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
    except Exception:
        n = 0
    return f"a-{n + 1:04d}"


def _write_audit(entry: dict) -> None:
    _ensure_audit_dir()
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _try_git_stage(project_root: Path, file_path: Path) -> tuple[bool, Optional[str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "add", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()[:200] or f"exit {proc.returncode}"
            return (False, err)
        return (True, None)
    except FileNotFoundError:
        return (False, "git executable not found")
    except subprocess.TimeoutExpired:
        return (False, "git add timeout")
    except Exception as exc:
        return (False, f"{type(exc).__name__}: {exc}")


def _proposal_dir(skills_root: Path, skill_type: SkillType) -> Path:
    return skills_root / "_proposed" / skill_type.value


def _proposal_path(skills_root: Path, skill_type: SkillType, name: str) -> Path:
    return _proposal_dir(skills_root, skill_type) / f"{name}.md"


# ---------------------------------------------------------------------------
# Serialization — YAML frontmatter + body (mirrors dream proposal format)
# ---------------------------------------------------------------------------

_PREAMBLE = "---\n"
_BODY_MARKER = "---\n"


def _serialize(proposal: SkillProposal) -> str:
    meta = {
        "skill_type": proposal.skill_type.value,
        "name": proposal.name,
        "title": proposal.title,
        "description": textwrap.indent(proposal.description, "  ").strip(),
        "confidence": proposal.confidence,
        "trigger_source": proposal.trigger_source,
        "supporting_cases": proposal.supporting_cases,
        "created_at": proposal.created_at,
    }
    frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"{_PREAMBLE}{frontmatter}{_BODY_MARKER}\n\n```\n{proposal.generated_code}\n```\n"


def _deserialize(content: str) -> Optional[SkillProposal]:
    if not content.startswith(_PREAMBLE):
        return None
    # Find the SECOND "---\n" — that separates frontmatter from body.
    end_idx = content.find(_BODY_MARKER, len(_PREAMBLE))
    if end_idx == -1:
        return None
    try:
        meta = yaml.safe_load(content[len(_PREAMBLE):end_idx])
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None

    body = content[end_idx + len(_BODY_MARKER):].strip()
    if body.startswith("```") and body.endswith("```"):
        inner = body[3:-3].strip()
        if inner.startswith("python") or inner.startswith("yaml"):
            newline_idx = inner.index("\n") if "\n" in inner else -1
            inner = inner[newline_idx + 1:] if newline_idx >= 0 else ""
        body = inner

    try:
        return SkillProposal(
            skill_type=SkillType(meta.get("skill_type", "tool")),
            name=meta.get("name", ""),
            title=meta.get("title", ""),
            description=meta.get("description", ""),
            generated_code=body,
            confidence=meta.get("confidence", "med"),
            trigger_source=meta.get("trigger_source", "user_cli"),
            supporting_cases=meta.get("supporting_cases", []),
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_proposal(
    proposal: SkillProposal,
    skills_root: Optional[Path] = None,
) -> Path:
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    dest = _proposal_path(skills_root, proposal.skill_type, proposal.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_serialize(proposal), encoding="utf-8")
    proposal.target_path = str(dest)
    return dest


def load_proposal(
    name: str,
    skill_type: SkillType,
    skills_root: Optional[Path] = None,
) -> Optional[SkillProposal]:
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    path = _proposal_path(skills_root, skill_type, name)
    if not path.exists():
        return None
    return _deserialize(path.read_text(encoding="utf-8"))


def list_proposals(
    skill_type: Optional[SkillType] = None,
    skills_root: Optional[Path] = None,
) -> list[SkillProposal]:
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    proposed_root = skills_root / "_proposed"
    if not proposed_root.exists():
        return []

    results: list[SkillProposal] = []
    types_to_scan = [skill_type] if skill_type else list(SkillType)
    for st in types_to_scan:
        d = _proposal_dir(skills_root, st)
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            proposal = _deserialize(f.read_text(encoding="utf-8"))
            if proposal:
                proposal.target_path = str(f)
                results.append(proposal)
    return results


def accept_proposal(
    name: str,
    skill_type: SkillType,
    skills_root: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> dict:
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    project_root = project_root or Path.cwd()
    src = _proposal_path(skills_root, skill_type, name)
    if not src.exists():
        return {"error": f"No proposal found: {skill_type.value}/{name}"}

    proposal = _deserialize(src.read_text(encoding="utf-8"))
    if proposal is None:
        return {"error": f"Could not parse proposal: {src}"}

    dst = _deploy_target(skill_type, name, skills_root, project_root)
    if dst is None:
        return {"error": f"Unknown skill type: {skill_type.value}"}

    if dst.exists():
        return {"error": f"Refusing to overwrite existing file: {dst}"}

    code = proposal.generated_code

    # Deploy the generated code.
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(code, encoding="utf-8")

    # Post-deploy registration.
    reg_ok, reg_err = _register_skill(skill_type, name, project_root)

    # Best-effort git stage.
    git_ok, git_err = _try_git_stage(project_root, dst)

    # Audit trail.
    audit_id = _next_audit_id()
    entry: dict[str, Any] = {
        "id": audit_id,
        "ts": _now_iso(),
        "action": "skill_accept",
        "source": "cli",
        "skill_type": skill_type.value,
        "name": name,
        "src": str(src.resolve()),
        "dst": str(dst.resolve()),
        "project_root": str(project_root.resolve()),
        "git_staged": git_ok,
        "registered": reg_ok,
    }
    if git_err:
        entry["git_error"] = git_err
    if reg_err:
        entry["reg_error"] = reg_err

    try:
        _write_audit(entry)
    except Exception as exc:
        try:
            dst.unlink()
        except Exception:
            pass
        return {"error": f"Audit write failed: {exc}; rolled back deploy"}

    # Remove the proposal file (it's now deployed).
    src.unlink()

    return {
        "id": audit_id,
        "action": "skill_accept",
        "name": name,
        "skill_type": skill_type.value,
        "dst": str(dst),
        "git_staged": git_ok,
        "registered": reg_ok,
    }


def reject_proposal(
    name: str,
    skill_type: SkillType,
    skills_root: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> dict:
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    project_root = project_root or Path.cwd()
    src = _proposal_path(skills_root, skill_type, name)
    if not src.exists():
        return {"error": f"No proposal found: {skill_type.value}/{name}"}

    backup = src.read_text(encoding="utf-8")
    src.unlink()

    audit_id = _next_audit_id()
    entry: dict[str, Any] = {
        "id": audit_id,
        "ts": _now_iso(),
        "action": "skill_reject",
        "source": "cli",
        "skill_type": skill_type.value,
        "name": name,
        "src": str(src.resolve()),
        "project_root": str(project_root.resolve()),
    }

    try:
        _write_audit(entry)
    except Exception as exc:
        try:
            src.write_text(backup, encoding="utf-8")
        except Exception:
            pass
        return {"error": f"Audit write failed: {exc}; rolled back delete"}

    return {
        "id": audit_id,
        "action": "skill_reject",
        "name": name,
        "skill_type": skill_type.value,
    }


# ---------------------------------------------------------------------------
# Deploy + Register helpers
# ---------------------------------------------------------------------------

def _deploy_target(
    skill_type: SkillType,
    name: str,
    skills_root: Path,
    project_root: Path,
) -> Optional[Path]:
    if skill_type == SkillType.AGENT:
        return project_root / "src" / "financial_analyst" / "agent" / "tier2" / f"{name}.py"
    if skill_type == SkillType.TOOL:
        return skills_root / "tools" / f"{name}.py"
    if skill_type == SkillType.PRESET:
        return project_root / "config" / "swarm" / f"{name}.yaml"
    return None


def _register_skill(
    skill_type: SkillType,
    name: str,
    project_root: Path,
) -> tuple[bool, Optional[str]]:
    if skill_type == SkillType.AGENT:
        return _register_agent_plugin(name, project_root)
    if skill_type == SkillType.TOOL:
        return _register_tool_hotload(name)
    if skill_type == SkillType.PRESET:
        return (True, None)  # YAML presets need no registration
    return (False, f"Unknown skill type: {skill_type}")


def _register_agent_plugin(name: str, project_root: Path) -> tuple[bool, Optional[str]]:
    """Append the new agent module to config/plugins.yaml load_at_startup."""
    plugin_conf = project_root / "config" / "plugins.yaml"
    if not plugin_conf.exists():
        return (False, f"plugins.yaml not found at {plugin_conf}")

    try:
        doc = yaml.safe_load(plugin_conf.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return (False, f"YAML error in plugins.yaml: {e}")

    module_path = f"src.financial_analyst.agent.tier2.{name}"
    startup_list: list = doc.get("load_at_startup", [])
    if module_path in startup_list:
        return (True, None)

    startup_list.append(module_path)
    doc["load_at_startup"] = startup_list

    backup = plugin_conf.read_text(encoding="utf-8")
    try:
        plugin_conf.write_text(
            yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return (True, None)
    except Exception as exc:
        try:
            plugin_conf.write_text(backup, encoding="utf-8")
        except Exception:
            pass
        return (False, f"Failed to write plugins.yaml: {exc}")


def _register_tool_hotload(name: str) -> tuple[bool, Optional[str]]:
    """Try to hot-load the newly deployed tool module."""
    try:
        import importlib
        mod = importlib.import_module(f"skills.tools.{name}")
        if hasattr(mod, "register"):
            tool = mod.register()
            from financial_analyst.buddy.tools import add_dynamic_tool
            add_dynamic_tool(tool)
            return (True, None)
        return (False, f"Tool module skills.tools.{name} has no register() function")
    except Exception as exc:
        return (False, f"Hot-load failed: {exc}")


def get_skills_root(project_root: Optional[Path] = None) -> Path:
    project_root = project_root or Path.cwd()
    return project_root / "skills"


# ---------------------------------------------------------------------------
# Auto-accept — deploy immediately without user review (Hermes-style auto mode)
# ---------------------------------------------------------------------------


def auto_accept_proposal(
    proposal: SkillProposal,
    skills_root: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> dict:
    """Deploy a skill proposal immediately, bypassing human review.

    Used in ``auto`` mode by the background reviewer. Overwrites existing
    files (unlike ``accept_proposal`` which refuses to overwrite).

    Returns same shape as ``accept_proposal``: dict with optional 'error' key.
    """
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    project_root = project_root or Path.cwd()

    st = proposal.skill_type
    name = proposal.name
    dst = _deploy_target(st, name, skills_root, project_root)
    if dst is None:
        return {"error": f"Unknown skill type: {st.value}"}

    code = proposal.generated_code

    # Auto mode allows overwrite — archive the existing file first
    if dst.exists():
        _archive_existing(dst, skills_root)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(code, encoding="utf-8")

    reg_ok, reg_err = _register_skill(st, name, project_root)
    git_ok, git_err = _try_git_stage(project_root, dst)

    audit_id = _next_audit_id()
    entry: dict[str, Any] = {
        "id": audit_id,
        "ts": _now_iso(),
        "action": "skill_auto_accept",
        "source": proposal.trigger_source,
        "skill_type": st.value,
        "name": name,
        "src": "auto-generated",
        "dst": str(dst.resolve()),
        "project_root": str(project_root.resolve()),
        "git_staged": git_ok,
        "registered": reg_ok,
    }
    if git_err:
        entry["git_error"] = git_err
    if reg_err:
        entry["reg_error"] = reg_err

    try:
        _write_audit(entry)
    except Exception as exc:
        try:
            dst.unlink()
        except Exception:
            pass
        return {"error": f"Audit write failed: {exc}; rolled back deploy"}

    # Record usage on creation
    _record_skill_usage(name, st.value, proposal.created_by)

    return {
        "id": audit_id,
        "action": "skill_auto_accept",
        "name": name,
        "skill_type": st.value,
        "dst": str(dst),
        "git_staged": git_ok,
        "registered": reg_ok,
    }


def _archive_existing(file_path: Path, skills_root: Path) -> None:
    """Move an existing deployed skill file to .archive/ before overwriting."""
    archive_dir = skills_root / ".archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = _now_iso().replace(":", "-")[:19]
    dest = archive_dir / f"{file_path.stem}.{ts}{file_path.suffix}"
    shutil.move(str(file_path), str(dest))


# ---------------------------------------------------------------------------
# Usage tracking (Hermes-style ~/.hermes/skills/.usage.json)
# ---------------------------------------------------------------------------


_USAGE_PATH = Path.home() / ".financial-analyst" / "skills" / ".usage.json"


def record_skill_usage(name: str, skill_type: str) -> None:
    """Call this whenever a skill is loaded or used. Updates the usage sidecar."""
    _record_skill_usage(name, skill_type, created_by="unknown")


def _record_skill_usage(name: str, skill_type: str, created_by: str = "unknown") -> None:
    import json as _json
    _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if _USAGE_PATH.exists():
        try:
            data = _json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    key = f"{skill_type}/{name}"
    if key in data:
        entry = data[key]
        entry["use_count"] = entry.get("use_count", 0) + 1
        entry["last_used_at"] = _now_iso()
        entry["last_activity_at"] = _now_iso()
    else:
        data[key] = {
            "name": name,
            "skill_type": skill_type,
            "created_by": created_by,
            "created_at": _now_iso(),
            "use_count": 1,
            "view_count": 0,
            "patch_count": 0,
            "lifecycle_state": "active",
            "pinned": False,
            "last_used_at": _now_iso(),
            "last_activity_at": _now_iso(),
        }

    try:
        _USAGE_PATH.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def record_skill_patch(name: str, skill_type: str) -> None:
    """Increment patch counter in usage sidecar."""
    import json as _json
    if not _USAGE_PATH.exists():
        return
    try:
        data = _json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    key = f"{skill_type}/{name}"
    if key in data:
        data[key]["patch_count"] = data[key].get("patch_count", 0) + 1
        data[key]["last_activity_at"] = _now_iso()
        try:
            _USAGE_PATH.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def list_active_skills(skills_root: Optional[Path] = None) -> list[dict]:
    """List all deployed skills (not proposals) for display in prompts."""
    skills_root = skills_root or DEFAULT_SKILLS_ROOT
    result: list[dict] = []

    tools_dir = skills_root / "tools"
    if tools_dir.exists():
        for f in sorted(tools_dir.glob("*.py")):
            result.append({
                "name": f.stem,
                "skill_type": "tool",
                "path": str(f),
            })

    agent_dir = Path("src/financial_analyst/agent/tier2")
    if agent_dir.exists():
        for f in sorted(agent_dir.glob("*.py")):
            result.append({
                "name": f.stem,
                "skill_type": "agent",
                "path": str(f),
            })

    preset_dir = Path("config/swarm")
    if preset_dir.exists():
        for f in sorted(preset_dir.glob("*.yaml")):
            result.append({
                "name": f.stem,
                "skill_type": "preset",
                "path": str(f),
            })

    return result


# ---------------------------------------------------------------------------
# Skill mode config — auto vs manual (persisted in buddy.yaml)
# ---------------------------------------------------------------------------


def get_skill_mode() -> str:
    """Read skill_mode from ~/.financial-analyst/buddy.yaml. Default: 'manual'."""
    prefs_path = Path.home() / ".financial-analyst" / "buddy.yaml"
    if not prefs_path.exists():
        return "manual"
    try:
        data = yaml.safe_load(prefs_path.read_text(encoding="utf-8")) or {}
        return data.get("skill_mode", "manual")
    except Exception:
        return "manual"


def set_skill_mode(mode: str) -> None:
    """Persist skill_mode to ~/.financial-analyst/buddy.yaml."""
    if mode not in ("auto", "manual"):
        raise ValueError(f"skill_mode must be 'auto' or 'manual', got {mode!r}")

    prefs_path = Path.home() / ".financial-analyst" / "buddy.yaml"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if prefs_path.exists():
        try:
            data = yaml.safe_load(prefs_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    data["skill_mode"] = mode
    prefs_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
