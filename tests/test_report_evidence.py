# -*- coding: utf-8 -*-
"""guanlan_v2.reports.evidence 单测:十 section 全打桩,零网络。

打桩靶点=十个模块级 `_sec_xxx` 薄函数(build_evidence_pack 按名字动态 globals() 查,
故 monkeypatch.setattr(evidence, "_sec_xxx", stub) 对其生效)。断言覆盖:
- schema 键全集(十 section 名与 spec §2 一致)
- 单 section 抛错 → 该键 null + errors 记录,但 ok 仍 True、其余 section 不受影响
- 文件真落盘且可解析 JSON、norm code 进文件名
- sections_ok 列表正确(仅记录「无异常」的 section,不因合法 None 而剔除)
- 零网络:桩掉全部十个薄函数后,即便真实网络/IO 函数被打成必炸,build 仍成功
"""
from __future__ import annotations

import json

import pytest

from guanlan_v2.reports import evidence

_SECTION_NAMES = (
    "quote_live", "fundflow", "board_eco", "sentiment", "kuaixun",
    "chain", "quant", "mainline", "macro", "holding",
)


def _stub_all(monkeypatch, value_fn=None):
    """把十个 _sec_xxx 全部打桩成同一逻辑(默认返回 {"stub": name});
    可传 value_fn(name, code) 自定义每 section 返回值。"""
    def _make(name):
        def _fn(code):
            return value_fn(name, code) if value_fn else {"stub": name, "code": code}
        return _fn
    for name in _SECTION_NAMES:
        monkeypatch.setattr(evidence, f"_sec_{name}", _make(name))


# ── schema 键全集 ────────────────────────────────────────────────────────────

def test_section_names_match_spec_schema():
    """spec §2 pack schema 的 sections 十键,与本模块注册表逐一对应。"""
    assert tuple(evidence._SECTION_NAMES) == _SECTION_NAMES
    assert set(evidence._SECTION_NAMES) == {
        "quote_live", "fundflow", "board_eco", "sentiment", "kuaixun",
        "chain", "quant", "mainline", "macro", "holding",
    }


# ── 全桩成功路径:文件落盘 + sections_ok + norm code 进文件名 ──────────────────

def test_build_all_stubbed_writes_valid_pack(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("603986", out_dir=tmp_path)

    assert result["ok"] is True
    assert result["errors"] == {}
    assert set(result["sections_ok"]) == set(_SECTION_NAMES)

    path = result["path"]
    assert path is not None
    from pathlib import Path
    p = Path(path)
    assert p.exists()
    assert p.parent == tmp_path
    # norm code(裸 6 位 → SH 前缀,600/601/603/605/688 段判 SH)进文件名
    assert p.name.startswith("SH603986_")
    assert p.name.endswith(".json")

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["code"] == "SH603986"
    assert "generated_at" in data and data["generated_at"]
    sections = data["sections"]
    assert set(sections.keys()) == set(_SECTION_NAMES)
    for name in _SECTION_NAMES:
        assert sections[name] == {"stub": name, "code": "SH603986"}


def test_build_returns_dict_with_required_top_level_keys(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("SZ300750", out_dir=tmp_path)
    assert set(result.keys()) >= {"ok", "path", "sections_ok", "errors"}


# ── 单 section 失败 → null + errors,包仍产出;其余 section 不受影响 ───────────

def test_single_section_exception_degrades_to_null_others_unaffected(monkeypatch, tmp_path):
    def _value_fn(name, code):
        if name == "chain":
            raise RuntimeError("boom: 产业链板不可用")
        return {"stub": name}
    _stub_all(monkeypatch, value_fn=_value_fn)

    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)

    assert result["ok"] is True   # 单 section 挂绝不拖垮整包
    assert "chain" not in result["sections_ok"]
    assert set(result["sections_ok"]) == set(_SECTION_NAMES) - {"chain"}
    assert "chain" in result["errors"]
    assert "boom" in result["errors"]["chain"]

    from pathlib import Path
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert data["sections"]["chain"] is None
    for name in _SECTION_NAMES:
        if name != "chain":
            assert data["sections"][name] == {"stub": name}


def test_multiple_sections_fail_independently(monkeypatch, tmp_path):
    failing = {"quote_live", "quant", "holding"}

    def _value_fn(name, code):
        if name in failing:
            raise ValueError(f"{name} 挂了")
        return {"ok": True, "name": name}
    _stub_all(monkeypatch, value_fn=_value_fn)

    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)

    assert result["ok"] is True
    assert set(result["errors"].keys()) == failing
    assert set(result["sections_ok"]) == set(_SECTION_NAMES) - failing
    for name in failing:
        assert f"{name} 挂了" in result["errors"][name]


# ── 合法 None(非异常)不算失败:sections_ok 仍纳入该名 ──────────────────────

def test_legitimate_none_counts_as_ok_not_error(monkeypatch, tmp_path):
    """例如未持有该票(holding=None)/链外票(chain=None)是合法结局,非失败。"""
    def _value_fn(name, code):
        if name in ("holding", "chain"):
            return None
        return {"stub": name}
    _stub_all(monkeypatch, value_fn=_value_fn)

    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)

    assert result["ok"] is True
    assert result["errors"] == {}
    assert set(result["sections_ok"]) == set(_SECTION_NAMES)   # 全部「查过」,无一异常

    from pathlib import Path
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert data["sections"]["holding"] is None
    assert data["sections"]["chain"] is None


# ── norm code 各形态 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_prefix", [
    ("603986", "SH603986"),
    ("300750", "SZ300750"),
    ("sh603986", "SH603986"),
    ("300750.SZ", "SZ300750"),
])
def test_norm_code_variants_in_filename(monkeypatch, tmp_path, raw, expected_prefix):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack(raw, out_dir=tmp_path)
    from pathlib import Path
    assert Path(result["path"]).name.startswith(expected_prefix + "_")


# ── 零网络:全桩后即便真实取数函数被打成必炸,build 仍成功(证明桩生效、零触网) ──

def test_zero_network_when_all_sections_stubbed(monkeypatch, tmp_path):
    def _explode(*a, **k):
        raise AssertionError("不应触网:section 应已被桩替换")

    # 若任一真实薄函数体在打桩后仍被调用,这些会在其内部被触发并炸出 AssertionError,
    # 该 section 会落 errors 而非真正验证「零触网」——为让违规能显形而非被 try/except 吞掉,
    # 断言运行后 errors 必须为空(否则说明真实网络/IO 路径被打中过)。
    monkeypatch.setattr("guanlan_v2.datafeed.live_client.probe", _explode)
    monkeypatch.setattr("guanlan_v2.datafeed.kuaixun.fetch_kuaixun", _explode)
    monkeypatch.setattr("guanlan_v2.datafeed.market_tape.read_tape", _explode)
    monkeypatch.setattr("guanlan_v2.fundflow.pulse.read_live", _explode)
    monkeypatch.setattr("guanlan_v2.datafeed.sentiment.read_summary", _explode)
    monkeypatch.setattr("guanlan_v2.screen.rescore.industry_scores", _explode)
    monkeypatch.setattr("guanlan_v2.strategy.ranking.load_v4_ranking", _explode)
    monkeypatch.setattr("guanlan_v2.strategy.ranking.mainline_status_map", _explode)
    monkeypatch.setattr("guanlan_v2.screen.market_temp.build_market_temp", _explode)
    monkeypatch.setattr("guanlan_v2.seats.api._ledger_events", _explode)
    monkeypatch.setattr("guanlan_v2.seats.live_book.read_orderbook", _explode)

    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)

    assert result["ok"] is True
    assert result["errors"] == {}
    assert set(result["sections_ok"]) == set(_SECTION_NAMES)


# ── 默认落盘目录常量指向 var/reports/evidence(不实际写,只断言路径) ────────────

def test_default_out_dir_points_at_var_reports_evidence():
    parts = evidence._DEFAULT_OUT_DIR.parts
    assert parts[-3:] == ("var", "reports", "evidence")


# ── 文件名时间戳格式(YYYYMMDDHHMM) ───────────────────────────────────────────

def test_filename_timestamp_format(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)
    from pathlib import Path
    stem = Path(result["path"]).stem   # SH603986_YYYYMMDDHHMM
    code_part, _, ts_part = stem.partition("_")
    assert code_part == "SH603986"
    assert len(ts_part) == 12 and ts_part.isdigit()


# ── mkdir parents:out_dir 多级不存在也能建 ───────────────────────────────────

def test_out_dir_created_with_parents(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    nested = tmp_path / "a" / "b" / "evidence"
    assert not nested.exists()
    result = evidence.build_evidence_pack("SH603986", out_dir=nested)
    assert result["ok"] is True
    assert nested.exists()
