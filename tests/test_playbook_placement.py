"""Guard: the 九视角 playbook (V1-V10) must live in bull-advocate's OWN memory, not _shared.

It is only referenced by tier3 bull-advocate ("anchor each bullet to V1-V9 from the
analyst playbook in memory"). Keeping it in memories/_shared/ force-loaded ~7.6K tokens
of an unused doc into all ~16 other report agents on every report run. This test fails
if it ever drifts back into _shared (e.g. a re-seed or an accidental move).
"""
import sys
import pathlib

_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

_MEM = pathlib.Path(__file__).resolve().parents[1] / "memories"
_NEEDLE = "V1: Industry tailwind"   # a stable line unique to the playbook


def _load_all(agent):
    from financial_analyst.agent.memory import AgentMemory
    return AgentMemory(agent, _MEM).load_all()


def test_playbook_lives_in_bull_advocate_only():
    # The one true consumer still has it (so its V-anchor reasoning is unaffected).
    assert _NEEDLE in _load_all("bull-advocate"), "bull-advocate lost the playbook"
    # Agents that never reference the playbook must NOT load it.
    for agent in ["report-writer", "fundamental-analyst", "technical-analyst",
                  "whale-analyst", "bear-advocate", "risk-officer", "news-sentiment"]:
        assert _NEEDLE not in _load_all(agent), \
            f"{agent} should not load the playbook (it does not use V-anchors)"


def test_playbook_not_in_shared():
    # _shared is force-loaded into every agent; the playbook must not sit there.
    shared = _MEM / "_shared" / "playbook_V1_V10.md"
    assert not shared.exists(), "playbook_V1_V10.md is back in _shared (force-loads into all agents)"
    own = _MEM / "bull-advocate" / "playbook_V1_V10.md"
    assert own.exists(), "playbook_V1_V10.md missing from bull-advocate's own memory dir"
