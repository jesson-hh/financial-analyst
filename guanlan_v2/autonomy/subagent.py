# -*- coding: utf-8 -*-
"""段 agent 派工:BuddyAgent fork(照 console _run_review_bg 先例)。
隔离上下文=只喂简报;工具白名单;confirm 型工具一律拒(_auto_decline,只读红线);
产物写文件(段间文件交接);daemon 线程内 asyncio.run 独立事件循环(watcher 先例)。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Set

from guanlan_v2.screen.llm import LLM_CONFIG_PATH


async def _auto_decline(tool_name: str, args) -> bool:
    """复盘官全链只读:任何 confirm_required 工具直接拒。"""
    return False


def _make_agent(system_prompt: str, max_iters: int, token_budget: int, seat: str):
    from financial_analyst.buddy.agent import BuddyAgent
    from financial_analyst.llm.client import LLMClient
    from guanlan_v2.console import tools as ct
    ct.register_console_tools()
    ra = BuddyAgent(system_prompt=system_prompt, max_tool_iters=max_iters,
                    turn_token_budget=token_budget)
    # 座席换脑(fast=review_section / deep=review_officer,单元一思考档位)
    ra._client = LLMClient.for_agent(seat, config_path=LLM_CONFIG_PATH)
    return ra


def run_section_agent(*, name: str, system_prompt: str, brief_text: str,
                      allowed_tools: Set[str], out_path: Path,
                      seat: str = "review_section", max_iters: int = 6,
                      token_budget: int = 6000,
                      timeout_sec: float = 300) -> Dict[str, Any]:
    texts: list = []
    calls = {"n": 0}

    async def _drive():
        ra = _make_agent(system_prompt, max_iters, token_budget, seat)
        async for evt in ra.run_turn(brief_text, confirm_callback=_auto_decline,
                                     allowed_tools=set(allowed_tools)):
            if evt.kind == "tool_call":
                calls["n"] += 1
            elif evt.kind == "text" and evt.payload:
                texts.append(str(evt.payload))

    async def _outer():
        await asyncio.wait_for(_drive(), timeout=float(timeout_sec))

    try:
        asyncio.run(_outer())
    except asyncio.TimeoutError:
        return {"ok": False, "name": name, "text": "", "tool_calls": calls["n"],
                "error": f"段超时(>{int(timeout_sec)}s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "name": name, "text": "", "tool_calls": calls["n"],
                "error": f"{type(exc).__name__}: {exc}"}
    final = "\n\n".join(t for t in texts if t).strip()
    if not final:
        return {"ok": False, "name": name, "text": "", "tool_calls": calls["n"],
                "error": "段 agent 无文本产出"}
    try:
        Path(out_path).write_text(final, encoding="utf-8")
    except Exception:  # noqa: BLE001 — 落盘失败不吞文本,调用方仍拿到 text
        pass
    return {"ok": True, "name": name, "text": final, "tool_calls": calls["n"]}
