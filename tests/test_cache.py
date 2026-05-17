import pandas as pd
import time
from pathlib import Path
from financial_analyst.data.cache import ParquetCache

def test_cache_miss_returns_none(tmp_path):
    cache = ParquetCache(tmp_path, ttl_seconds=60)
    assert cache.get("quote", {"code": "SH600519"}) is None

def test_cache_set_and_get(tmp_path):
    cache = ParquetCache(tmp_path, ttl_seconds=60)
    df = pd.DataFrame({"close": [10.0, 11.0]})
    cache.set("quote", {"code": "SH600519"}, df)
    got = cache.get("quote", {"code": "SH600519"})
    assert got is not None
    assert got["close"].tolist() == [10.0, 11.0]

def test_cache_expires(tmp_path):
    cache = ParquetCache(tmp_path, ttl_seconds=1)
    df = pd.DataFrame({"close": [10.0]})
    cache.set("quote", {"code": "SH600519"}, df)
    time.sleep(1.5)
    assert cache.get("quote", {"code": "SH600519"}) is None

def test_cache_key_differs_by_params(tmp_path):
    cache = ParquetCache(tmp_path, ttl_seconds=60)
    cache.set("quote", {"code": "SH600519"}, pd.DataFrame({"x": [1]}))
    cache.set("quote", {"code": "SZ000858"}, pd.DataFrame({"x": [2]}))
    assert cache.get("quote", {"code": "SH600519"})["x"].tolist() == [1]
    assert cache.get("quote", {"code": "SZ000858"})["x"].tolist() == [2]
