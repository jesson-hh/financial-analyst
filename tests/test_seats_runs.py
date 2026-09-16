# tests/test_seats_runs.py
# 落子「让 agent 真跑」run 化(2026-06-12 luozi-run-rework Task 1):
# - POST /seats/decide 透传 run_id 落盘(无 run_id → 落盘记录**无该键**,钉死向后兼容)
# - POST/GET /seats/runs run 头注册 + 查询(数字核匹配、逆序、文件不存在恒 200 空列表)
# - run 头注册以同 run+同票+同策略的 decide 真行校正，收口停止时在途落盘竞态
# - GET /seats/decisions 新增 run_id / exclude_runs 过滤(默认行为不变 = 含全部)
# - GET /seats/calibration 记录收集剔除带 run_id 的 decide(防 PIT 回放污染命中率)
# 全部 monkeypatch 到 tmp_path,不碰真 var/ 落盘文件。
import json
import sys
from pathlib import Path

# 优先用在仓 engine/(venv 里的可编辑安装是旧分支)—— 同 tests/test_seats_benchmark.py 先例。
_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import pandas as pd  # noqa: E402

from guanlan_v2.seats import api as seats_api  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


class _FakeLLMClient:
    """decide 内部 `from financial_analyst.llm.client import LLMClient` 的替身:
    固定成功返回一个 JSON 结论(fast 模式整段即 JSON)。"""
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        return {"choices": [{"message": {
            "content": '{"direction":"买入","confidence":77,'
                       '"rationale":"测试桩","key_evidence":["e1"]}',
            "reasoning_content": ""}}]}


class _FakeLoader:
    """日线替身:返回锚点内的最小可信 OHLCV，隔离 run_id 行为。"""

    def fetch_quote(self, code, start, end, freq):
        days = pd.date_range(end=str(end)[:10], periods=80, freq="B")
        close = [50 + i * 0.1 for i in range(len(days))]
        return pd.DataFrame({
            "trade_date": days, "open": close,
            "high": [x + 0.5 for x in close], "low": [x - 0.5 for x in close],
            "close": close, "vol": [1000 + i for i in range(len(days))],
            "amount": [close[i] * (1000 + i) for i in range(len(days))],
        })


def _patch_decide_chain(monkeypatch):
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLMClient)


def _read_jsonl(p: Path) -> list:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ───────────────────────── 1) decide 透传 run_id 落盘 ─────────────────────────

def test_decide_persists_run_id(tmp_path, monkeypatch):
    log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    _patch_decide_chain(monkeypatch)
    client = _client()

    body = {"code": "SZ300750", "name": "宁德时代", "date": "2026-06-05", "mode": "fast"}
    r = client.post("/seats/decide", json={**body, "run_id": "run_x1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    recs = _read_jsonl(log)
    assert recs[-1]["run_id"] == "run_x1"
    assert recs[-1]["kind"] == "decide" and recs[-1]["direction"] == "买入"

    # 不带 run_id → 落盘记录**无 run_id 键**(钉死:绝不落空键,旧记录形状保持)
    r2 = client.post("/seats/decide", json=body)
    assert r2.status_code == 200 and r2.json()["ok"] is True
    recs = _read_jsonl(log)
    assert len(recs) == 2
    assert "run_id" not in recs[-1]


# ───────────────────────── 2) run 头注册 + 查询 ─────────────────────────

def test_runs_clear_watermark(tmp_path, monkeypatch):
    """「清空回测历史」= append 水位标记,不改写历史行;列表只显水位之后的 run。"""
    runs_log = tmp_path / "seats_runs.jsonl"
    decisions_log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_RUNS_LOG", runs_log)
    monkeypatch.setattr(seats_api, "_DEC_LOG", decisions_log)
    client = _client()

    client.post("/seats/runs", json={"run_id": "r1", "code": "SH688012"})
    client.post("/seats/runs", json={"run_id": "r2", "code": "SZ300750"})
    # 按票清空 688012 → 本票空、别票照旧
    rc = client.post("/seats/runs/clear", json={"code": "688012"})
    assert rc.status_code == 200 and rc.json() == {"ok": True, "cleared": "688012"}
    assert client.get("/seats/runs?code=688012").json()["total"] == 0
    assert client.get("/seats/runs?code=300750").json()["total"] == 1
    # 水位后新注册 → 重新可见
    client.post("/seats/runs", json={"run_id": "r3", "code": "SH688012"})
    assert [r["run_id"] for r in client.get("/seats/runs?code=688012").json()["runs"]] == ["r3"]
    # 全局清空 → 全部隐藏;历史行仍在文件里(append-only 铁证)
    assert client.post("/seats/runs/clear", json={}).json()["cleared"] == "all"
    assert client.get("/seats/runs").json()["total"] == 0
    lines = runs_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5 and json.loads(lines[0])["run_id"] == "r1"


def test_runs_register_and_list(tmp_path, monkeypatch):
    runs_log = tmp_path / "seats_runs.jsonl"
    decisions_log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_RUNS_LOG", runs_log)
    monkeypatch.setattr(seats_api, "_DEC_LOG", decisions_log)
    client = _client()

    # 文件不存在 → 恒 200 空列表
    r0 = client.get("/seats/runs")
    assert r0.status_code == 200 and r0.json() == {"ok": True, "runs": [], "total": 0}

    head = {"run_id": "run_a1", "code": "SH688012", "strategy_id": "s1",
            "strategy_name": "动量", "tf": "D",
            "start_date": "2026-06-01", "end_date": "2026-06-05",
            "n_buy": 2, "n_sell": 0, "n_watch": 3, "n_err": 0,
            "model": "deepseek-chat"}
    r = client.post("/seats/runs", json=head)
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["run_id"] == "run_a1"

    # run_id / code 非空校验 → 422
    assert client.post("/seats/runs", json={"code": "SH688012"}).status_code == 422
    assert client.post("/seats/runs", json={"run_id": "run_bad"}).status_code == 422

    # 数字核匹配:688012 ↔ SH688012
    j = client.get("/seats/runs", params={"code": "688012"}).json()
    assert j["ok"] is True and [x["run_id"] for x in j["runs"]] == ["run_a1"]
    assert j["runs"][0]["strategy_name"] == "动量" and j["runs"][0]["n_buy"] == 2
    assert j["runs"][0].get("ts")                      # 自动补 ts

    # 再注册一条 → 逆序(新在前)
    client.post("/seats/runs", json={**head, "run_id": "run_b2"})
    j2 = client.get("/seats/runs", params={"code": "SH688012"}).json()
    assert [x["run_id"] for x in j2["runs"]] == ["run_b2", "run_a1"]
    # 不匹配的数字核 → 空
    assert client.get("/seats/runs", params={"code": "600519"}).json()["runs"] == []


def test_runs_register_reconciles_from_matching_persisted_decisions(tmp_path, monkeypatch):
    """停止竞态的后端收口：run 头必须以同 run+同票+同策略的真 decide 行为准。

    模拟前端只计到 5 条时，第 6 个在途请求已在服务端落盘；同时掺入
    同 run_id 的其他股票、其他策略以及条件单，确保都不会污染汇总。
    """
    decisions_log = tmp_path / "seats_decisions.jsonl"
    runs_log = tmp_path / "seats_runs.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", decisions_log)
    monkeypatch.setattr(seats_api, "_RUNS_LOG", runs_log)

    directions = ["买入", "观望", "卖出", "观望", "买入", "观望"]
    dates = [f"2026-02-{day:02d}" for day in range(20, 26)]
    for direction, asof in zip(directions, dates):
        seats_api._persist_decision("decide", {
            "run_id": "run_stop_race", "code": "SZ300750", "strategy_id": "s_main",
            "direction": direction, "asof": asof,
            "model_name": "deepseek/deepseek-v4-flash",
        })

    # 噪声 1：非 decide，即使 run/code/strategy 全相同也不统计。
    seats_api._persist_decision("order", {
        "run_id": "run_stop_race", "code": "SZ300750", "strategy_id": "s_main",
        "side": "买入", "asof": "2026-02-26",
    })
    # 噪声 2/3：同 run_id 但票或策略不同。
    seats_api._persist_decision("decide", {
        "run_id": "run_stop_race", "code": "SH600519", "strategy_id": "s_main",
        "direction": "买入", "asof": "2026-02-27",
    })
    seats_api._persist_decision("decide", {
        "run_id": "run_stop_race", "code": "SZ300750", "strategy_id": "s_other",
        "direction": "卖出", "asof": "2026-02-28",
    })

    stale_header = {
        "run_id": "run_stop_race", "code": "300750", "strategy_id": "s_main",
        "strategy_name": "动量", "tf": "D",
        "start_date": "2026-02-20", "end_date": "2026-02-24",
        "n_buy": 1, "n_sell": 1, "n_watch": 3,
        "n_err": 2, "model": "stale-client-model",
    }
    client = _client()
    r = client.post("/seats/runs", json=stale_header)
    assert r.status_code == 200 and r.json()["ok"] is True

    head = client.get("/seats/runs", params={"code": "SZ300750"}).json()["runs"][0]
    assert (head["n_buy"], head["n_sell"], head["n_watch"]) == (2, 1, 3)
    assert head["start_date"] == "2026-02-20"
    assert head["end_date"] == "2026-02-25"       # 第 6 条在途落盘已纳入
    assert head["model"] == "deepseek/deepseek-v4-flash"
    assert head["n_err"] == 2                       # 失败数无成功落盘真相，不猜


def test_runs_register_does_not_merge_ambiguous_strategies(tmp_path, monkeypatch):
    """旧调用方不传 strategy_id 且同 run+code 混有多策略时，保留原头而不猜。"""
    decisions_log = tmp_path / "seats_decisions.jsonl"
    runs_log = tmp_path / "seats_runs.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", decisions_log)
    monkeypatch.setattr(seats_api, "_RUNS_LOG", runs_log)
    for strategy_id, direction in (("s1", "买入"), ("s2", "卖出")):
        seats_api._persist_decision("decide", {
            "run_id": "run_ambiguous", "code": "SZ300750", "strategy_id": strategy_id,
            "direction": direction, "asof": "2026-02-20",
        })

    client = _client()
    original = {
        "run_id": "run_ambiguous", "code": "SZ300750",
        "n_buy": 7, "n_sell": 8, "n_watch": 9,
        "start_date": "legacy-start", "end_date": "legacy-end", "n_err": 4,
    }
    assert client.post("/seats/runs", json=original).json()["ok"] is True
    head = client.get("/seats/runs", params={"code": "300750"}).json()["runs"][0]
    for key in ("n_buy", "n_sell", "n_watch", "start_date", "end_date", "n_err"):
        assert head[key] == original[key]


def test_runs_list_projects_historical_stale_header_without_rewriting_log(tmp_path, monkeypatch):
    """旧的错误 run 头在读取时按真行修正，但 append-only 原始日志不被改写。"""
    decisions_log = tmp_path / "seats_decisions.jsonl"
    runs_log = tmp_path / "seats_runs.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", decisions_log)
    monkeypatch.setattr(seats_api, "_RUNS_LOG", runs_log)

    stale = {
        "run_id": "run_old_race", "code": "SZ300750", "strategy_id": "s_main",
        "start_date": "2026-02-20", "end_date": "2026-02-24",
        "n_buy": 0, "n_sell": 0, "n_watch": 5, "n_err": 0,
    }
    runs_log.write_text(json.dumps(stale, ensure_ascii=False) + "\n", encoding="utf-8")
    for day in range(20, 26):
        seats_api._persist_decision("decide", {
            "run_id": "run_old_race", "code": "300750", "strategy_id": "s_main",
            "direction": "观望", "asof": f"2026-02-{day:02d}",
            "model_name": "deepseek/deepseek-v4-flash",
        })

    head = _client().get("/seats/runs", params={"code": "300750"}).json()["runs"][0]
    assert head["n_watch"] == 6
    assert head["end_date"] == "2026-02-25"
    assert head["model"] == "deepseek/deepseek-v4-flash"
    assert json.loads(runs_log.read_text(encoding="utf-8"))["n_watch"] == 5
    assert json.loads(runs_log.read_text(encoding="utf-8"))["end_date"] == "2026-02-24"


# ───────────────────────── 3) /decisions 的 run 过滤 ─────────────────────────

def test_decisions_filter_run_id(tmp_path, monkeypatch):
    log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    seats_api._persist_decision("decide", {"code": "SH600001", "direction": "买入",
                                           "run_id": "run_a"})
    seats_api._persist_decision("decide", {"code": "SH600001", "direction": "观望",
                                           "run_id": "run_a"})
    seats_api._persist_decision("decide", {"code": "SH600001", "direction": "卖出"})
    client = _client()

    j = client.get("/seats/decisions", params={"run_id": "run_a"}).json()
    assert j["total"] == 2 and all(x["run_id"] == "run_a" for x in j["decisions"])

    # 无参数默认含全部 3 条(向后兼容:旧调用方行为不变)
    j_all = client.get("/seats/decisions").json()
    assert j_all["total"] == 3

    j_x = client.get("/seats/decisions", params={"exclude_runs": 1}).json()
    assert j_x["total"] == 1
    assert "run_id" not in j_x["decisions"][0]
    assert j_x["decisions"][0]["direction"] == "卖出"


# ───────────────────────── 4) calibration 剔除 run 记录 ─────────────────────────

def test_calibration_excludes_runs(tmp_path, monkeypatch):
    log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    seats_api._persist_decision("decide", {"code": "SH600001", "direction": "买入",
                                           "confidence": 85, "asof": "2026-06-01",
                                           "run_id": "run_z"})
    seats_api._persist_decision("decide", {"code": "SH600002", "direction": "买入",
                                           "confidence": 70, "asof": "2026-06-01"})

    monkeypatch.setattr(seats_api, "local_query_loader", lambda: _FakeLoader())

    import guanlan_v2.seats.calibration as calib
    captured: dict = {}

    def _fake_evaluate(records, closes_by_code, horizon=5):
        captured["records"] = list(records)
        return []

    monkeypatch.setattr(calib, "evaluate", _fake_evaluate)

    r = _client().get("/seats/calibration")          # 新 router → _CALIB_CACHE 全新,无缓存干扰
    assert r.status_code == 200 and r.json()["ok"] is True
    fed = captured["records"]
    assert all(not x.get("run_id") for x in fed)     # run 记录绝不进校准
    assert [x["code"] for x in fed if x.get("kind") == "decide"] == ["SH600002"]


# ───────────────────────── 5) /decisions 数字核 code 匹配 ─────────────────────────

def test_decisions_filter_code_numeric_core(tmp_path, monkeypatch):
    """裸码 ↔ 带前缀同口径(数字核):落盘 SZ000630,000630 / SZ000630 都命中;
    无 code = 全部(向后兼容);非等价数字核不误命中。"""
    log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    seats_api._persist_decision("decide", {"code": "SZ000630", "direction": "观望"})
    seats_api._persist_decision("decide", {"code": "SH600519", "direction": "买入"})
    client = _client()

    assert client.get("/seats/decisions", params={"code": "000630"}).json()["total"] == 1
    assert client.get("/seats/decisions", params={"code": "SZ000630"}).json()["total"] == 1
    assert client.get("/seats/decisions", params={"code": "630"}).json()["total"] == 0
    assert client.get("/seats/decisions").json()["total"] == 2
