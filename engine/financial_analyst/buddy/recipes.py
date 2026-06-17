"""BuddyAgent Recipe 层 — 固化高频能力, 压幻觉 (方案 A: 确定性 recipe + 受约束综合).

对命中的高频意图, 用代码确定性编排工具序列 → 结构化 data_pack → LLM 只做带引用的
综合 → 产出 gate 校验. 未命中 → 现有自由 ``run_turn`` (零回归).

幻觉四来源 → recipe 各个堵死:
  - 调错工具/跳步/多调 → 工具序列写死在 ``Recipe.steps``, LLM 不参与选工具.
  - 编造参数 (错股票代码) → slot 在 ``resolve_slots`` 里代码解析+校验, 解析不出 → fallback.
  - 叙述没真跑的步骤 → 只有真跑的 ``tool.run()`` 结果进 pack; 综合 prompt 只给 pack.
  - 夸大/虚构结果数字 → 综合 system 禁 pack 外事实 + 每条挂 [§N]; ``gate`` 校验.

这是 ``report_v2.py`` 已验证的范式 (确定性管线 + 受约束 sub-agent + 两道 hard-fail gate),
只是搬到对话界面的实时路径.

设计纪律 (镜像 watch/signal_pack.py):
  - ``buddy.agent`` / ``buddy.tools`` 的 import 都在函数内 (recipes ← agent/tools 可能循环).
  - 工具入参名用真实 TOOL_REGISTRY 的 schema property 名 (code / target / days ...).
  - 复用 ``TurnEvent`` 的 tool_call / tool_result / text / done — 零前端/SSE 改动.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类 + 异常
# ---------------------------------------------------------------------------
@dataclass
class RecipeStep:
    """编排里的一步. ``args`` 把已解析的 slots 映射成工具 kwargs."""
    tool: str                                   # TOOL_REGISTRY 里的工具名
    args: Callable[[dict], dict]                # slots -> tool kwargs
    label: str                                  # UI chain 上的中文步骤名
    optional: bool = True                       # 失败不中断 recipe (缺这维度 pack 标 None)


@dataclass
class Recipe:
    """一条确定性工作流: 固定工具序列 + 受约束综合 + 产出 gate."""
    intent: str                                 # 对应 intent.classify() 的 key
    name: str                                   # 中文名 (UI plan label)
    # (query, context) -> slots 或 None (解析不出关键参数 → 路由器 fall back 自由循环)
    resolve_slots: Callable[[str, dict], Optional[dict]]
    steps: List[RecipeStep]
    synthesis_system: str                       # 综合 LLM 的 system (禁 pack 外事实 + 强制 [§N])
    synthesis_user_tmpl: str                    # .format(query=, pack=) 的用户消息模板
    # (answer, pack, slots) -> 违规列表 (空 = 通过)
    gate: Callable[[str, dict, dict], List[str]]


class RecipeFallback(Exception):
    """slot 解析不出关键参数时抛出 → 路由器捕获 → 落自由 run_turn."""


# ---------------------------------------------------------------------------
# pack 渲染 (给综合 LLM 看的 [§N] 编号文本)
# ---------------------------------------------------------------------------
def render_pack(pack: Dict[str, Any], steps: List[RecipeStep]) -> str:
    """把 step→ToolResult 的 pack 渲染成带 [§N] 编号的文本块.

    每个真跑过的 step 一段; 缺数据 (optional 失败 / None) 显式标 '无数据', 不留白
    让 LLM 编造. 编号顺序 = steps 顺序, 与 gate 的 [§N] 校验一致.
    """
    blocks: List[str] = []
    n = 0
    for step in steps:
        n += 1
        res = pack.get(step.tool)
        header = f"[§{n}] {step.label}"
        if res is None:
            blocks.append(f"{header}: 无数据 (该步未取到 / 跳过).")
            continue
        content = getattr(res, "content", "") or ""
        is_error = getattr(res, "is_error", False)
        if is_error:
            blocks.append(f"{header}: 取数失败 — {content.strip()}")
        else:
            blocks.append(f"{header}:\n{content.strip()}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 多轮记忆 — 从 agent 取近若干轮干净对话, 垫进综合 prompt
# (修根因: recipe 路径原先只发 [system, user], 跨轮失忆。详见 run_recipe 第 3 步)
# ---------------------------------------------------------------------------
_HISTORY_MAX_MSGS = 8        # 近 ~4 轮 user/assistant
_HISTORY_MAX_CHARS = 4000    # 注入历史总字数上限, 防上下文膨胀
_GATE_COMMENT = re.compile(r"<!--gate:.*?-->", re.S)


def recipe_history_messages(agent: Any,
                            max_msgs: int = _HISTORY_MAX_MSGS,
                            max_chars: int = _HISTORY_MAX_CHARS) -> List[Dict[str, str]]:
    """把 ``agent.messages`` 里近若干轮 **干净** 对话转成 chat messages, 供综合 LLM 看上下文。

    只保留 ``role in {user, assistant}`` 且 content 为非空字符串的轮次 —— tool 原文 / 空消息
    一律剔除 (噪声且撑爆上下文); assistant 文里的内部 ``<!--gate:...-->`` 留痕也抹掉。
    末尾按 ``max_msgs`` / ``max_chars`` 双重截断, 保留最近的。``agent`` 为 None / 无历史 → ``[]``
    (即旧行为, 零回归)。
    """
    msgs = getattr(agent, "messages", None) if agent is not None else None
    if not msgs:
        return []
    clean: List[Dict[str, str]] = []
    for m in msgs:
        role = getattr(m, "role", None)
        content = getattr(m, "content", None)
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = _GATE_COMMENT.sub("", content).strip()
        if text:
            clean.append({"role": role, "content": text})
    clean = clean[-max_msgs:]
    while len(clean) > 1 and sum(len(x["content"]) for x in clean) > max_chars:
        clean.pop(0)
    return clean


# ---------------------------------------------------------------------------
# run_recipe — 确定性执行 + 受约束综合 + gate
# ---------------------------------------------------------------------------
def _extract_answer(resp: Any) -> str:
    """从 client.chat 的 OpenAI-compat envelope 取文本 (兼容 dict 与对象两形态).

    仿 agent.py run_turn L305-311 + compact L229-231.
    """
    try:
        choice = resp["choices"][0]["message"]
    except Exception:
        return ""
    if isinstance(choice, dict):
        return (choice.get("content") or "")
    return (getattr(choice, "content", "") or "")


async def run_recipe(recipe: Recipe, query: str, context: Optional[dict],
                     client, confirm_cb=None, agent=None) -> AsyncIterator["Any"]:
    """执行一条 recipe, yield 与 run_turn 同款的 TurnEvent.

    行为 (spec run_recipe 6 步):
      1. slots = recipe.resolve_slots(query, context); None → 抛 RecipeFallback.
      2. 逐 step: tool_call → tool.run(**args) (to_thread) → tool_result;
         存 pack[step.tool]=result (失败且 optional → None 继续; 失败且非 optional → 标错继续).
      3. 一次 client.chat([synthesis_system, synthesis_user_tmpl.format(...)], tools=None, t=0.2) → answer.
      4. violations = recipe.gate(...); 非空 → 重试一次 (把 violations 塞回反馈);
         仍违规 → answer 末尾追加 '<!--gate: ...-->' (不阻断, 留痕).
      5. yield text(answer) → yield done.

    函数内 import TurnEvent / get_tool / ToolResult 避免 recipes ← agent/tools 循环.
    """
    import asyncio as _asyncio

    from financial_analyst.buddy.agent import TurnEvent
    from financial_analyst.buddy.tools import ToolResult, get_tool

    ctx = context or {}

    # 1. slot 解析 — 解析不出关键参数 → fallback 自由循环 (绝不硬编造).
    #    resolve_slots 自身抛异常也当 fallback 处理 (否则穿透 server 的 except RecipeFallback
    #    退化成 error 帧, 而非优雅落自由循环 — HOLE-RESOLVE-RAISE).
    try:
        slots = recipe.resolve_slots(query, ctx)
    except RecipeFallback:
        raise
    except Exception as exc:
        raise RecipeFallback(
            f"recipe '{recipe.intent}' slot 解析异常 → 落自由循环: {type(exc).__name__}: {exc}"
        ) from exc
    if slots is None:
        raise RecipeFallback(f"recipe '{recipe.intent}' 解析不出关键参数, 落自由循环")

    # 2. 逐 step 确定性执行
    pack: Dict[str, Any] = {}
    for step in recipe.steps:
        try:
            args = step.args(slots)
        except Exception as exc:                  # args lambda 自身炸 (理论上不该)
            log.warning("recipe %s step %s 构造参数失败: %s", recipe.intent, step.tool, exc)
            pack[step.tool] = None
            continue

        yield TurnEvent("tool_call", {"name": step.tool, "args": args})

        tool = get_tool(step.tool)
        if tool is None:
            err = f"Unknown tool: {step.tool}"
            yield TurnEvent("tool_result", {
                "name": step.tool, "content": err, "is_error": True, "side_effect": None,
            })
            pack[step.tool] = None if step.optional else ToolResult(err, is_error=True)
            continue

        # 确认门 (与 run_turn 同款): confirm_required 且有回调 → 询问
        if getattr(tool, "confirm_required", False) and confirm_cb is not None:
            ok = await confirm_cb(step.tool, args)
            if not ok:
                msg = "User declined to run this tool."
                yield TurnEvent("tool_result", {
                    "name": step.tool, "content": msg, "is_error": False, "side_effect": None,
                })
                pack[step.tool] = None
                continue

        # 同步工具跑在 worker thread, 不阻塞事件循环 (仿 run_turn L398)
        try:
            result = await _asyncio.to_thread(tool.run, **args)
        except TypeError as exc:
            result = ToolResult(f"Tool argument error: {exc}", is_error=True)
        except Exception as exc:
            result = ToolResult(f"Tool failed: {type(exc).__name__}: {exc}", is_error=True)

        yield TurnEvent("tool_result", {
            "name": step.tool, "content": result.content,
            "is_error": result.is_error, "side_effect": result.side_effect,
        })

        if result.is_error and step.optional:
            pack[step.tool] = None                # optional 失败 → 缺这维度, pack 标 None
        else:
            pack[step.tool] = result              # 成功 或 非 optional 失败 (标错进 pack)

    # 3. 受约束综合 (一次)
    pack_text = render_pack(pack, recipe.steps)
    user_msg = recipe.synthesis_user_tmpl.format(query=query, pack=pack_text)
    # 多轮记忆: 把近几轮干净对话垫在当前轮之前, recipe 路径才能延续上下文
    # (修根因: 原先只发 [system, user] → 跨轮失忆。agent=None → prior=[] 零回归)。
    prior = recipe_history_messages(agent)
    messages = (
        [{"role": "system", "content": recipe.synthesis_system}]
        + prior
        + [{"role": "user", "content": user_msg}]
    )
    try:
        resp = await client.chat(messages=messages, tools=None, temperature=0.2)
        answer = _extract_answer(resp).strip()
    except Exception as exc:
        log.warning("recipe %s 综合 LLM 调用失败: %s", recipe.intent, exc)
        yield TurnEvent("error", f"综合失败: {type(exc).__name__}: {exc}")
        yield TurnEvent("done", None)
        return

    # 4. gate 校验 — 违规重试一次 (把违规当反馈塞回, 仿 agent_output_review)
    violations = recipe.gate(answer, pack, slots)
    if violations:
        log.info("recipe %s gate 首轮违规: %s", recipe.intent, violations)
        feedback = (
            "上一版回答有以下问题, 请修正后重写 (只输出修正后的正文, 不要解释):\n"
            + "\n".join(f"- {v}" for v in violations)
        )
        retry_messages = messages + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": feedback},
        ]
        try:
            resp2 = await client.chat(messages=retry_messages, tools=None, temperature=0.2)
            answer2 = _extract_answer(resp2).strip()
            if answer2:
                answer = answer2
        except Exception as exc:
            log.warning("recipe %s gate 重试 LLM 调用失败: %s", recipe.intent, exc)
        violations = recipe.gate(answer, pack, slots)
        if violations:
            # 仍违规 → 不阻断, 末尾追加内部告警留痕
            log.warning("recipe %s gate 重试后仍违规: %s", recipe.intent, violations)
            answer = answer + "\n<!--gate: " + "; ".join(violations) + "-->"

    # 5. 写回共享历史 — 让后续轮 (下一条 recipe, 或解析失败落自由循环 run_turn) 看得到这轮。
    #    只存干净的 (query, answer), 不存工具 pack (避免上下文膨胀, 同 compact 取舍)。
    #    综合 LLM 失败已在上面提前 return → 失败轮不入历史 (守 "LLM 失败不落盘")。
    if agent is not None and answer:
        try:
            from financial_analyst.buddy.agent import Message as _Message
            agent.add_user(query)
            agent.messages.append(_Message(role="assistant", content=answer))
        except Exception as exc:  # 写回失败绝不阻断本轮产出
            log.warning("recipe %s 写回历史失败: %s", recipe.intent, exc)

    # 6. 产出
    yield TurnEvent("text", answer)
    yield TurnEvent("done", None)


# ===========================================================================
# slot 解析辅助
# ===========================================================================
_CODE_RE = re.compile(r"(?<![0-9A-Za-z])(SH|SZ|BJ)?\s*([0356789]\d{5})(?![0-9])", re.I)


def _resolve_code(query: str, context: dict) -> Optional[str]:
    """从 query / context 解析出标准化股票代码 (SH/SZ/BJ + 6 位), 取不到 → None.

    优先级:
      1. context 里显式 code (UI 点选自选时带过来).
      2. query 里的代码格式 (裸 6 位 / 带前缀 / 带后缀) → normalize_code.
      3. query 里的中文名 → IndustryLoader 缓存 名称→代码 (精确后子串).
    任何一步异常都吞掉继续往下; 全失败 → None (路由器 fallback, 绝不硬编造).
    """
    from financial_analyst.buddy.tools import normalize_code

    # 1) context 显式 code
    for key in ("code", "symbol", "ts_code"):
        v = (context or {}).get(key)
        if v:
            try:
                norm = normalize_code(str(v))
                if re.match(r"^(SH|SZ|BJ)\d{6}$", norm):
                    return norm
            except Exception:
                pass

    q = query or ""

    # 2) query 里的代码格式
    m = _CODE_RE.search(q)
    if m:
        raw = (m.group(1) or "") + m.group(2)
        try:
            norm = normalize_code(raw)
            if re.match(r"^(SH|SZ|BJ)\d{6}$", norm):
                return norm
        except Exception:
            pass

    # 3) query 里的中文名 → 行业缓存 名称→代码.
    #    HOLE-MISRESOLVE 防御: 精确匹配优先; 无精确则子串兜底**必须唯一映射到单一代码**才用,
    #    歧义 (多个不同代码) 或无命中 → None (宁可 fallback 自由循环, 绝不硬猜成错的票).
    name = _extract_name(q)
    if name and len(name) >= 2:
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader

            df = IndustryLoader()._load_cache()
            if df is not None and not df.empty and "name" in df.columns:
                names = df["name"].astype(str)
                exact = df[names == name]
                if not exact.empty:
                    hit = exact
                else:
                    cand = df[names.str.contains(re.escape(name), na=False)]
                    codes = set(cand["code"].astype(str)) if not cand.empty else set()
                    hit = cand if len(codes) == 1 else None   # 唯一才用, 歧义→None
                if hit is not None and not hit.empty:
                    norm = normalize_code(str(hit.iloc[0]["code"]))
                    if re.match(r"^(SH|SZ|BJ)\d{6}$", norm):
                        return norm
        except Exception as exc:
            log.debug("_resolve_code 名称解析失败: %s", exc)

    return None


_NAME_STRIP_RE = re.compile(
    r"(帮我|帮忙|看下|看一下|看看|分析|研判|速览|怎么样|怎么看|如何|的|这只|这支|股票|个股|最近|现在|今天|"
    r"美股|港股|大盘|市场|指数|板块|题材|行情|走势|资金|主力|新闻|消息)"
)


def _extract_name(query: str) -> Optional[str]:
    """从中文 query 里抠出可能的股票简称 (连续 2-6 个中文字).

    去掉常见动词/口水词后取最长的中文片段. 启发式, 失败由 IndustryLoader 子串兜底.
    """
    q = _NAME_STRIP_RE.sub(" ", query or "")
    cands = re.findall(r"[一-龥]{2,6}", q)
    if not cands:
        return None
    return max(cands, key=len)


# ===========================================================================
# gate 辅助
# ===========================================================================
_PLACEHOLDER_RE = re.compile(
    r"(待填|占位|placeholder|TODO|TBD|xxx|XXX|\bN/?A\b|未知数据|此处填|\{[^}]*\}|\[\s*\])"
)
_SECTION_RE = re.compile(r"\[§\s*\d+\]")
# query/answer 里出现的 6 位代码 (带可选前缀)
_ANY_CODE_RE = re.compile(r"(SH|SZ|BJ)?\s*(\d{6})", re.I)


def _codes_in(text: str) -> List[str]:
    """抽 text 里所有 6 位股票代码 (归一成裸 6 位数字串)."""
    out = []
    for m in _ANY_CODE_RE.finditer(text or ""):
        out.append(m.group(2))
    return out


def _numbers_in(text: str) -> List[str]:
    """抽 text 里所有 '数字%' 或 价格样式数字 (用于溯源轻校验)."""
    return re.findall(r"\d+(?:\.\d+)?%|\d+\.\d{2,4}", text or "")


def _num_unsourced(nums: List[str], pack_text: str) -> List[str]:
    """返回在 pack_text 里找不到来源的数字. token 边界匹配 (前后不接数字/小数点),
    防 HOLE-GATE-SUBSTRING: '35.20' 不应被 '135.20' 当作有源."""
    pt = pack_text or ""
    out: List[str] = []
    for x in nums:
        core = x.rstrip("%")
        if not re.search(r"(?<![\d.])" + re.escape(core) + r"(?![\d])", pt):
            out.append(x)
    return out


def _has_no_placeholder(answer: str) -> bool:
    return not _PLACEHOLDER_RE.search(answer or "")


# ===========================================================================
# Recipe 1: brief — 个股速览
# ===========================================================================
def _brief_resolve_slots(query: str, context: dict) -> Optional[dict]:
    code = _resolve_code(query, context)
    if not code:
        return None
    return {"code": code}


_BRIEF_SYNTH_SYSTEM = (
    "你是 A 股个股速览助手. 只能依据下面提供的真实数据块 (每块标了 [§N]) 给研判, "
    "**严禁使用数据块以外的任何事实** (不得编造价格/估值/资金/消息/产业链信息). "
    "输出要求:\n"
    "1. 分五个维度速览: 估值与行情 / 财务基本面 (ROE/营收/净利/EPS/成长/负债) / 资金流向 / 消息面 / 产业链, 缺哪个维度的数据就直接写 '无数据', 不得编造.\n"
    "2. 每条结论后挂来源编号 [§N], 指明依据哪一块.\n"
    "3. 涉及具体数字 (价格/涨跌幅/净流入额等) 必须与数据块一致, 不得改写或外推.\n"
    "4. 末尾给一句话综合倾向 (中性偏多 / 中性 / 中性偏空), 不喊单不给目标价.\n"
    "5. 用简体中文, 不要寒暄, 不要复述本提示."
)

_BRIEF_SYNTH_USER = (
    "用户问: {query}\n\n"
    "以下是为该股确定性抓取的真实数据 (每块带 [§N] 编号):\n\n"
    "{pack}\n\n"
    "请据此给出带 [§N] 引用的个股速览研判."
)


def _brief_gate(answer: str, pack: dict, slots: dict) -> List[str]:
    """brief 产出 gate:
      ① answer 里出现的股票代码必须 == slots.code (不许串到别的票);
      ② 不含占位符;
      ③ 至少 1 个 [§N];
      ④ (轻) answer 里的百分比/价格能在 pack 文本里找到 (溯源, 防编造数字).
    """
    violations: List[str] = []
    target = str(slots.get("code", "")).upper()
    target_digits = re.sub(r"^(SH|SZ|BJ)", "", target)

    # ① 代码一致 — answer 提到的任何 6 位代码都得是目标票
    for c in _codes_in(answer):
        if c != target_digits:
            violations.append(f"出现了与目标股 {target} 不一致的代码 {c}, 不得串票")
            break

    # ② 无占位符
    if not _has_no_placeholder(answer):
        violations.append("正文包含占位符/待填字样, 必须给出真实结论或明说'无数据'")

    # ③ 至少 1 个引用
    if not _SECTION_RE.search(answer or ""):
        violations.append("正文缺少 [§N] 来源引用, 每条结论必须挂数据块编号")

    # ④ 数字溯源 (轻) — answer 里的 %/价格须在 pack 文本里出现过前缀
    pack_text = render_pack(pack, _BRIEF_RECIPE.steps) if _BRIEF_RECIPE is not None else ""
    nums = _numbers_in(answer)
    if nums and pack_text:
        unsourced = _num_unsourced(nums, pack_text)
        # 容忍少量 (综合句可能出现派生数字), 超过半数无源才判违规
        if unsourced and len(unsourced) > max(1, len(nums) // 2):
            violations.append(
                f"以下数字在数据块里找不到来源, 疑似编造: {', '.join(unsourced[:5])}"
            )
    return violations


# 前向引用占位, 在 REGISTRY 构造后回填 (gate 里要读 steps 渲染 pack)
_BRIEF_RECIPE: Optional[Recipe] = None


def _build_brief_recipe() -> Recipe:
    return Recipe(
        intent="brief",
        name="个股速览",
        resolve_slots=_brief_resolve_slots,
        steps=[
            RecipeStep(
                tool="realtime_quote",
                args=lambda s: {"code": s["code"]},
                label="实时行情/估值",
                optional=False,
            ),
            RecipeStep(
                tool="financials",
                args=lambda s: {"code": s["code"], "periods": 4},
                label="财务基本面 (PIT)",
                optional=True,
            ),
            RecipeStep(
                tool="ths_fund_flow",
                args=lambda s: {"target": "gegu"},
                label="资金流向",
                optional=True,
            ),
            RecipeStep(
                tool="news_query",
                args=lambda s: {"code": s["code"], "days": 7},
                label="消息面 (近 7 日)",
                optional=True,
            ),
            RecipeStep(
                tool="chain_for",
                args=lambda s: {"code": s["code"]},
                label="产业链定位",
                optional=True,
            ),
        ],
        synthesis_system=_BRIEF_SYNTH_SYSTEM,
        synthesis_user_tmpl=_BRIEF_SYNTH_USER,
        gate=_brief_gate,
    )


# ===========================================================================
# Recipe 2: market — 大盘研判
# ===========================================================================
def _market_resolve_slots(query: str, context: dict) -> Optional[dict]:
    """全市场无参 → 恒返回 {} (永不 fallback)."""
    return {}


_MARKET_SYNTH_SYSTEM = (
    "你是 A 股大盘研判助手. 只能依据下面提供的真实市场状态数据块 (每块标了 [§N]) 研判, "
    "**严禁编造涨停家数 / 指数点位 / regime 等任何数字**, 数据块没有的就写 '数据未提供'.\n"
    "输出要求:\n"
    "1. 先给当前市场状态: regime (牛/熊/震荡) + 涨停家数 / 涨跌家数 + 主线题材, 全部引用 [§N].\n"
    "2. **必须标注数据的 as-of 日期** (数据块里给的 date / as_of), 并提醒主线雷达是月级、可能 stale.\n"
    "3. 据此给今日大盘倾向 + 仓位倾斜建议 (加仓/中性/降仓), 不预测具体点位.\n"
    "4. 每条结论挂 [§N]; 涉及的家数/比例必须与数据块一致.\n"
    "5. 用简体中文, 不要寒暄, 不要复述本提示."
)

_MARKET_SYNTH_USER = (
    "用户问: {query}\n\n"
    "以下是确定性读取的真实市场状态 (每块带 [§N] 编号):\n\n"
    "{pack}\n\n"
    "请据此给出带 [§N] 引用、标注 as-of 的大盘研判与仓位倾斜建议."
)

# as-of 日期样式 (YYYY-MM-DD) 与 'as-of'/'截至' 字样
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_ASOF_RE = re.compile(r"as[- ]?of|截至|as_of|数据日期|日期")


def _market_gate(answer: str, pack: dict, slots: dict) -> List[str]:
    """market 产出 gate:
      ① 关键数字 (涨停家数 / 比例) 必须能在 pack 文本里找到来源;
      ② 必须标了 as-of (日期 或 'as-of'/'截至' 字样);
      ③ 不含占位符;
      ④ 至少 1 个 [§N].
    """
    violations: List[str] = []
    pack_text = render_pack(pack, _MARKET_RECIPE.steps) if _MARKET_RECIPE is not None else ""

    # ④ 引用
    if not _SECTION_RE.search(answer or ""):
        violations.append("正文缺少 [§N] 来源引用, 每条结论必须挂数据块编号")

    # ③ 占位符
    if not _has_no_placeholder(answer):
        violations.append("正文包含占位符/待填字样, 必须给出真实结论或明说'数据未提供'")

    # ② as-of 标注 — 必须出现真实 YYYY-MM-DD 日期 (HOLE-GATE-ASOF-WEAK:
    #    旧版接受裸 '日期'/'截至' 字样 → LLM 写句 '数据日期见上' 即过关, 形同虚设. 现强制真日期).
    if not _DATE_RE.search(answer or ""):
        violations.append("未标注数据 as-of 真实日期 (需 YYYY-MM-DD), 大盘研判必须注明数据截至时间")

    # ① 关键数字溯源 — answer 里的整数家数/百分比须在 pack 文本里出现 (token 边界匹配)
    if pack_text:
        nums = re.findall(r"\d+(?:\.\d+)?%|\b\d{2,4}\b", answer or "")
        # 剔除日期组成数字 (年/月/日, 已被 ② as-of 校验覆盖, 避免误判)
        date_bits = set(re.findall(r"\d+", " ".join(_DATE_RE.findall(answer or ""))))
        nums = [x for x in nums if x not in date_bits]
        unsourced = _num_unsourced(nums, pack_text)
        if unsourced and len(unsourced) > max(1, len(nums) // 2):
            violations.append(
                f"以下数字在市场数据块里找不到来源, 疑似编造涨停家数/点位: {', '.join(unsourced[:5])}"
            )
    return violations


_MARKET_RECIPE: Optional[Recipe] = None


def _build_market_recipe() -> Recipe:
    return Recipe(
        intent="market",
        name="大盘研判",
        resolve_slots=_market_resolve_slots,
        steps=[
            RecipeStep(
                tool="market_status",
                args=lambda s: {},
                label="市场状态 (regime/涨停/主线)",
                optional=False,
            ),
            RecipeStep(
                tool="ths_fund_flow",
                args=lambda s: {"target": "gainian"},
                label="概念板块资金",
                optional=True,
            ),
        ],
        synthesis_system=_MARKET_SYNTH_SYSTEM,
        synthesis_user_tmpl=_MARKET_SYNTH_USER,
        gate=_market_gate,
    )


def _pack_text_from_dict(pack: dict) -> str:
    """把 pack 里所有真跑过的 ToolResult.content 拼成一段文本 (给 gate 数字溯源用).
    不依赖 recipe.steps 全局引用 — 比 render_pack(pack, _X_RECIPE.steps) 干净."""
    parts = []
    for res in pack.values():
        if res is None:
            continue
        parts.append(getattr(res, "content", "") or "")
    return "\n".join(parts)


# ===========================================================================
# Recipe 3: why_move — 驱动归因 ("X 为什么涨/跌")
# ===========================================================================
def _whymove_resolve_slots(query: str, context: dict) -> Optional[dict]:
    code = _resolve_code(query, context)
    if not code:
        return None                              # 没锁定到具体股 → fallback 自由循环
    return {"code": code}


_WHYMOVE_SYNTH_SYSTEM = (
    "你是 A 股个股驱动归因助手. 只能依据下面提供的真实数据块 (每块标了 [§N]) 解释该股今日"
    "涨跌的可能驱动, **严禁编造消息/催化/资金动向**, 数据块没有的维度写 '无明确信号'.\n"
    "输出要求:\n"
    "1. 先点出今日涨跌幅 (来自行情块), 再从三角度归因: 消息催化 / 资金主力 / 板块联动.\n"
    "2. 每条归因挂 [§N]; 没有支撑数据的角度明说 '无明确信号', 不得臆测原因.\n"
    "3. 涉及数字 (涨跌幅/净流入) 必须与数据块一致.\n"
    "4. 末尾一句话: 驱动是否可持续 (基于数据, 不喊单不预测点位).\n"
    "5. 简体中文, 不寒暄, 不复述本提示."
)
_WHYMOVE_SYNTH_USER = (
    "用户问: {query}\n\n以下是为该股确定性抓取的真实数据 (每块带 [§N] 编号):\n\n{pack}\n\n"
    "请据此做带 [§N] 引用的驱动归因."
)


def _whymove_gate(answer: str, pack: dict, slots: dict) -> List[str]:
    """why_move 产出 gate (同 brief: 代码一致 / 占位符 / 引用 / 数字溯源)."""
    violations: List[str] = []
    target = str(slots.get("code", "")).upper()
    target_digits = re.sub(r"^(SH|SZ|BJ)", "", target)
    for c in _codes_in(answer):
        if c != target_digits:
            violations.append(f"出现了与目标股 {target} 不一致的代码 {c}, 不得串票")
            break
    if not _has_no_placeholder(answer):
        violations.append("正文包含占位符/待填字样, 必须给真实归因或明说'无明确信号'")
    if not _SECTION_RE.search(answer or ""):
        violations.append("正文缺少 [§N] 来源引用, 每条归因必须挂数据块编号")
    pack_text = _pack_text_from_dict(pack)
    nums = _numbers_in(answer)
    if nums and pack_text:
        unsourced = _num_unsourced(nums, pack_text)
        if unsourced and len(unsourced) > max(1, len(nums) // 2):
            violations.append(f"以下数字在数据块里找不到来源, 疑似编造: {', '.join(unsourced[:5])}")
    return violations


def _build_whymove_recipe() -> Recipe:
    return Recipe(
        intent="why_move",
        name="驱动归因",
        resolve_slots=_whymove_resolve_slots,
        steps=[
            RecipeStep(tool="realtime_quote", args=lambda s: {"code": s["code"]},
                       label="今日涨跌/行情", optional=False),
            RecipeStep(tool="news_query", args=lambda s: {"code": s["code"], "days": 7},
                       label="消息催化 (近 7 日)", optional=True),
            RecipeStep(tool="fund_flow_change", args=lambda s: {"code": s["code"], "target": "gegu"},
                       label="资金/主力净流入趋势", optional=True),
            RecipeStep(tool="chain_for", args=lambda s: {"code": s["code"]},
                       label="板块联动", optional=True),
        ],
        synthesis_system=_WHYMOVE_SYNTH_SYSTEM,
        synthesis_user_tmpl=_WHYMOVE_SYNTH_USER,
        gate=_whymove_gate,
    )


# ===========================================================================
# Recipe 4: fundflow — 资金流扫描 (市场级; 个股资金流 → fallback 自由循环)
# ===========================================================================
def _fundflow_resolve_slots(query: str, context: dict) -> Optional[dict]:
    """资金流扫描 = 市场级排行 (个股主力榜 + 概念 + 行业).
    若 query 锁定了具体股 (如 '比亚迪资金流') → 返回 None 落自由循环: per-stock 资金流用
    fund_flow_change 工具更合适, 不在本市场级 recipe 内硬塞市场榜单答非所问."""
    if _resolve_code(query, context):
        return None                              # 个股资金流问题 → fallback
    return {}                                    # 市场级扫描无参


_FUNDFLOW_SYNTH_SYSTEM = (
    "你是 A 股资金流扫描助手. 只能依据下面提供的真实资金流榜单数据块 (每块标了 [§N]) 总结今日"
    "资金流向, **严禁编造个股/板块名或净流入数字**, 数据块没有就写 '榜单未提供'.\n"
    "输出要求:\n"
    "1. 分三层总结: 个股主力净流入 TOP / 概念板块 / 行业, 每条挂 [§N].\n"
    "2. 指出主力在加仓哪些方向 / 流出哪些, 涉及的名称与净额必须与榜单一致.\n"
    "3. 末尾一句话资金面倾向 (流入扩散 / 分歧 / 流出), 不喊单.\n"
    "4. 简体中文, 不寒暄, 不复述本提示."
)
_FUNDFLOW_SYNTH_USER = (
    "用户问: {query}\n\n以下是确定性抓取的真实资金流榜单 (每块带 [§N] 编号):\n\n{pack}\n\n"
    "请据此总结今日资金流向 (带 [§N] 引用)."
)


def _fundflow_gate(answer: str, pack: dict, slots: dict) -> List[str]:
    """fundflow 产出 gate (市场级, 无目标股: 占位符 / 引用 / 数字溯源)."""
    violations: List[str] = []
    if not _has_no_placeholder(answer):
        violations.append("正文包含占位符/待填字样, 必须给真实总结或明说'榜单未提供'")
    if not _SECTION_RE.search(answer or ""):
        violations.append("正文缺少 [§N] 来源引用, 每条结论必须挂榜单编号")
    pack_text = _pack_text_from_dict(pack)
    nums = _numbers_in(answer)
    if nums and pack_text:
        unsourced = _num_unsourced(nums, pack_text)
        if unsourced and len(unsourced) > max(1, len(nums) // 2):
            violations.append(f"以下数字在榜单里找不到来源, 疑似编造净额: {', '.join(unsourced[:5])}")
    return violations


def _build_fundflow_recipe() -> Recipe:
    return Recipe(
        intent="fundflow",
        name="资金流扫描",
        resolve_slots=_fundflow_resolve_slots,
        steps=[
            RecipeStep(tool="ths_fund_flow", args=lambda s: {"target": "gegu"},
                       label="个股主力净流入榜", optional=False),
            RecipeStep(tool="ths_fund_flow", args=lambda s: {"target": "gainian"},
                       label="概念板块资金", optional=True),
            RecipeStep(tool="ths_fund_flow", args=lambda s: {"target": "hangye"},
                       label="行业板块资金", optional=True),
        ],
        synthesis_system=_FUNDFLOW_SYNTH_SYSTEM,
        synthesis_user_tmpl=_FUNDFLOW_SYNTH_USER,
        gate=_fundflow_gate,
    )


# ===========================================================================
# REGISTRY
# ===========================================================================
_BRIEF_RECIPE = _build_brief_recipe()
_MARKET_RECIPE = _build_market_recipe()
_WHYMOVE_RECIPE = _build_whymove_recipe()
_FUNDFLOW_RECIPE = _build_fundflow_recipe()

REGISTRY: Dict[str, Recipe] = {
    _BRIEF_RECIPE.intent: _BRIEF_RECIPE,
    _MARKET_RECIPE.intent: _MARKET_RECIPE,
    _WHYMOVE_RECIPE.intent: _WHYMOVE_RECIPE,
    _FUNDFLOW_RECIPE.intent: _FUNDFLOW_RECIPE,
}


def get_recipe(intent: str) -> Optional[Recipe]:
    """按 intent key 取 recipe; 未命中 → None (路由器落自由 run_turn)."""
    return REGISTRY.get(intent)


def list_recipes() -> List[Recipe]:
    return list(REGISTRY.values())
