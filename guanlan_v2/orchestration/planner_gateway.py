# -*- coding: utf-8 -*-
"""Production planner :class:`ModelGateway` on the pinned ``planner`` seat.

This is the *only* concrete, network-capable :class:`~guanlan_v2.orchestration.
worker.ModelGateway` in the orchestration kernel. It is the service port the
dynamic-Planner loop (:func:`guanlan_v2.orchestration.orchestrator.run_planner`)
invokes exactly once per generation attempt. Everything security-relevant is
enforced *before* a single provider byte is sent, and nothing is ever fabricated
on failure.

What ``invoke`` does, in order
------------------------------
1. **Byte-rehash refusal (invariant 1).** It resolves the persisted
   ``PromptAssemblyRecord`` through the injected ``payload_reader`` and rehashes
   the exact ``canonical_request_bytes`` against that record's request digest via
   the shared :func:`~guanlan_v2.orchestration.worker.verify_model_request_binding`.
   Any ref / schema / namespace / content / assembler / order / digest mismatch
   raises :class:`~guanlan_v2.orchestration.worker.PromptBindingError` **before**
   the engine client is built or called — a forged or detached prompt can never
   reach the provider.
2. **Explicit repo config path (invariant 2).** It resolves the engine
   ``LLMClient`` via ``LLMClient.for_agent(seat, config_path=<repo config/llm.yaml>)``
   — *never* ``find_config``. The engine's ``find_config`` prefers the pinned
   workspace ``G:\\financial-analyst\\config\\llm.yaml`` over the repo's, which
   would silently shadow the planner seat with a stale/foreign config; passing the
   explicit repo path is what defeats that shadow. This mirrors the
   ``guanlan_v2/screen/llm.py`` precedent verbatim.
3. **Exactly one single-shot completion (invariant 3).** It requests JSON output
   (``response_format={"type": "json_object"}``) on the ``planner`` seat and
   returns the provider text + usage in the Phase-2 :class:`ModelResult` shape.
   There is no tool loop, no second invocation and no detached-prompt entry point.
   A provider failure raises the typed :class:`PlannerModelInvocationError` (its
   ``__cause__`` preserved) which the loop maps to ``model_error`` — never a
   fabricated ``ModelResult``.
4. **No engine file is modified (invariant 4).** Only the engine's stable
   ``LLMClient`` surface is used.

Thread discipline
------------------
The provider call is **synchronous** from the caller's perspective: ``invoke``
blocks. The engine ``LLMClient.chat`` is a coroutine, so the gateway drives it on
a private, gateway-owned event loop (created lazily, reused across invokes). A
persistent loop is deliberate: the engine caches one ``httpx.AsyncClient`` per
provider in a module global, and an ``httpx`` client binds to the loop it is first
used on — driving each invoke on a fresh ``asyncio.run`` loop would raise
"Event loop is closed" on the second call. Reusing one loop keeps the cached
transport valid for the gateway's lifetime.

Because ``invoke`` blocks, any caller reached from the 9999 asyncio event loop
MUST wrap the call in :func:`asyncio.to_thread` (no synchronous HTTP inside the
loop — the standing house red line). ``invoke`` is not safe for *concurrent* calls
on one gateway instance; ``run_planner`` drives it strictly sequentially. Call
:meth:`close` (or use the gateway as a context manager) to tear the loop down.

Timeout — where the hard deadline lives
---------------------------------------
The hard per-request deadline lives in the **engine client transport**: the
``planner`` seat's ``timeout: 300`` (``config/llm.yaml``) is read by
``LLMClient.for_agent`` into ``default_timeout`` and passed by ``chat`` to the
OpenAI SDK as a per-request ``timeout`` kwarg, which overrides the httpx client's
default (see ``engine/financial_analyst/llm/client.py``). On timeout the SDK
raises ``APITimeoutError`` → after the client's bounded transient retries it is
re-raised → the gateway wraps it as :class:`PlannerModelInvocationError` → the loop
classifies ``model_error``.

Because the engine client retries transient errors (up to ``max_retries``),
per-request bounds can compound. The gateway therefore also wraps the single
completion in an outer ``asyncio.wait_for`` at ``timeout_sec + buffer`` (mirroring
``screen/llm.py``'s ``_effective_timeout``: the SDK per-request timeout fires first
with an accurate message; the outer wait is a total-wall-clock backstop that
cancels a genuinely hung call). This is the concrete resolution of the Task-4
carry: ``run_planner``'s ``attempt_timeout_sec`` is a *post-hoc* wall-clock
classifier that cannot preempt a hung synchronous call — the real, preemptive
deadline is enforced here.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from guanlan_v2.orchestration.refs import TypedPayloadRef
from guanlan_v2.orchestration.worker import (
    AssembledModelRequest,
    ModelResult,
    verify_model_request_binding,
)

__all__ = ["PlannerLLMModelGateway", "PlannerModelInvocationError"]

#: The repo-pinned deepseek config. Explicit path defeats the engine's workspace
#: ``find_config`` shadow (see module docstring + ``guanlan_v2/screen/llm.py``).
#: ``parents[2]`` == repo root (``.../guanlan_v2/orchestration/planner_gateway.py``).
REPO_LLM_CONFIG_PATH: Path = Path(__file__).resolve().parents[2] / "config" / "llm.yaml"

#: Plan synthesis temperature. deepseek-reasoner accepts (and ignores) it; kept low
#: for the rare non-reasoner fallback. JSON output is forced via ``response_format``.
_PLANNER_TEMPERATURE = 0.2

#: Outer-wait buffer (seconds) over ``timeout_sec`` so the engine transport's own
#: per-request timeout fires first with an accurate message (mirrors screen/llm.py).
_OUTER_WAIT_BUFFER_SEC = 5.0


class PlannerModelInvocationError(Exception):
    """A provider/transport failure during the single planner completion.

    Raised only *after* the request/prompt binding verified (so this never masks a
    binding refusal) and only for a genuine provider-side failure. It is a plain
    :class:`Exception` so ``run_planner``'s broad ``except Exception`` maps it to
    ``model_error``; the underlying provider exception is preserved as ``__cause__``.
    """


def _messages_from_canonical(canonical_request_bytes: bytes) -> list[dict[str, str]]:
    """Reconstruct provider chat messages from the assembler-canonical request bytes.

    The canonical bytes ARE the authorized request. Trusted instruction text
    (``system`` + ordered ``skills`` + ``guardrails``) goes in the system role; the
    full canonical channel (which carries untrusted context only as schema/digest
    *references*, never inlined bytes) is echoed verbatim in the user role so the
    exact authorized payload — and nothing else — reaches the model. Extraction is
    defensive so the gateway is not coupled to any single assembler channel schema.
    """
    text = canonical_request_bytes.decode("utf-8", "replace")
    try:
        channel = json.loads(text)
    except (ValueError, TypeError):
        channel = None

    if isinstance(channel, dict):
        parts: list[str] = []
        sys_text = channel.get("system")
        if isinstance(sys_text, str) and sys_text:
            parts.append(sys_text)
        for key in ("skills", "guardrails"):
            for item in channel.get(key) or ():
                if isinstance(item, str) and item:
                    parts.append(item)
        system_text = "\n\n".join(parts)
        user_text = json.dumps(channel, sort_keys=True, ensure_ascii=False)
    else:
        system_text = ""
        user_text = text

    messages: list[dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append(
        {
            "role": "user",
            "content": user_text + "\n\nReturn a single JSON object and nothing else.",
        }
    )
    return messages


def _extract_content(response: Any) -> str:
    """Pull the completion text out of an OpenAI/litellm-shaped response dict."""
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - shape guard
        raise PlannerModelInvocationError(
            "provider response had no choices[0].message.content"
        ) from exc
    return content if isinstance(content, str) else ""


class PlannerLLMModelGateway:
    """The production single-shot planner gateway (see module docstring).

    Conforms to the :class:`~guanlan_v2.orchestration.worker.ModelGateway` protocol
    (``invoke(request, *, prompt_assembly_ref) -> ModelResult``) so ``run_planner``
    accepts it interchangeably with the test fakes.
    """

    def __init__(
        self,
        *,
        payload_reader,
        seat: str = "planner",
        config_path: Path | None = None,
        timeout_sec: float = 300.0,
    ) -> None:
        self._reader = payload_reader
        self._seat = seat
        self._config_path = Path(config_path) if config_path is not None else REPO_LLM_CONFIG_PATH
        self._timeout_sec = float(timeout_sec)
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle ---------------------------------------------------------- #
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def close(self) -> None:
        """Tear down the gateway-owned event loop (idempotent)."""
        loop = self._loop
        self._loop = None
        if loop is not None and not loop.is_closed():
            try:
                loop.close()
            except Exception:  # pragma: no cover - defensive teardown
                pass

    def __enter__(self) -> "PlannerLLMModelGateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - GC-timing dependent
        self.close()

    # -- the single-shot invocation ---------------------------------------- #
    def invoke(
        self, request: AssembledModelRequest, *, prompt_assembly_ref: TypedPayloadRef
    ) -> ModelResult:
        # (1) rehash-refuse BEFORE any provider bytes. Raises PromptBindingError on
        #     any mismatch; nothing below runs.
        verify_model_request_binding(request, prompt_assembly_ref, reader=self._reader)

        # (2) build provider messages from the exact authorized canonical bytes.
        messages = _messages_from_canonical(request.canonical_request_bytes)

        # (3) resolve the engine client on the EXPLICIT repo config path (never the
        #     workspace find_config shadow). Imported lazily so this module loads in
        #     envs without the engine and so monkeypatching for_agent stays effective.
        from financial_analyst.llm.client import LLMClient

        client = LLMClient.for_agent(self._seat, config_path=self._config_path)

        # (4) exactly one single-shot completion, JSON requested, bounded. The hard
        #     per-request deadline lives in the engine transport (seat timeout: 300);
        #     the outer wait_for is a total-wall-clock backstop.
        loop = self._ensure_loop()
        try:
            response = loop.run_until_complete(
                asyncio.wait_for(
                    client.chat(
                        messages,
                        response_format={"type": "json_object"},
                        temperature=_PLANNER_TEMPERATURE,
                    ),
                    timeout=self._timeout_sec + _OUTER_WAIT_BUFFER_SEC,
                )
            )
        except Exception as exc:  # provider/transport/timeout -> typed model error
            raise PlannerModelInvocationError(
                f"planner provider invocation failed: {type(exc).__name__}: {exc}"
            ) from exc

        rendered_text = _extract_content(response)
        return ModelResult(
            payload=None,
            rendered_text=rendered_text,
            input_tokens=int(getattr(client, "total_prompt_tokens", 0) or 0),
            output_tokens=int(getattr(client, "total_completion_tokens", 0) or 0),
            provider=getattr(client, "provider", None),
            model=getattr(client, "model", None),
        )
