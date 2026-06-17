# guanlan_v2.strategy 消费者契约测试(读 vendored v4 排名产物)
#
# 锁 v4 产物列契约 + 名称/行业映射 + code 转换。产物 schema 变了(上游 v4 改输出)→ 红。
import re

import pandas as pd

from guanlan_v2.strategy import (
    V4_COLUMNS,
    load_v4_ranking,
    name_industry_map,
    ranking_date,
    ts_to_qlib,
)


def test_load_v4_ranking_columns():
    df = load_v4_ranking()
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 1000                       # 全市场排名(~5400)
    for c in V4_COLUMNS:
        assert c in df.columns, f"v4 排名缺列 {c}(产物 schema 变了)"


def test_ranking_date_format():
    d = ranking_date()
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", d), f"排名日期格式异常: {d!r}"


def test_v4_rated_subset_present():
    df = load_v4_ranking()
    assert df["v4_total"].notna().sum() >= 50   # 顶200 含五维评级(v4_total/v4_layer)


def test_ts_to_qlib():
    assert ts_to_qlib("600519.SH") == "SH600519"
    assert ts_to_qlib("000001.SZ") == "SZ000001"
    assert ts_to_qlib("BJ920690") == "BJ920690"  # 无点原样


def test_name_industry_map_structure():
    m = name_industry_map()
    assert len(m) > 1000
    k = next(iter(m))
    nm, ind = m[k]
    assert isinstance(nm, str) and nm           # 名称非空
    assert isinstance(ind, str)                 # 行业字段在(可空)
