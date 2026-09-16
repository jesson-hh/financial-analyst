# -*- coding: utf-8 -*-
"""guanlan_v2.reports.evidence 单测:十五 section 全打桩,零网络。

打桩靶点=十五个模块级 `_sec_xxx` 薄函数(build_evidence_pack 按名字动态 globals() 查,
故 monkeypatch.setattr(evidence, "_sec_xxx", stub) 对其生效)。断言覆盖:
- company-research-evidence@2 schema 键全集(十五 section)
- schema_version / coverage 随返回值与落盘正文一起持久化
- 单 section 抛错 → 该键 null + errors 记录,但 ok 仍 True、其余 section 不受影响
- 文件真落盘且可解析 JSON、norm code 进文件名
- sections_ok 列表正确(仅记录「无异常」的 section,不因合法 None 而剔除)
- 零网络:桩掉全部十个薄函数后,即便真实网络/IO 函数被打成必炸,build 仍成功
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from financial_analyst.data.trusted_receipts import KEY_ENV, KEY_ID_ENV
from guanlan_v2.reports import evidence

_SECTION_NAMES = (
    "quote_live", "company_facts", "announcements", "expectations",
    "valuation", "research_views", "fundflow", "board_eco", "sentiment",
    "kuaixun", "chain", "quant", "mainline", "macro", "holding",
)
_COMPANY_CORE_SECTIONS = (
    "company_facts", "announcements", "expectations", "valuation",
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
    """company-research-evidence@2 的十五个 sections 与注册表逐一对应。"""
    assert tuple(evidence._SECTION_NAMES) == _SECTION_NAMES
    assert tuple(evidence._COMPANY_CORE_SECTIONS) == _COMPANY_CORE_SECTIONS
    assert evidence._SCHEMA_VERSION == "company-research-evidence@2"
    assert len(evidence._SECTION_NAMES) == 15


# ── 全桩成功路径:文件落盘 + sections_ok + norm code 进文件名 ──────────────────

def test_build_all_stubbed_writes_valid_pack(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("603986", out_dir=tmp_path)

    assert result["ok"] is True
    assert result["schema_version"] == "company-research-evidence@2"
    assert result["errors"] == {}
    assert set(result["sections_ok"]) == set(_SECTION_NAMES)
    assert result["coverage"] == {
        "present": list(_SECTION_NAMES),
        "missing": [],
        "company_core": {
            "required": list(_COMPANY_CORE_SECTIONS),
            "missing": [],
            "status": "complete",
        },
    }

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
    assert data["schema_version"] == "company-research-evidence@2"
    assert data["code"] == "SH603986"
    assert "generated_at" in data and data["generated_at"]
    assert data["coverage"] == result["coverage"]
    assert data["errors"] == {}
    sections = data["sections"]
    assert set(sections.keys()) == set(_SECTION_NAMES)
    for name in _SECTION_NAMES:
        assert sections[name] == {"stub": name, "code": "SH603986"}


def test_build_returns_dict_with_required_top_level_keys(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("SZ300750", out_dir=tmp_path)
    assert set(result.keys()) >= {
        "ok", "path", "schema_version", "coverage", "sections_ok", "errors",
    }


def test_quote_live_keeps_retrieval_time_separate_from_market_date(monkeypatch):
    from guanlan_v2.seats import live_book
    from guanlan_v2.datafeed import live_client

    monkeypatch.setattr(live_book, "read_quote_failover", lambda _code: {
        "ok": True, "price": 17.22, "changePercent": -4.2,
        "captured_at": "2026-09-05T13:10:34+08:00",
    })
    monkeypatch.setattr(live_book, "read_orderbook", lambda _code: {
        "ok": False, "levels": [],
        "captured_at": "2026-09-05T13:10:34+08:00",
    })
    monkeypatch.setattr(live_book, "read_ticks", lambda _code, limit: {
        "ok": False, "ticks": [], "n": 0,
        "captured_at": "2026-09-05T13:10:34+08:00",
    })
    monkeypatch.setattr(live_client, "probe", lambda source, code="", limit=0: {
        "ok": True, "status": "ok", "source": "tdx_kline",
        "provider": "tdx", "items": [{
            "source_id": "tdx_kline", "provider": "tdx",
            "revision_id": "bar-20260904",
            "raw": {"code": "600667", "freq": "day", "close": 17.22,
                    "datetime": "2026-09-04 15:00"},
        }],
    })

    section = evidence._sec_quote_live("SH600667")

    assert section["price"] == 17.22
    assert section["as_of"] == "2026-09-04"
    assert section["market_data_as_of"] == "2026-09-04"
    assert section["retrieved_at"]


def test_quote_live_remains_undated_when_daily_close_does_not_match(monkeypatch):
    from guanlan_v2.seats import live_book
    from guanlan_v2.datafeed import live_client

    monkeypatch.setattr(live_book, "read_quote_failover", lambda _code: {
        "ok": True, "price": 17.22, "changePercent": -4.2,
    })
    monkeypatch.setattr(live_book, "read_orderbook", lambda _code: {
        "ok": False, "levels": [],
    })
    monkeypatch.setattr(live_book, "read_ticks", lambda _code, limit: {
        "ok": False, "ticks": [], "n": 0,
    })
    monkeypatch.setattr(live_client, "probe", lambda source, code="", limit=0: {
        "ok": True, "status": "ok", "source": "tdx_kline", "items": [{
            "source_id": "tdx_kline",
            "raw": {"code": "600667", "freq": "day", "close": 17.21,
                    "datetime": "2026-09-04 15:00"},
        }],
    })

    section = evidence._sec_quote_live("SH600667")

    assert "as_of" not in section
    assert "market_data_as_of" not in section
    assert section["market_data_date_status"] == "unverified"


def test_current_quote_receipt_never_invents_requested_day_as_market_day():
    receipt = evidence._section_receipt(
        name="quote_live", code="SH600667", requested_asof="2026-09-05",
        historical=False,
        section={"price": 17.22, "retrieved_at": "2026-09-05T13:10:34+08:00"},
        generated_at="2026-09-05T13:10:34+08:00",
    )

    assert receipt["status"] == "available"
    assert receipt["data_asof"] is None
    assert "acquisition time only" in receipt["reason"]


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
    assert result["coverage"]["missing"] == ["chain"]
    assert result["coverage"]["company_core"]["status"] == "complete"

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
    assert set(result["coverage"]["missing"]) == failing
    assert result["coverage"]["company_core"]["status"] == "complete"
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
    assert result["coverage"]["missing"] == ["chain", "holding"]
    assert result["coverage"]["company_core"]["status"] == "complete"

    from pathlib import Path
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert data["sections"]["holding"] is None
    assert data["sections"]["chain"] is None


def test_company_core_coverage_marks_legitimate_none_incomplete(monkeypatch, tmp_path):
    """核心段查过但无数据仍属 sections_ok；coverage 必须诚实标为 incomplete。"""
    _stub_all(
        monkeypatch,
        value_fn=lambda name, code: None if name == "expectations" else {"stub": name},
    )

    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)

    assert "expectations" in result["sections_ok"]
    assert result["errors"] == {}
    assert result["coverage"]["missing"] == ["expectations"]
    assert result["coverage"]["company_core"] == {
        "required": list(_COMPANY_CORE_SECTIONS),
        "missing": ["expectations"],
        "status": "incomplete",
    }


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


def test_asof_keeps_existing_one_argument_section_stubs_compatible(
        monkeypatch, tmp_path):
    """PIT 参数只交给真实 F10 函数；既有单参数 section 桩不被破坏。"""
    monkeypatch.setenv(KEY_ID_ENV, "pit-test-key-v1")
    monkeypatch.setenv(
        KEY_ENV, "test-only-report-pit-hmac-key-material-32-bytes-minimum",
    )
    _stub_all(monkeypatch)

    result = evidence.build_evidence_pack(
        "SH603986", out_dir=tmp_path, asof="2026-06-30",
    )

    assert result["ok"] is True
    assert result["errors"] == {}
    from pathlib import Path
    body = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert body["cutoff_as_of"] == "2026-06-30"
    assert body["sections"]["company_facts"] == {
        "stub": "company_facts", "code": "SH603986",
    }


def test_build_passes_cutoff_to_both_real_f10_sections_only(
        monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_ID_ENV, "pit-test-key-v1")
    monkeypatch.setenv(
        KEY_ENV, "test-only-report-pit-hmac-key-material-32-bytes-minimum",
    )
    calls = []

    class _Facts:
        def to_dict(self):
            return {
                "snapshot_date": "20260630",
                "valuation": {"report_period": "2026-03-31", "eps": 1.0},
                "events": [{"date": "2026-06-20", "title": "fixture"}],
                "broker": {"ratings": []},
                "holders": None,
                "main_capital": None,
                "lhb": {"margin": [], "moneyflow": [], "abnormal": []},
                "provenance": ["fixture"],
                "honest_note": "",
            }

    class _F10:
        @staticmethod
        def load_facts(code, asof=None):
            calls.append((code, asof))
            return _Facts()

    monkeypatch.setattr(evidence, "_engine_data_module", lambda name: _F10)
    monkeypatch.setattr(
        evidence, "_valuation_bands",
        lambda code: {"as_of": "2026-06-30", "pe_ttm": {"current": 10}},
    )
    # Leave the two real PIT-aware functions intact; all other sections retain
    # their historical one-argument callable contract.
    for name in set(_SECTION_NAMES) - {"company_facts", "valuation"}:
        monkeypatch.setattr(
            evidence, f"_sec_{name}",
            lambda code, _name=name: {"stub": _name, "code": code},
        )

    result = evidence.build_evidence_pack(
        "SH603986", out_dir=tmp_path, asof="2026-06-30T23:59:59+08:00",
    )

    assert result["ok"] is True
    assert result["errors"] == {}
    assert calls == [
        ("SH603986", "2026-06-30"),
        ("SH603986", "2026-06-30"),
    ]
    from pathlib import Path
    body = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert body["cutoff_as_of"] == "2026-06-30"
    assert body["sections"]["company_facts"]["as_of"] == "2026-03-31"
    assert body["sections"]["valuation"]["f10"]["report_period"] == "2026-03-31"


def test_valuation_bands_maps_pe_pb_from_probe(monkeypatch):
    """估值分位段:把 baidu_valuation_percentile 探针的 pe_ttm/pb 行映射成研报「贵不贵」锚。"""
    def _fake_probe(source, code="", date="", limit=20):
        assert source == "baidu_valuation_percentile"
        return {"ok": True, "status": "ok", "items": [
            {"raw": {"metric": "pe_ttm", "current": 18.4, "percentile": 32.1, "p20": 15,
                     "p50": 22, "p80": 30, "min": 10, "max": 45, "n": 900}},
            {"raw": {"metric": "pb", "current": 5.6, "percentile": 48.0, "p20": 4,
                     "p50": 5.5, "p80": 7, "min": 3, "max": 9, "n": 900}},
        ], "n": 2, "note": ""}
    monkeypatch.setattr("guanlan_v2.datafeed.live_client.probe", _fake_probe)
    v = evidence._valuation_bands("SH600519")
    assert v["pe_ttm"]["current"] == 18.4 and v["pe_ttm"]["percentile"] == 32.1
    assert v["pb"]["p80"] == 7 and v["pb"]["n"] == 900


def test_board_eco_section_passes_through_ladder_and_max_streak(monkeypatch):
    """打板生态段透传 max_streak(真连板)与连板梯队 ladder 到研报证据包(供 LLM 看形状不只看标量)。"""
    tape = {"warming": False, "pulled_at": "2026-07-15T15:00:00",
            "derived": {"zt_count": 60, "zb_count": 5, "break_rate": 0.1,
                        "promotion_rate": 0.3, "max_streak": 4,
                        "ladder": {"first": 40, "2": 12, "3": 5, "4": 1, "5plus": 2},
                        "north_net": -12.3},
            "sources": {}}
    monkeypatch.setattr("guanlan_v2.datafeed.market_tape.read_tape", lambda: tape)
    sec = evidence._sec_board_eco("SH600000")
    assert sec["max_streak"] == 4
    assert sec["ladder"] == {"first": 40, "2": 12, "3": 5, "4": 1, "5plus": 2}


def test_board_eco_uses_core_pulled_at_and_resolved_lhb_alias(monkeypatch):
    """打板证据龄期取核心源时间；龙虎榜必须从 live_client 规范化后的别名取数。"""
    from guanlan_v2.datafeed import live_client as lc

    aliases = {"eastmoney_lhb": "canonical_lhb", "northbound": "canonical_north"}
    monkeypatch.setattr(lc, "resolve_source", lambda source: aliases.get(source, ""))
    tape = {
        "warming": False,
        "pulled_at": "2026-07-01T09:00:00",
        "core_pulled_at": "2026-07-15T15:00:00",
        "freshness": {"stale": False},
        "derived": {"zt_count": 42, "north_net": -12.3},
        "sources": {
            "canonical_lhb": {
                "content_date": "2026-07-15",
                "rows": [
                    {"code": "600000.SH", "reason": "日涨幅偏离值达7%"},
                    {"code": "000001.SZ", "reason": "不属于目标股票"},
                ],
            },
            "canonical_north": {"content_date": "2026-07-15", "rows": []},
        },
    }
    monkeypatch.setattr("guanlan_v2.datafeed.market_tape.read_tape", lambda: tape)

    sec = evidence._sec_board_eco("SH600000")

    assert sec["as_of"] == "2026-07-15T15:00:00"
    assert sec["core_pulled_at"] == "2026-07-15T15:00:00"
    assert sec["source_as_of"] == {
        "lhb": "2026-07-15", "northbound": "2026-07-15",
    }
    assert sec["lhb"] == [{"code": "600000.SH", "reason": "日涨幅偏离值达7%"}]


# ── 生产 fallback 不变；pytest 默认落盘被重定向到安全临时根 ────────────────

def test_default_out_dir_points_at_var_reports_evidence():
    assert evidence._DEFAULT_OUT_DIR == Path(
        os.environ["GUANLAN_REPORT_EVIDENCE_DIR"]
    )
    assert evidence._DEFAULT_OUT_DIR.is_relative_to(
        Path(os.environ["GUANLAN_TEST_ROOT"])
    )
    production_parts = evidence._default_out_dir({}).parts
    assert production_parts[-3:] == ("var", "reports", "evidence")


# ── 文件名时间戳格式(YYYYMMDDHHMMSSffffff,20 位) ────────────────────────────

def test_filename_timestamp_format(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    result = evidence.build_evidence_pack("SH603986", out_dir=tmp_path)
    from pathlib import Path
    stem = Path(result["path"]).stem   # SH603986_YYYYMMDDHHMMSSffffff
    code_part, _, ts_part = stem.partition("_")
    assert code_part == "SH603986"
    assert len(ts_part) == 20 and ts_part.isdigit()


# ── mkdir parents:out_dir 多级不存在也能建 ───────────────────────────────────

def test_out_dir_created_with_parents(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    nested = tmp_path / "a" / "b" / "evidence"
    assert not nested.exists()
    result = evidence.build_evidence_pack("SH603986", out_dir=nested)
    assert result["ok"] is True
    assert nested.exists()
