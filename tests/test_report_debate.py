"""Task 6:bear 反驳波(deps 加 bull-advocate)+ 辩护人/风控/写手升 reasoner deep 档。

零网络零真 LLM:LLMClient.for_agent 全桩(照 tests/test_report_prompt_evidence.py 的
_CapLLM 同款)。覆盖:
1. 两份 yaml 解析:字节一致 + bear.deps/input_keys 含 bull-advocate + load_preset
   全节点(含 evidence-loader)build 成功、DAG 无环(照 test_evidence_loader.py 的
   Kahn 拓扑排序手法)。
2. 桩 LLM 捕 prompt:bear inputs 带 bull-advocate 输出(含 thesis 文本)→ prompt 含该
   thesis 文本(拼进 upstream json,落 user 消息)+「逐条针对性反驳」指令(落 system
   消息);无 bull 键 → 不含反驳指令,但诚实标「bull-advocate 论点缺席」(不假装完成
   反驳,不静默回退)。
3. 波次仿真:纯 python 模拟 orchestrator._ready 的按 deps∈done 迭代分波逻辑(不跑真
   orchestrator),断言 bear 现在比 bull 晚一波(= bull 波次 + 1),且不在 bear 下游因
   果链上的节点(tier1/tier2 + bull-advocate 自身)波次与"假设 bear 无 bull 依赖"的
   基线完全一致——即 bear 加 bull 依赖零 orchestrator 改动、零上游节点连带改动。
4. config/llm.yaml 守护:四座席(bull-advocate/bear-advocate/risk-officer/
   report-writer)deep 档字段存在(照 test_llm_config_guard.py 现有模式独立复核)。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENGINE = _REPO_ROOT / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.agent.tier3 import bear_advocate as bear_mod  # noqa: E402

_MEMORIES = _REPO_ROOT / "memories"
_REPO_YAML = _REPO_ROOT / "config" / "swarm" / "stock-deep-dive.yaml"
_BUNDLED_YAML = (
    _REPO_ROOT / "engine" / "financial_analyst" / "_resources" / "config"
    / "swarm" / "stock-deep-dive.yaml"
)

_CAP: Dict[str, Any] = {}


class _CapLLM:
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        _CAP["system"] = messages[0]["content"]
        _CAP["user"] = messages[-1]["content"]
        _CAP["messages"] = list(messages)
        return {"choices": [{"message": {"content": _CAP["_response"]}}]}


def _run(monkeypatch, mod, agent_cls, inputs: dict, response: dict) -> Dict[str, Any]:
    _CAP.clear()
    _CAP["_response"] = json.dumps(response, ensure_ascii=False)
    monkeypatch.setattr(mod, "LLMClient", _CapLLM)
    agent = agent_cls(memory_root=_MEMORIES)
    asyncio.run(agent._execute(inputs))
    return _CAP


def _quote(mv_yi=150.0, pe=180.0, ret_60d=0.62, price=23.4) -> dict:
    return {"code": "SH603986", "asof_date": "2026-07-12", "price": price,
            "pe": pe, "mv_yi": mv_yi, "ret_60d": ret_60d, "name": "兆易创新"}


def _bull_out(**overrides) -> dict:
    out = {
        "thesis_bullets": ["[V1] 液冷产业链景气度上行且公司份额领先反驳测试专属锚文本XYZ123"],
        "catalysts": ["Q3 订单放量"],
        "target_price_high": 45.0,
        "target_price_base": 38.0,
        "disproof_signals": ["行业排产不及预期"],
        "v_anchors": ["V1"],
    }
    out.update(overrides)
    return out


# ---------------------------------------------------------------------------
# 1. yaml 解析:字节一致 + bear.deps/input_keys 含 bull-advocate + load_preset 无环
# ---------------------------------------------------------------------------

def test_yaml_repo_and_bundled_identical():
    assert _REPO_YAML.read_text(encoding="utf-8") == _BUNDLED_YAML.read_text(encoding="utf-8")


def test_yaml_bear_deps_and_input_keys_include_bull():
    for path in (_REPO_YAML, _BUNDLED_YAML):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        by_name = {a["name"]: a for a in cfg["agents"]}
        bear = by_name["bear-advocate"]
        assert "bull-advocate" in bear["deps"], f"{path}: bear.deps 须含 bull-advocate(反驳波依赖)"
        assert "bull-advocate" in bear["input_keys"], f"{path}: bear.input_keys 须含 bull-advocate"


def test_load_preset_builds_all_nodes_no_cycle(tmp_path):
    from financial_analyst import tui
    from financial_analyst.swarm.loader import load_preset

    tui._ensure_registered()
    nodes = load_preset(
        "stock-deep-dive",
        memory_root=tmp_path,
        preset_dir=_REPO_ROOT / "config" / "swarm",
    )

    names = [n.agent.NAME for n in nodes]
    assert "evidence-loader" in names
    assert "bear-advocate" in names and "bull-advocate" in names
    assert len(names) == len(set(names)), "重复节点名"

    name_set = set(names)
    deps_map = {}
    for n in nodes:
        for d in n.deps:
            assert d in name_set, f"{n.agent.NAME} 的 dep {d!r} 引用不闭合"
        deps_map[n.agent.NAME] = set(n.deps)

    bear_deps = deps_map["bear-advocate"]
    assert "bull-advocate" in bear_deps, "load_preset build 出的 bear 节点须含 bull-advocate 依赖"

    # 拓扑排序(Kahn)验证 DAG 无环 —— orchestrator 本身不用跑
    remaining = {k: set(v) for k, v in deps_map.items()}
    resolved_order = []
    while remaining:
        ready = [k for k, v in remaining.items() if not v]
        assert ready, f"DAG 存在环,剩余未解析节点: {sorted(remaining)}"
        for k in ready:
            del remaining[k]
        for v in remaining.values():
            v -= set(ready)
        resolved_order.extend(ready)

    assert len(resolved_order) == len(nodes)


# ---------------------------------------------------------------------------
# 2. 桩 LLM 捕 prompt:bull 在场 → thesis 文本 + 反驳指令;bull 缺席 → 诚实降级
# ---------------------------------------------------------------------------

def test_bear_with_bull_output_includes_thesis_text_and_rebuttal_instruction(monkeypatch):
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {"quote-fetcher": _quote(), "bull-advocate": _bull_out()},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    # bull thesis 文本经 upstream json 拼入 user 消息
    assert "反驳测试专属锚文本XYZ123" in cap["user"]
    # 反驳硬指令落 system 消息
    assert "逐条针对性反驳" in cap["system"]
    assert "bull-advocate 论点缺席" not in cap["system"]


def test_bear_without_bull_output_omits_rebuttal_instruction_honestly(monkeypatch):
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {"quote-fetcher": _quote()},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    assert "逐条针对性反驳" not in cap["system"], "无 bull 键不该冒充完成了反驳"
    assert "bull-advocate 论点缺席" in cap["system"], "须诚实标注 bull 论点缺席(诚实降级红线)"
    assert "反驳测试专属锚文本XYZ123" not in cap["user"]


# ---------------------------------------------------------------------------
# 3. 波次仿真:纯 python 模拟 orchestrator._ready 的迭代分波(照 T5 评审的仿真法)
# ---------------------------------------------------------------------------

def _compute_waves(deps_map: Dict[str, set]) -> Dict[str, int]:
    """按 _ready 语义(deps∈done 才可跑)做 Kahn 分层——本模拟假设全部成功(soft/hard
    无区别),只算"第几批可跑",与 orchestrator.py:24-32 的动态分波逻辑同构。"""
    remaining = {k: set(v) for k, v in deps_map.items()}
    wave: Dict[str, int] = {}
    w = 0
    while remaining:
        ready = [k for k, v in remaining.items() if not v]
        assert ready, f"DAG 存在环: {sorted(remaining)}"
        for k in ready:
            wave[k] = w
            del remaining[k]
        for v in remaining.values():
            v -= set(ready)
        w += 1
    return wave


def test_bear_wave_shifts_after_bull_other_upstream_nodes_unchanged():
    cfg = yaml.safe_load(_REPO_YAML.read_text(encoding="utf-8"))
    deps_map = {a["name"]: set(a.get("deps", [])) for a in cfg["agents"]}

    new_wave = _compute_waves(deps_map)

    # 基线:假设 bear 没有 bull-advocate 依赖(T6 改动前的形态)
    baseline_deps_map = {k: set(v) for k, v in deps_map.items()}
    baseline_deps_map["bear-advocate"] = baseline_deps_map["bear-advocate"] - {"bull-advocate"}
    old_wave = _compute_waves(baseline_deps_map)

    # bear 现在比 bull 晚整整一波(反驳波依赖生效)
    assert new_wave["bear-advocate"] == new_wave["bull-advocate"] + 1
    assert new_wave["bear-advocate"] == old_wave["bear-advocate"] + 1, "bear 应比改动前晚恰好一波"

    # 不在 bear 下游因果链上的节点(tier1/tier2 + bull-advocate 自身)波次原封不动——
    # 零 orchestrator 改动、零上游连带改动。
    downstream_of_bear = {"bear-advocate", "risk-officer", "report-writer", "introspector"}
    for name in deps_map:
        if name in downstream_of_bear:
            continue
        assert new_wave[name] == old_wave[name], f"{name} 波次不该因 bear 加 bull 依赖而改变"

    # 下游因果链按预期整体顺延一波(bear 更晚→依赖它的节点自然更晚,这是波次计算的
    # 自然结果,不是 bug;risk-officer/report-writer/introspector 都晚一波)。
    for name in ("risk-officer", "report-writer", "introspector"):
        assert new_wave[name] == old_wave[name] + 1, f"{name} 应随 bear 顺延一波"


# ---------------------------------------------------------------------------
# 4. config/llm.yaml 守护:四座席 deep 档(独立复核 test_llm_config_guard.py 同款断言)
# ---------------------------------------------------------------------------

def test_llm_yaml_four_debate_seats_deep_tier():
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
    ov = cfg.get("agent_overrides") or {}
    for seat in ("bull-advocate", "bear-advocate", "risk-officer", "report-writer"):
        entry = ov.get(seat)
        assert entry is not None, f"llm.yaml 缺 {seat} 的 agent_overrides"
        assert entry.get("provider") == "deepseek"
        assert entry.get("model") == "deepseek-reasoner", f"{seat} 应为 deep 档"
        assert entry.get("max_tokens") == 8192
        assert entry.get("timeout") == 300
