import os
from financial_analyst.settings import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("FA_LOG_LEVEL", "DEBUG")
    settings = Settings()
    assert settings.tushare_token == "fake-token"
    assert settings.anthropic_api_key == "sk-ant-fake"
    assert settings.log_level == "DEBUG"


def test_settings_default_log_level(monkeypatch):
    monkeypatch.delenv("FA_LOG_LEVEL", raising=False)
    settings = Settings()
    assert settings.log_level == "INFO"


def test_cache_dir_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_CACHE_DIR", str(tmp_path / "cache"))
    settings = Settings()
    assert settings.cache_dir == tmp_path / "cache"
