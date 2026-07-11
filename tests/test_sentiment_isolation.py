# -*- coding: utf-8 -*-
"""守护:测试进程内 sentiment._ROOT 必须已被 conftest autouse 隔离到 tmp,
且 write_market 真的写进隔离目录而非生产 var/sentiment(2026-07-12 as_of 冻结事故根修)。"""
from pathlib import Path

from guanlan_v2.datafeed import sentiment as sm

REPO_VAR = Path(__file__).resolve().parents[1] / "var" / "sentiment"


def test_root_is_isolated():
    assert Path(sm._ROOT).resolve() != REPO_VAR.resolve()


def test_write_market_lands_in_isolated_root():
    assert sm.write_market("2026-01-02", "偏多", None, "2026-01-02 09:31", "unit-test")
    files = list(Path(sm._ROOT).glob("market-*.jsonl"))
    assert files, "写入未落隔离目录"
    assert not (REPO_VAR / "market-202601.jsonl").exists()
