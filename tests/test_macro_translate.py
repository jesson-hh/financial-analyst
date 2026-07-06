# -*- coding: utf-8 -*-
"""macro 翻译缓存层:缓存优先/批量LLM/失败英文回落+note/脏缓存容忍。mock LLM 不打真 API。"""
import json

from guanlan_v2.macro import translate as mt


def _llm_ok(system, user):
    """按输入顺序等长返回 zh 数组(mock deepseek JSON 模式)。"""
    qs = json.loads(user)["questions"]
    return {"ok": True, "data": {"zh": [f"中文{i}" for i, _ in enumerate(qs)]},
            "model": "deepseek/mock", "tokens": 10}


def test_translate_batch_and_cache_roundtrip(tmp_path):
    cache = tmp_path / "zh_cache.json"
    out, note = mt.translate_questions(["Will the Fed cut rates?", "Recession in 2026?"],
                                       cache_path=cache, llm_fn=_llm_ok)
    assert out == {"Will the Fed cut rates?": "中文0", "Recession in 2026?": "中文1"}
    assert note == ""
    # 二跳全命中缓存:llm_fn 不再被调
    def boom(system, user):
        raise AssertionError("缓存命中不得再调 LLM")
    out2, note2 = mt.translate_questions(["Recession in 2026?"], cache_path=cache, llm_fn=boom)
    assert out2 == {"Recession in 2026?": "中文1"} and note2 == ""


def test_translate_llm_fail_falls_back_honest(tmp_path):
    cache = tmp_path / "zh_cache.json"
    cache.write_text(json.dumps({"cached q": "已缓存中文"}), encoding="utf-8")
    def fail(system, user):
        return {"ok": False, "reason": "LLM 超时(>30s)"}
    out, note = mt.translate_questions(["cached q", "new q"], cache_path=cache, llm_fn=fail)
    assert out == {"cached q": "已缓存中文"}      # 已缓存的照用,新问题诚实缺席
    assert "超时" in note


def test_translate_len_mismatch_rejected(tmp_path):
    """LLM 返回数组长度不匹配 → 整批作废不部分采用(防错位张冠李戴)。"""
    cache = tmp_path / "zh_cache.json"
    def bad(system, user):
        return {"ok": True, "data": {"zh": ["只有一条"]}, "model": "m", "tokens": 1}
    out, note = mt.translate_questions(["q1", "q2"], cache_path=cache, llm_fn=bad)
    assert out == {} and "长度不匹配" in note
    assert not (cache.exists() and json.loads(cache.read_text(encoding="utf-8") or "{}"))  # 不落脏缓存


def test_translate_dirty_cache_tolerated(tmp_path):
    cache = tmp_path / "zh_cache.json"
    cache.write_text("NOT-JSON{", encoding="utf-8")
    out, note = mt.translate_questions(["q1"], cache_path=cache, llm_fn=_llm_ok)
    assert out == {"q1": "中文0"}


def test_translate_empty_input_noop(tmp_path):
    def boom(system, user):
        raise AssertionError("空输入不得调 LLM")
    out, note = mt.translate_questions([], cache_path=tmp_path / "c.json", llm_fn=boom)
    assert out == {} and note == ""
