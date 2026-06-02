"""Workflow Copilot — NL → workflow JSON 草案.

Spec: docs/superpowers/specs/2026-06-02-workflow-lab-v2-design.md §SP-W2B.

3 步走:
1. ``collect_context(goal, ...)`` — 并发收集 NodeRegistry schemas / 因子库 / 经验 chunks
2. ``build_messages(goal, context)`` — 拼 system+user prompt 给 LLM
3. ``stream_draft(goal, ...)`` — async generator, yield SSE events:
   - ``("thought", {text: ...})`` × N 流式 token (注: 当前 ``LLMClient`` 无 stream API,
     v1 走非流式 ``chat()``, 一次 thought 事件吐完整 LLM raw response 给前端展示)
   - ``("draft", {workflow_json, cited_experiences, risk_flags, used_factors})`` 一次
   - ``("done", {})`` 终止
   - ``("error", {message})`` 任意阶段失败 (LLM 调用 / JSON 解析 / 验证)

注入式 LLM ``complete_fn`` (同 compose/advisor 套路) 让测试不调真 LLM. 默认走
``LLMClient.for_agent('buddy')``, env ``FA_COPILOT_LLM`` 可切 deepseek/openai.

Workflow JSON 形状契约 = ``financial_analyst.workflow.schema.Workflow`` (id 服务端
塞, 客户端拿 ``draft.workflow_json`` 直接 POST /workflow/create 即可落盘).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


# 同步签名: 输入 messages → 输出 LLM 完整文本. 注入 stub 用 (测试).
# 默认实现走 LLMClient.chat() (非流式). 之后可扩 stream-token-by-token.
CompleteFn = Callable[[List[Dict[str, Any]]], str]


@dataclass
class CopilotContext:
    """LLM 调用前收集的上下文 (节点 / 因子 / 经验). 测试可直接构造跳过 collect_context."""

    nodes: List[Dict[str, Any]] = field(default_factory=list)
    """``[{type, description, group, tag, params_schema_summary}, ...]`` — 节点目录."""

    factors: List[Dict[str, Any]] = field(default_factory=list)
    """``[{name, family, description}, ...]`` — 442 alpha + user factor 名清单."""

    knowledge_chunks: List[Dict[str, Any]] = field(default_factory=list)
    """``[{source, section, text}, ...]`` — KnowledgeIndex.search 命中的经验片段."""

    goal: str = ""
    universe_default: str = "csi300_active"
    freq_default: str = "day"


def _summarize_node(reg: Any) -> Dict[str, Any]:
    """把一个 ``RegisteredNode`` 摘要成 LLM 友好的 dict.

    ``params_schema_summary`` 是 ``{prop_name: type}`` 简化形式 (完整 JSON Schema
    塞 LLM token 太贵), 节点描述 + group/tag 留全.
    """
    type_key = getattr(reg, "type", "")
    meta = getattr(reg, "meta", {}) or {}
    desc = meta.get("description", "")
    group = getattr(reg, "group", "misc")
    tag = list(getattr(reg, "tag", []) or [])
    params_model = getattr(reg, "params_model", None)
    schema_summary: Dict[str, str] = {}
    if params_model is not None:
        try:
            schema = params_model.model_json_schema()
            props = (schema.get("properties") or {})
            for k, v in props.items():
                # 取 type / 默认值 / description
                t = v.get("type", "any")
                default = v.get("default")
                if default is not None:
                    schema_summary[k] = f"{t}={default!r}"
                else:
                    schema_summary[k] = t
        except Exception:
            pass
    return {
        "type": type_key,
        "description": desc,
        "group": group,
        "tag": tag,
        "params_schema_summary": schema_summary,
    }


def collect_context(
    goal: str,
    universe: str = "csi300_active",
    freq: str = "day",
    k_knowledge: int = 5,
    skip_knowledge: bool = False,
) -> CopilotContext:
    """收集 LLM 所需上下文 (节点 + 因子 + 经验). 同步, 失败子项静默返空.

    设计上**并不**并发 (依赖都是本地 / 无网), 顺序 invoke 即可. KnowledgeIndex
    可能初始化 chroma 慢, 用 ``skip_knowledge=True`` 测试跳过.

    Args:
        goal: 自然语言目标 (用于 KnowledgeIndex.search)
        universe: 默认 universe 名 (传给 Copilot 提示)
        freq: 默认频率
        k_knowledge: KnowledgeIndex 取 top-K
        skip_knowledge: True → 不调 KnowledgeIndex (用于纯单测/启动期)
    """
    ctx = CopilotContext(goal=goal, universe_default=universe, freq_default=freq)

    # 1. 节点目录 — 触发 lazy import + 拿 NodeRegistry 全表
    try:
        from financial_analyst.workflow.registry import NodeRegistry
        # 让 5 个真节点 + 3 mock 都注册 (server 端 _ensure_workflow_nodes_loaded
        # 已做, 但 collect_context 也可能被 server 之外的 caller 调, 自己也兜底).
        try:
            from financial_analyst.workflow import mock_nodes  # noqa: F401
        except Exception:
            pass
        try:
            from financial_analyst.factors import workflow_nodes  # noqa: F401
        except Exception:
            pass
        regs = NodeRegistry.list()
        # 过滤 group=demo (mock 节点 LLM 不该用)
        ctx.nodes = sorted(
            (_summarize_node(r) for r in regs.values() if r.group != "demo"),
            key=lambda d: d["type"],
        )
    except Exception:
        ctx.nodes = []

    # 2. 因子库 — 442 alpha + user factors
    try:
        import financial_analyst.factors.zoo  # noqa: F401
        from financial_analyst.factors.zoo.registry import list_alphas
        from financial_analyst.factors.forge import UserFactorStore

        registered = list_alphas(None)
        # 内置 alpha 多 (442), 名+family 都给, description 截短防 token 爆
        factors: List[Dict[str, Any]] = []
        for s in registered:
            factors.append({
                "name": s.name,
                "family": s.family,
                "description": (s.description or "")[:120],
            })
        user = UserFactorStore().list()
        for u in user:
            factors.append({
                "name": u.get("name", ""),
                "family": "user",
                "description": (u.get("description") or "")[:120],
            })
        ctx.factors = factors
    except Exception:
        ctx.factors = []

    # 3. 经验 chunks via KnowledgeIndex (慢, 可选跳过)
    if not skip_knowledge:
        try:
            from financial_analyst.data.knowledge_index import KnowledgeIndex
            idx = KnowledgeIndex()
            hits = idx.search(goal, k=k_knowledge)
            ctx.knowledge_chunks = [
                {
                    "source": h.source,
                    "section": h.section,
                    "text": (h.text or "")[:600],
                    "score": h.score,
                }
                for h in hits
            ]
        except Exception:
            ctx.knowledge_chunks = []

    return ctx


# ─────────── prompt 构造 ───────────

_COPILOT_SYSTEM = """你是 A 股量化工作流设计师. 任务: 把用户自然语言目标翻译成 workflow JSON.

铁律:
1. 只用上下文给的节点 (节点 type 必须精确出现在节点目录里).
2. 节点链路要合逻辑: 数据 → 因子 → 评测. 典型 4 节点链:
   data.universe → data.load_panel → factor.from_registry / factor.from_expression → eval.factor_report
3. 工作流 JSON 形状:
   {
     "name": "<简短中文名>",
     "nodes": [
       {"id": "<unique>", "type": "<exact type>", "params": {...}, "inputs": {...}}
     ]
   }
   - inputs 引用上游用 "<upstream_id>.output" 字符串
   - data.universe 的输出名是 "output", 给 data.load_panel 的 inputs.codes 用
   - data.load_panel 输出给 factor.* 用 inputs.panel
   - factor.* 输出给 eval.factor_report 用 inputs.alpha + inputs.panel (panel 来自 load_panel)
4. 引用经验时, 在 cited_experiences 数组里给 [{source, section}], 不要在 workflow_json 里编造引用.
5. 已证伪因子 (经验中标 "失效" / "禁用" / "复现失败" 等) 不要用, 进 risk_flags.
6. **rev_20 不存在**, financial-analyst 仓库 alpha 命名是 alpha001-alpha101, gtja001-gtja191, qlib_*, user_*. 反转因子默认用 alpha001 / gtja001 / 或 DSL 表达式 'rank(-delta(close, 20))'.
7. **alpha003 在小池/短窗口下数值不稳**, 不要选作默认.

输出**严格 JSON** (no markdown), 形状:
{
  "workflow_json": {... 工作流 ...},
  "cited_experiences": [{"source": "factor_insights.md", "section": "rev_20 历史"}],
  "risk_flags": ["短描述风险点"],
  "used_factors": ["alpha001", ...]
}
"""


def build_messages(ctx: CopilotContext) -> List[Dict[str, str]]:
    """拼 system + user prompt. user 消息含 ctx 压缩成 JSON dump."""
    # 限制因子清单 token: 截前 30 项. 用户 goal 含关键词时 LLM 会问名字而不是逐个看
    factor_names_brief = [{"name": f["name"], "family": f["family"]}
                          for f in ctx.factors[:80]]
    # 知识 chunks 已 truncated 到 600 字; 5 个 chunk 共 ~3000 字, 可接受
    user_payload = {
        "goal": ctx.goal,
        "default_universe": ctx.universe_default,
        "default_freq": ctx.freq_default,
        "available_nodes": ctx.nodes,
        "available_factors_brief": factor_names_brief,
        "n_factors_total": len(ctx.factors),
        "experience_chunks": ctx.knowledge_chunks,
    }
    return [
        {"role": "system", "content": _COPILOT_SYSTEM},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
    ]


# ─────────── LLM 后端 ───────────


def _complete_default(messages: List[Dict[str, Any]]) -> str:
    """默认走 ``LLMClient.for_agent('buddy')`` 的同步调 (asyncio.run).

    env ``FA_COPILOT_LLM=deepseek`` / ``openai`` 切换 agent (用 with_overrides
    不太合适, 因为 agent_overrides 在 config 里, 这里直接给 provider 名).
    """
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    provider_override = (os.environ.get("FA_COPILOT_LLM") or "").strip().lower()
    if provider_override and provider_override != client.provider:
        # 用 with_overrides 切换 provider (复用 config 里的 base_url + key)
        # 模型默认走 client.config['providers'][provider]['models'][0]
        cfg = client.config.get("providers", {}).get(provider_override, {})
        models = cfg.get("models") or []
        if models:
            client = client.with_overrides(provider=provider_override, model=models[0])
    resp = asyncio.run(client.chat(messages, response_format={"type": "json_object"},
                                    temperature=0.2))
    return resp["choices"][0]["message"]["content"]


# ─────────── 输出校验 ───────────


def parse_and_validate(content: str, ctx: CopilotContext) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """LLM raw → ``(parsed_dict, error)``.

    校验:
    - 合法 JSON
    - 含 workflow_json (含 nodes, len>=1)
    - 所有 nodes[].type 都在 ctx.nodes 的 type 集合里
    - cited_experiences / risk_flags / used_factors 缺省补空 list

    返回:
    - ``(payload, None)`` — 成功
    - ``(None, "<error msg>")`` — JSON 不合法 / 形状不对 / 节点不存在
    """
    if not content or not content.strip():
        return None, "LLM 返回空内容"
    # 容错: LLM 偶尔会带 markdown fence
    text = content.strip()
    if text.startswith("```"):
        # 剥 fence
        m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"LLM 输出非合法 JSON: {e}"
    if not isinstance(obj, dict):
        return None, f"LLM 输出非 dict (实际 {type(obj).__name__})"
    wf = obj.get("workflow_json")
    if not isinstance(wf, dict):
        return None, "缺 workflow_json 字段 (或非 dict)"
    nodes = wf.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        return None, "workflow_json.nodes 必须是非空 list"
    valid_types = {n["type"] for n in ctx.nodes}
    bad_types = [n.get("type") for n in nodes if n.get("type") not in valid_types]
    if bad_types:
        return None, f"workflow 引用了未注册节点 type: {bad_types}"
    # 缺省字段补
    payload = {
        "workflow_json": wf,
        "cited_experiences": obj.get("cited_experiences") or [],
        "risk_flags": obj.get("risk_flags") or [],
        "used_factors": obj.get("used_factors") or [],
    }
    return payload, None


# ─────────── SSE async generator ───────────


SSEEvent = Tuple[str, Dict[str, Any]]


async def stream_draft(
    goal: str,
    universe: str = "csi300_active",
    freq: str = "day",
    complete_fn: Optional[CompleteFn] = None,
    context: Optional[CopilotContext] = None,
    skip_knowledge: bool = False,
) -> "asyncio.Queue[SSEEvent]":
    """启动 Copilot 草稿生成, 返回事件队列 (caller drain → SSE frames).

    设计成 ``asyncio.Queue`` 而非 async generator: server 端的 SSE 端点已是
    StreamingResponse + 内部生成器, 用 queue 让 LLM 调用跑在 thread (asyncio.to_thread)
    时事件能 push 进队列. 测试可直接 await ``produce_draft()`` 拿队列 drain.

    返回的 queue 终态: ``("__end__", {})`` 哨兵 (caller 见此就停)
    """
    q: "asyncio.Queue[SSEEvent]" = asyncio.Queue()
    asyncio.create_task(
        _produce_draft(q, goal, universe, freq, complete_fn, context, skip_knowledge)
    )
    return q


async def _produce_draft(
    q: "asyncio.Queue[SSEEvent]",
    goal: str,
    universe: str,
    freq: str,
    complete_fn: Optional[CompleteFn],
    context: Optional[CopilotContext],
    skip_knowledge: bool,
) -> None:
    """实际生产 SSE 事件. 任意异常都翻成 error event, 不外抛."""
    try:
        # 1. 收集上下文 (在 thread 里, KnowledgeIndex 可能阻塞)
        if context is None:
            context = await asyncio.to_thread(
                collect_context, goal, universe, freq, 5, skip_knowledge,
            )
        # 先推一条 thought 让 UI 知道在干活
        await q.put(("thought", {
            "text": (
                f"已收集 {len(context.nodes)} 节点 + {len(context.factors)} 因子 + "
                f"{len(context.knowledge_chunks)} 经验片段, 调 LLM 设计..."
            ),
        }))

        # 2. 构造 messages
        messages = build_messages(context)

        # 3. 调 LLM (sync, 跑在 thread)
        complete = complete_fn or _complete_default
        try:
            content = await asyncio.to_thread(complete, messages)
        except Exception as e:
            await q.put(("error", {"message": f"LLM 调用失败: {type(e).__name__}: {e}"}))
            await q.put(("__end__", {}))
            return

        # 4. 把 LLM raw 当一条 thought 推 (前端可截短显示)
        await q.put(("thought", {"text": content[:600]}))

        # 5. 解析 + 校验
        payload, err = parse_and_validate(content, context)
        if err is not None:
            await q.put(("error", {"message": err}))
            await q.put(("__end__", {}))
            return

        # 6. 推 draft
        await q.put(("draft", payload))
        await q.put(("done", {}))
    except Exception as e:
        await q.put(("error", {"message": f"{type(e).__name__}: {e}"}))
    finally:
        await q.put(("__end__", {}))


__all__ = [
    "CopilotContext",
    "CompleteFn",
    "collect_context",
    "build_messages",
    "parse_and_validate",
    "stream_draft",
]
