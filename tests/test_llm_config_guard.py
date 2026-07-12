# -*- coding: utf-8 -*-
"""座席档位 schema 守护:字段白名单/模型在册/类型合法;FA_CONFIG_DIR 路由钉死。"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_KEYS = {"provider", "model", "max_tokens", "timeout"}


def _cfg():
    with open(ROOT / "config" / "llm.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_overrides_schema_and_models_in_register():
    cfg = _cfg()
    providers = cfg["providers"]
    for name, ov in (cfg.get("agent_overrides") or {}).items():
        assert set(ov) <= ALLOWED_KEYS, f"{name} 有未知字段: {set(ov) - ALLOWED_KEYS}"
        prov = ov.get("provider", cfg["default_provider"])
        assert prov in providers, f"{name} provider 不在册"
        assert ov.get("model", cfg["default_model"]) in providers[prov]["models"], f"{name} model 不在册"
        if "max_tokens" in ov:
            assert isinstance(ov["max_tokens"], int) and ov["max_tokens"] > 0
        if "timeout" in ov:
            assert isinstance(ov["timeout"], (int, float)) and ov["timeout"] > 0


def test_deep_tier_seats_exist():
    ov = _cfg().get("agent_overrides") or {}
    for seat in ("rerank", "review_officer",
                 "bull-advocate", "bear-advocate", "risk-officer", "report-writer"):
        assert ov.get(seat, {}).get("model") == "deepseek-reasoner", f"{seat} 应为 deep 档"
        assert ov[seat].get("timeout", 0) >= 180, f"{seat} deep 档须放宽超时(reasoner 思考 1-3 分钟)"
    assert ov.get("review_section", {}).get("model") == "deepseek-chat"


def test_fa_config_dir_routes_to_repo():
    """9999 进程模型路由钉死:server._CONFIG_DIR 必须指向仓内 config/ 且 llm.yaml 存在
    (server.py create_app 对 FA_CONFIG_DIR setdefault 到它;2026-07-12 审计批判环坐实)。"""
    from guanlan_v2 import server
    assert server._CONFIG_DIR == ROOT / "config"
    assert (server._CONFIG_DIR / "llm.yaml").is_file()
