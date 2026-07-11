# guanlan_v2.screen.market_temp · 市场温度上下文 + 决策层护盾 v4.4
#
# 三层覆盖:_gate 纯函数全分支 / build_market_temp 四块独立降级(单块挂绝不拖垮)/
# converge(market_temp=…) 行为——risk_off 只减半仓位档、overheat 只警示,
# **绝不动星级/排序/剔票**(playbook 红线:勿用滞后单维否决前瞻)。
from datetime import datetime

import guanlan_v2.datafeed.market_tape as market_tape
import guanlan_v2.datafeed.sentiment as dfs
import guanlan_v2.fundflow.pulse as ff_pulse
import guanlan_v2.macro.pulse as macro_pulse
from guanlan_v2.screen.market_temp import _gate, build_market_temp
from guanlan_v2.strategy.decision import converge


# ── _gate 纯函数 ─────────────────────────────────────────────────────────────

def test_gate_all_none_sleeps():
    # 温度与主力净额全缺 → 数据不足护盾休眠(诚实 None,绝不猜)
    assert _gate(None, None, None) is None
    assert _gate(None, None, 0.5) is None    # 只有炸板率不足以开闸


def test_gate_risk_off_cold_temp():
    g = _gate(20, 100, 0.1)
    assert g["level"] == "risk_off"
    assert any("≤25" in r for r in g["reasons"])


def test_gate_risk_off_main_outflow():
    # 温度缺席、仅主力大流出也触发(真机 2026-07-10 实值 -397.91亿)
    g = _gate(None, -397.91, None)
    assert g["level"] == "risk_off"
    assert any("-300" in r for r in g["reasons"])


def test_gate_risk_off_wins_over_overheat():
    # 高温 + 主力大流出并存 → 保守取 risk_off
    g = _gate(90, -500, 0.5)
    assert g["level"] == "risk_off"


def test_gate_overheat_requires_temp_and_break_rate():
    assert _gate(90, 0, 0.4)["level"] == "overheat"
    assert _gate(90, 0, 0.2)["level"] == "neutral"    # 炸板率不足
    assert _gate(90, 0, None)["level"] == "neutral"   # 炸板率缺 → 不猜过热
    assert _gate(80, 0, 0.5)["level"] == "neutral"    # 温度不足


def test_gate_neutral_reasons_empty():
    g = _gate(60, 50, 0.2)
    assert g["level"] == "neutral" and g["reasons"] == []


# ── build_market_temp:四块独立组装 + 降级 ──────────────────────────────────

def _boom(*a, **k):
    raise RuntimeError("boom")


def _patch_all_ok(monkeypatch):
    """四源全打桩为健康值(全部走缓存/快照语义,零网络)。

    flow 块先探缓存再 read_live:_load_live_cache 桩为真(缓存在),
    _trigger_live_refresh 桩死(护栏:测试进程绝不 spawn 真拉线程)。"""
    snap = {"ts": "2026-07-11T18:38:35",
            "temps": {"fed": 48.2, "china": None, "crypto_risk": 57.8},
            "astock_temp": 62.3}
    monkeypatch.setattr(macro_pulse, "_read_snapshots", lambda p: [snap])
    monkeypatch.setattr(market_tape, "read_tape", lambda: {
        "warming": False,
        "derived": {"zt_count": 47, "zb_count": 12, "break_rate": 0.2034,
                    "promotion_rate": 0.31},
        "freshness": {"overall_age_s": 120}})
    monkeypatch.setattr(ff_pulse, "_load_live_cache", lambda kind, cache_dir=None: {"ok": True})
    monkeypatch.setattr(ff_pulse, "_trigger_live_refresh", lambda *a, **k: False)
    monkeypatch.setattr(ff_pulse, "read_live", lambda kind: {
        "market": {"main_net": -39791411200.0, "super_net": -29097578496.0},
        "pulled_at": "2026-07-11T14:22:30"})
    monkeypatch.setattr(dfs, "latest_market", lambda: {
        "market_read": "偏多", "market_tilt": None,
        "as_of": "2026-07-11 09:31", "ts": "2026-07-11T09:31:00"})


def test_build_happy_path(monkeypatch):
    _patch_all_ok(monkeypatch)
    out = build_market_temp(now=datetime(2026, 7, 11, 19, 38, 35))
    g = out["global"]
    assert g["g_temp"] == 53.0                      # (48.2+57.8)/2,None 主题不计
    assert g["astock_temp"] == 62.3
    assert g["stale_min"] == 60.0
    b = out["board"]
    assert b["zt_count"] == 47 and b["break_rate"] == 0.2034
    assert b["age_s"] == 120
    assert out["flow"]["main_net_yi"] == -397.91    # 元 → 亿换算
    assert out["flow"]["pulled_at"] == "2026-07-11T14:22:30"
    assert out["llm"]["market_read"] == "偏多"
    # 主力 -397.91亿 ≤-300 → risk_off(真机口径)
    assert out["gate"]["level"] == "risk_off"


def test_single_block_failure_isolated(monkeypatch):
    # global 挂 → 该块 None + note;gate 按余下信号走(主力大流出仍 risk_off)
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(macro_pulse, "_read_snapshots", _boom)
    out = build_market_temp()
    assert out["global"] is None
    assert any("global 块异常" in n for n in out["notes"])
    assert out["board"] is not None and out["flow"] is not None
    assert out["gate"]["level"] == "risk_off"


def test_flow_failure_gate_follows_temp(monkeypatch):
    # flow 挂 → 主力净额缺席;gate 仅由 astock_temp 62.3 判 → neutral
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(ff_pulse, "read_live", _boom)
    out = build_market_temp()
    assert out["flow"] is None
    assert any("flow 块异常" in n for n in out["notes"])
    assert out["gate"]["level"] == "neutral"


def test_all_blocks_down_gate_none(monkeypatch):
    # 四源全挂 → 全 None + gate None(护盾休眠),函数绝不抛
    # flow 块首个触点是 _load_live_cache(探缓存),在它上炸即覆盖整块
    for mod, name in ((macro_pulse, "_read_snapshots"), (market_tape, "read_tape"),
                      (ff_pulse, "_load_live_cache"), (dfs, "latest_market")):
        monkeypatch.setattr(mod, name, _boom)
    out = build_market_temp()
    assert out["global"] is None and out["board"] is None
    assert out["flow"] is None and out["llm"] is None
    assert out["gate"] is None
    assert len(out["notes"]) == 4


def test_empty_sources_honest_none(monkeypatch):
    # 空值(非异常)同样诚实 None:无快照 / tape 预热中 / market 空 / 今日无判读
    monkeypatch.setattr(macro_pulse, "_read_snapshots", lambda p: [])
    monkeypatch.setattr(market_tape, "read_tape", lambda: {"warming": True})
    monkeypatch.setattr(ff_pulse, "_load_live_cache", lambda kind, cache_dir=None: {"ok": True})
    monkeypatch.setattr(ff_pulse, "_trigger_live_refresh", lambda *a, **k: False)
    monkeypatch.setattr(ff_pulse, "read_live", lambda kind: {"market": {}, "pulled_at": "x"})
    monkeypatch.setattr(dfs, "latest_market", lambda: {
        "market_read": None, "market_tilt": None, "as_of": None, "ts": None})
    out = build_market_temp()
    assert out["global"] is None and out["board"] is None
    assert out["flow"] is None and out["llm"] is None
    assert out["gate"] is None


def test_flow_no_cache_never_blocks(monkeypatch):
    # 无缓存(新部署/var 被清)→ 绝不走 read_live(其冷启动=同步阻塞真拉,最坏 ~270s):
    # 触发后台单飞预热 + 本次诚实 None。read_live 桩死为炸——被调即证阻塞路径泄漏。
    _patch_all_ok(monkeypatch)
    triggered: list = []
    monkeypatch.setattr(ff_pulse, "_load_live_cache", lambda kind, cache_dir=None: None)
    monkeypatch.setattr(ff_pulse, "_trigger_live_refresh",
                        lambda kind, *a, **k: triggered.append(kind) or True)
    monkeypatch.setattr(ff_pulse, "read_live", _boom)
    out = build_market_temp()
    assert out["flow"] is None
    assert triggered == ["industry"]                       # 预热已触发(后台单飞,非本请求)
    assert any("无缓存" in n and "预热" in n for n in out["notes"])
    assert not any("flow 块异常" in n for n in out["notes"])   # read_live 从未被触到
    # 其余块不受影响,gate 按余下信号走(astock_temp 62.3 → neutral)
    assert out["board"] is not None and out["gate"]["level"] == "neutral"


# ── converge(market_temp=…) · 护盾 v4.4 ────────────────────────────────────

def _rows(n=3):
    # v4_total 6.0/5.9/5.8:首只 ★★★★★ 重仓(25-35),其余 ★★★★ 标准(15-20)
    return [{"s": {"code": f"C{i}", "name": f"股{i}", "ind": f"业{i}",
                   "v4_total": 6 - i * 0.1}} for i in range(n)]


def test_converge_default_output_unchanged():
    # 回归护栏:缺省/显式 None → 无 market_temp 键,输出与旧版完全一致
    out = converge(_rows(), {}, max_n=5)
    assert "market_temp" not in out
    assert out["final"][0]["band"] == {"tier": "重仓", "lo": 25, "hi": 35}
    assert not [s for it in out["final"] for s in it["shields"] if s["id"] == "v4.4"]
    assert converge(_rows(), {}, max_n=5, market_temp=None) == out


def test_converge_risk_off_halves_band_keeps_stars():
    mt = {"gate": {"level": "risk_off", "reasons": ["大盘主力净额 -397.9亿 ≤-300亿(大幅流出)"]},
          "global": None, "board": None,
          "flow": {"main_net_yi": -397.9}, "llm": None, "notes": []}
    base = converge(_rows(), {}, max_n=5)
    out = converge(_rows(), {}, max_n=5, market_temp=mt)
    assert out["market_temp"] is mt
    assert len(out["final"]) == len(base["final"])            # 绝不剔票
    assert [it["code"] for it in out["final"]] == [it["code"] for it in base["final"]]  # 排序不动
    for it, ref in zip(out["final"], base["final"]):
        assert it["stars"] == ref["stars"]                    # 星级不动
        assert it["band"]["lo"] == ref["band"]["lo"] // 2     # 仓位区间减半(int)
        assert it["band"]["hi"] == ref["band"]["hi"] // 2
        sh = [s for s in it["shields"] if s["id"] == "v4.4"]
        assert sh and sh[0]["level"] == "warn" and sh[0]["name"] == "市场温度"
        assert "-300亿" in sh[0]["text"]                       # 触发依据显形
        assert f"{ref['band']['lo']}-{ref['band']['hi']}%" in sh[0]["text"]   # 原区间显形
        assert f"{it['band']['lo']}-{it['band']['hi']}%" in sh[0]["text"]     # 新区间显形
    assert any("v4.4" in n for n in out["notes"])


def test_converge_overheat_info_shield_band_unchanged():
    mt = {"gate": {"level": "overheat",
                   "reasons": ["A股打板温度 90 ≥85(过热)且炸板率 40% ≥35%(分化)"]}}
    base = converge(_rows(), {}, max_n=5)
    out = converge(_rows(), {}, max_n=5, market_temp=mt)
    for it, ref in zip(out["final"], base["final"]):
        assert it["band"] == ref["band"]                      # 仓位档不动
        assert it["stars"] == ref["stars"]
        sh = [s for s in it["shields"] if s["id"] == "v4.4"]
        assert sh and sh[0]["level"] == "info"
    assert any("v4.4" in n for n in out["notes"])


def test_converge_neutral_and_none_gate_touch_nothing():
    base = converge(_rows(), {}, max_n=5)
    for gate in ({"level": "neutral", "reasons": []}, None):
        mt = {"gate": gate, "global": None, "board": None, "flow": None,
              "llm": None, "notes": []}
        out = converge(_rows(), {}, max_n=5, market_temp=mt)
        assert out["market_temp"] is mt                       # 上下文条永远挂(前端要显)
        for it, ref in zip(out["final"], base["final"]):
            assert it["band"] == ref["band"]
            assert not [s for s in it["shields"] if s["id"] == "v4.4"]
        assert not any("v4.4" in n for n in out["notes"])
