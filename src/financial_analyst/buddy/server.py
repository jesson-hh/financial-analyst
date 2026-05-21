"""HTTP/SSE bridge for the 觀瀾 desktop UI (Tauri).

Exposes ``BuddyAgent.run_turn`` over Server-Sent Events so the desktop
front-end (agent-adapter.jsx) can drive the real agent instead of its
mock. Started via ``financial-analyst serve``.

Protocol (SSE events on POST /run):
  plan            {intent, label, turn_id}     — turn opened (chain empty; grows live)
  tool_start      {idx, name, args}            — a tool began (idx grows as tools appear)
  tool_done       {idx, result, is_error}      — that tool finished
  brief           {sym}                        — stock_brief structured card data
  answer_progress {text}                       — cumulative LLM summary text
  confirm_request {turn_id, tool, args}        — needs y/n/a (front-end POSTs /confirm)
  done            {}
  error           {message}

Confirmation is bidirectional: when the agent hits a tool that needs
approval (per ``mode``), the stream emits ``confirm_request`` and the
agent blocks until the UI calls ``POST /confirm {turn_id, choice}``.

This module imports fastapi/uvicorn lazily so the core package doesn't
need them unless you actually run the server (``pip install
financial-analyst[serve]``).
"""
from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel  # core dependency — safe at module level


class RunReq(BaseModel):
    query: str
    mode: str = "default"
    context: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None   # v1.9.3: multi-turn history key
    model: Optional[str] = None        # v1.9.3: switch backend model


class ConfirmReq(BaseModel):
    turn_id: str
    choice: str = "n"


def _sse(event: str, **data: Any) -> str:
    """Format one SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_app():
    """Construct the FastAPI app. Imported lazily by ``serve``."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import StreamingResponse, JSONResponse
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "serve mode needs fastapi + uvicorn. Install:\n"
            "  pip install financial-analyst[serve]\n"
            "  (or: pip install fastapi uvicorn)"
        ) from exc

    from financial_analyst.buddy.agent import BuddyAgent
    from financial_analyst.buddy.tools import get_tool
    from financial_analyst.buddy.intent import classify, label_for

    app = FastAPI(title="financial-analyst buddy SSE bridge")
    # Tauri webview / localhost dev — allow all origins.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"],
    )

    # turn_id -> asyncio.Future awaiting the user's y/n/a choice
    pending_confirms: Dict[str, "asyncio.Future[str]"] = {}

    # v1.9.3: session_id -> BuddyAgent, reused across /run so追问 keeps
    # conversation history. Bounded LRU so memory doesn't grow forever.
    from collections import OrderedDict
    sessions: "OrderedDict[str, BuddyAgent]" = OrderedDict()
    MAX_SESSIONS = 24

    def _agent_for(session_id: Optional[str], model: Optional[str]) -> BuddyAgent:
        if not session_id:
            agent = BuddyAgent()  # stateless one-off
        elif session_id in sessions:
            agent = sessions.pop(session_id)
            sessions[session_id] = agent  # move to MRU end
        else:
            agent = BuddyAgent()
            sessions[session_id] = agent
            while len(sessions) > MAX_SESSIONS:
                sessions.popitem(last=False)  # evict LRU
        # optional live model switch
        if model:
            try:
                avail = agent._client.list_models()
                prov = next((p for p, ms in avail.items() if model in ms), None)
                if prov:
                    agent._client = agent._client.with_overrides(provider=prov, model=model)
            except Exception:
                pass
        return agent

    def _should_confirm(tool_name: str, mode: str) -> bool:
        if mode == "auto":
            return False
        tool = get_tool(tool_name)
        cost = tool.cost_hint if tool else "instant"
        if mode == "default":
            return cost == "minutes" or bool(tool and tool.confirm_required)
        # safe → confirm everything
        return True

    @app.post("/run")
    async def run(body: RunReq):
        query = (body.query or "").strip()
        mode = body.mode
        turn_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()

        q: "asyncio.Queue[tuple]" = asyncio.Queue()

        async def confirm_cb(tool_name: str, args: dict) -> bool:
            if not _should_confirm(tool_name, mode):
                return True
            fut: "asyncio.Future[str]" = loop.create_future()
            pending_confirms[turn_id] = fut
            await q.put(("confirm_request",
                         {"turn_id": turn_id, "tool": tool_name, "args": args}))
            try:
                choice = await fut
            finally:
                pending_confirms.pop(turn_id, None)
            return choice in ("y", "a", "yes", "always")

        async def produce():
            try:
                agent = _agent_for(body.session_id, body.model)
                intent = classify(query)
                await q.put(("plan", {"turn_id": turn_id, "intent": intent,
                                      "label": label_for(intent)}))
                idx = -1
                async for evt in agent.run_turn(query, confirm_callback=confirm_cb):
                    if evt.kind == "tool_call":
                        idx += 1
                        await q.put(("tool_start", {
                            "idx": idx,
                            "name": evt.payload.get("name"),
                            "args": evt.payload.get("args"),
                        }))
                    elif evt.kind == "tool_result":
                        await q.put(("tool_done", {
                            "idx": idx,
                            "result": (evt.payload.get("content") or "")[:240],
                            "is_error": evt.payload.get("is_error", False),
                        }))
                        se = evt.payload.get("side_effect") or {}
                        if isinstance(se, dict) and se.get("brief"):
                            await q.put(("brief", {"sym": se["brief"]}))
                    elif evt.kind == "text":
                        if evt.payload:
                            await q.put(("answer_progress", {"text": evt.payload}))
                    elif evt.kind == "error":
                        await q.put(("error", {"message": str(evt.payload)}))
                    # 'done' handled after loop
                await q.put(("done", {}))
            except Exception as exc:  # pragma: no cover
                await q.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                await q.put(("__end__", None))

        async def stream():
            task = loop.create_task(produce())
            try:
                while True:
                    kind, data = await q.get()
                    if kind == "__end__":
                        break
                    yield _sse(kind, **data)
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/confirm")
    async def confirm(body: ConfirmReq):
        turn_id = body.turn_id
        choice = (body.choice or "n").lower()
        fut = pending_confirms.get(turn_id)
        if fut is None or fut.done():
            return JSONResponse({"ok": False, "reason": "no pending confirm"}, status_code=404)
        fut.set_result(choice)
        return JSONResponse({"ok": True})

    @app.get("/health")
    async def health():
        from financial_analyst import __version__
        from financial_analyst.buddy.tools import TOOL_REGISTRY
        return JSONResponse({"ok": True, "version": __version__,
                             "tools": len(TOOL_REGISTRY)})

    @app.get("/tools")
    async def tools():
        from financial_analyst.buddy.tools import TOOL_REGISTRY
        return JSONResponse([
            {"name": t.name, "cost": t.cost_hint, "desc": t.description.split(".")[0]}
            for t in TOOL_REGISTRY
        ])

    @app.get("/quotes")
    async def quotes(codes: str):
        """Batch real-time quotes for the UI monitoring wall. ~120ms for
        dozens, no cookie. Poll this every few seconds.
        ``GET /quotes?codes=SH600519,SZ300750,002594``"""
        from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
        code_list = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]
        if not code_list:
            return JSONResponse({"ok": False, "error": "no codes"}, status_code=400)
        try:
            # offload sync HTTP to a thread so we don't block the loop
            data = await asyncio.to_thread(TencentQuoteCollector().fetch, code_list)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        return JSONResponse({"ok": True, "quotes": data})

    @app.get("/models")
    async def models():
        """Available LLM models (for the front-end model picker)."""
        try:
            from financial_analyst.llm.client import LLMClient
            avail = LLMClient.for_agent("buddy").list_models()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True, "models": avail})

    @app.get("/alerts")
    async def alerts():
        """Current price-alert rules (for the UI 盯盘 list — reads alerts.yaml)."""
        from financial_analyst.buddy.alerts import AlertStore
        store = AlertStore()
        return JSONResponse({"ok": True, "alerts": [
            {"id": r.id, "code": r.code, "kind": r.kind, "threshold": r.threshold,
             "note": r.note, "desc": r.describe(), "last_fired": r.last_fired}
            for r in store.list()
        ]})

    @app.get("/alerts/check")
    async def alerts_check():
        """Evaluate all alerts once (Tencent batch) and return any that
        fired. The UI polls this every N seconds → real toast on trigger.
        Honours交易时段 (off-hours returns empty)."""
        from financial_analyst.buddy.alerts import (
            AlertStore, evaluate_batch, market_session,
        )
        from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
        store = AlertStore()
        if len(store) == 0:
            return JSONResponse({"ok": True, "session": market_session(), "fired": []})
        if market_session() != "open":
            return JSONResponse({"ok": True, "session": market_session(),
                                 "fired": [], "note": "非交易时段, 不评估"})
        coll = TencentQuoteCollector()
        fired = await asyncio.to_thread(evaluate_batch, store, coll.fetch)
        return JSONResponse({"ok": True, "session": "open", "fired": [
            {"id": r.id, "desc": r.describe(), "code": r.code,
             "price": q.get("price"), "changePercent": q.get("changePercent"),
             "name": q.get("name")}
            for r, q in fired
        ]})

    return app


def serve(host: str = "127.0.0.1", port: int = 9999) -> None:
    """Run the SSE bridge (blocking). Called by ``financial-analyst serve``."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "serve mode needs uvicorn. Install: pip install financial-analyst[serve]"
        ) from exc
    app = build_app()
    print(f"financial-analyst buddy SSE bridge → http://{host}:{port}")
    print(f"  POST /run  ·  POST /confirm  ·  GET /health  ·  GET /tools")
    uvicorn.run(app, host=host, port=port, log_level="warning")
