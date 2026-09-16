# -*- coding: utf-8 -*-
"""座席档位 schema 守护:字段白名单/模型在册/类型合法;FA_CONFIG_DIR 路由钉死。"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_KEYS = {
    "provider", "model", "max_tokens", "timeout", "thinking", "reasoning_effort",
}


def _cfg():
    with open(ROOT / "config" / "llm.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_overrides_schema_and_models_in_register():
    cfg = _cfg()
    providers = cfg["providers"]
    assert cfg["default_model"] in providers[cfg["default_provider"]]["models"]
    for name, ov in (cfg.get("agent_overrides") or {}).items():
        assert set(ov) <= ALLOWED_KEYS, f"{name} 有未知字段: {set(ov) - ALLOWED_KEYS}"
        prov = ov.get("provider", cfg["default_provider"])
        assert prov in providers, f"{name} provider 不在册"
        assert ov.get("model", cfg["default_model"]) in providers[prov]["models"], f"{name} model 不在册"
        if "max_tokens" in ov:
            assert isinstance(ov["max_tokens"], int) and ov["max_tokens"] > 0
        if "timeout" in ov:
            assert isinstance(ov["timeout"], (int, float)) and ov["timeout"] > 0
        if "thinking" in ov:
            assert isinstance(ov["thinking"], bool)
        if "reasoning_effort" in ov:
            assert ov["thinking"] is True
            assert ov["reasoning_effort"] in {"low", "high", "max"}


def test_release_network_defaults_use_system_trust_store():
    cfg = _cfg()
    for name in ("deepseek", "anthropic", "openai"):
        assert cfg["providers"][name]["network_profile"] == "intl_system"

    bundled = yaml.safe_load(
        (ROOT / "engine" / "financial_analyst" / "_resources" / "config" / "llm.yaml")
        .read_text(encoding="utf-8")
    )
    for name in ("deepseek", "anthropic", "openai"):
        assert bundled["providers"][name]["network_profile"] == "intl_system"


def test_opt_in_clash_profile_keeps_tls_verification_by_default(monkeypatch):
    import httpx
    from financial_analyst.llm import client

    captured = {}

    def fake_async_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.delenv("FA_INTL_VERIFY", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)
    client._build_http_client("intl_clash")
    assert captured["verify"] is True


def test_deep_tier_seats_exist():
    ov = _cfg().get("agent_overrides") or {}
    for seat in ("rerank", "review_officer", "bull-advocate", "bear-advocate"):
        assert ov.get(seat, {}).get("model") == "deepseek-v4-flash", f"{seat} 应为 V4 思考档"
        assert ov[seat].get("thinking") is True
        assert ov[seat].get("reasoning_effort") == "high"
        assert ov[seat].get("timeout", 0) >= 180, f"{seat} 思考档须放宽超时"
    # 终端结构化节点继承上游深思考结论，但自身关闭 thinking，避免推理
    # token 挤占最终 JSON；仍保留足量输出与超时预算。
    for seat in ("risk-officer", "report-writer"):
        assert ov.get(seat, {}).get("model") == "deepseek-v4-flash"
        assert ov[seat].get("thinking") is False
        assert ov[seat].get("max_tokens", 0) >= 8192
        assert ov[seat].get("timeout", 0) >= 180
    assert ov.get("review_section", {}).get("model") == "deepseek-v4-flash"
    assert ov["review_section"].get("thinking") is False


def test_retired_deepseek_aliases_are_absent_from_live_config():
    cfg = _cfg()
    assert cfg["default_model"] == "deepseek-v4-flash"
    assert cfg.get("default_thinking") is False
    assert set(cfg["providers"]["deepseek"]["models"]) == {
        "deepseek-v4-flash", "deepseek-v4-pro",
    }
    for ov in (cfg.get("agent_overrides") or {}).values():
        if ov.get("provider", cfg["default_provider"]) == "deepseek":
            assert ov.get("model", cfg["default_model"]) not in {
                "deepseek-chat", "deepseek-reasoner",
            }


def test_weiwo_standard_and_pro_seats_are_pinned():
    ov = _cfg().get("agent_overrides") or {}
    standard = ov.get("buddy") or {}
    assert standard.get("provider") == "deepseek"
    assert standard.get("model") == "deepseek-v4-flash"
    assert standard.get("thinking") is False
    pro = ov.get("buddy-pro") or {}
    assert pro.get("provider") == "deepseek"
    assert pro.get("model") == "deepseek-v4-pro"
    assert pro.get("thinking") is True
    assert pro.get("reasoning_effort") == "max"
    assert pro.get("max_tokens", 0) >= 8192
    assert pro.get("timeout", 0) >= 180


def test_fa_config_dir_routes_to_repo():
    """9999 进程模型路由钉死:server._CONFIG_DIR 必须指向仓内 config/ 且 llm.yaml 存在
    (server.py create_app 对 FA_CONFIG_DIR setdefault 到它;2026-07-12 审计批判环坐实)。"""
    from guanlan_v2 import server
    assert server._CONFIG_DIR == ROOT / "config"
    assert (server._CONFIG_DIR / "llm.yaml").is_file()
