"""Tests for BaseNewsCollector / BaseF10Collector ABCs."""
import pytest
from pathlib import Path
from financial_analyst.data.collectors.news.base import BaseNewsCollector
from financial_analyst.data.collectors.f10.base import BaseF10Collector


def test_news_collector_is_abstract():
    with pytest.raises(TypeError):
        BaseNewsCollector()


def test_f10_collector_is_abstract():
    with pytest.raises(TypeError):
        BaseF10Collector()


def test_concrete_news_collector(tmp_path):
    class _Fake(BaseNewsCollector):
        def collect(self, code, days=7, target_dir=Path("news")):
            target = Path(target_dir) / code.upper()
            target.mkdir(parents=True, exist_ok=True)
            f = target / "2026-05-18.txt"
            f.write_text("hello", encoding="utf-8")
            return [f]
    c = _Fake()
    written = c.collect("SH600519", target_dir=tmp_path)
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == "hello"


def test_concrete_f10_collector(tmp_path):
    class _Fake(BaseF10Collector):
        def collect(self, code, days=30, target_dir=Path("f10")):
            target = Path(target_dir) / code.upper()
            target.mkdir(parents=True, exist_ok=True)
            f = target / "lhb.txt"
            f.write_text("LHB data", encoding="utf-8")
            return [f]
    c = _Fake()
    written = c.collect("SH600519", target_dir=tmp_path)
    assert len(written) == 1


def test_default_supports_a_share():
    class _Fake(BaseNewsCollector):
        def collect(self, code, days=7, target_dir=Path("news")):
            return []
    c = _Fake()
    assert c.supports("a_share") is True
    assert c.supports("us") is False
