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
    for fid in {
        "lib_gl_fb_potential_no_recent_board",
        "lib_gl_fb_event_quality_score",
        "lib_gl_fb_breakout_mainline",
        "lib_gl_fb_pullback_mainline",
        "lib_gl_fb_tail_eod_3_95",
        "lib_gl_fb_10cm_tail_turnover_hot",
        "lib_gl_fb_10cm_strict_tail_pos",
    }:
        assert fid in by_id
        assert by_id[fid]["supported"] is True


def test_catalog_allows_factorlib_indmean(monkeypatch):
    import guanlan_v2.screen.catalog as cat

    class FakeStore:
        def load_all(self):
            return [{"name": "lib_gl_fb_indmean_probe",
                     "expr": "rank(indmean(returns,industry))",
                     "description": "industry mean probe",
                     "source": "test"}]

        def _zoo_expr(self, entry):
            return entry["expr"]

    monkeypatch.setattr("guanlan_v2.factorlib.store.LibraryFactorStore", FakeStore)
    defs = cat._build()
    assert "lib_gl_fb_indmean_probe" in defs
    assert defs["lib_gl_fb_indmean_probe"]["expr"] == "rank(indmean(returns,industry))"


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


def test_model_train_second_concurrent_returns_busy(monkeypatch):
    """回归:第2个 /model/train 命中 busy-guard 时,不因 _model_public_state() 重入锁而永久死锁。"""
    import guanlan_v2.screen.api as api

    # 桩:让子进程 runner 成 no-op,_MODEL_STATE["running"] 保持 True 直到测试结束。
    monkeypatch.setattr(api, "_run_model_train_subprocess", lambda spec: None)
    # 确保从干净状态出发(防上一个测试污染)。
    api._MODEL_STATE["running"] = False

    app = _client()  # 复用本文件的 _client() helper
    body = {"name": "deadlock-test", "factor_ids": [], "base_features": ["rev_20"]}

    j1 = app.post("/screen/model/train", json=body).json()
    assert j1.get("ok") is True, f"第1次 train 应成功启动: {j1}"

    # 第2次命中 busy-guard。修复前此处会因 _model_public_state() 重入 _MODEL_LOCK 而永久挂起。
    j2 = app.post("/screen/model/train", json=body).json()
    assert j2.get("ok") is False, f"第2次 train 应返回 busy: {j2}"
    assert "已有训练在跑" in j2.get("reason", ""), f"reason 字段不对: {j2}"
    assert "state" in j2, "busy 响应应携带 state 字段"

    # 收尾:复位防跨测污染
    api._MODEL_STATE["running"] = False


def test_resolve_model_id_default_pointer(tmp_path, monkeypatch):
    from guanlan_v2.screen import api as sapi, model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    (tmp_path / "models" / "m_x").mkdir(parents=True)
    (tmp_path / "models" / "m_x" / "v4_ranking.parquet").write_bytes(b"stub")
    assert sapi._resolve_model_id("prod") == "prod"        # 没设默认 = prod(零变化)
    assert sapi._resolve_model_id("") == "prod"
    assert sapi._resolve_model_id("m_y") == "m_y"           # 显式变体原样(解析不校验存在)
    reg.set_default_model("m_x")
    assert sapi._resolve_model_id("prod") == "m_x"          # 设了默认 → 变体
    assert sapi._resolve_model_id("m_y") == "m_y"           # 显式仍优先于默认
    reg.set_default_model(None)
    assert sapi._resolve_model_id("prod") == "prod"         # 清除 → 回 prod


def test_model_default_endpoint_and_models_flag(tmp_path, monkeypatch):
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    (tmp_path / "models" / "m_x").mkdir(parents=True)
    (tmp_path / "models" / "m_x" / "meta.json").write_text(
        '{"id": "m_x", "name": "测试"}', encoding="utf-8")
    (tmp_path / "models" / "m_x" / "v4_ranking.parquet").write_bytes(b"stub")
    c = _client()
    assert c.get("/screen/models").json()["default_model"] is None
    r = c.post("/screen/model/default", json={"id": "m_x"}).json()
    assert r["ok"] is True and r["default"] == "m_x"
    j = c.get("/screen/models").json()
    assert j["default_model"] == "m_x"
    assert any(v.get("is_default") for v in j["variants"])
    assert c.post("/screen/model/default", json={"id": "m_nope"}).json()["ok"] is False
    assert c.post("/screen/model/default", json={"id": "prod"}).json()["default"] is None


# —— regime 因子族动态权重(2026-07-02 spec):opt-in 隔离 + 徽章 + 只读端点 ——

def test_screen_run_default_path_untouched(monkeypatch):
    """红线1硬回归:缺省请求绝不触碰 regime 代码,响应无 regime_weights 键。"""
    import guanlan_v2.strategy.compute.factor_regime as FR
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return None, {}

    monkeypatch.setattr(FR, "resolve_regime_weights", _spy)
    r = _client().post("/screen/run", json=dict(_CFG))
    assert r.status_code == 200
    assert called["n"] == 0
    assert "regime_weights" not in r.json()


def test_screen_run_optin_badge():
    """opt-in:v4 路径可用时必带 regime_weights 徽章(applied 或 fallback_reason 非空)。"""
    import pytest as _pytest
    r = _client().post("/screen/run", json={**_CFG, "regimeWeights": True})
    assert r.status_code == 200
    j = r.json()
    if j.get("source") != "v4_ranking":
        _pytest.skip("v4 产物不可用(artifacts 未恢复),opt-in 徽章仅在 v4 路径下发")
    assert "regime_weights" in j
    b = j["regime_weights"]
    assert b["applied"] is True or b["fallback_reason"]


def test_screen_regime_endpoint_honest(monkeypatch, tmp_path):
    """GET /screen/regime:缺产物 → ok:false;在位 → families+gate 下发。"""
    import json as _json

    import pandas as _pd

    import guanlan_v2.strategy.compute.factor_regime as FR
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    monkeypatch.setattr(FR, "FACTOR_REGIME_GATE_JSON", tmp_path / "gate.json")
    c = _client()
    assert c.get("/screen/regime").json()["ok"] is False
    _pd.DataFrame({"date": [_pd.Timestamp("2026-07-01")], "family": ["技术"],
                   "p_fav": [0.8], "state": [1],
                   "confirmed_since": [_pd.Timestamp("2026-06-20")]}
                  ).to_parquet(tmp_path / "rg.parquet", index=False)
    (tmp_path / "gate.json").write_text(_json.dumps(
        {"spec_hash": FR.SPEC_HASH, "activated": ["技术"], "asof": "2026-07-01"}),
        encoding="utf-8")
    j = c.get("/screen/regime").json()
    assert j["ok"] is True and j["families"][0]["family"] == "技术"
    assert j["gate"]["activated"] == ["技术"] and j["gate"]["stale"] is False


# ── P0 §1: picks 落档 ──────────────────────────────────────────────────────

def test_screen_run_records_picks(monkeypatch, tmp_path):
    """v4 主路径成功 → picks 落档一行 + 响应 picks_recorded:true + GET /screen/picks 读回。"""
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "picks.jsonl")
    c = _client()
    j = c.post("/screen/run", json={**_CFG, "snapshot": True, "note": "t_p0"}).json()
    assert j["ok"] is True and j["source"] == "v4_ranking"
    assert j["picks_recorded"] is True
    rows = picks.read_picks(snapshot_only=True, limit=5)
    assert rows and rows[0]["note"] == "t_p0" and rows[0]["snapshot"] is True
    assert rows[0]["model"] == j["model"] and rows[0]["date"] == j["date"]
    assert rows[0]["picks"] and rows[0]["picks"][0]["rank"] == 1
    assert rows[0]["picks"][0]["code"] and "score" in rows[0]["picks"][0]
    assert rows[0]["topN"] == _CFG["topN"] and "constraints" in rows[0]
    g = c.get("/screen/picks?snapshot_only=1&limit=3").json()
    assert g["ok"] is True and g["n"] >= 1 and g["items"][0]["note"] == "t_p0"


def test_screen_run_picks_failure_is_visible(monkeypatch, tmp_path):
    """落盘失败 → 选股照常成功,但 picks_recorded:false 显形(红线:失败显形不阻断)。"""
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "append_pick", lambda rec: False)
    j = _client().post("/screen/run", json=_CFG).json()
    assert j["ok"] is True and j["picks_recorded"] is False


def test_screen_fallback_path_does_not_record(monkeypatch):
    """玩具回退路径(非生产口径)不落档(spec §1)。"""
    import guanlan_v2.screen.api as api
    import guanlan_v2.screen.picks as picks
    calls = {"n": 0}
    def _spy(rec):
        calls["n"] += 1
        return True
    monkeypatch.setattr(picks, "append_pick", _spy)
    monkeypatch.setattr(api, "_screen_via_v4", lambda body: None)
    j = _client().post("/screen/run", json=_CFG).json()
    assert j["ok"] is True and calls["n"] == 0 and "picks_recorded" not in j


def test_record_picks_never_raises():
    """红线:_record_picks 构造 rec 阶段抛错也不许穿透(回 False,/screen/run 不 500)。"""
    from guanlan_v2.screen.api import ScreenIn, _record_picks
    body = ScreenIn()
    malformed = {"chosen": [123, "not-a-dict"], "pool": None}   # 元素无 .get → 构造必炸
    assert _record_picks(body, malformed, "prod", "2026-07-01") is False


# ── P1 §4: regen 每日定时(opt-in 默认关)────────────────────────────────────

def test_regen_scheduler_default_off(monkeypatch):
    import guanlan_v2.screen.api as api
    monkeypatch.delenv("GUANLAN_REGEN_DAILY", raising=False)
    monkeypatch.setattr(api, "_regen_sched_started", False)
    monkeypatch.setattr(api, "_REGEN_SCHED",
                        {"enabled": False, "last_auto_ts": None, "last_auto_date": None})
    api.start_regen_daily_scheduler()
    assert api._REGEN_SCHED["enabled"] is False                # env 缺省=不起线程,零行为变化
    assert api._regen_sched_started is False


def test_regen_sched_tick_fires_once_per_day(monkeypatch):
    import datetime as dt
    import guanlan_v2.screen.api as api
    calls = {"n": 0}
    monkeypatch.setattr(api, "_start_regen_bg",
                        lambda end=None: calls.__setitem__("n", calls["n"] + 1) or True)
    monkeypatch.setattr(api, "_REGEN_SCHED",
                        {"enabled": True, "last_auto_ts": None, "last_auto_date": None})
    monkeypatch.delenv("GUANLAN_REGEN_DAILY_HOUR", raising=False)
    assert api._regen_sched_tick(dt.datetime(2026, 7, 2, 17, 59)) is False   # 未到 18 点
    assert calls["n"] == 0
    assert api._regen_sched_tick(dt.datetime(2026, 7, 2, 18, 1)) is True     # 触发
    assert calls["n"] == 1 and api._REGEN_SCHED["last_auto_ts"].startswith("2026-07-02T18:01")
    assert api._regen_sched_tick(dt.datetime(2026, 7, 2, 20, 0)) is False    # 当日不重复
    assert calls["n"] == 1
    assert api._regen_sched_tick(dt.datetime(2026, 7, 3, 18, 5)) is True     # 次日再触发
    assert calls["n"] == 2


def test_start_regen_bg_singleflight(monkeypatch):
    import guanlan_v2.screen.api as api
    monkeypatch.setattr(api, "_run_regen_subprocess", lambda end: None)      # 桩:不真跑
    with api._REGEN_LOCK:
        api._REGEN_STATE["running"] = True
    assert api._start_regen_bg() is False                                    # 已在跑 → False
    with api._REGEN_LOCK:
        api._REGEN_STATE["running"] = False
    assert api._start_regen_bg() is True                                     # 空闲 → 启动
    import time
    time.sleep(0.2)                                                          # 桩线程瞬时结束
    with api._REGEN_LOCK:
        api._REGEN_STATE["running"] = False                                  # 复位防跨测污染


def test_health_has_regen_scheduler_block():
    j = _client().get("/screen/health").json()
    assert "regen_scheduler" in j
    assert set(j["regen_scheduler"].keys()) == {"enabled", "last_auto_ts"}


# ── P1 §5: models draft 过滤 + set_default 拒 draft ─────────────────────────

def _seed_variants(monkeypatch, tmp_path):
    import pandas as pd
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    row = pd.DataFrame({"code": ["SH600519"], "lgb_score": [1.0], "lgb_pct": [0.9],
                        "lgb_rank": [1], "v4_total": [5], "v4_layer": ["大盘"],
                        "date": ["2026-07-01"]})
    reg.save_variant("m_ok", row, {"id": "m_ok", "name": "过门", "oos_ic": 0.03})
    reg.save_variant("m_dr", row, {"id": "m_dr", "name": "草稿", "oos_ic": 0.001,
                                   "status": "draft",
                                   "gate": {"min_oos_ic": 0.01, "oos_ic": 0.001,
                                            "passed": False}})
    return reg


def test_models_filters_draft_by_default(monkeypatch, tmp_path):
    _seed_variants(monkeypatch, tmp_path)
    c = _client()
    ids = [v["id"] for v in c.get("/screen/models").json()["variants"]]
    assert "m_ok" in ids and "m_dr" not in ids                 # 默认不见 draft
    j = c.get("/screen/models?include_draft=1").json()
    ids2 = {v["id"]: v for v in j["variants"]}
    assert "m_dr" in ids2 and ids2["m_dr"]["status"] == "draft"


def test_set_default_rejects_draft(monkeypatch, tmp_path):
    reg = _seed_variants(monkeypatch, tmp_path)
    import pytest as _pt
    with _pt.raises(ValueError):
        reg.set_default_model("m_dr")
    j = _client().post("/screen/model/default", json={"id": "m_dr"}).json()
    assert j["ok"] is False and "draft" in j["reason"]
    reg.set_default_model("m_ok")                              # 非 draft 照常可设
    assert reg.get_default_model() == "m_ok"
