"""篮子前向收益(P1 §2)单测:纯函数 + 端点(fake loader)。"""
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import guanlan_v2.seats.api as seats_api  # noqa: E402
from guanlan_v2.seats.basket_perf import compute_basket_perf  # noqa: E402


_SER = [("2026-06-02", 10.0), ("2026-06-03", 10.5), ("2026-06-04", 11.0),
        ("2026-06-05", 10.8), ("2026-06-08", 11.2), ("2026-06-09", 11.5)]
_BENCH = pd.DataFrame({"date": [d for d, _ in _SER],
                       "ret": [0.0, 0.01, 0.01, -0.005, 0.01, 0.005], "n": [100] * 6})


def test_matured_basket_with_bench():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-02", horizon=3,
                              bench_df=_BENCH)
    assert out["ok"] is True and out["n"] == 1 and out["matured_n"] == 1
    p = out["per_code"][0]
    assert p["entry_date"] == "2026-06-02" and p["exit_date"] == "2026-06-05"
    assert p["ret"] == pytest.approx(10.8 / 10.0 - 1) and p["matured"] is True
    assert out["bench_ret"] == pytest.approx(1.01 * 1.01 * 0.995 - 1)
    assert out["excess"] == pytest.approx(out["avg_ret"] - out["bench_ret"])
    assert "口径" in out["note"]


def test_entry_shifts_to_first_bar_after_start():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-06", horizon=1,
                              bench_df=_BENCH)                 # 06-06/07 无bar → 首根 06-08
    p = out["per_code"][0]
    assert p["entry_date"] == "2026-06-08" and p["exit_date"] == "2026-06-09"


def test_immature_honest():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-08", horizon=5,
                              bench_df=_BENCH)                 # 只剩1根后续bar
    p = out["per_code"][0]
    assert p["matured"] is False and out["matured_n"] == 0
    assert p["exit_date"] == "2026-06-09"                      # 给到最新段,不冒充已实现
    assert out["bench_ret"] is not None                        # 同窗基准仍可算


def test_bench_missing_and_partial():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-02", horizon=3,
                              bench_df=None)
    assert out["ok"] is True and out["bench_ret"] is None and out["excess"] is None
    short_bench = _BENCH[_BENCH["date"] <= "2026-06-04"]       # 尾部不覆盖 → 整体 null
    out2 = compute_basket_perf({"SH600001": _SER}, start="2026-06-02", horizon=3,
                               bench_df=short_bench)
    assert out2["bench_ret"] is None


def test_bad_codes_warned_and_all_bad_fails():
    out = compute_basket_perf({"SH600001": _SER, "SHBAD": []}, start="2026-06-02",
                              horizon=3, bench_df=None)
    assert out["n"] == 1 and any("SHBAD" in w for w in out["warnings"])
    out2 = compute_basket_perf({"SHBAD": []}, start="2026-06-02", horizon=3)
    assert out2["ok"] is False and "reason" in out2


class _FakeLoader:
    def fetch_quote(self, code, start, end, freq):
        if re.sub(r"\D", "", str(code)) != "600001":
            return None
        return pd.DataFrame({"trade_date": [d for d, _ in _SER],
                             "close": [v for _, v in _SER]})


class _RecordingLoader:
    """录下每次 fetch_quote 收到的 start 实参——钉死『取数起始日=每对 pick ts』。"""
    def __init__(self):
        self.calls = []

    def fetch_quote(self, code, start, end, freq):
        self.calls.append((str(code), str(start)))
        return pd.DataFrame({"trade_date": [d for d, _ in _SER],
                             "close": [v for _, v in _SER]})


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    """kind=rerank_ab 测试用:挂 seats 路由(basket_perf 归属 seats.api)。"""
    return _client()


def test_endpoint_basket_perf(monkeypatch):
    import financial_analyst.data.loader_factory as _lf
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader())
    monkeypatch.setattr(EQ, "load_eqw_ret", lambda: _BENCH)
    j = _client().get("/seats/basket_perf?codes=600001,999999&start=2026-06-02&horizon=3").json()
    assert j["ok"] is True and j["n"] == 1 and j["matured_n"] == 1
    assert j["bench_ret"] == pytest.approx(1.01 * 1.01 * 0.995 - 1)
    assert any("999999" in w for w in j["warnings"])           # 坏票剔除+显形


def test_endpoint_requires_params():
    j = _client().get("/seats/basket_perf").json()
    assert j["ok"] is False and "必填" in j["reason"]


def test_basket_perf_default_behavior_unchanged(client):
    """无 kind:codes/start 必填契约原样(守护现有消费方零变化)。"""
    r = client.get("/seats/basket_perf").json()
    assert r["ok"] is False and "必填" in r["reason"]


def test_basket_perf_rerank_ab_pairs(tmp_path, monkeypatch, client):
    import financial_analyst.data.loader_factory as _lf
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader())
    monkeypatch.setattr(EQ, "load_eqw_ret", lambda: _BENCH)
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    ts = "2026-07-01T18:00:00"
    pk.append_pick({"kind": "rerank_ab", "arm": "data", "codes": ["SH600000"],
                    "run_id": "rs_a", "ts": ts, "snapshot": False})
    pk.append_pick({"kind": "rerank_ab", "arm": "rerank", "codes": ["SZ000001"],
                    "run_id": "rs_a", "ts": ts, "snapshot": False,
                    "model": "deepseek/deepseek-reasoner"})
    pk.append_pick({"kind": "rerank_ab", "arm": "data", "codes": ["SH600001"],
                    "run_id": "rs_half", "ts": ts, "snapshot": False})   # 半对→跳过
    r = client.get("/seats/basket_perf", params={"kind": "rerank_ab", "limit": 5}).json()
    assert r["ok"] and r["kind"] == "rerank_ab" and r["n"] == 1
    pair = r["pairs"][0]
    assert pair["run_id"] == "rs_a" and set(pair["arms"]) == {"data", "rerank"}
    assert pair["model"] == "deepseek/deepseek-reasoner"
    # 两臂各为 compute_basket_perf 结果;测试环境无行情时两臂 ok:false 也如实并列(不编数)


def test_rerank_ab_uses_per_pair_start(tmp_path, monkeypatch, client):
    """缺陷A 的精确反面:取数起始日=每对 pick ts;查询参数 start 被忽略(docstring 契约)。"""
    import financial_analyst.data.loader_factory as _lf
    import guanlan_v2.strategy.compute.eqw_market as EQ
    from guanlan_v2.screen import picks as pk
    rec = _RecordingLoader()
    monkeypatch.setattr(_lf, "get_default_loader", lambda: rec)
    monkeypatch.setattr(EQ, "load_eqw_ret", lambda: _BENCH)
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    ts = "2026-06-01T18:00:00"                      # 早于 _SER 首根 06-02 → 首根即入场
    for arm in ("data", "rerank"):
        pk.append_pick({"kind": "rerank_ab", "arm": arm, "codes": ["SH600001"],
                        "run_id": "rs_a", "ts": ts, "snapshot": False})
    r = client.get("/seats/basket_perf",
                   params={"kind": "rerank_ab", "limit": 5, "start": "2099-01-01"}).json()
    assert r["ok"] and r["n"] == 1
    pair = r["pairs"][0]
    assert pair["start"] == "2026-06-01"                              # 载荷显形实际起始日
    assert rec.calls and all(s == "2026-06-01" for _, s in rec.calls)  # 每次取数都用对内 ts,2099 被忽略
    for arm in ("data", "rerank"):
        a = pair["arms"][arm]
        assert a["ok"] is True and a["per_code"][0]["entry_date"] == "2026-06-02"
    assert pair["excess_diff"] == pytest.approx(0.0)                  # 两臂同码 → 恒等


def test_default_path_validation_first(monkeypatch, client):
    """codes/start 必填校验须先于任何 I/O 构造(loader 等)执行——即便 loader 会抛异常,
    默认路径(无 kind)仍必须先给出确定性的「codes 与 start 必填」,不能被 loader 异常劫持。
    """
    import financial_analyst.data.loader_factory as _lf

    def _boom():
        raise RuntimeError("loader 故意炸——证明校验没抢在它前面跑")

    monkeypatch.setattr(_lf, "get_default_loader", _boom)
    r = client.get("/seats/basket_perf").json()
    assert r == {"ok": False, "reason": "codes 与 start 必填"}
