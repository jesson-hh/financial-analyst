"""炼:对话精炼经验卡 —— 接引擎大模型(deepseek)。

给模型一段**基础 system prompt**,让它清楚自己在精炼一张「经验卡」,并严格返回 JSON。
真模型经引擎 ``LLMClient``(config/llm.yaml → deepseek)调用;``client`` 可注入便于测试。
"""
from __future__ import annotations

import json
from typing import Any, Optional

SYSTEM_PROMPT = """你是「觀瀾」,A 股量化研究工作台里的经验卡精炼助手。

你面对的是一张「经验卡」—— 从研报 / 复盘 / B 站视频等研究文本里提炼出的、可复用的交易经验。一张卡包含:
- 名称(name):简短标题
- 洞察(insight):这条经验的核心结论与逻辑(最重要的一段)
- 触发条件(conds):形如 [["指标","运算符","阈值"], ...] 的结构化条件,可为空数组
- 适用场景(scenes):适用的市场环境 / 频率 / 标的类型标签(字符串数组)
- 因子表达式(expr):把经验量化成因子的表达式,尚未量化时为空字符串

用户会用自然语言要求你修改这张卡(例如:调整阈值、改频率、补充或收敛适用场景、改写洞察、精简表达式)。

规则:
1. 只改用户要求的部分,其余字段原样保留。
2. 保持简洁、专业的 A 股研究员口吻;不要编造回测数字或承诺收益。
3. 触发条件 conds 用 [["条件名","运算符","阈值"]] 数组;无法量化的留空数组 []。
4. 因子表达式 expr 只能用下面这份 DSL 白名单里的字段与算子写,否则引擎编译会报错;难以量化的经验就把 expr 留空字符串,别硬编。
   因子表达式 DSL 白名单(写 expr 只能用这些,否则引擎编译报错):
   字段(价量,日频):close open high low volume vwap amount returns industry
   字段(基本面,日频):pe_ttm pb ps_ttm dv_ttm total_mv circ_mv turnover_rate
   算子:rank scale indneutralize(x,industry) ts_mean ts_sum stddev ts_max ts_min ts_argmax ts_argmin ts_rank delta delay product correlation(x,y,n) covariance(x,y,n) decay_linear(x,n) wma(x,n) sma(x,n,m) signedpower(x,p) power(x,p) log sign abs max_pair min_pair filter_where cross(x,y)
   运算:+ - * / ** 比较 ()
   禁止:__ / import / lambda;禁止用清单外的字段/函数(如 ret、close_price、mean、sum 等都非法)。
   例:5日反转 rank(-delta(close,5));20日动量 rank(ts_sum(returns,20));量价背离 -correlation(rank(close),rank(volume),10);低换手 -rank(turnover_rate);均线突破 cross(close, ts_mean(close,20))。
5. 严格只返回一个 JSON 对象(不要 markdown 代码块、不要任何解释文字),字段如下:
{"reply":"一句话说明你做了什么修改","name":"","insight":"","conds":[["条件名","运算符","阈值"]],"scenes":[""],"expr":"因子表达式"}"""


def _load_dsl_kb() -> str:
    """加载「概念→DSL 范例库」(factor_dsl_kb.md)做 expr grounding;缺失则空串。"""
    from pathlib import Path
    try:
        return (Path(__file__).resolve().parent / "factor_dsl_kb.md").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


# 把范例库追加进 system prompt:让模型照「已验证可编译」的范例仿写、不支持的指标留空、组合用 *。
_DSL_KB = _load_dsl_kb()
if _DSL_KB:
    SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n---\n" + _DSL_KB


def _load_ta_examples() -> str:
    """从已验证 TA 指标库(factorlib/base/ta_indicators.json)生成「概念→expr」范例块。

    只读 JSON(库里只放 /factor/report status=ok 的条目),不连引擎、不碰数据。
    缺文件/坏 JSON → 返回空串(grounding 退化,不崩)。
    """
    import json
    from pathlib import Path
    fp = Path(__file__).resolve().parent.parent / "factorlib" / "base" / "ta_indicators.json"
    try:
        entries = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lines = [f"- {e.get('description') or e['name']}: `{e['expr']}`"
             for e in entries if e.get("family") == "ta" and e.get("expr")]
    if not lines:
        return ""
    return ("## TA 指标范例(已 /factor/report 实测 status=ok,照着仿写)\n"
            "本引擎用 `sma(x,n,m)`=EMA(α=m/n,故 P 日 EMA=`sma(x,P+1,2)`)等算子可重建常见技术指标:\n"
            + "\n".join(lines))


# 把已验证 TA 指标范例追加进 system prompt(在通用 KB 之后)。
_TA_EXAMPLES = _load_ta_examples()
if _TA_EXAMPLES:
    SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n---\n" + _TA_EXAMPLES

_PATCH_KEYS = ("name", "insight", "conds", "scenes", "expr")


def build_refine_messages(card: dict, chat: Optional[list], instruction: str) -> list:
    """组装 [system 基础 prompt, user 当前卡+历史+指令]。"""
    conds = card.get("conds") or []
    scenes = card.get("scenes") or []
    cond_str = ";".join(" ".join(str(x) for x in c) for c in conds) or "(无)"
    user_turns = [m.get("text", "") for m in (chat or []) if m.get("role") == "user"][-3:]
    hist_block = ("\n此前的修改记录:\n" + "\n".join("- " + t for t in user_turns) + "\n") if user_turns else ""
    user = (
        "当前经验卡:\n"
        f"名称: {card.get('name', '')}\n"
        f"洞察: {card.get('insight', '')}\n"
        f"触发条件: {cond_str}\n"
        f"适用场景: {'、'.join(scenes) if scenes else '(无)'}\n"
        f"因子表达式: {card.get('expr') or '(空)'}\n"
        f"{hist_block}"
        f'用户本次指令: "{instruction}"'
    )
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


def parse_refine_output(text: str) -> dict:
    """LLM 输出 → {reply, patch}。容错:去 ```json``` 围栏、从环绕文字里抠出 {...}。"""
    s = str(text or "").strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        s = parts[1] if len(parts) > 1 else s
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip().rstrip("`").strip()
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        s = s[i:j + 1]
    p = json.loads(s)
    patch = {k: p[k] for k in _PATCH_KEYS if k in p and p[k] is not None}
    return {"reply": p.get("reply") or "已更新经验。", "patch": patch}


async def refine_card(card: dict, chat: Optional[list], instruction: str,
                      client: Optional[Any] = None) -> dict:
    """精炼一张经验卡。client 缺省走引擎 LLMClient(deepseek);注入假 client 可单测。"""
    if client is None:
        from financial_analyst.llm.client import LLMClient
        client = LLMClient.for_agent("cards")
    messages = build_refine_messages(card, chat, instruction)
    resp = await client.chat(messages, response_format={"type": "json_object"},
                             temperature=0.3)
    content = resp["choices"][0]["message"]["content"]
    return parse_refine_output(content)
