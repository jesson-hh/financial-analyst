"""evidence-loader 零LLM节点 + swarm yaml 主本(仓内 config/swarm/)守护。

覆盖:
1. tmp 证据包文件(经 FA_EVIDENCE_PACK env)→ 节点 run 出 ok:True,sections 透传。
2. 无 env(pack_path 缺)→ run 出 ok:False,error 含"平台证据缺失"(FileNotFoundError
   自然冒泡,base.py run() 广谱捕获 -> ok:False,不静默吞掉)。
3. 证据包 JSON 坏 → run 出 ok:False(json 异常自然抛,同样被 run() 捕获)。
4. yaml 主本守护:find_config 实际调用形态("swarm/<name>.yaml",经
   financial_analyst._config.find_config,被 swarm/loader.py:67 原样调用)在
   FA_CONFIG_DIR=仓内 config 时解析到仓内副本;仓内与 _resources 两份字节相同(防漂移)。
5. load_preset("stock-deep-dive") 全节点(含 evidence-loader)build 成功、
   deps 引用闭合、DAG 无环(拓扑排序验证,orchestrator 本身不用跑)。
"""
import sys
import json
import pathlib
import asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.agent.tier1.evidence_loader import EvidenceLoader

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO_YAML = _REPO_ROOT / "config" / "swarm" / "stock-deep-dive.yaml"
_BUNDLED_YAML = (
    _REPO_ROOT / "engine" / "financial_analyst" / "_resources" / "config"
    / "swarm" / "stock-deep-dive.yaml"
)


def _pack(tmp_path, **overrides):
    payload = {
        "generated_at": "2026-07-12T09:30:00",
        "sections": {"quote_live": {"price": 12.3}, "macro": {"temp": "warm"}},
        "errors": {"fundflow": "NEED_CODE 探针未验证"},
    }
    payload.update(overrides)
    p = tmp_path / "evidence_pack.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. tmp pack → ok:True, sections 透传
# ---------------------------------------------------------------------------

def test_evidence_loader_reads_pack_via_env(tmp_path, monkeypatch):
    pack_path = _pack(tmp_path)
    monkeypatch.setenv("FA_EVIDENCE_PACK", str(pack_path))

    agent = EvidenceLoader(memory_root=tmp_path)
    res = asyncio.run(agent.run(inputs={}))

    assert res.ok is True
    assert res.output.ok is True
    assert res.output.generated_at == "2026-07-12T09:30:00"
    assert res.output.sections == {"quote_live": {"price": 12.3}, "macro": {"temp": "warm"}}
    assert res.output.errors == {"fundflow": "NEED_CODE 探针未验证"}


def test_evidence_loader_ctor_pack_path_wins_over_env(tmp_path, monkeypatch):
    """ctor > env 优先级(照 mainline_classifier.py:63-65 三层先例)。"""
    env_pack = _pack(tmp_path, generated_at="ENV-VERSION")
    ctor_dir = tmp_path / "ctor"
    ctor_dir.mkdir()
    ctor_pack = _pack(ctor_dir, generated_at="CTOR-VERSION")
    monkeypatch.setenv("FA_EVIDENCE_PACK", str(env_pack))

    agent = EvidenceLoader(memory_root=tmp_path, pack_path=str(ctor_pack))
    res = asyncio.run(agent.run(inputs={}))

    assert res.ok is True
    assert res.output.generated_at == "CTOR-VERSION"


# ---------------------------------------------------------------------------
# 2. 无 env → ok:False, error 含"平台证据缺失"
# ---------------------------------------------------------------------------

def test_evidence_loader_no_env_degrades_honestly(tmp_path, monkeypatch):
    monkeypatch.delenv("FA_EVIDENCE_PACK", raising=False)

    agent = EvidenceLoader(memory_root=tmp_path)
    res = asyncio.run(agent.run(inputs={}))

    assert res.ok is False
    assert "平台证据缺失" in res.error


def test_evidence_loader_env_set_but_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.json"
    monkeypatch.setenv("FA_EVIDENCE_PACK", str(missing))

    agent = EvidenceLoader(memory_root=tmp_path)
    res = asyncio.run(agent.run(inputs={}))

    assert res.ok is False
    assert "平台证据缺失" in res.error


# ---------------------------------------------------------------------------
# 3. 坏 JSON → ok:False
# ---------------------------------------------------------------------------

def test_evidence_loader_bad_json_degrades(tmp_path, monkeypatch):
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json,,,", encoding="utf-8")
    monkeypatch.setenv("FA_EVIDENCE_PACK", str(bad))

    agent = EvidenceLoader(memory_root=tmp_path)
    res = asyncio.run(agent.run(inputs={}))

    assert res.ok is False
    assert res.error  # json 异常原样冒泡,不吞


# ---------------------------------------------------------------------------
# 4. yaml 主本守护:find_config 实际调用形态 + 仓内/_resources 防漂移
# ---------------------------------------------------------------------------

def test_swarm_yaml_master_resolves_to_repo_config(monkeypatch):
    """swarm/loader.py:67 用 find_config(f"swarm/{name}.yaml") 原样解析——
    钉 FA_CONFIG_DIR=仓内 config/ 后必须解析到仓内副本(赢过 pinned workspace/legacy/bundled)。"""
    from financial_analyst._config import find_config

    monkeypatch.setenv("FA_CONFIG_DIR", str(_REPO_ROOT / "config"))
    resolved = find_config("swarm/stock-deep-dive.yaml")

    assert resolved.resolve() == _REPO_YAML.resolve()


def test_swarm_yaml_repo_copy_matches_bundled_copy():
    """仓内主本与引擎 bundled 兜底须字节相同,防漂移(FA_CONFIG_DIR 缺席时兜底不落伍)。"""
    assert _REPO_YAML.read_text(encoding="utf-8") == _BUNDLED_YAML.read_text(encoding="utf-8")


def test_swarm_yaml_has_evidence_loader_node():
    import yaml
    for path in (_REPO_YAML, _BUNDLED_YAML):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        names = [a["name"] for a in cfg["agents"]]
        assert "evidence-loader" in names, f"{path} 缺 evidence-loader 节点"


# ---------------------------------------------------------------------------
# 5. load_preset 全节点 build 成功 + deps 闭合 + DAG 无环
# ---------------------------------------------------------------------------

def test_load_preset_builds_all_nodes_including_evidence_loader(tmp_path):
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
    assert len(names) == len(set(names)), "重复节点名"

    name_set = set(names)
    deps_map = {}
    for n in nodes:
        for d in n.deps:
            assert d in name_set, f"{n.agent.NAME} 的 dep {d!r} 引用不闭合"
        deps_map[n.agent.NAME] = set(n.deps)

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

    evidence_node = next(n for n in nodes if n.agent.NAME == "evidence-loader")
    assert evidence_node.deps == []
