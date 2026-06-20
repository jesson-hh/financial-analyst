# guanlan_v2.screen.api · /screen/* 端点测试(FastAPI TestClient)
#
# /screen/run 现以 vendored v4 排名为 L1(消费产物,无需引擎/qlib);引擎面板仅补展示价,
# 测试环境无引擎时价为 0、不影响排名/行业/主线。契约形(chosen/benched/pool/scored/stat)恒在。
import re
from collections import Counter

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.screen.api import build_screen_router

_SHAPE_KEYS = ("chosen", "benched", "pool", "scored", "stat")
_CFG = {"factors": [{"id": "fa_reversal", "w": 1.0}], "topN": 20, "industryNeutral": True,
        "indCap": 0.25, "liqMin": 5, "exclST": True, "universe": "csi_fast"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_screen_router())
    return TestClient(app)


def test_factors_lists_defs():
    """选股页 2.0:动态目录(~56 因子·11 族·全 supported);幽灵因子(北向/PEAD/消息面)已除名。"""
    j = _client().get("/screen/factors").json()
    assert j["ok"] is True
    by_id = {f["id"]: f for f in j["factors"]}
    assert by_id["fa_reversal"]["supported"] is True       # legacy 价量仍在(老配置兼容)
    assert "fa_news" not in by_id and "fa_north" not in by_id and "fa_pead" not in by_id  # 幽灵已清
    assert len(by_id) >= 40                                # 目录扩容(workflow catalog 过滤后)
    assert all(f["supported"] for f in j["factors"])       # 不再下发不可用因子
    assert any("idx_ret" in (f.get("expr") or "") for f in j["factors"])  # 含大盘因子(共振/跟随)
    assert {"family", "ic", "desc"} <= set(j["factors"][0])  # 族/实测IC字段(ic 可为 null)
    assert j.get("families")                                # 族序下发


def test_health_reports_v4_freshness():
    j = _client().get("/screen/health").json()
    assert j["ok"] is True
    assert j["v4_ranking"]["rows"] > 1000
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", j["v4_ranking"]["date"])


def test_run_uses_vendored_v4():
    r = _client().post("/screen/run", json=_CFG)
    assert r.status_code == 200            # 诚实:HTTP 恒 200
    j = r.json()
    assert j["ok"] is True
    assert j["source"] == "v4_ranking"     # L1 走 vendored v4(非玩具因子)
    for k in _SHAPE_KEYS:
        assert k in j
    assert j["stat"]["n"] == len(j["chosen"])
    assert len(j["scored"]) >= len(j["chosen"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", j["date"])


def test_run_topN_respected():
    j = _client().post("/screen/run", json={**_CFG, "topN": 5}).json()
    assert j["ok"] is True
    assert len(j["chosen"]) <= 5


def test_run_rows_carry_v4_and_mainline():
    j = _client().post("/screen/run", json=_CFG).json()
    chosen = j["chosen"]
    assert chosen
    assert any(x["s"].get("v4_total") is not None for x in chosen)   # 五维分透传
    assert all("lgb_rank" in x["s"] for x in chosen)                  # LGB 排名透传
    assert j.get("mainline_as_of")                                    # L2 主线 as_of
    assert any(x["s"].get("mainline") for x in chosen)                # 行业 join 出主线状态


def test_industry_neutral_cap():
    j = _client().post("/screen/run", json={**_CFG, "topN": 20, "industryNeutral": True, "indCap": 0.25}).json()
    cnt = Counter(x["s"]["ind"] for x in j["chosen"])
    assert all(v <= 5 for v in cnt.values())                          # 每行业 ≤ ceil(20*0.25)=5


def test_run_carries_l4_views_and_l5_rating():
    j = _client().post("/screen/run", json=_CFG).json()
    chosen = j["chosen"]
    assert chosen
    # L4:逐行九视角 readout(V1-V10,每项带 conf 标签)
    v0 = chosen[0]["views"]
    assert [v["v"] for v in v0] == ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10"]
    assert all(v["conf"] in ("data", "proxy", "gap") for v in v0)
    # L5:逐行评级 + 仓位档 + 护盾透传
    assert all("rating" in x["s"] and "pos_band" in x["s"] for x in chosen)
    assert all("shields" in x["s"] for x in chosen)


def test_run_decision_converges_to_5():
    j = _client().post("/screen/run", json=_CFG).json()
    dec = j["decision"]
    assert len(dec["final"]) <= 5                                     # ≤5 收敛
    inds = [f["ind"] for f in dec["final"]]
    assert len(inds) == len(set(inds))                               # 行业去重
    assert all(f["stars"] >= 4.0 for f in dec["final"])              # 仅 ★★★★+ 入持仓
    assert all(f.get("band") and f.get("op") for f in dec["final"])  # 带仓位档 + 操作建议
    assert "market" in j                                             # V1 节奏(可能为 None)


def test_run_v4_path_reports_unsupported_factors():
    """v4 主路径诚实回报未识别因子(原静默丢弃 → 看不见的错)。掌控审计 2026-06-15。"""
    j = _client().post("/screen/run", json={
        **_CFG, "blend": 0.5,
        "factors": [{"id": "fa_reversal", "w": 1.0}, {"id": "totally_bogus_id", "w": 1.0}]}).json()
    assert j["ok"] is True and j["source"] == "v4_ranking"
    assert "unsupported_factors" in j
    assert "totally_bogus_id" in j["unsupported_factors"]   # 不存在/无表达式的 id 现形
    assert "fa_reversal" not in j["unsupported_factors"]    # 合法因子不误报


def test_run_v4_path_no_unsupported_when_all_valid():
    j = _client().post("/screen/run", json={
        **_CFG, "blend": 0.5, "factors": [{"id": "fa_reversal", "w": 1.0}]}).json()
    assert j["ok"] is True and j.get("unsupported_factors") == []


def test_run_uses_model_variant(monkeypatch):
    # _screen_via_v4 调 `S.load_v4_ranking`(S = guanlan_v2.strategy 包级再导出绑定),
    # 故 spy 必须打在包属性上,而非 ranking 子模块(那是另一个绑定,看不到)。
    import guanlan_v2.strategy as S
    calls, real = {}, S.load_v4_ranking

    def spy(model_id=None):
        calls["model_id"] = model_id
        return real(model_id=model_id)

    monkeypatch.setattr(S, "load_v4_ranking", spy)
    j = _client().post("/screen/run", json={**_CFG, "model": "prod"}).json()
    assert j["ok"] is True and calls.get("model_id") in (None, "prod")
    assert j.get("model") == "prod"                             # 响应回报实际所跑模型


def test_run_bad_model_falls_back():
    j = _client().post("/screen/run", json={**_CFG, "model": "does_not_exist"}).json()
    assert j["ok"] is True and j["source"] == "v4_ranking"      # 回落 prod,不 500
    assert j.get("model") == "prod"                             # 变体缺失 → 诚实回落 prod


def test_model_endpoints(monkeypatch, tmp_path):
    import pandas as pd, guanlan_v2.screen.api as api
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    reg.save_variant("m_seed", pd.DataFrame({"code":["SH600519"],"lgb_score":[1.0],"lgb_pct":[0.9],
        "lgb_rank":[1],"v4_total":[5],"v4_layer":["大盘"],"date":["2026-06-17"]}),
        {"id":"m_seed","name":"种子","oos_ic":0.03})
    c = _client()
    assert any(v["id"]=="m_seed" for v in c.get("/screen/models").json()["variants"])
    monkeypatch.setattr(api, "_run_model_train_subprocess", lambda spec: None)
    assert c.post("/screen/model/train", json={"name":"t","factor_ids":[],"base_features":["rev_20"]}).json()["started"] is True
    assert c.post("/screen/model/train", json={"name":"t","factor_ids":[],"base_features":[]}).json()["ok"] is False
    api._MODEL_STATE["running"] = False   # 桩 runner 不清 running,手动复位防跨测污染
    assert c.post("/screen/model/delete", json={"id":"prod"}).json()["ok"] is False
    assert c.post("/screen/model/delete", json={"id":"m_seed"}).json()["ok"] is True
    assert c.get("/screen/base_features").json()["ok"] is True
