# tests/test_seats_benchmark.py
# 落子 /seats/benchmark · 真沪深300日收盘(与 workflow 绩效同源 etf_index.parquet 399300.SZ)。
# 供盯盘/舰队净值对标,替代前端 mulberry32 合成指数。失败 ok:False 诚实降级(前端隐藏基准线)。
import sys
from pathlib import Path

# 优先用在仓 engine/(venv 里的可编辑安装是旧分支)—— 同 tests/test_ta_indicators.py 先例。
_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from guanlan_v2.seats.api import build_seats_router  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_seats_router())
    return TestClient(app)


def test_seats_benchmark_returns_real_csi300():
    r = _client().get("/seats/benchmark", params={"n": 250})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    bars = j["bars"]
    assert len(bars) > 100
    dates = [b["date"] for b in bars]
    assert dates == sorted(dates)                      # 升序
    assert all(b["close"] > 0 for b in bars)           # 真价
    assert bars[-1]["date"] >= "2026-01-01"            # 新鲜


def test_seats_benchmark_window():
    r = _client().get("/seats/benchmark",
                      params={"start": "2025-06-01", "end": "2026-06-09"})
    j = r.json()
    assert j["ok"] is True
    assert j["bars"][0]["date"] >= "2025-06-01"
    assert j["bars"][-1]["date"] <= "2026-06-09"


def test_seats_benchmark_degrades_honestly(monkeypatch):
    """读失败 → HTTP 200 + ok:False(护前端 `j.ok && j.bars` 契约:隐藏基准线,绝不 500/假基准)。"""
    from guanlan_v2.seats import api as seats_api

    def _boom(**kw):
        raise RuntimeError("etf_index 读失败(测试注入)")

    monkeypatch.setattr(seats_api, "_load_csi300", _boom)
    r = _client().get("/seats/benchmark")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert "error" in j
