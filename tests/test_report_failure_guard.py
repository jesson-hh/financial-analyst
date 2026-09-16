"""研报失败链路两个 HIGH BUG 的回归锁(后端审计 2026-06-15 发现)。

BUG#1 研报失败仍 exit 0 → code-only glob 会把隔日/另一 asof 报告冒充本次成功。
   修法 = 只接受精确 {code}_{asof}.md 且 mtime ≥ 子进程起跑时刻 t0。
   锁:_freshest_report_md 同时拒绝陈旧精确档与新鲜但错误 asof 的档。

BUG#2 report-writer 13 个硬依赖,任一上游(含纯上下文 agent)失败即整份研报夭折。
   修法 = orchestrator 支持 soft_deps(软依赖只需 done 不需 ok),把上下文类 agent 标软。
   锁:软依赖失败时下游仍 ready 并跑成功;硬依赖失败时下游仍被阻塞。
"""
import asyncio
import os
import time

from financial_analyst.agent.orchestrator import Orchestrator, DAGNode
from financial_analyst.agent.base import SubAgentResult

from guanlan_v2.console.api import _freshest_report_md


# ── BUG#2: orchestrator soft_deps ──
class _FakeAgent:
    def __init__(self, name, ok=True):
        self.NAME = name
        self._ok = ok

    async def run(self, inputs):
        return SubAgentResult(ok=self._ok, agent_name=self.NAME,
                              error=None if self._ok else "boom")


def _node(name, ok=True, deps=None, soft=None):
    return DAGNode(agent=_FakeAgent(name, ok), deps=deps or [],
                   input_keys=[], soft_deps=soft or [])


def test_soft_dep_failure_does_not_block_dependent():
    ctx = _node("ctx", ok=False)                 # 上下文 agent 失败
    core = _node("core", ok=True)
    writer = _node("writer", ok=True, deps=["core", "ctx"], soft=["ctx"])
    done = asyncio.run(Orchestrator([ctx, core, writer]).run({}))
    assert done["ctx"].ok is False
    assert done["writer"].ok is True, "软依赖失败时下游仍应跑(降级出报告)"


def test_hard_dep_failure_still_blocks_dependent():
    core = _node("core", ok=False)               # 硬依赖失败
    writer = _node("writer", ok=True, deps=["core"], soft=[])
    done = asyncio.run(Orchestrator([core, writer]).run({}))
    assert done["writer"].ok is False, "硬依赖失败必须仍然阻塞下游"
    assert "upstream" in (done["writer"].error or "")


# ── BUG#1: stale-md guard ──
def test_freshest_rejects_stale_only(tmp_path):
    t_now = time.time()
    old = tmp_path / "SH600519_2026-01-01.md"
    old.write_text("旧研报", encoding="utf-8")
    os.utime(old, (t_now - 10000, t_now - 10000))   # 远早于本次起跑
    assert _freshest_report_md(tmp_path, "SH600519", t_now, asof="2026-01-01") is None, \
        "只有旧档时必须返 None(拒绝冒充)"


def test_freshest_picks_this_run_output(tmp_path):
    t0 = time.time()
    exact = tmp_path / "SH600519_2026-06-15.md"
    exact.write_text("本次精确研报", encoding="utf-8")
    os.utime(exact, (t0 + 20, t0 + 20))
    other = tmp_path / "SH600519_2026-06-16.md"
    other.write_text("另一截面且更新", encoding="utf-8")
    os.utime(other, (t0 + 30, t0 + 30))

    got = _freshest_report_md(tmp_path, "SH600519", t0, asof="2026-06-15")
    assert got is not None and got.name == "SH600519_2026-06-15.md"


def test_freshest_never_falls_back_to_other_asof(tmp_path):
    t0 = time.time()
    wrong = tmp_path / "SH600519_2026-06-16.md"
    wrong.write_text("错误截面", encoding="utf-8")
    os.utime(wrong, (t0 + 30, t0 + 30))

    assert _freshest_report_md(tmp_path, "SH600519", t0, asof="2026-06-15") is None


def test_freshest_default_current_request_keeps_today_filename(tmp_path):
    from datetime import date

    t0 = time.time()
    today = date.today().isoformat()
    current = tmp_path / f"SH600519_{today}.md"
    current.write_text("今天研报", encoding="utf-8")
    os.utime(current, (t0 + 10, t0 + 10))

    assert _freshest_report_md(tmp_path, "SH600519", t0) == current
