"""BacktestRunReq P2 扩字段: pool/hold_days/factor_name/stop_loss_pct/take_profit_pct"""
import pytest
from pydantic import ValidationError
from financial_analyst.buddy.server import BacktestRunReq


class TestBacktestRunReqExtended:
    def test_default_pool_is_csi300(self):
        req = BacktestRunReq()
        assert req.pool == "csi300"

    def test_default_hold_days_is_3(self):
        req = BacktestRunReq()
        assert req.hold_days == 3

    def test_default_factor_name_is_rev_20(self):
        req = BacktestRunReq()
        assert req.factor_name == "rev_20"

    def test_default_stop_take_are_none(self):
        req = BacktestRunReq()
        assert req.stop_loss_pct is None
        assert req.take_profit_pct is None

    def test_pool_accepts_whitelist(self):
        for pool in ("csi300", "csi_fast", "csi500", "csi800"):
            req = BacktestRunReq(pool=pool)
            assert req.pool == pool

    def test_hold_days_range(self):
        BacktestRunReq(hold_days=1)
        BacktestRunReq(hold_days=60)
        with pytest.raises(ValidationError):
            BacktestRunReq(hold_days=0)
        with pytest.raises(ValidationError):
            BacktestRunReq(hold_days=61)

    def test_stop_loss_range(self):
        BacktestRunReq(stop_loss_pct=0.05)
        BacktestRunReq(stop_loss_pct=0.5)
        with pytest.raises(ValidationError):
            BacktestRunReq(stop_loss_pct=0.0)
        with pytest.raises(ValidationError):
            BacktestRunReq(stop_loss_pct=0.6)

    def test_take_profit_range(self):
        BacktestRunReq(take_profit_pct=0.1)
        BacktestRunReq(take_profit_pct=2.0)
        with pytest.raises(ValidationError):
            BacktestRunReq(take_profit_pct=0.0)
        with pytest.raises(ValidationError):
            BacktestRunReq(take_profit_pct=2.1)

    # B-I-1 fix: pool/factor_name 在 model 层就拒 (不靠 endpoint if-block)
    def test_pool_literal_rejects_non_whitelist_at_model_level(self):
        for bad in ("all", "csiall", "csi1000", "", "CSI300"):
            with pytest.raises(ValidationError):
                BacktestRunReq(pool=bad)

    def test_factor_name_literal_rejects_non_rev_20_at_model_level(self):
        for bad in ("mom_20", "vol_60", "rev_10", ""):
            with pytest.raises(ValidationError):
                BacktestRunReq(factor_name=bad)


from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


class TestEndpointValidation:
    def setup_method(self):
        app = build_app()
        self.client = TestClient(app)

    def test_rejects_pool_all(self):
        # B-I-1: Pydantic Literal 在 body 解析期拦, FastAPI 返 422 (不再走 endpoint 自定义 400)
        r = self.client.post("/backtest/run", json={"pool": "all", "mode": "mock"})
        assert r.status_code == 422
        # FastAPI 默认 ValidationError body: {"detail":[{"loc":["body","pool"],...,"msg":"..."}]}
        body = r.json()
        assert "detail" in body
        assert any("pool" in str(e.get("loc", [])) for e in body["detail"])

    def test_rejects_non_whitelist_factor(self):
        # B-I-1: factor_name Literal 拒, 同上 422
        r = self.client.post("/backtest/run", json={"factor_name": "mom_20", "mode": "mock"})
        assert r.status_code == 422
        body = r.json()
        assert "detail" in body
        assert any("factor_name" in str(e.get("loc", [])) for e in body["detail"])

    def test_accepts_full_p2_payload(self):
        r = self.client.post("/backtest/run", json={
            "pool": "csi_fast", "hold_days": 5,
            "stop_loss_pct": 0.05, "take_profit_pct": 0.1,
            "mode": "mock"
        })
        assert r.status_code == 200
        assert r.json()["status"] == "running"
