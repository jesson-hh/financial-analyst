"""研报失败链路两个 HIGH BUG 的回归锁(后端审计 2026-06-15 发现)。

BUG#1 研报失败仍 exit 0 → _call_buddy_report 用 glob 取最新 md,会把隔日陈旧报告冒充本次成功。
   修法 = 只接受 mtime ≥ 子进程起跑时刻 t0 的 md(本次真产出的);否则诚实 ok:False,拒绝旧档。
   锁:_freshest_report_md 对「只有旧档」返 None、对「有本次新档」返最新的那份。

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
    old = tmp_path / "SH600519_20260101_120000.md"
    old.write_text("旧研报", encoding="utf-8")
    os.utime(old, (t_now - 10000, t_now - 10000))   # 远早于本次起跑
    assert _freshest_report_md(tmp_path, "SH600519", t_now) is None, \
        "只有旧档时必须返 None(拒绝冒充)"


def test_freshest_picks_this_run_output(tmp_path):
    t0 = time.time()
    old = tmp_path / "SH600519_20260101_120000.md"
    old.write_text("旧研报", encoding="utf-8")
    os.utime(old, (t0 - 10000, t0 - 10000))
    new = tmp_path / "SH600519_20260615_103000.md"
    new.write_text("本次新研报", encoding="utf-8")
    os.utime(new, (t0 + 30, t0 + 30))               # 本次起跑后写入
    got = _freshest_report_md(tmp_path, "SH600519", t0)
    assert got is not None and got.name == "SH600519_20260615_103000.md"
