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


class CompactReq(BaseModel):
    session_id: Optional[str] = None
    transcript: Optional[str] = None   # frontend-rendered convo (fallback source)


class LessonReq(BaseModel):
    text: str


class AlertAddReq(BaseModel):
    """Add a price-watch rule. Invoked from the UI sidebar's "+ Add" button."""
    code: str
    kind: str = "price_below"   # price_below / price_above / pct_above / pct_below
    threshold: float
    note: str = ""


class ConvReq(BaseModel):
    """A conversation to persist. Mirrors the frontend session shape;
    extra fields are allowed and round-tripped verbatim."""
    model_config = {"extra": "allow"}
    id: str
    title: Optional[str] = None
    createdAt: Optional[int] = None
    updatedAt: Optional[int] = None
    context: Optional[Dict[str, Any]] = None
    messages: Optional[list] = None


def _sse(event: str, **data: Any) -> str:
    """Format one SSE frame."""
    return f"event: {event}\ndata: {_safe_json_dumps(data)}\n\n"


def _safe_json_dumps(data) -> str:
    """SSE JSON serialization: replace NaN/Inf with None (browser JSON.parse does not
    accept 'NaN'/'Infinity' literals, which breaks the entire SSE event — the LianAnWei
    quick-view card once permanently failed to render because pe=NaN).
    Python json.dumps defaults to allow_nan=True, which is a silent pitfall."""
    import math as _math
    def _clean(x):
        if isinstance(x, float) and not _math.isfinite(x):
            return None
        if isinstance(x, dict):
            return {k: _clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [_clean(v) for v in x]
        return x
    return json.dumps(_clean(data), ensure_ascii=False)


async def _comments_sentiment(items: list) -> Optional[Dict[str, Any]]:
    """LLM-classify a batch of xueqiu comments into bull/bear/neutral %.

    Returns {bull, bear, neutral, summary} or None on any failure (graceful —
    the UI just hides the sentiment bar)."""
    texts = "\n".join(f"- {(it.get('text') or '')[:120]}" for it in items[:20] if it.get("text"))
    if not texts.strip():
        return None
    try:
        from financial_analyst.llm.client import LLMClient
        # Note: tried switching to qwen3.5-flash but the provider does not accept that name
        # (BadRequestError model not supported); deepseek is unreachable, anthropic has no
        # key. Only qwen3.5-plus works, ~10-20s per call.
        # The front-end has been switched to a two-call pattern (sentiment=0 returns comments
        # instantly first, sentiment=1 fetches sentiment in the background), so the latency
        # here no longer blocks the UI.
        client = LLMClient.for_agent("buddy")
        resp = await client.chat(
            messages=[
                {"role": "system", "content": (
                    "你是雪球评论情绪分析器。读下面某只股票的雪球散户评论，判断整体情绪倾向。"
                    "只输出 JSON，不要任何多余文字: "
                    '{"bull": 看多占比0-100整数, "bear": 看空占比, "neutral": 中性占比, '
                    '"summary": "一句话中文总结"}。bull+bear+neutral 必须=100。'
                )},
                {"role": "user", "content": texts},
            ],
            temperature=0.2,
        )
        choice = resp["choices"][0]["message"]
        content = (choice.get("content") if isinstance(choice, dict)
                   else getattr(choice, "content", "")) or ""
        import re as _re
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        if not m:
            return None
        d = json.loads(m.group(0))
        return {
            "bull": int(d.get("bull", 0) or 0),
            "bear": int(d.get("bear", 0) or 0),
            "neutral": int(d.get("neutral", 0) or 0),
            "summary": str(d.get("summary", "")),
        }
    except Exception:
        return None


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

    # v1.9.3: session_id -> BuddyAgent, reused across /run so follow-up queries keep
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
                        if isinstance(se, dict) and se.get("md_path"):
                            await q.put(("report", {"path": se["md_path"]}))
                    elif evt.kind == "text":
                        if evt.payload:
                            await q.put(("answer_progress", {"text": evt.payload}))
                    elif evt.kind == "error":
                        await q.put(("error", {"message": str(evt.payload)}))
                    # 'done' handled after loop
                tok = getattr(getattr(agent, "_client", None), "total_tokens", None)
                await q.put(("done", {"tokens": tok}))
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

    @app.post("/compact")
    async def compact(body: CompactReq):
        """Summarize a session's history into a short digest, freeing context.
        Returns {ok, summary}. Uses the in-memory agent history, or the
        provided ``transcript`` if the server lost it (e.g. after restart)."""
        try:
            agent = _agent_for(body.session_id, None)
            summary = await agent.compact(transcript=body.transcript)
            return JSONResponse({"ok": True, "summary": summary})
        except Exception as exc:  # pragma: no cover
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                status_code=500)

    # ── conversation disk store (survives browser cache clears) ──
    from financial_analyst.buddy.conversations import ConversationStore
    conv_store = ConversationStore()

    @app.post("/conversations")
    async def conv_save(body: ConvReq):
        cid = conv_store.save(body.model_dump())
        return JSONResponse({"ok": cid is not None, "id": cid})

    @app.get("/conversations")
    async def conv_list():
        return JSONResponse({"ok": True, "conversations": conv_store.list()})

    # ⚠ The static path /conversations/trash MUST be registered before the dynamic
    # /conversations/{cid}, otherwise FastAPI will treat "trash" as a cid and hit
    # conv_get returning 404 (pitfall hit on 2026-05-24).
    @app.get("/conversations/trash")
    async def conv_list_trash():
        """Trash conversation list (includes deletedAt). Called by the UI 'Deleted' tab."""
        # While listing, also purge old ones (>30 days)
        purged = conv_store.purge_old_trash()
        items = conv_store.list_trash()
        return JSONResponse({"ok": True, "conversations": items,
                             "purged": purged})

    @app.post("/conversations/{cid}/restore")
    async def conv_restore(cid: str, body: Optional[Dict[str, Any]] = None):
        """Restore one conversation from the trash. Body optionally takes ``{trash_filename: "..."}``
        to pick which copy to restore (multiple copies may exist when the same cid was deleted repeatedly)."""
        trash_fn = (body or {}).get("trash_filename") if body else None
        ok = conv_store.restore(cid, trash_filename=trash_fn)
        return JSONResponse({"ok": ok})

    @app.get("/conversations/{cid}")
    async def conv_get(cid: str):
        conv = conv_store.load(cid)
        if conv is None:
            return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "conversation": conv})

    @app.delete("/conversations/{cid}")
    async def conv_delete(cid: str, permanent: int = 0):
        """**Soft delete** by default — move to the ``_trash/`` subdirectory and auto-purge after 30 days.
        ``?permanent=1`` does an immediate hard delete (skips trash, unrecoverable)."""
        if permanent:
            ok = conv_store.permanent_delete(cid)
        else:
            ok = conv_store.delete(cid)
        return JSONResponse({"ok": ok, "permanent": bool(permanent)})

    # ── Xueqiu community: per-stock comments (local instant / refresh live-pulls) + sentiment aggregation ──
    @app.get("/comments")
    async def comments(code: str, refresh: int = 0, limit: int = 8, sentiment: int = 0):
        from financial_analyst.buddy.tools import normalize_code, _format_social_posts
        from financial_analyst.data.news_db import NewsDB
        norm = normalize_code(code)
        err = None
        if refresh:
            try:
                from financial_analyst.data.collectors.opencli.xueqiu_comments import (
                    XueqiuCommentsCollector)
                raw = await asyncio.to_thread(
                    XueqiuCommentsCollector().fetch, norm, max(limit, 20))
                if raw:
                    ndb = NewsDB()
                    ndb.upsert_social_posts(norm, raw, "xueqiu-comments")
                    ndb.close()
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
        ndb = NewsDB()
        posts = ndb.query_social_posts(norm, since_days=365, limit=limit)
        ndb.close()
        items = _format_social_posts(posts)
        senti = await _comments_sentiment(items) if (sentiment and items) else None
        return JSONResponse({"ok": err is None, "comments": items,
                             "sentiment": senti, "error": err})

    # ── Xueqiu community: hot-stock ranking / watchlist feed (in-memory TTL cache, opencli is slow) ──
    _xq_cache: Dict[str, tuple] = {}

    async def _xq_cached(key: str, ttl: int, fn):
        import time as _t
        now = _t.time()
        hit = _xq_cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1], True
        data = await asyncio.to_thread(fn)
        _xq_cache[key] = (now, data)
        return data, False

    @app.get("/xueqiu/hot")
    async def xueqiu_hot(limit: int = 20, type: int = 10):
        try:
            from financial_analyst.data.collectors.opencli.xueqiu_hot_stock import (
                XueqiuHotStockCollector)
            data, cached = await _xq_cached(
                f"hot:{type}:{limit}", 300,
                lambda: XueqiuHotStockCollector().fetch(limit=limit, type_=type))
            return JSONResponse({"ok": True, "cached": cached, "stocks": data or []})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "stocks": []})

    @app.get("/xueqiu/feed")
    async def xueqiu_feed(limit: int = 20):
        try:
            from financial_analyst.data.collectors.opencli.xueqiu_feed import (
                XueqiuFeedCollector)
            data, cached = await _xq_cached(
                f"feed:{limit}", 300,
                lambda: XueqiuFeedCollector().fetch(limit=limit))
            return JSONResponse({"ok": True, "cached": cached, "posts": data or []})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}", "posts": []})

    # ── API stability deep probe: 5 sources + LLM, single parallel pull, returns ok/latency/detail per source ──
    # Usage: GET /diag (full run, ~20s mostly LLM); GET /diag?quick=1 (skip LLM, ~2s)
    @app.get("/diag")
    async def diag(quick: int = 0):
        import time as _t

        def _make(name, fn):
            async def _run():
                t = _t.time()
                try:
                    detail = await asyncio.wait_for(asyncio.to_thread(fn), timeout=15)
                    return {"name": name, "ok": True,
                            "latency_ms": int((_t.time() - t) * 1000),
                            "detail": str(detail)[:200]}
                except asyncio.TimeoutError:
                    return {"name": name, "ok": False,
                            "latency_ms": int((_t.time() - t) * 1000),
                            "detail": "timeout >15s"}
                except Exception as e:
                    return {"name": name, "ok": False,
                            "latency_ms": int((_t.time() - t) * 1000),
                            "detail": f"{type(e).__name__}: {str(e)[:160]}"}
            return _run

        def _p_xq_comments():
            from financial_analyst.data.collectors.opencli.xueqiu_comments import (
                XueqiuCommentsCollector)
            rows = XueqiuCommentsCollector().fetch("SH600519", 3)
            return f"{len(rows)} comments"

        def _p_xq_hot():
            from financial_analyst.data.collectors.opencli.xueqiu_hot_stock import (
                XueqiuHotStockCollector)
            rows = XueqiuHotStockCollector().fetch(limit=5, type_=10)
            top = rows[0].get("name") if rows else None
            return f"{len(rows)} hot stocks, top={top!r}"

        def _p_tencent():
            from financial_analyst.data.collectors.tencent_quote import (
                TencentQuoteCollector)
            q = TencentQuoteCollector().quote("SH600519")
            if not q or q.get("price") is None:
                raise RuntimeError("empty quote returned")
            return f"price={q.get('price')} pct={q.get('changePercent')}"

        def _p_news_db():
            from financial_analyst.data.news_db import NewsDB
            db = NewsDB()
            rows = db.query_news(since_days=7, limit=1)
            db.close()
            return f"{len(rows)} recent news in db"

        async def _p_llm():
            t = _t.time()
            if quick:
                return {"name": "llm", "ok": True, "latency_ms": 0,
                        "detail": "skipped (quick=1)"}
            try:
                from financial_analyst.llm.client import LLMClient
                client = LLMClient.for_agent("buddy")
                resp = await asyncio.wait_for(client.chat(
                    messages=[{"role": "user",
                               "content": "reply with the single word: OK"}],
                    temperature=0,
                ), timeout=25)
                ch = resp["choices"][0]["message"]
                content = (ch.get("content") if isinstance(ch, dict)
                           else getattr(ch, "content", "")) or ""
                return {"name": "llm", "ok": "ok" in content.lower(),
                        "latency_ms": int((_t.time() - t) * 1000),
                        "detail": f"model={client.model} reply={content.strip()[:60]!r}"}
            except asyncio.TimeoutError:
                return {"name": "llm", "ok": False,
                        "latency_ms": int((_t.time() - t) * 1000),
                        "detail": "timeout >25s"}
            except Exception as e:
                return {"name": "llm", "ok": False,
                        "latency_ms": int((_t.time() - t) * 1000),
                        "detail": f"{type(e).__name__}: {str(e)[:160]}"}

        results = await asyncio.gather(
            _make("xueqiu_comments", _p_xq_comments)(),
            _make("xueqiu_hot_stock", _p_xq_hot)(),
            _make("tencent_quote", _p_tencent)(),
            _make("news_db", _p_news_db)(),
            _p_llm(),
        )
        # Cumulative stats from the rate-limit/retry/cache layer (data/net.py)
        from financial_analyst.data.net import source_stats
        return JSONResponse({"ok": all(r["ok"] for r in results),
                             "results": results,
                             "rate_limit_stats": source_stats()})

    # /report-progress?code=X — let the front-end poll the in-progress state of `financial-analyst report`.
    # tui.run_report_oneshot writes out/<CODE>_progress.json live in the orchestrator on_event,
    # this endpoint reads it back. Usage: the front-end polls every 1-2s after firing the run_report tool,
    # surfacing per-agent state (pending/running/done/fail) + elapsed for all 14 agents.
    @app.get("/report-progress")
    async def report_progress(code: str):
        from financial_analyst.buddy.tools import _project_root
        code = (code or "").strip().upper()
        if not code:
            return JSONResponse({"ok": False, "error": "code required"})
        progress_path = _project_root() / "out" / f"{code}_progress.json"
        if not progress_path.exists():
            return JSONResponse({"ok": False, "error": "not_started",
                                 "agents": {}, "total": 0, "done": 0,
                                 "running": 0, "pending": 0})
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            return JSONResponse({"ok": True, **data})
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})

    # POST /lesson { text } — the user records one lesson via buddy slash command
    # `/lesson <text>`. Written to memories/_shared/conversation_lessons.md; on the
    # next session buddy automatically prepends these lessons to SYSTEM_PROMPT.
    # Closes the "persist conversation experience" loop.
    @app.post("/lesson")
    async def lesson(body: LessonReq):
        text = (body.text or "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty text"})
        from financial_analyst.buddy.tools import _project_root
        import datetime as _dt
        f = _project_root() / "memories" / "_shared" / "conversation_lessons.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text(
                "# Conversation Lessons\n\n"
                "用户在 buddy 对话里 `/lesson <text>` 沉淀的经验. buddy 每次启动自动 prepend 到 SYSTEM_PROMPT.\n",
                encoding="utf-8",
            )
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"\n- [{stamp}] {text}\n")
        return JSONResponse({"ok": True, "appended": text,
                             "note": "下次重启 buddy 后端后生效"})

    @app.get("/resolve")
    async def resolve(q: str):
        """Resolve user input ('code or name') into {code, name} — used for bulk-add to the watchlist."""
        import re as _re
        from financial_analyst.buddy.tools import normalize_code
        qq = (q or "").strip()
        if not qq:
            return JSONResponse({"ok": False})
        # Code: 6 digits (optionally with SH/SZ/BJ prefix)
        if _re.match(r"^(SH|SZ|BJ)?\d{6}$", qq, _re.I):
            norm = normalize_code(qq)
            name = None
            try:
                from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
                qd = await asyncio.to_thread(TencentQuoteCollector().quote, norm)
                if qd:
                    name = qd.get("name")
            except Exception:
                pass
            return JSONResponse({"ok": True, "code": norm, "name": name or norm})
        # Name → code (industry cache has code/name columns)
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader
            df = IndustryLoader()._load_cache()
            if df is not None and not df.empty and "name" in df.columns:
                exact = df[df["name"].astype(str) == qq]
                hit = exact if not exact.empty else df[df["name"].astype(str).str.contains(_re.escape(qq), na=False)]
                if not hit.empty:
                    row = hit.iloc[0]
                    return JSONResponse({"ok": True, "code": str(row["code"]), "name": str(row["name"])})
        except Exception:
            pass
        return JSONResponse({"ok": False, "q": qq})

    @app.get("/report")
    async def report(path: str):
        """Read a generated deep-report .md (full text) for the UI drawer.
        Restricted to files under the project ``out/`` dir."""
        from pathlib import Path
        from financial_analyst.buddy.tools import _project_root
        try:
            out_dir = (_project_root() / "out").resolve()
            p = Path(path).resolve()
            if p.suffix.lower() != ".md" or not str(p).startswith(str(out_dir)) or not p.exists():
                return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
            return JSONResponse({"ok": True, "text": p.read_text(encoding="utf-8", errors="replace")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}, status_code=500)

    @app.get("/health")
    async def health():
        from financial_analyst import __version__
        from financial_analyst.buddy.tools import TOOL_REGISTRY
        return JSONResponse({"ok": True, "version": __version__,
                             "tools": len(TOOL_REGISTRY)})

    @app.get("/data/status")
    async def data_status():
        """Last-update timestamps + staleness flags for each data type.

        UI uses this to render the data-refresh button state (e.g. badge
        the button red if day data > 24h old).
        """
        try:
            from financial_analyst.data import last_update as _lu
            from financial_analyst.buddy.alerts import market_session
            implemented = set(_lu.IMPLEMENTED_TYPES)
            rows = []
            for dt, age, stale in _lu.status_summary():
                rows.append({
                    "type":        dt,
                    "age":         age,
                    "stale":       stale,
                    "implemented": dt in implemented,
                })
            # Only types we can actually refresh count towards the badge.
            # financials/f10 have no updater yet — they'd otherwise show
            # "never" forever and the red ⚠ would never clear.
            stale_count = sum(1 for r in rows if r["stale"] and r["implemented"])
            # Market session — frontend uses this to render an orange
            # "waiting for close" state when data is fresh but today's
            # trading day isn't done yet ('open' / 'lunch' on weekdays).
            session = market_session()
            return JSONResponse({
                "ok":             True,
                "items":          rows,
                "stale_count":    stale_count,
                "any_stale":      stale_count > 0,
                "market_session": session,  # 'open' / 'lunch' / 'closed' / 'weekend'
            })
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"},
                status_code=500,
            )

    @app.post("/data/refresh")
    async def data_refresh(skip_5min: bool = False):
        """Trigger an incremental data refresh — equivalent to `fa data refresh`.

        Spawns ``python -m financial_analyst.cli data update`` as a detached
        subprocess so the request returns immediately. The UI polls
        ``/data/status`` afterward to see when the timestamps move.

        For long-running runs (full-universe day + 5min ≈ 5-20 min) this is
        much better than holding the HTTP connection open.
        """
        import subprocess
        import sys
        from pathlib import Path
        cmd = [sys.executable, "-m", "financial_analyst.cli",
               "data", "update"]
        if skip_5min:
            cmd.append("--skip-5min")
        try:
            # Detached: stdout/stderr go to /dev/null so we don't accumulate
            # buffer in the buddy process. The CLI writes its own progress
            # to .fa-data-update.log if the user wants to watch.
            log_path = Path.cwd() / ".fa-data-update.log"
            proc = subprocess.Popen(
                cmd,
                stdout=open(log_path, "w", encoding="utf-8"),
                stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                               if sys.platform == "win32" else 0),
            )
            return JSONResponse({
                "ok": True,
                "pid": proc.pid,
                "log": str(log_path),
                "hint": "Poll /data/status every 5-10s to see updated timestamps.",
            })
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"},
                status_code=500,
            )

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
        """Available LLM models (for the front-end model picker).

        v1.9.6 change: **filter out providers without an API key** — models
        whose key the user has not configured should not show up in the picker
        (otherwise the user switches to one and discovers it does not work).
        For each provider, if ``api_key_env`` is empty in ``os.environ``, skip
        all of that provider's models.

        Returns:
            ``models`` flat array [{id, name, provider}]
            ``by_provider`` grouped
            ``disabled_providers`` [{name, reason}] lists the skipped + reason
        """
        import os
        try:
            from financial_analyst.llm.client import LLMClient
            client = LLMClient.for_agent("buddy")
            providers_cfg = client.config.get("providers", {}) or {}
            by_prov_all = client.list_models()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

        by_prov: Dict[str, list] = {}
        disabled = []
        for prov, models_list in by_prov_all.items():
            api_key_env = (providers_cfg.get(prov) or {}).get("api_key_env", "")
            if not api_key_env:
                # provider has no api_key_env field (abnormal config), skip
                disabled.append({"name": prov, "reason": "no api_key_env in llm.yaml"})
                continue
            if not os.environ.get(api_key_env, "").strip():
                disabled.append({"name": prov,
                                  "reason": f"{api_key_env} not set in env"})
                continue
            by_prov[prov] = models_list

        flat = [{"id": m, "name": m, "provider": p}
                for p, ms in by_prov.items() for m in ms]
        return JSONResponse({"ok": True, "models": flat,
                              "by_provider": by_prov,
                              "disabled_providers": disabled})

    @app.get("/alerts")
    async def alerts():
        """Current price-alert rules (for the UI watch-list — reads alerts.yaml)."""
        from financial_analyst.buddy.alerts import AlertStore
        store = AlertStore()
        return JSONResponse({"ok": True, "alerts": [
            {"id": r.id, "code": r.code, "kind": r.kind, "threshold": r.threshold,
             "note": r.note, "desc": r.describe(), "last_fired": r.last_fired}
            for r in store.list()
        ]})

    @app.post("/alerts")
    async def alert_add(body: AlertAddReq):
        """Add a price-watch rule (called by the UI sidebar "+ Add" button).

        kind: ``price_below`` / ``price_above`` / ``pct_above`` / ``pct_below``.
        Duplicate (code, kind) **updates** the threshold (AlertStore.add does upsert internally).
        """
        from financial_analyst.buddy.alerts import AlertStore, VALID_KINDS
        from financial_analyst.buddy.tools import normalize_code

        if body.kind not in VALID_KINDS:
            return JSONResponse(
                {"ok": False, "reason": f"kind 必须是 {list(VALID_KINDS)}"},
                status_code=400,
            )
        try:
            code = normalize_code(body.code)
            store = AlertStore()
            rule = store.add(code, body.kind, body.threshold, note=body.note)
            return JSONResponse({"ok": True, "rule": {
                "id": rule.id, "code": rule.code, "kind": rule.kind,
                "threshold": rule.threshold, "note": rule.note,
                "desc": rule.describe(),
            }})
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                status_code=400,
            )

    @app.delete("/alerts/{rule_id:path}")
    async def alert_remove(rule_id: str):
        """Delete one price-watch rule.

        ``rule_id`` takes the shape ``SH600519:price_below`` (from the ``id`` returned by GET /alerts),
        or just ``SH600519`` to delete all rules for that code.

        Called by the delete button on the UI watchlist wall / monitoring list. Equivalent to
        the LLM invoking the ``alert_remove`` tool, but the front-end doesn't need to go through chat.
        """
        from financial_analyst.buddy.alerts import AlertStore
        store = AlertStore()
        ok = store.remove(rule_id)
        if not ok:
            return JSONResponse({"ok": False, "rule_id": rule_id,
                                 "reason": "rule not found"}, status_code=404)
        return JSONResponse({"ok": True, "rule_id": rule_id})

    @app.get("/alerts/check")
    async def alerts_check():
        """Evaluate all alerts once (Tencent batch) and return any that
        fired. The UI polls this every N seconds → real toast on trigger.
        Honours trading hours (off-hours returns empty)."""
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
