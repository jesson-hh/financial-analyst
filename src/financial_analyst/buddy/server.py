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

fastapi / uvicorn are core dependencies as of v1.0.3, so ``pip install
financial-analyst`` is enough — no extras needed. Imports here stay
lazy anyway so importing this module never fails at install time.
"""
from __future__ import annotations
import asyncio
import io
import json
import uuid
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator  # core dependency — safe at module level
# fastapi is a core dependency too; UploadFile/File must be module-level so the
# /upload route annotation resolves under ``from __future__ import annotations``
# (forward refs are looked up in module globals, not build_app's local scope).
from fastapi import UploadFile, File as FastAPIFile


def _jsonable(obj):
    """Recursively convert a dataclass-asdict structure to valid JSON:
    NaN/Inf floats -> None (json.dumps allow_nan would emit invalid 'NaN').

    SP-C.1 direct factor endpoints return ``dataclasses.asdict(result)`` run
    through this so the browser's ``JSON.parse`` never chokes on a ``NaN``/
    ``Infinity`` literal (same pitfall ``_safe_json_dumps`` guards for SSE).

    Also coerces numpy scalars (np.float32/np.int64/np.bool_ — NOT Python
    float/int subclasses) to native types: ``/factor/bench``'s ``df.to_dict``
    can emit these, and Starlette's ``json.dumps(allow_nan=False)`` would 500
    on them (pandas-version-dependent). Belt-and-suspenders for portability."""
    import math
    import numpy as np
    if isinstance(obj, np.generic):  # numpy scalar → Python native first
        obj = obj.item()
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


class RunReq(BaseModel):
    query: str
    mode: str = "default"
    context: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None   # v1.9.3: multi-turn history key
    model: Optional[str] = None        # v1.9.3: switch backend model


# ── SP-C.1 直连因子端点的请求模型 (universe 默认小池 csi300_active 求秒级返回) ──
class ReportReq(BaseModel):
    expr_or_name: str
    universe: str = "csi300_active"
    freq: str = "month"
    start: Optional[str] = None
    end: Optional[str] = None
    archive: bool = True
    note: str = ""


class ForgeReq(BaseModel):
    idea: str
    universe: str = "csi300_active"
    quick_eval: bool = True


class ComposeReq(BaseModel):
    members: list
    method: str = "lgbm"
    universe: str = "csi300_active"
    freq: str = "month"
    train_frac: float = 0.6
    archive: bool = True
    note: str = ""
    interpret: bool = False


class AdviseReq(BaseModel):
    goal: str
    universe: str = "csi300_active"


class SaveReq(BaseModel):
    name: str
    expr: str
    description: str = ""
    parsed: list = []
    kpis: dict = {}


class EventReq(BaseModel):
    expr_or_name: str
    universe: str = "csi300_active"
    start: Optional[str] = None
    end: Optional[str] = None
    horizons: list = [1, 5, 10, 20]
    archive: bool = False
    note: str = ""


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


# ── QuantFlow Phase 2: workflow REST request shapes ─────────────────────
# Workflow create accepts an arbitrary dict that gets Workflow.model_validate()-ed
# (full Pydantic shape lives in financial_analyst.workflow.schema). We don't
# redeclare it here — keep the schema single-source.


class CopilotDraftReq(BaseModel):
    """SP-W2B Workflow Copilot 请求: 自然语言目标 + 默认 universe/freq."""
    goal: str
    universe: str = "csi300_active"
    freq: str = "day"


# ── Realtime Watch (盯盘) 端点请求模型 (Task 7) ──
class WatchItemReq(BaseModel):
    """One stock to watch. ``code`` accepts bare 6-digit / prefixed / suffixed
    (normalize_code handles it). ``stop_loss`` enables the stop_break channel."""
    code: str
    avg_cost: Optional[float] = None
    stop_loss: Optional[float] = None


class WatchStartReq(BaseModel):
    """Start the盯盘 loop over a list of items (+ optional loop tunables)."""
    items: list = []                       # list[WatchItemReq]; validated per-item
    tick_seconds: Optional[float] = None
    cooldown_minutes: Optional[int] = None
    global_llm_cap_per_session: Optional[int] = None


class WatchAckReq(BaseModel):
    """Acknowledge a recommendation (人工确认): writes user_action onto the rec."""
    ts: str
    code: str
    user_action: str = "confirm"           # confirm / ignore


class WatchItemOpReq(BaseModel):
    """Add / remove one watched item on the *running* loop."""
    op: str                                # "add" | "remove"
    code: str
    avg_cost: Optional[float] = None
    stop_loss: Optional[float] = None


# ── Backtest 端点请求模型 ──
class BacktestRunReq(BaseModel):
    start: Optional[str] = None         # 窗口起 (YYYY-MM-DD); None → 前端 probe 后填
    end: Optional[str] = None           # 窗口止; None → runner 自动 cap 到 data_end
    init_cash: float = 1_000_000.0      # 初始资金
    candidate_topn: int = 20            # 候选池 Top-N (映射 CandidateConfig.topn)
    mode: str = "mock"                  # "mock"(默认,确定性 stub agent) | "real"(真 LLM)
    match_freq: str = "day"             # "day" | "5min" (第一版 UI 只暴露 day)
    # P2 扩字段 ↓
    # pool / factor_name 用 Field(pattern=) 在 body 解析期拒非白名单 (B-I-1 review fix);
    # 不用 typing.Literal — server.py 头部 `from __future__ import annotations` 会让
    # Literal 被 stringify, Pydantic v2 + FastAPI TypeAdapter 解析失败 (PydanticUserError
    # "TypeAdapter ... is not fully defined"). regex pattern 不依赖 forward ref 解析,
    # 行为等价 (Pydantic 解析 body 时同样拒非白名单, 返 422).
    pool: str = Field(default="csi300", pattern=r"^(csi300|csi_fast|csi500|csi800)$",
                      description="候选池 (regex 白名单, 非匹配返 422)")
    # codes 模式 (2026-06-03): 用户指定代码 (单股 / 自定义 watchlist).
    # 非空时覆盖 pool 走 codes 路径, 见 CandidateConfig.codes / select_candidates 分支.
    # 不用 Field(pattern=...) 因为 pattern 只作用于 str, 列表元素要逐个校验 → field_validator.
    codes: Optional[List[str]] = Field(default=None, max_length=50,
                                       description="自定义候选代码 (单股/watchlist 模式, 非空则覆盖 pool, ≤50 只)")
    hold_days: int = Field(default=3, ge=1, le=60, description="mock 持有期 (1-60)")
    factor_name: str = Field(default="rev_20", pattern=r"^rev_20$",
                             description="候选排序因子 (第一版 regex 限定 rev_20, 后续放开)")
    stop_loss_pct: Optional[float] = Field(default=None, gt=0, le=0.5,
                                            description="持仓亏损止损阈, None=不触发")
    take_profit_pct: Optional[float] = Field(default=None, gt=0, le=2.0,
                                              description="持仓盈利止盈阈, None=不触发")

    @field_validator("codes")
    @classmethod
    def _codes_format(cls, v):
        """每个元素必须 ^(SH|SZ|BJ)\\d{6}$ — 兜底前端 (前端只做"非空"校验)."""
        if v is None:
            return v
        pat = re.compile(r"^(SH|SZ|BJ)\d{6}$")
        bad = [c for c in v if not (isinstance(c, str) and pat.match(c))]
        if bad:
            raise ValueError(
                f"codes 含非法格式 (要求 ^(SH|SZ|BJ)\\d{{6}}$): {bad[:5]}"
                f"{' ...' if len(bad) > 5 else ''}")
        return v


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


# ──────────────────────── Realtime Watch (盯盘) singleton ────────────────────────
#
# A **module-level** single WatchLoop + its background asyncio.Task. Module-level
# (not a build_app closure) so tests can inject a stub via ``server._watch_loop``
# and so the loop survives across requests within one ``fa serve`` process. Only
# one watcher runs at a time (the desktop UI has a single 盯盘 panel) — /watch/start
# replaces any prior loop.
_watch_loop: Any = None              # the live WatchLoop (or stub); None = stopped
_watch_task: "Optional[asyncio.Task]" = None   # background loop.run() task


def _watch_running() -> bool:
    """True iff a watch loop is registered and not stopped."""
    loop = _watch_loop
    return loop is not None and not getattr(loop, "stopped", False)


def _watch_items_view() -> list:
    """Serialise the current loop's items to ``[{code, avg_cost, stop_loss}]``."""
    loop = _watch_loop
    if loop is None:
        return []
    out = []
    for it in getattr(loop, "items", []) or []:
        out.append({"code": getattr(it, "code", None),
                    "avg_cost": getattr(it, "avg_cost", None),
                    "stop_loss": getattr(it, "stop_loss", None)})
    return out


async def _watch_stop_current() -> None:
    """Stop the live loop (if any) and cancel its background task. Idempotent."""
    global _watch_loop, _watch_task
    loop, task = _watch_loop, _watch_task
    if loop is not None:
        try:
            loop.stop()
        except Exception:  # noqa: BLE001 — stop must never raise
            pass
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _watch_task = None


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


# /etf/board snapshot cache (sina whole-market ETF list). Short TTL: the board
# is a browse/rank surface, not a tick stream — 30s avoids refetching on every
# tab open while staying fresh enough.
_ETF_BOARD_CACHE = {"ts": 0.0, "payload": None}
_ETF_BOARD_TTL = 30.0


# SP-W2C 冷启动修复: workflow 节点 import 走 lazy. 首次 GET /workflow/nodes
# 才触发 mock_nodes (3) + factors.workflow_nodes (5 真节点) 的 @node side-effect.
# build_app() 起飞期不卡 (factor zoo 完整 import 含 442 alpha 需 ~5s).
_WORKFLOW_NODES_LOADED = False
# 同样的 lazy: demo seed 写盘 (glob + json.dump) 也搬到首次 GET /workflow 列表时,
# 让 build_app() 完全不碰文件系统 (除了 mkdir 兜底). 用路径集合 (而非单 bool)
# 让测试 (每 case 新 tmp_path) 能各自 seed 自己的 defs_root.
_WORKFLOW_DEMO_SEEDED_ROOTS: "set[str]" = set()


def _ensure_workflow_nodes_loaded() -> None:
    """触发 mock_nodes + factors.workflow_nodes 的注册 side-effect.

    幂等: 多次调用只第一次真 import. 进程内单飞.
    """
    global _WORKFLOW_NODES_LOADED
    if _WORKFLOW_NODES_LOADED:
        return
    try:
        from financial_analyst.workflow import mock_nodes  # noqa: F401
    except Exception:
        pass
    try:
        from financial_analyst.factors import workflow_nodes  # noqa: F401
    except Exception:
        pass
    _WORKFLOW_NODES_LOADED = True


def _ensure_demo_seed(workflow_defs_root) -> None:
    """首次访问 ``/workflow`` 列表时检查并写 demo seed.

    幂等: 进程内按 root 单飞 + 文件系统层 ``if list(*.json)`` 双兜底.
    任何异常静默吞 (server 已起飞, 不让 demo seed 失败拖死端点).

    注: 测试每 case 新 tmp_path → 用 set 记 seeded 的 root, 让多 build_app() 实例
    各自能 seed 自己的 defs_root.
    """
    if workflow_defs_root is None:
        return
    root_key = str(workflow_defs_root)
    if root_key in _WORKFLOW_DEMO_SEEDED_ROOTS:
        return
    try:
        _demo_files = list(workflow_defs_root.glob("*.json"))
        if not _demo_files:
            _demo_seed = {
                "id": "demo-mock-3-nodes",
                "name": "Demo: 3 mock 节点链路",
                "version": 1,
                "nodes": [
                    {
                        "id": "universe",
                        "type": "data.constant_universe",
                        "params": {"codes": ["SH600519", "SH600036"]},
                    },
                    {
                        "id": "zeros",
                        "type": "factor.zeros",
                        "inputs": {"universe": "universe.output"},
                    },
                    {
                        "id": "rowcount",
                        "type": "eval.row_count",
                        "inputs": {"frame": "zeros.output"},
                    },
                ],
                "edges": [],
                "meta": {
                    "seed": True,
                    "description": "首次启动写入的 demo, 点 Run 即跑完. (3 mock 节点不接真数据, 演示用.)",
                },
            }
            (workflow_defs_root / "demo-mock-3-nodes.json").write_text(
                json.dumps(_demo_seed, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
    except Exception:
        pass
    _WORKFLOW_DEMO_SEEDED_ROOTS.add(root_key)


def build_app():
    """Construct the FastAPI app. Imported lazily by ``serve``."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import StreamingResponse, JSONResponse
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError as exc:  # pragma: no cover
        # fastapi/uvicorn are core deps as of v1.0.3, so an ImportError here
        # means the install is corrupted. Suggest re-install.
        raise RuntimeError(
            "fastapi import failed — the install looks broken.\n"
            "  pip install --force-reinstall financial-analyst\n"
            "  (or: pip install fastapi uvicorn)"
        ) from exc

    from financial_analyst.buddy.agent import BuddyAgent
    from financial_analyst.buddy.tools import get_tool
    from financial_analyst.buddy.intent import classify, label_for

    # MCP Streamable HTTP transport mounted at /mcp. The sub-app owns the
    # session manager's lifespan, but Starlette/FastAPI does NOT propagate
    # mounted sub-app lifespans automatically — we merge it into the parent
    # FastAPI lifespan so manager.run() bookends uvicorn startup/shutdown.
    from contextlib import asynccontextmanager
    from financial_analyst.mcp_http import build_mcp_http_app
    _mcp_app = build_mcp_http_app()

    @asynccontextmanager
    async def _lifespan(_app):
        async with _mcp_app.router.lifespan_context(_mcp_app):
            yield

    app = FastAPI(title="financial-analyst buddy SSE bridge", lifespan=_lifespan)

    # SP-B: 重建注册已入库的 user 炼因子 (DSL 字符串 → compile → register family='user')
    try:
        from financial_analyst.factors.forge import UserFactorStore
        UserFactorStore().register_all()
    except Exception:
        pass

    # ─── QuantFlow Phase 2: workflow store + run_log_root + demo seed ───
    # 共享 ArtifactStore + run_log_root 给所有 /workflow/* 端点用. 路径解析走
    # DataPaths (env var > yaml > user_dir > dev fallback), 测试通过设置
    # FA_WORKFLOW_DEFS_ROOT / FA_PARQUET_ROOT 注入 tmp 路径.
    #
    # SP-W2C 冷启动修复: 节点 import (mock_nodes + workflow_nodes 真节点) 走 lazy
    # path, 第一次 /workflow/nodes 才触发, build_app() 起飞期不卡. 见下方
    # ``_ensure_workflow_nodes_loaded()`` + ``_MOCK_NODES_LOADED`` 模块级 flag.
    try:
        from financial_analyst.data.paths import get_data_paths
        from financial_analyst.workflow.artifacts import ArtifactStore

        _dp = get_data_paths()
        _workflow_defs_root = _dp.workflow_defs_root
        # workflow_runs/ 落在 parquet_root.parent 旁边 (跟 workflow_defs_root 同级),
        # 让 fa init / 备份能一并整目录拷.
        _workflow_runs_root = _dp.parquet_root.parent / "workflow_store"
        _workflow_defs_root.mkdir(parents=True, exist_ok=True)
        _workflow_runs_root.mkdir(parents=True, exist_ok=True)
        _workflow_store = ArtifactStore(root=_workflow_runs_root)
        _workflow_run_log_root = _workflow_runs_root

        # SP-W2C 冷启动: demo seed 写盘搬到首次 GET /workflow 列表时 (见
        # ``_ensure_demo_seed`` + workflow_list_ep). build_app() 不碰 *.json
        # 文件系统, 让 server 起飞更快.
    except Exception:
        # 任何初始化失败不阻断 server 起飞; /workflow/* 端点自己再 raise.
        _workflow_defs_root = None  # type: ignore[assignment]
        _workflow_runs_root = None  # type: ignore[assignment]
        _workflow_store = None  # type: ignore[assignment]
        _workflow_run_log_root = None  # type: ignore[assignment]

    app.mount("/mcp", _mcp_app)
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
        from financial_analyst.memory_paths import default_memory_root
        import datetime as _dt
        f = default_memory_root() / "_shared" / "conversation_lessons.md"
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
    async def data_refresh(
        skip_5min: bool = False,
        include_f10: bool = False,
        f10_universe: str = "csi500",
        include_concepts: bool = False,
        include_financial: bool = False,
        include_stock_basic: bool = False,
        include_northbound: bool = False,
        include_fund_flow: bool = False,
        fund_flow_lmt: int = 120,
        include_margin: bool = False,
        include_lockup: bool = False,
        include_corporate_actions: bool = False,
        include_ths_hot: bool = False,
        include_announcements: bool = False,
        announcements_page_size: int = 30,
    ):
        """Trigger an incremental data refresh — equivalent to `fa data update`.

        Spawns ``python -m financial_analyst.cli data update`` as a detached
        subprocess so the request returns immediately. The UI polls
        ``/data/status`` afterward to see when the timestamps move.

        Query params mirror the CLI flags 1:1, so UI button rows can map
        directly to ``fa data update`` invocations:

          * ``skip_5min`` → ``--skip-5min`` (day-only, faster)
          * ``include_f10`` → ``--include-f10`` (zero-token, ~30 min csi500)
          * ``f10_universe`` → ``--f10-universe csi300|csi500|csi800|all``
          * ``include_concepts`` → ``--include-concepts`` (needs adata pkg)
          * ``include_financial`` → ``--include-financial`` (needs FA_TUSHARE_TOKEN env)
          * ``include_stock_basic`` → ``--include-stock-basic`` (needs FA_TUSHARE_TOKEN env)

        Tushare token is **not** accepted via query (would leak in URLs/logs).
        The subprocess inherits ``FA_TUSHARE_TOKEN`` from the server env — set
        it in ``.env`` or before launching ``fa start``.
        """
        import subprocess
        import sys
        from pathlib import Path
        cmd = [sys.executable, "-m", "financial_analyst.cli",
               "data", "update"]
        if skip_5min:
            cmd.append("--skip-5min")
        if include_f10:
            cmd.extend(["--include-f10", "--f10-universe", f10_universe])
        if include_concepts:
            cmd.append("--include-concepts")
        if include_financial:
            cmd.append("--include-financial")
        if include_stock_basic:
            cmd.append("--include-stock-basic")
        if include_northbound:
            cmd.append("--include-northbound")
        if include_fund_flow:
            cmd.extend(["--include-fund-flow", "--fund-flow-lmt", str(fund_flow_lmt)])
        if include_margin:
            cmd.append("--include-margin")
        if include_lockup:
            cmd.append("--include-lockup")
        if include_corporate_actions:
            cmd.append("--include-corporate-actions")
        if include_ths_hot:
            cmd.append("--include-ths-hot")
        if include_announcements:
            cmd.extend(["--include-announcements",
                        "--announcements-page-size", str(announcements_page_size)])
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

    @app.get("/etf/board")
    async def etf_board():
        """Whole-market ETF ranking board (sina source, 30s TTL cache).
        Rows: {code, name, price, change_pct, amount(元), volume(股)} desc by amount."""
        import time as _t
        import financial_analyst.data.etf_board as _eb
        now = _t.monotonic()
        cached = _ETF_BOARD_CACHE["payload"]
        if cached is not None and (now - _ETF_BOARD_CACHE["ts"]) < _ETF_BOARD_TTL:
            return JSONResponse(cached)
        try:
            rows = await asyncio.to_thread(_eb.etf_market_board)
        except Exception as exc:  # noqa
            return JSONResponse({"ok": False, "error": f"{exc.__class__.__name__}: {exc}"})
        payload = {"ok": True, "n": len(rows), "rows": rows}
        _ETF_BOARD_CACHE["ts"] = now
        _ETF_BOARD_CACHE["payload"] = payload
        return JSONResponse(payload)

    @app.get("/concepts")
    async def concepts():
        """List 同花顺 concept boards for the UI 板块 picker.

        Reads ``concept_ths_index.parquet`` written by
        ``fa data update --include-concepts``. Returns
        ``{available: bool, boards: [{name, code}]}``.
        """
        def _load():
            import pandas as pd
            from financial_analyst.data.paths import get_data_paths
            path = get_data_paths().parquet_root / "concept_ths_index.parquet"
            if not path.exists():
                return None
            df = pd.read_parquet(path)
            name_col = next((c for c in df.columns if "name" in c.lower()), df.columns[0])
            code_col = next((c for c in df.columns if "code" in c.lower()), None)
            if code_col == name_col:  # no real name column → avoid echoing the code as the name
                code_col = None
            out = []
            for _, row in df.iterrows():
                nm = str(row[name_col]).strip()
                if nm and nm.lower() != "nan":
                    out.append({"name": nm, "code": str(row[code_col]) if code_col else None})
            return out

        try:
            boards = await asyncio.to_thread(_load)
        except Exception as exc:
            return JSONResponse({"available": False, "boards": [], "error": str(exc)}, status_code=200)
        if boards is None:
            return JSONResponse({"available": False, "boards": []})
        return JSONResponse({"available": True, "boards": boards})

    @app.post("/upload")
    async def upload(file: UploadFile = FastAPIFile(...)):
        """Extract text from an uploaded doc for the composer 上传 button.

        csv/txt/md → utf-8 decode; pdf → pypdf page text. Caps raw size at
        10 MB and extracted text at 20k chars. Returns {id, name, chars, truncated, text}.
        """
        MAX_BYTES = 10 * 1024 * 1024
        MAX_CHARS = 20_000
        name = file.filename or "upload"
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext not in (".txt", ".md", ".csv", ".pdf"):
            return JSONResponse({"error": f"不支持的文件类型: {ext or '无扩展名'}"}, status_code=400)
        # pre-read gate: reject by the parsed part size before buffering the whole body
        if file.size and file.size > MAX_BYTES:
            return JSONResponse({"error": f"文件过大 (>{MAX_BYTES // 1024 // 1024}MB)"}, status_code=413)
        raw = await file.read()
        if len(raw) > MAX_BYTES:
            return JSONResponse({"error": f"文件过大 (>{MAX_BYTES // 1024 // 1024}MB)"}, status_code=413)

        def _extract():
            if ext == ".pdf":
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(raw))
                return "\n".join((p.extract_text() or "") for p in reader.pages)
            return raw.decode("utf-8", errors="replace")

        try:
            text = await asyncio.to_thread(_extract)
        except Exception as exc:
            return JSONResponse({"error": f"解析失败: {exc}"}, status_code=422)
        truncated = len(text) > MAX_CHARS
        text = text[:MAX_CHARS]
        return JSONResponse({"id": uuid.uuid4().hex, "name": name, "chars": len(text),
                             "truncated": truncated, "text": text})

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

    # ════════════════════════════════════════════════════════════════════
    # SP-C.1 直连因子 REST 端点 (不走 agent /run 循环, 给量化工作台 UI 直接喂数据)
    #
    # 共同纪律:
    #   * 业务结构化失败 (status=empty_universe/load_error/compute_error/
    #     fit_error/too_few_factors; forge compile_ok=False) → HTTP 200 + body
    #     带 status/error (前端按 status 渲染, 与 agent 工具一致)。
    #   * 内部未预期异常 → try/except → 500 + {error} (不泄栈)。
    #   * 所有 dataclass 经 dataclasses.asdict → _jsonable (NaN/Inf→null) 再返回,
    #     保证浏览器 JSON.parse 不挂。
    #   * factor_report / forge_factor / compose_factors 都经各自 home 模块的
    #     属性访问调用 (而非 from-import 绑定), 便于测试 monkeypatch。
    # ════════════════════════════════════════════════════════════════════
    from dataclasses import asdict as _asdict

    @app.post("/factor/report")
    async def factor_report_ep(req: ReportReq):
        """单因子评测报告 (IC / 分位 / 多空组合 / 特征)。

        ``expr_or_name`` 可以是注册 alpha 名 (如 alpha019) 或白名单表达式
        (如 ``rank(-delta(close,5))``)。默认小池 csi300_active 求秒级。
        """
        try:
            from financial_analyst.factors.eval import EvalConfig
            from financial_analyst.factors import eval as _eval_mod
            cfg = EvalConfig(universe=req.universe, freq=req.freq,
                             start=req.start, end=req.end)
            rpt = _eval_mod.factor_report(req.expr_or_name, cfg)
            if req.archive and getattr(rpt, "status", "") == "ok":
                try:
                    from financial_analyst.factors.research import (
                        ResearchArchive, record_from_report)
                    ResearchArchive().append(record_from_report(rpt, note=req.note))
                except Exception:
                    pass  # 归档失败不拖垮报告主体
            return _jsonable(_asdict(rpt))
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/factor/forge")
    def factor_forge_ep(req: ForgeReq):
        # 同步 path op (FastAPI 在线程池跑) — forge_factor 的默认 complete_fn 用
        # asyncio.run()，必须脱离请求事件循环, 否则 "asyncio.run() cannot be
        # called from a running event loop"。见 test_forge_endpoint_runs_off_event_loop。
        """炼因子: 自然语言想法 → 截面因子表达式 (+ 可选快测 IC)。

        ``forge_factor`` 永不抛 (失败落在 ForgeResult.error/compile_ok)。
        当 compile_ok 且 quick_eval=True 时附带一个 quick IC dict
        (任何快测异常 → quick_ic=None, 不影响主结果)。
        """
        try:
            from financial_analyst.factors import forge as _forge_mod
            fr = _forge_mod.forge_factor(req.idea)
            body = _asdict(fr)
            if fr.compile_ok and req.quick_eval:
                try:
                    from financial_analyst.buddy.tools import _quick_ic
                    from financial_analyst.factors.zoo.expr import compile_factor
                    body["quick_ic"] = _quick_ic(
                        compile_factor(fr.expr), req.universe,
                        "2024-01-01", "2024-12-31")
                except Exception:
                    body["quick_ic"] = None
            return _jsonable(body)
        except Exception as exc:  # forge_factor never raises; guard anyway
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/factor/compose")
    def factor_compose_ep(req: ComposeReq):
        """多因子合成: N(>=2) 个成员 → 综合分, OOS 评测 + 成员对比 → verdict。

        ``method``: equal / ic_weighted / linear / lgbm。成员数 <2 → 400。
        interpret=True 时附 LLM 研判 (interpret_compose 用 asyncio.run, 故端点为 sync def)。
        """
        if len(req.members) < 2:
            return JSONResponse(
                status_code=400,
                content={"error": "members 至少 2 个", "status": "too_few_factors"},
            )
        try:
            from financial_analyst.factors.eval import EvalConfig
            from financial_analyst.factors import compose as _compose_mod
            from financial_analyst.factors.compose import advisor as _advisor_mod
            cfg = EvalConfig(universe=req.universe, freq=req.freq)
            res = _compose_mod.compose_factors(
                req.members, cfg, method=req.method, train_frac=req.train_frac)
            if req.archive and getattr(res, "status", "") == "ok":
                try:
                    from financial_analyst.factors.research import (
                        ResearchArchive, record_from_compose)
                    ResearchArchive().append(record_from_compose(res, note=req.note))
                except Exception:
                    pass
            body = _jsonable(_asdict(res))
            if req.interpret and getattr(res, "status", "") == "ok":
                try:
                    body["interpretation"] = _advisor_mod.interpret_compose(res)
                except Exception:
                    body["interpretation"] = ""
            return body
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/factor/compose/advise")
    def factor_compose_advise_ep(req: AdviseReq):
        """输入顾问: 自然语言目标 → 合成配方 (成员表达式 + 方法 + 理由)。

        sync def — compose_advisor 用 asyncio.run, 须脱离事件循环 (同 forge 端点)。
        """
        try:
            from financial_analyst.factors.compose import advisor as _advisor_mod
            rec = _advisor_mod.compose_advisor(req.goal)
            return _jsonable(_asdict(rec))
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/factor/archive")
    async def factor_archive_ep(target: str = "", compare: str = ""):
        """研究档案 (评测运行日志)。

          * ``?compare=r0001,r0002`` → 两次运行的指标 diff (dict)。
          * ``?target=<因子名>``      → 该 target 的运行历史 (按时间序)。
          * 无参数                     → 全部运行列表。
        """
        try:
            from financial_analyst.factors.research import ResearchArchive
            arch = ResearchArchive()
            if "," in compare:
                ids = compare.split(",", 1)
                return _jsonable(arch.compare(ids[0].strip(), ids[1].strip()))
            if target:
                return {"history": [_jsonable(_asdict(r)) for r in arch.history(target)]}
            return {"runs": [_jsonable(_asdict(r)) for r in arch.list()]}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/factor/bench")
    async def factor_bench_ep(universe: str = "csi300_active", family: str = "alpha101",
                              since: str = "2024-01-01", until: str = "2024-12-31",
                              max_codes: int = 120):
        """批量跑一个 family 的全部 alpha 的截面 IC → 喂因子库横条。

        ``rows`` 每行: name / family / ic / rank_ic / ir / rank_ir /
        hit_rate / state。缺数据 → 空 rows。
        """
        try:
            # 触发 zoo/__init__ 注册各 family 的 @register (alpha101 等)。
            import financial_analyst.factors.zoo  # noqa: F401
            from financial_analyst.data import universe as _univ_mod
            from financial_analyst.data import loader_factory as _lf_mod
            from financial_analyst.factors.zoo.panel import PanelData
            from financial_analyst.factors.zoo.bench_runner import run_bench

            codes = _univ_mod.resolve_universe_codes(universe) or []
            codes = codes[: max(1, int(max_codes))]
            if not codes:
                return {"rows": []}
            loader = _lf_mod.get_default_loader()
            try:
                from financial_analyst.data.loaders.industry import (
                    IndustryLoader, industry_map_path)
                ind = IndustryLoader() if industry_map_path().exists() else None
            except Exception:
                ind = None
            from financial_analyst.factors.zoo.panel_cache import load_panel_cached
            panel = load_panel_cached(loader, codes, since, until,
                                      freq="day", industry_loader=ind)
            df = run_bench(panel, family=family, fwd_days=5)
            return {"rows": _jsonable(df.to_dict(orient="records"))}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/factor/list")
    async def factor_list_ep(family: str = ""):
        """列出可用因子: ``registered`` (内置 alpha) + ``user`` (炼因子入库)。"""
        try:
            # 触发 zoo/__init__ 注册各 family, 否则 list_alphas 可能为空。
            import financial_analyst.factors.zoo  # noqa: F401
            from financial_analyst.factors.zoo.registry import list_alphas
            from financial_analyst.factors.forge import UserFactorStore
            registered = [{"name": s.name, "family": s.family,
                           "formula": s.formula_text}
                          for s in list_alphas(family or None)]
            user = UserFactorStore().list()
            return {"registered": registered, "user": _jsonable(user)}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/factor/save")
    async def factor_save_ep(req: SaveReq):
        """把炼出的因子入库 (持久化 + 注册，立即可被 /factor/report 评 + 出现在 /factor/list)。"""
        try:
            from financial_analyst.factors.forge import UserFactorStore
            entry = UserFactorStore().add({
                "name": req.name, "family": "user", "expr": req.expr,
                "description": req.description, "parsed": req.parsed, "kpis": req.kpis})
            return _jsonable(entry)
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/factor/event")
    async def factor_event_ep(req: EventReq):
        """事件研究: 触发表达式/名 → 事件后各 horizon 前向收益 (原始+市场调整) + CAR + 逐年。

        触发型因子 (金叉/突破/连续/放量) 专用; 截面打分因子用 /factor/report。
        archive 字段 v1 暂忽略 (档案 schema 是 report/compose; 事件 metrics 不同)。
        """
        try:
            from financial_analyst.factors.eval import EvalConfig
            from financial_analyst.factors import eval as _eval_mod
            cfg = EvalConfig(universe=req.universe, start=req.start, end=req.end)
            hs = tuple(int(x) for x in req.horizons) or (1, 5, 10, 20)
            rpt = _eval_mod.event_report(req.expr_or_name, cfg, horizons=hs)
            return _jsonable(_asdict(rpt))
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    # ════════════════════════════════════════════════════════════════════
    # QuantFlow Phase 2 — Workflow Lab REST endpoints
    #
    # 9 个端点 + SSE 流, 给前端 (quant.jsx Workflow Lab) 直接调.
    # 共享 _workflow_store (ArtifactStore) + _workflow_run_log_root + DataPaths
    # 解析的 _workflow_defs_root, 在 build_app 顶部已初始化 + demo seed.
    #
    # 错误纪律 (同 /factor/* 端点):
    #   * 业务错误 (wf_id 找不到, run_id 找不到) → 404 + {error}
    #   * Pydantic 校验失败 → 422 (FastAPI 自动)
    #   * 内部异常 → 500 + {error} (不泄栈)
    #
    # 工作流初始化失败 (_workflow_store is None) → 503 不能用.
    # ════════════════════════════════════════════════════════════════════
    def _workflow_unavailable() -> Any:
        """Workflow 子系统未初始化时返 503."""
        return JSONResponse(
            status_code=503,
            content={"error": "Workflow subsystem not initialized (DataPaths / NodeRegistry 失败), 检查 server 启动日志"},
        )

    @app.get("/workflow/nodes")
    async def workflow_nodes_ep():
        """列 NodeRegistry 全部节点 + 每节点的 params_model JSON Schema.

        前端用这个构造工具栏 + 参数表单 (AutoForm 读 params_schema 渲染 input).
        SP-W2A 额外返 ``group`` + ``tag`` 字段 (前端按 group 分组工具栏, Copilot
        按 tag 过滤候选).

        SP-W2C: lazy import workflow 节点 — 首次访问才触发 ``mock_nodes`` +
        ``factors.workflow_nodes`` 注册 side-effect, build_app() 起飞期不卡.
        """
        if _workflow_store is None:
            return _workflow_unavailable()
        try:
            _ensure_workflow_nodes_loaded()
            from financial_analyst.workflow.registry import NodeRegistry
            nodes = []
            for type_key, reg in NodeRegistry.list().items():
                schema = {}
                if reg.params_model is not None:
                    try:
                        schema = reg.params_model.model_json_schema()
                    except Exception:
                        schema = {}
                outputs_schema = {}
                if reg.outputs_model is not None:
                    try:
                        outputs_schema = reg.outputs_model.model_json_schema()
                    except Exception:
                        outputs_schema = {}
                nodes.append({
                    "type": type_key,
                    "description": (reg.meta or {}).get("description", ""),
                    "params_schema": schema,
                    "outputs_schema": outputs_schema,
                    "risk": reg.risk,
                    "pit": reg.pit,
                    "group": reg.group,
                    "tag": list(reg.tag or []),
                })
            # 类型名按字典序排, 让 UI 排列稳定 (不影响功能, 只是用户体验).
            nodes.sort(key=lambda n: n["type"])
            return {"nodes": _jsonable(nodes)}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow/nodes/by-group")
    async def workflow_nodes_by_group_ep(group: str = ""):
        """按 group 过滤节点 (前端工具栏分组用).

        SP-W2A: group 为空时返全部节点. group='factor' 只返 factor.* 系列等.
        """
        if _workflow_store is None:
            return _workflow_unavailable()
        try:
            _ensure_workflow_nodes_loaded()
            from financial_analyst.workflow.registry import NodeRegistry
            regs = (NodeRegistry.list_by_group(group)
                    if group else list(NodeRegistry.list().values()))
            nodes = []
            for reg in regs:
                schema = {}
                if reg.params_model is not None:
                    try:
                        schema = reg.params_model.model_json_schema()
                    except Exception:
                        schema = {}
                outputs_schema = {}
                if reg.outputs_model is not None:
                    try:
                        outputs_schema = reg.outputs_model.model_json_schema()
                    except Exception:
                        outputs_schema = {}
                nodes.append({
                    "type": reg.type,
                    "description": (reg.meta or {}).get("description", ""),
                    "params_schema": schema,
                    "outputs_schema": outputs_schema,
                    "risk": reg.risk,
                    "pit": reg.pit,
                    "group": reg.group,
                    "tag": list(reg.tag or []),
                })
            nodes.sort(key=lambda n: n["type"])
            return {"nodes": _jsonable(nodes), "group": group}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow/factors/registry")
    async def workflow_factors_registry_ep():
        """442 内置 alpha + user_xxx 炼因子的名/简介索引.

        SP-W2A: 给 Copilot 上下文 + UI 下拉用. 返
        ``{registered: [...], user: [...]}`` 结构与 /factor/list 兼容,
        但每条多带 ``description`` (供 Copilot 引用).
        """
        try:
            import financial_analyst.factors.zoo  # noqa: F401 — trigger registration
            from financial_analyst.factors.zoo.registry import list_alphas
            from financial_analyst.factors.forge import UserFactorStore

            registered = []
            for s in list_alphas(None):
                registered.append({
                    "name": s.name,
                    "family": s.family,
                    "description": s.description,
                    "formula": s.formula_text,
                })
            user = UserFactorStore().list()
            return {"registered": _jsonable(registered), "user": _jsonable(user)}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    # ════════════════════════════════════════════════════════════════════
    # SP-W2B Workflow Copilot — NL → workflow JSON 草案 (SSE 流)
    # ════════════════════════════════════════════════════════════════════
    @app.post("/workflow/copilot/draft")
    async def workflow_copilot_draft_ep(req: CopilotDraftReq):
        """SSE 流: 收上下文 + 调 LLM + 推 thought/draft/done/error 事件.

        事件协议:
          - ``thought {text}`` — LLM 推理过程片段 (v1 一次性吐完 raw response)
          - ``draft {workflow_json, cited_experiences, risk_flags, used_factors}``
          - ``done {}``  — 终止
          - ``error {message}`` — 任意阶段异常

        前端 Copilot 栏调这个: 用户点 [Go] → fetch POST 拿 stream → 监听事件 →
        ``draft`` 来了后给 [✓ 用这个] 按钮, 点了把 workflow_json 加到画板 currentWorkflow.
        """
        if _workflow_store is None:
            return _workflow_unavailable()
        # 触发节点注册 side-effect, 让 collect_context 能拿到 NodeRegistry 全表
        _ensure_workflow_nodes_loaded()
        try:
            from financial_analyst.workflow.copilot import stream_draft
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"copilot import 失败: {type(exc).__name__}: {exc}"},
            )

        async def stream():
            try:
                q = await stream_draft(
                    goal=req.goal, universe=req.universe, freq=req.freq,
                )
                while True:
                    kind, data = await q.get()
                    if kind == "__end__":
                        break
                    yield _sse(kind, **data)
            except Exception as e:
                yield _sse("error", message=f"{type(e).__name__}: {e}")

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/workflow/create")
    async def workflow_create_ep(req: Dict[str, Any]):
        """校验 + 落盘新 workflow JSON, 返 wf_id.

        请求体 = Workflow 字段 dict (id 可缺省, 服务端自动塞 wf_id). 服务端用
        Workflow.model_validate(req) 强 schema 校验; 失败 → 422.
        """
        if _workflow_store is None or _workflow_defs_root is None:
            return _workflow_unavailable()
        try:
            from financial_analyst.workflow.schema import Workflow

            # wf_id: uuid4 前 12 字符. 用户可能传 id, 我们一律覆盖, 防客户端伪造冲突.
            wf_id = uuid.uuid4().hex[:12]
            body = dict(req or {})
            body["id"] = wf_id
            # name 缺省 → 同 id (Workflow.name min_length=1)
            if not body.get("name"):
                body["name"] = wf_id
            wf = Workflow.model_validate(body)
            # 落盘 (model_dump_json 让 Enum / NaN 都规范)
            (_workflow_defs_root / f"{wf_id}.json").write_text(
                wf.model_dump_json(indent=2), encoding="utf-8",
            )
            return {"wf_id": wf_id}
        except Exception as exc:
            # Pydantic ValidationError 不是 4xx (return 直接), 但用户体验上"参数错"
            # 应是 400/422. FastAPI 自动 handler 只挂请求体 = Pydantic model 的情况;
            # 我们这里入参是 Dict[str, Any], 错只能手抓.
            from pydantic import ValidationError
            if isinstance(exc, ValidationError):
                return JSONResponse(
                    status_code=422,
                    content={"error": f"Workflow schema 校验失败: {exc}"},
                )
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow")
    async def workflow_list_ep():
        """列所有 workflow defs, mtime desc, 返 {workflows: [{wf_id, name, mtime, node_count}]}.

        SP-W2C: 首次访问触发 demo seed 写盘 (从 build_app 搬过来, 让 server 起飞期不碰盘).
        """
        if _workflow_defs_root is None:
            return _workflow_unavailable()
        try:
            _ensure_demo_seed(_workflow_defs_root)
            out: list[dict] = []
            for p in _workflow_defs_root.glob("*.json"):
                try:
                    body = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                out.append({
                    "wf_id": body.get("id", p.stem),
                    "name": body.get("name", p.stem),
                    "mtime": p.stat().st_mtime,
                    "node_count": len(body.get("nodes", []) or []),
                })
            out.sort(key=lambda x: x["mtime"], reverse=True)
            return {"workflows": _jsonable(out)}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/workflow/{wf_id}/run")
    async def workflow_run_ep(wf_id: str):
        """异步启 WorkflowRunner (asyncio.to_thread), 返 {run_id}.

        实际执行由 WorkflowRunner 同步跑 (节点已是同步函数), 在 thread pool 跑
        让 FastAPI 事件循环不被堵. SSE 流由独立端点 /workflow/runs/{run_id}/stream
        提供 (浏览器 EventSource 是 GET, 不能 POST).
        """
        if _workflow_store is None or _workflow_run_log_root is None or _workflow_defs_root is None:
            return _workflow_unavailable()
        if "/" in wf_id or ".." in wf_id:
            return JSONResponse(status_code=400, content={"error": "非法 wf_id"})
        wf_path = _workflow_defs_root / f"{wf_id}.json"
        if not wf_path.is_file():
            return JSONResponse(
                status_code=404,
                content={"error": f"Workflow {wf_id!r} 不存在"},
            )
        try:
            # SP-W2C 冷启动: 保证 mock_nodes + workflow_nodes 真节点都已注册,
            # 否则 runner.run() 会因 NodeRegistry.get(n.type) miss 抛 NodeNotFoundError.
            _ensure_workflow_nodes_loaded()

            from financial_analyst.workflow.runner import WorkflowRunner
            from financial_analyst.workflow.schema import Workflow

            wf_dict = json.loads(wf_path.read_text(encoding="utf-8"))
            workflow = Workflow.model_validate(wf_dict)
            run_id = uuid.uuid4().hex[:12]

            runner = WorkflowRunner(
                store=_workflow_store, run_log_root=_workflow_run_log_root,
            )

            # 起 background task. asyncio.to_thread 让同步 runner.run() 跑在线程池里,
            # 不堵当前请求事件循环 (SSE 端点用 tail polling 看 run_log.jsonl).
            # 不需要 await 拿结果 — 客户端去 SSE 流看进度.
            asyncio.create_task(asyncio.to_thread(runner.run, workflow, run_id=run_id))
            return {"run_id": run_id}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow/runs/{run_id}/stream")
    async def workflow_run_stream_ep(run_id: str):
        """SSE 流: tail run_log.jsonl, 推 node_start / node_done / workflow_done 事件.

        实现: 每 200ms 比 os.path.getsize, 文件长大则 seek 读新行解析 NodeRun,
        翻译成前端期望的事件形状. NodeRun.status (RUNNING / SUCCESS / FAILED /
        SKIPPED) → node_start (RUNNING) / node_done (SUCCESS/FAILED/SKIPPED).

        终止条件: 等到 workflow_done 事件 (聚合所有节点最终态后推) 或 30 秒无新事件 + 已有节点 done 推过.
        """
        if _workflow_run_log_root is None:
            return _workflow_unavailable()
        if "/" in run_id or ".." in run_id:
            return JSONResponse(status_code=400, content={"error": "非法 run_id"})

        log_path = _workflow_run_log_root / "workflow_runs" / run_id / "run_log.jsonl"
        wf_json_path = _workflow_run_log_root / "workflow_runs" / run_id / "workflow.json"

        async def stream():
            from financial_analyst.workflow.schema import NodeStatus
            import time as _time
            last_size = 0
            offset = 0
            sent_workflow_done = False
            # 节点状态跟踪: 推过 done 的 node_id 集合; 收齐时合成 workflow_done
            done_status: dict[str, str] = {}
            # 期望节点数 — 从 workflow.json 读 (若已落盘)
            n_expected: Optional[int] = None
            # SSE 心跳: 防客户端代理超时切连. 用 keep-alive 注释 frame.
            last_heartbeat = _time.monotonic()
            # 整体超时上限 (秒): workflow.json 都没落盘 + 5s 内还没 run_log → 放弃
            start_wall = _time.monotonic()
            max_wall = 60.0
            # node_start 索引顺序追踪 (前端要 idx, n)
            seen_start: list[str] = []

            while True:
                # workflow.json 落盘后才能知道 n_expected; runner 启动时立刻写, 故几乎瞬间可见.
                if n_expected is None and wf_json_path.exists():
                    try:
                        wf_doc = json.loads(wf_json_path.read_text(encoding="utf-8"))
                        n_expected = len(wf_doc.get("nodes") or [])
                    except Exception:
                        n_expected = None

                # run_log.jsonl tail 实现 — 比 size, 长大则 seek + 读后缀
                if log_path.exists():
                    try:
                        size = log_path.stat().st_size
                        if size > last_size:
                            with log_path.open("rb") as fh:
                                fh.seek(last_size)
                                chunk = fh.read(size - last_size)
                            last_size = size
                            # 解析新增的 JSONL 行 (可能包含 partial 最后一行 — 简单忽略,
                            # 下次轮询补上). Phase 0 一次 append 一行 + flush, 实际不会 partial.
                            text = chunk.decode("utf-8", errors="replace")
                            for line in text.splitlines():
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    rec = json.loads(line)
                                except Exception:
                                    continue
                                status = rec.get("status", "")
                                node_id = rec.get("node_id", "")
                                node_type = rec.get("node_type", "")
                                if status == NodeStatus.RUNNING.value:
                                    if node_id not in seen_start:
                                        seen_start.append(node_id)
                                    idx = seen_start.index(node_id)
                                    yield _sse(
                                        "node_start",
                                        node_id=node_id, type=node_type,
                                        idx=idx, n=(n_expected or 0),
                                    )
                                elif status in (
                                    NodeStatus.SUCCESS.value,
                                    NodeStatus.FAILED.value,
                                    NodeStatus.SKIPPED.value,
                                ):
                                    done_status[node_id] = status
                                    yield _sse(
                                        "node_done",
                                        node_id=node_id,
                                        status=status,
                                        duration_ms=rec.get("duration_ms"),
                                        artifact_uri=rec.get("output_artifact_uri"),
                                    )
                            last_heartbeat = _time.monotonic()
                    except (OSError, IOError):
                        pass  # 文件被改名 / 一瞬间不可读 — 下次再试

                # 收齐所有节点终态 → 合成 workflow_done + 退出
                if (
                    not sent_workflow_done
                    and n_expected is not None
                    and len(done_status) >= n_expected
                ):
                    n_success = sum(1 for v in done_status.values() if v == "success")
                    n_failed = sum(1 for v in done_status.values() if v == "failed")
                    n_skipped = sum(1 for v in done_status.values() if v == "skipped")
                    overall = "success" if n_failed == 0 and n_skipped == 0 else "failed"
                    yield _sse(
                        "workflow_done",
                        run_id=run_id, status=overall,
                        n_success=n_success, n_failed=n_failed, n_skipped=n_skipped,
                    )
                    sent_workflow_done = True
                    return  # 显式 return — generator 收尾

                # 心跳 — 每 15s 一个 SSE 注释帧 (": keepalive\n\n"), 客户端无副作用
                now = _time.monotonic()
                if now - last_heartbeat >= 15.0:
                    yield ": keepalive\n\n"
                    last_heartbeat = now

                # 整体超时兜底 (workflow.json 都没出现 = runner 没起来)
                if now - start_wall >= max_wall and not sent_workflow_done:
                    yield _sse(
                        "error",
                        message=f"等待 run_id={run_id!r} 超时 ({max_wall}s)",
                    )
                    return

                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/workflow/runs/{run_id}")
    async def workflow_run_status_ep(run_id: str):
        """状态摘要 — 聚合 run_log.jsonl. 缺失 → 404."""
        if _workflow_run_log_root is None:
            return _workflow_unavailable()
        if "/" in run_id or ".." in run_id:
            return JSONResponse(status_code=400, content={"error": "非法 run_id"})
        run_dir = _workflow_run_log_root / "workflow_runs" / run_id
        log_path = run_dir / "run_log.jsonl"
        wf_path = run_dir / "workflow.json"
        if not run_dir.is_dir():
            return JSONResponse(
                status_code=404,
                content={"error": f"Run {run_id!r} 不存在"},
            )
        try:
            from financial_analyst.workflow.run_log import RunLog
            from financial_analyst.workflow.schema import NodeStatus
            log = RunLog(log_path)
            runs = log.read_all()
            # 取每个 node_id 的最末态 (用 latest_status 的等效逻辑 — 反向首遇)
            seen: dict[str, str] = {}
            for r in reversed(runs):
                if r.node_id not in seen:
                    seen[r.node_id] = r.status.value
            n_success = sum(1 for v in seen.values() if v == NodeStatus.SUCCESS.value)
            n_failed = sum(1 for v in seen.values() if v == NodeStatus.FAILED.value)
            n_skipped = sum(1 for v in seen.values() if v == NodeStatus.SKIPPED.value)
            wf_id = ""
            if wf_path.exists():
                try:
                    wf_id = json.loads(wf_path.read_text(encoding="utf-8")).get("id", "")
                except Exception:
                    pass
            # 状态: 若所有节点都已 final 且 n_failed=0 + n_skipped=0 → ok, 否则若仍 RUNNING → running.
            # n_total 取 workflow.json 里 nodes 长度 (落后 final 时 seen 可能 < n_total).
            n_total = len(seen)
            if wf_path.exists():
                try:
                    n_total = len(json.loads(wf_path.read_text(encoding="utf-8")).get("nodes", []) or [])
                except Exception:
                    pass
            if n_success == n_total and n_failed == 0 and n_skipped == 0 and n_total > 0:
                overall = "ok"
            elif n_failed > 0:
                overall = "failed"
            elif n_skipped > 0:
                overall = "partial"
            elif runs:
                # 有 RUNNING 但没 final → still running
                overall = "running"
            else:
                overall = "pending"
            started_at = runs[0].started_at if runs else None
            ended_at = runs[-1].ended_at if runs else None
            return {
                "run_id": run_id,
                "wf_id": wf_id,
                "status": overall,
                "started_at": started_at,
                "ended_at": ended_at,
                "n_total": n_total,
                "n_success": n_success,
                "n_failed": n_failed,
                "n_skipped": n_skipped,
            }
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow/runs/{run_id}/logs")
    async def workflow_run_logs_ep(run_id: str):
        """返回 run_log.jsonl 全部 NodeRun 条目 (list, 写入顺序)."""
        if _workflow_run_log_root is None:
            return _workflow_unavailable()
        if "/" in run_id or ".." in run_id:
            return JSONResponse(status_code=400, content={"error": "非法 run_id"})
        log_path = _workflow_run_log_root / "workflow_runs" / run_id / "run_log.jsonl"
        if not log_path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": f"Run {run_id!r} 不存在 (run_log.jsonl 缺)"},
            )
        try:
            from financial_analyst.workflow.run_log import RunLog
            log = RunLog(log_path)
            return {"logs": _jsonable([r.model_dump(mode="json") for r in log.read_all()])}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow/runs/{run_id}/artifacts/{node_id}")
    async def workflow_run_artifact_ep(run_id: str, node_id: str):
        """ArtifactStore.read(run_id, node_id, 'output') → JSON.

        DataFrame 输出走 ``df.to_dict(orient='records')`` → list of dict, NaN/Inf
        经 ``_jsonable`` 转 null. 缺失 → 404.
        """
        if _workflow_store is None:
            return _workflow_unavailable()
        if "/" in run_id or ".." in run_id or "/" in node_id or ".." in node_id:
            return JSONResponse(status_code=400, content={"error": "非法 run_id / node_id"})
        try:
            import pandas as _pd
            payload = _workflow_store.read(run_id, node_id, "output")
            if isinstance(payload, _pd.DataFrame):
                # to_dict(records) 让前端能直接 map 渲染 (而非 columnar dict).
                # NaN/Inf → None 走 _jsonable.
                return {
                    "kind": "dataframe",
                    "shape": [int(payload.shape[0]), int(payload.shape[1])],
                    "columns": list(payload.columns),
                    "records": _jsonable(payload.to_dict(orient="records")),
                }
            return {"kind": "json", "value": _jsonable(payload)}
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content={"error": f"Artifact ({run_id!r}, {node_id!r}) 不存在: {exc}"},
            )
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.get("/workflow/runs")
    async def workflow_runs_list_ep(limit: int = 20):
        """列最近 N (默认 20) runs, 扫 workflow_runs/*/ 按 mtime desc."""
        if _workflow_run_log_root is None:
            return _workflow_unavailable()
        try:
            runs_dir = _workflow_run_log_root / "workflow_runs"
            if not runs_dir.is_dir():
                return {"runs": []}
            out: list[dict] = []
            for p in runs_dir.iterdir():
                if not p.is_dir():
                    continue
                wf_path = p / "workflow.json"
                log_path = p / "run_log.jsonl"
                wf_id = ""
                if wf_path.exists():
                    try:
                        wf_id = json.loads(wf_path.read_text(encoding="utf-8")).get("id", "")
                    except Exception:
                        pass
                out.append({
                    "run_id": p.name,
                    "wf_id": wf_id,
                    "mtime": p.stat().st_mtime,
                    "has_logs": log_path.exists(),
                })
            out.sort(key=lambda x: x["mtime"], reverse=True)
            limit = max(1, min(int(limit), 200))
            return {"runs": _jsonable(out[:limit])}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    # 路由顺序注意: /workflow/{wf_id} 是 catch-all, 必须放在 /workflow/runs +
    # /workflow/runs/* 之后, 否则 FastAPI 会把 "runs" 当 wf_id 匹配, /workflow/runs
    # 永远 404. 同样 /workflow/{wf_id}/run + /workflow/runs/{run_id}/... 也必须排在
    # 这个 catch-all 前 (FastAPI 是按注册顺序匹配, 不是最长前缀).
    @app.get("/workflow/{wf_id}")
    async def workflow_get_ep(wf_id: str):
        """按 wf_id 读回 workflow JSON. 缺失 → 404.

        SP-W2C: demo seed 也走 lazy path — 直接 GET /workflow/demo-mock-3-nodes
        在还没列过 /workflow 时也应能拿到, 这里兜底触发一次 ``_ensure_demo_seed``.
        """
        if _workflow_defs_root is None:
            return _workflow_unavailable()
        # 路径形状校验防穿越 (uuid4 前 12 字符是 hex, 不应含 / 或 ..)
        if "/" in wf_id or ".." in wf_id or wf_id in ("", "."):
            return JSONResponse(status_code=400, content={"error": "非法 wf_id"})
        _ensure_demo_seed(_workflow_defs_root)
        path = _workflow_defs_root / f"{wf_id}.json"
        if not path.is_file():
            return JSONResponse(
                status_code=404,
                content={"error": f"Workflow {wf_id!r} 不存在"},
            )
        try:
            return _jsonable(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )


    # ════════════════════════════════════════════════════════════════════
    # P5 Agent 回测 REST 端点 (跑 P2 BacktestRunner, PIT-safe; 仿 /factor/* 纪律)
    #   * run() 全程仅 1 个 await(agent.decide), 其余是同步阻塞 bin 读, mock 的
    #     decide 还无 await → 绝不能 asyncio.create_task 在事件循环里跑(会冻住
    #     uvicorn, 饿死前端轮询 GET)。仿 /factor/forge: 工作线程内 asyncio.run。
    #   * run_id → 进程内有界 dict (保留最近 _BT_MAX 条, 弹最旧 done/error)。
    #     单进程内存存储 (uvicorn 无 --workers), 重启即丢, 不持久化, 不去重缓存。
    #   * mock 模式注入确定性 stub agent (0 次 LLM, 不依赖 DASHSCOPE key)。
    #   * real 模式包 asyncio.wait_for 总超时, 避免 hang 死在 running。
    #   * 结果经 _jsonable(NaN/Inf→null) 再返回; 错误终态返 HTTP 200+status=error
    #     (轮询语义里 error 是正常终态, 让前端 q() 读到中文 error)。
    # ════════════════════════════════════════════════════════════════════
    import uuid as _uuid
    import time as _time
    _BT_RUNS: "OrderedDict[str, dict]" = OrderedDict()  # run_id -> rec
    _BT_MAX = 24                  # 注册表上限 (有界淘汰防内存泄漏)
    _BT_MAX_RUNNING = 2           # 并发 running 软上限 (防线程/内存耗尽)
    _BT_REAL_TIMEOUT = 600.0      # real 模式单 run 总超时 (秒)

    def _bt_purge():
        # 超上限时弹出最旧的非 running 项 (running 永不丢)
        while len(_BT_RUNS) > _BT_MAX:
            for k, v in list(_BT_RUNS.items()):
                if v["status"] != "running":
                    _BT_RUNS.pop(k, None)
                    break
            else:
                break  # 全在 running, 不强删

    @app.post("/backtest/run")
    async def backtest_run_ep(req: BacktestRunReq):
        # ---- 同步参数校验 (立即拒, 不进线程) ----
        if req.mode not in ("mock", "real"):
            return JSONResponse(status_code=400, content={
                "error": f"mode must be mock|real, got {req.mode}",
                "status": "bad_request"})
        if req.init_cash <= 0:
            return JSONResponse(status_code=400, content={
                "error": "init_cash must be > 0", "status": "bad_request"})
        if req.candidate_topn < 1:
            return JSONResponse(status_code=400, content={
                "error": "candidate_topn must be >= 1", "status": "bad_request"})
        # pool / factor_name 白名单由 BacktestRunReq Field(pattern=) 在 body 解析期拦 → 422
        # (旧版本曾在此处 if-block 校验返 400, 现已下沉到 model 层)
        for label, val in (("start", req.start), ("end", req.end)):
            if val is not None:
                try:
                    import pandas as _pd
                    _pd.Timestamp(val)
                except Exception:
                    return JSONResponse(status_code=400, content={
                        "error": f"invalid {label} date: {val}",
                        "status": "bad_request"})
        if req.start and req.end:
            import pandas as _pd
            if _pd.Timestamp(req.start) > _pd.Timestamp(req.end):
                return JSONResponse(status_code=400, content={
                    "error": "start > end", "status": "bad_request"})
        n_running = sum(1 for r in _BT_RUNS.values() if r["status"] == "running")
        if n_running >= _BT_MAX_RUNNING:
            return JSONResponse(status_code=429, content={
                "error": f"too many running backtests ({n_running})，请稍候",
                "status": "too_many_running"})

        run_id = "bt_" + _uuid.uuid4().hex[:8]
        _BT_RUNS[run_id] = {"status": "running", "mode": req.mode,
                            "result": None, "error": None,
                            "params": req.model_dump(), "created_at": _time.time()}
        _bt_purge()

        def _run_sync():
            # 工作线程: 脱离主事件循环跑 coroutine (同 /factor/forge 的 asyncio.run)
            import asyncio as _aio
            from financial_analyst.buddy.backtest_run import run_backtest
            if req.mode == "real":
                async def _capped():
                    return await _aio.wait_for(run_backtest(req),
                                               timeout=_BT_REAL_TIMEOUT)
                return _aio.run(_capped())
            return _aio.run(run_backtest(req))

        async def _job():
            try:
                result = await asyncio.to_thread(_run_sync)   # 阻塞跑在线程池
                _BT_RUNS[run_id]["result"] = result
                _BT_RUNS[run_id]["status"] = "done"
            except asyncio.TimeoutError:
                _BT_RUNS[run_id]["error"] = "timeout: 回测超过总时长上限"
                _BT_RUNS[run_id]["status"] = "error"
            except Exception as exc:
                _BT_RUNS[run_id]["error"] = f"{type(exc).__name__}: {exc}"
                _BT_RUNS[run_id]["status"] = "error"

        asyncio.create_task(_job())   # 只调度一个"await to_thread"的轻协程, 不冻循环
        return {"run_id": run_id, "status": "running", "mode": req.mode}

    @app.get("/backtest/result/{run_id}")
    async def backtest_result_ep(run_id: str):
        rec = _BT_RUNS.get(run_id)
        if rec is None:
            return JSONResponse(status_code=404, content={
                "error": f"unknown run_id {run_id}", "status": "not_found"})
        if rec["status"] == "running":
            return {"status": "running", "run_id": run_id}
        if rec["status"] == "error":
            # 轮询语义: error 是正常终态 → HTTP 200, 让前端 q() 读到中文 error
            return {"status": "error", "run_id": run_id,
                    "error": rec["error"], "mode": rec["mode"]}
        # done → 已是 _jsonable 后的 dict
        body = dict(rec["result"])
        body["status"] = "done"
        body["run_id"] = run_id
        return body

    # ════════════════════════════════════════════════════════════════════
    # Realtime Watch (盯盘) — start/stop/status + SSE stream + ack + item ops
    #
    # Drives the single module-level WatchLoop singleton (``server._watch_loop``).
    # The loop runs as a background asyncio.Task (loop.run()); /watch/stream
    # drains its event queue over SSE; /watch/ack flips a rec's user_action;
    # /watch/item add/remove mutates the live watchlist. Only ONE watcher runs at
    # a time (the UI has a single 盯盘 panel) — /watch/start replaces any prior.
    # ════════════════════════════════════════════════════════════════════

    def _watch_items_from_req(raw_items: list) -> list:
        """Validate + normalize an incoming items list → list[WatchItem]."""
        from financial_analyst.watch.models import WatchItem
        from financial_analyst.buddy.tools import normalize_code
        items = []
        seen = set()
        for raw in raw_items or []:
            wi = raw if isinstance(raw, WatchItemReq) else WatchItemReq(**raw)
            code = normalize_code(wi.code)
            if code in seen:          # dedup within the start payload
                continue
            seen.add(code)
            items.append(WatchItem(code=code, avg_cost=wi.avg_cost,
                                   stop_loss=wi.stop_loss))
        return items

    @app.post("/watch/start")
    async def watch_start(body: WatchStartReq):
        """Start (or replace) the盯盘 loop over ``items``.

        Builds a real :class:`WatchFeed` + :class:`WatchAgent` (lazy imports, no
        network until the first tick) and a :class:`WatchLoop`, then launches
        ``loop.run()`` as a background task. Any previously running loop is
        stopped first. Returns ``{ok, running, n_items}``.
        """
        global _watch_loop, _watch_task
        items = _watch_items_from_req(body.items)
        if not items:
            return JSONResponse({"ok": False, "reason": "items 不能为空"},
                                status_code=400)
        try:
            # WatchLoop referenced via the module so tests can monkeypatch it.
            from financial_analyst.watch import loop as _watch_mod
            from financial_analyst.watch.loop import WatchLoopConfig
            from financial_analyst.watch.feed import WatchFeed
            from financial_analyst.watch.agent import WatchAgent

            # stop any prior watcher before replacing it
            await _watch_stop_current()

            cfg_kw: Dict[str, Any] = {}
            if body.tick_seconds is not None:
                cfg_kw["tick_seconds"] = body.tick_seconds
            if body.cooldown_minutes is not None:
                cfg_kw["cooldown_minutes"] = body.cooldown_minutes
            if body.global_llm_cap_per_session is not None:
                cfg_kw["global_llm_cap_per_session"] = body.global_llm_cap_per_session
            config = WatchLoopConfig(**cfg_kw) if cfg_kw else WatchLoopConfig()

            # 实时新闻流 (用户选定的输入之一): eastmoney 7x24 快讯 → 按股票过滤.
            # opencli 不可用时返回 None → 新闻通道静默关闭 (loop 照常跑行情+触发).
            news_provider = None
            try:
                from financial_analyst.watch.news import make_default_news_provider
                news_provider = make_default_news_provider()
            except Exception:  # noqa: BLE001 — 新闻是增强通道, 绝不拖垮启动
                news_provider = None
            # 节假日感知交易日闸; 日历缺失时 loop 内部回退到周一~周五.
            is_trading_day = None
            try:
                from financial_analyst.watch.calendar import make_market_open_check
                is_trading_day = make_market_open_check()
            except Exception:  # noqa: BLE001
                is_trading_day = None

            # 负向事件预警 (B1): 读 tdx_f10_warnings_latest.parquet; 缺文件→{} 静默关.
            warnings_provider = None
            try:
                from financial_analyst.watch.signals import load_negative_warnings
                warnings_provider = load_negative_warnings
            except Exception:  # noqa: BLE001
                warnings_provider = None

            # 量能 regime (B2): RegimeProvider 读日线 close+换手 (loader, 缓存) 算
            # super_distr/distr/tail_surge → 走 advisor. 构造失败→None 静默关.
            regime_provider = None
            try:
                from financial_analyst.watch.signals import RegimeProvider
                regime_provider = RegimeProvider()
            except Exception:  # noqa: BLE001
                regime_provider = None

            loop = _watch_mod.WatchLoop(
                items=items, feed=WatchFeed(), agent=WatchAgent(), config=config,
                news_provider=news_provider, is_trading_day=is_trading_day,
                warnings_provider=warnings_provider, regime_provider=regime_provider)
            task = asyncio.create_task(loop.run())
            _watch_loop = loop
            _watch_task = task
            return JSONResponse({"ok": True, "running": True,
                                 "n_items": len(items)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                status_code=500)

    @app.post("/watch/stop")
    async def watch_stop():
        """Stop the running盯盘 loop (idempotent). Returns ``{ok, running: False}``."""
        global _watch_loop
        await _watch_stop_current()
        _watch_loop = None
        return JSONResponse({"ok": True, "running": False})

    @app.get("/watch/status")
    async def watch_status():
        """Current盯盘 state: running flag, item list, tick/LLM counters + 当前 cfg."""
        loop = _watch_loop
        items = _watch_items_view()
        cfg = getattr(loop, "cfg", None) if loop else None
        return JSONResponse({
            "ok": True,
            "running": _watch_running(),
            "n_items": len(items),
            "items": items,
            "tick_count": int(getattr(loop, "tick_count", 0)) if loop else 0,
            "llm_calls_made": int(getattr(loop, "llm_calls_made", 0)) if loop else 0,
            # P0.2 新增 (让前端显示当前 cfg, 不只默认值)
            "tick_seconds": float(getattr(cfg, "tick_seconds", 60)) if cfg else 60.0,
            "cooldown_minutes": int(getattr(cfg, "cooldown_minutes", 15)) if cfg else 15,
            "global_llm_cap_per_session": int(getattr(cfg, "global_llm_cap_per_session", 20)) if cfg else 20,
        })

    @app.get("/watch/bars")
    async def watch_bars(code: str, n: int = 240):
        """Historical 5min K线 for the realtime chart (蜡烛历史回放).

        Reuses the running loop's :class:`WatchFeed` if present, else a transient
        one (closed after). Any error → ``ok: False`` + empty bars (HTTP 200) so
        the chart degrades to '等待数据' rather than throwing a 500 at the UI.
        Returns ``{ok, code, n, bars:[{open,high,low,close,vol,trade_date}]}`` —
        vol already in 手 (the feed converts pytdx 股 → 手).
        """
        from financial_analyst.buddy.tools import normalize_code
        try:
            c = normalize_code(code)
        except Exception:  # noqa: BLE001
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "bars": [],
                                 "reason": "code 为空"})
        n = max(1, min(int(n or 240), 480))

        def _num(v: Any) -> Optional[float]:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if f != f else f          # NaN → None

        loop = _watch_loop
        feed = getattr(loop, "feed", None)
        transient = False
        try:
            if feed is None:
                from financial_analyst.watch.feed import WatchFeed
                feed = WatchFeed()
                transient = True
            df = await asyncio.to_thread(feed.bars5, c, n)
            bars = []
            if df is not None and len(df) > 0:
                for rec in df.to_dict("records"):
                    bars.append({
                        "open": _num(rec.get("open")),
                        "high": _num(rec.get("high")),
                        "low": _num(rec.get("low")),
                        "close": _num(rec.get("close")),
                        "vol": _num(rec.get("vol")),
                        "trade_date": (str(rec.get("trade_date"))
                                       if rec.get("trade_date") is not None else None),
                    })
            return JSONResponse({"ok": True, "code": c, "n": len(bars), "bars": bars})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "code": c, "bars": [],
                                 "reason": f"{type(exc).__name__}: {exc}"})
        finally:
            if transient and feed is not None:
                try:
                    feed.close()
                except Exception:  # noqa: BLE001
                    pass

    @app.get("/watch/stream")
    async def watch_stream(max_events: Optional[int] = None):
        """SSE stream of live watch events (``quote_update`` / ``recommendation``).

        Drains the loop's event queue and yields one SSE frame per event (the
        frame's ``event:`` is the dict's ``type``). A periodic comment heartbeat
        keeps the connection alive and makes the generator wake often enough that
        a client disconnect (the ``with`` block closing) cancels it gracefully,
        and that it stops on its own once the watcher is stopped. 404 if no
        watcher is running.

        ``max_events`` bounds the number of *data* frames emitted then returns —
        used by tests (Starlette's ``TestClient`` cannot consume an unbounded
        stream); production omits it for an open-ended stream.
        """
        loop = _watch_loop
        if loop is None:
            return JSONResponse({"ok": False, "reason": "watch not running"},
                                status_code=404)
        cap = int(max_events) if max_events is not None else None

        async def stream():
            sent = 0
            # flush anything already queued (events emitted before subscribe).
            for ev in loop.drain():
                kind = ev.pop("type", "message")
                yield _sse(kind, **ev)
                sent += 1
                if cap is not None and sent >= cap:
                    return
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(loop._queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        # stop streaming once the loop is gone/stopped …
                        if _watch_loop is not loop or getattr(loop, "stopped", False):
                            break
                        yield ": keepalive\n\n"   # … else SSE comment heartbeat
                        continue
                    kind = ev.pop("type", "message")
                    yield _sse(kind, **ev)
                    sent += 1
                    if cap is not None and sent >= cap:
                        return
            except asyncio.CancelledError:  # client disconnected → exit quietly
                raise

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/watch/ack")
    async def watch_ack(body: WatchAckReq):
        """人工确认一条推荐: stamp ``user_action`` onto the persisted rec.

        ``user_action`` must be ``confirm`` or ``ignore``. Returns ``{ok}`` where
        ``ok`` reflects whether a matching row was updated.
        """
        ua = (body.user_action or "").strip().lower()
        if ua not in ("confirm", "ignore"):
            return JSONResponse(
                {"ok": False, "reason": "user_action 必须是 confirm / ignore"},
                status_code=400)
        try:
            from financial_analyst.watch import store as _watch_store
            from financial_analyst.buddy.tools import normalize_code
            code = normalize_code(body.code)
            ok = _watch_store.ack_rec(None, ts=body.ts, code=code, user_action=ua)
            return JSONResponse({"ok": bool(ok), "ts": body.ts, "code": code,
                                 "user_action": ua})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                status_code=500)

    @app.post("/watch/item")
    async def watch_item(body: WatchItemOpReq):
        """Add / remove one watched item on the *running* loop.

        ``op`` = ``add`` (idempotent — duplicate code is a no-op) or ``remove``.
        400 if no watcher is running or ``op`` is invalid. Returns ``{ok, n_items}``.
        """
        op = (body.op or "").strip().lower()
        if op not in ("add", "remove"):
            return JSONResponse({"ok": False, "reason": "op 必须是 add / remove"},
                                status_code=400)
        loop = _watch_loop
        if loop is None:
            return JSONResponse({"ok": False, "reason": "watch not running"},
                                status_code=400)
        from financial_analyst.watch.models import WatchItem
        from financial_analyst.buddy.tools import normalize_code
        code = normalize_code(body.code)
        items = list(getattr(loop, "items", []) or [])
        if op == "add":
            if not any(getattr(it, "code", None) == code for it in items):
                items.append(WatchItem(code=code, avg_cost=body.avg_cost,
                                       stop_loss=body.stop_loss))
        else:  # remove
            items = [it for it in items if getattr(it, "code", None) != code]
        loop.items = items
        return JSONResponse({"ok": True, "op": op, "code": code,
                             "n_items": len(items)})

    @app.post("/watch/outcome/backfill")
    async def watch_outcome_backfill():
        """复盘回填: 给到期推荐打 T+1/T+5 outcome 分 (盘后批量, 离线).

        Runs :func:`watch.outcome.backfill_outcomes` in a thread (reads the rec log
        + day bins, re-scores new/pending recs). Returns ``{ok, n_total, n_scored,
        n_pending}``. 500 on error (loader / parquet failure).
        """
        try:
            from financial_analyst.watch.outcome import backfill_outcomes
            df = await asyncio.to_thread(backfill_outcomes)
            n_total = int(len(df))
            if n_total:
                final = df["verdict"].astype(str).isin(("correct", "partial", "wrong"))
                n_scored = int(final.sum())
            else:
                n_scored = 0
            return JSONResponse({"ok": True, "n_total": n_total, "n_scored": n_scored,
                                 "n_pending": n_total - n_scored})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                                status_code=500)

    @app.get("/watch/hitrate")
    async def watch_hitrate():
        """命中率看板: aggregate the outcome log → overall + per-trigger + per-action.

        Returns ``{ok, overall, by_trigger, by_action}`` (each bucket: n / correct /
        partial / wrong / win_rate / avg_return_t1 / avg_return_t5). pending excluded.
        """
        try:
            from financial_analyst.watch.outcome import compute_hitrate, load_outcomes
            df = await asyncio.to_thread(load_outcomes)
            h = compute_hitrate(df)
            return JSONResponse({"ok": True, **h})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                                status_code=500)

    @app.get("/watch/history")
    async def watch_history(day: Optional[str] = None, n: int = 100):
        """推荐历史: rec 日志 left-join outcome (verdict/T+1/T+5) → newest-first.

        ``day`` optional ``YYYY-MM-DD`` filter; ``n`` caps rows (1..500). Degrades to
        ``ok:False`` + empty rows (HTTP 200) on error so the history page never 500s.
        """
        try:
            from financial_analyst.watch.outcome import join_history, load_outcomes
            from financial_analyst.watch.store import load_recs
            cap = max(1, min(int(n or 100), 500))
            recs = await asyncio.to_thread(load_recs, None, day)
            outs = await asyncio.to_thread(load_outcomes)
            rows = join_history(recs, outs, cap)
            return JSONResponse({"ok": True, "n": len(rows), "rows": rows})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}",
                                 "rows": []})

    return app


def serve(host: str = "127.0.0.1", port: int = 9999) -> None:
    """Run the SSE bridge (blocking). Called by ``financial-analyst serve``."""
    try:
        import uvicorn
    except ImportError as exc:
        # uvicorn is a core dep as of v1.0.3. Missing = broken install.
        raise RuntimeError(
            "uvicorn import failed — install looks broken.\n"
            "  pip install --force-reinstall financial-analyst"
        ) from exc
    app = build_app()
    print(f"financial-analyst buddy SSE bridge → http://{host}:{port}")
    print("  POST /run  ·  POST /confirm  ·  GET /health  ·  GET /tools")
    uvicorn.run(app, host=host, port=port, log_level="warning")
