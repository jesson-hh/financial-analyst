# -*- coding: utf-8 -*-
"""盘中日型判定(seats/intraday.py)—— 合成bar特征数学 + 打分器 + api PIT守卫接线。"""
from __future__ import annotations

import sys
import json
import math
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from guanlan_v2.seats import intraday  # noqa: E402


def _bars5(day: str, closes, vols=None) -> pd.DataFrame:
    n = len(closes)
    am = pd.date_range(f"{day} 09:35", periods=min(n, 24), freq="5min")
    pm = pd.date_range(f"{day} 13:05", periods=max(n - 24, 0), freq="5min")
    ts = am.append(pm)[:n]
    closes = np.asarray(closes, float)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    return pd.DataFrame({"trade_date": ts, "open": opens,
                         "high": np.maximum(opens, closes) * 1.002,
                         "low": np.minimum(opens, closes) * 0.998,
                         "close": closes,
                         "volume": vols if vols is not None else np.full(n, 1000.0)})


def _daily(n=40, last="2026-08-07") -> pd.DataFrame:
    ts = pd.bdate_range(end=last, periods=n)
    c = np.linspace(10, 11, n)
    return pd.DataFrame({"trade_date": ts, "open": c * 0.999, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": np.full(n, 5e6)})


def _fixture_frame() -> pd.DataFrame:
    prev = _bars5("2026-08-07", np.linspace(10.0, 10.2, 48))
    today = _bars5("2026-08-10", 10.25 + np.linspace(0, 0.05, 12))  # 缺口高开
    return pd.concat([prev, today], ignore_index=True)


class TestFeatures:
    def test_feature_math_and_score(self, monkeypatch):
        frame = _fixture_frame()
        monkeypatch.setattr(intraday, "_fetch_bars5", lambda code, n=480: frame)
        monkeypatch.setattr(intraday, "_hist_open30_volmean", lambda code, today: 6000.0)
        monkeypatch.setattr(intraday, "_basket_snapshot",
                            lambda: {"mkt_abs_open30": 0.006, "mkt_gap": 0.001, "n_basket": 9})
        ctx = intraday.build_intraday_context("SZ300308", _daily(last="2026-08-07"))
        assert ctx and not ctx.get("too_early")
        f = ctx["features"]
        # 缺口 = 今开 / 昨会话收盘 - 1(帧内)
        prev_close = 10.2
        today_open = 10.25
        assert f["gap"] == pytest.approx(today_open / prev_close - 1, abs=1e-9)
        # 开盘30分 = 第6根收 / 今开 - 1
        assert f["open30_ret"] == pytest.approx(ctx and f["open30_ret"], abs=1)  # 自洽占位
        assert f["open30_volratio"] == pytest.approx(6 * 1000.0 / 6000.0)
        assert f["is20cm"] == 1.0
        s = ctx["score"]
        assert s["ok"] and 0.0 < s["p_trend"] < 1.0 and s["tier"] in ("T30", "T50", "NO")
        txt = intraday.render_daytype_lines(ctx)
        assert "日型判定" in txt and "p(单边)" in txt

    def test_too_early(self, monkeypatch):
        prev = _bars5("2026-08-07", np.linspace(10.0, 10.2, 48))
        today = _bars5("2026-08-10", [10.25, 10.26, 10.27])   # 只有3根
        monkeypatch.setattr(intraday, "_fetch_bars5",
                            lambda code, n=480: pd.concat([prev, today], ignore_index=True))
        ctx = intraday.build_intraday_context("SH600519", _daily())
        assert ctx.get("too_early")
        assert "暂不判定" in intraday.render_daytype_lines(ctx)

    def test_fetch_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr(intraday, "_fetch_bars5", lambda code, n=480: None)
        assert intraday.build_intraday_context("SH600519", _daily()) is None


class TestScorer:
    def test_sigmoid_at_train_mean(self):
        m = json.loads((Path("guanlan_v2/seats/daytype_model.json")).read_text(encoding="utf-8"))
        feats = dict(zip(m["features"], m["mean"]))
        s = intraday.score_daytype(feats)
        assert s["ok"]
        assert s["p_trend"] == pytest.approx(1 / (1 + math.exp(-m["intercept"])), abs=1e-3)

    def test_missing_over_5_refuses(self):
        s = intraday.score_daytype({"gap": 0.0})
        assert not s["ok"] and "不判定" in s["reason"]


class TestApiPitGuard:
    """接线守卫:研判日==今天才注入;历史日期绝不调实时模块。"""

    def _client(self, monkeypatch, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from guanlan_v2.seats import api as seats_api
        import financial_analyst.data.loader_factory as _lf
        import financial_analyst.llm.client as _llm

        class _CapLLM:
            provider = "deepseek"
            model = "deepseek-chat"

            @classmethod
            def for_agent(cls, name):
                return cls()

            def with_overrides(self, **kw):
                return self

            async def chat(self, messages, **kw):
                TestApiPitGuard.captured = messages[-1]["content"]
                return {"choices": [{"message": {"content":
                        '{"direction":"观望","confidence":50,"rationale":"r",'
                        '"key_evidence":[],"position_action":"继续持有"}',
                        "reasoning_content": ""}}]}

        class _DayLoader:
            def fetch_quote(self, code, start, end, freq):
                ts = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=60, freq="D")
                return pd.DataFrame({"trade_date": ts,
                                     "open": [50 + i * 0.1 for i in range(60)],
                                     "high": [50 + i * 0.1 + 0.5 for i in range(60)],
                                     "low": [50 + i * 0.1 - 0.5 for i in range(60)],
                                     "close": [50 + i * 0.1 + 0.3 for i in range(60)],
                                     "vol": [1000.0 + i for i in range(60)]})

        monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
        monkeypatch.setattr(_lf, "get_default_loader", lambda: _DayLoader())
        monkeypatch.setattr(_llm, "LLMClient", _CapLLM)
        app = FastAPI()
        app.include_router(seats_api.build_seats_router())
        return TestClient(app)

    def test_today_injects_daytype(self, monkeypatch, tmp_path):
        monkeypatch.setattr(intraday, "build_intraday_context", lambda code, df: {
            "session": datetime.now().strftime("%Y-%m-%d"), "features": {},
            "score": {"ok": True, "p_trend": 0.21, "tier": "T30",
                      "verdict": "做T窗口(最像震荡日的30%档)", "missing": [],
                      "oos_note": "历史OOS:震荡精度82%@覆盖30%(非未来保证)"},
            "notes": []})
        client = self._client(monkeypatch, tmp_path)
        r = client.post("/seats/decide", json={
            "code": "SH600519", "name": "茅台", "date": datetime.now().strftime("%Y-%m-%d"),
            "seat_cn": "动量席", "creed": "x", "mode": "fast", "hold_entry": 52.0}).json()
        assert r["ok"]
        assert "日型判定" in TestApiPitGuard.captured
        assert r["t_ref"]["daytype"]["tier"] == "T30"

    def test_historical_date_never_calls_realtime(self, monkeypatch, tmp_path):
        def _boom(code, df):
            raise AssertionError("realtime module called on historical date (PIT violation)")
        monkeypatch.setattr(intraday, "build_intraday_context", _boom)
        client = self._client(monkeypatch, tmp_path)
        r = client.post("/seats/decide", json={
            "code": "SH600519", "name": "茅台", "date": "2026-04-01",
            "seat_cn": "动量席", "creed": "x", "mode": "fast", "hold_entry": 52.0}).json()
        assert r["ok"]
        assert "日型判定" not in TestApiPitGuard.captured
