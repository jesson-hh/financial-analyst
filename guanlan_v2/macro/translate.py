# -*- coding: utf-8 -*-
"""预测市场问题中文翻译层:缓存优先(var/macro_pulse/zh_cache.json 永久复用),
新问题一次批量 LLM(deepseek,screen.llm 同接缝);失败/长度不匹配 → 整批作废英文回落+note,
绝不部分采用防错位。机翻仅为可读性展示,原文以悬停/展开保留。"""
from __future__ import annotations

import json
from pathlib import Path

_CACHE_DEFAULT = Path(__file__).resolve().parents[2] / "var" / "macro_pulse" / "zh_cache.json"
_SYSTEM = (
    "你是金融翻译。把预测市场(Polymarket/Kalshi)的英文问题逐条翻成简洁中文,"
    "保留机构名/日期/数字/币种语义(Fed=美联储,bps=基点)。"
    '只输出 JSON:{"zh": ["...", ...]},数组与输入 questions 等长同序,不增不减。'
)


def _default_llm(system: str, user: str) -> dict:
    """daemon/worker 线程内同步跑异步 _call_llm_json(rerank._call_llm 同款)。"""
    import asyncio
    from guanlan_v2.screen.llm import _call_llm_json
    return asyncio.run(_call_llm_json(system, user, timeout=60.0, temperature=0.1))


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def translate_questions(questions, cache_path=None, llm_fn=None):
    """返回 ({英文问题: 中文}, note)。缓存命中零成本;新问题一次批量;失败诚实回落。"""
    path = Path(cache_path) if cache_path else _CACHE_DEFAULT
    llm_fn = llm_fn or _default_llm
    uniq = list(dict.fromkeys(q for q in questions if q))
    if not uniq:
        return {}, ""
    cache = _load_cache(path)
    missing = [q for q in uniq if q not in cache]
    note = ""
    if missing:
        try:
            r = llm_fn(_SYSTEM, json.dumps({"questions": missing}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        if not r.get("ok"):
            note = f"翻译层不可用({r.get('reason', '未知')}),新市场显示英文原文"
        else:
            zh = (r.get("data") or {}).get("zh")
            if not isinstance(zh, list) or len(zh) != len(missing):
                note = f"翻译返回长度不匹配({len(zh) if isinstance(zh, list) else '非数组'}/{len(missing)}),整批作废显示英文"
            else:
                for q, z in zip(missing, zh):
                    if isinstance(z, str) and z.strip():
                        cache[q] = z.strip()
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(cache, ensure_ascii=False, indent=0),
                                    encoding="utf-8")
                except OSError as e:
                    note = f"翻译缓存落盘失败: {e}"
    return {q: cache[q] for q in uniq if q in cache}, note
