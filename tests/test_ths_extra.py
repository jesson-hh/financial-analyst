"""Tests for ths-extra (iwencai / ths-fund-flow / ths-concept-board)."""
from __future__ import annotations
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from financial_analyst.data.collectors.opencli.ths_extra import (
    IWencaiCollector, THSFundFlowCollector, THSConceptBoardCollector,
)
from financial_analyst.data.news_db import NewsDB


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as d:
        db = NewsDB(path=Path(d) / "t.sqlite")
        yield db
        db.close()


# ----- collectors -----------------------------------------------------------


def test_install_hint_points_to_bundled_plugin():
    from financial_analyst.data.collectors.opencli.ths_extra import install_hint
    h = install_hint()
    assert "opencli plugin install" in h
    assert "opencli-plugin-ths-extra" in h


def test_run_ths_extra_raises_friendly_when_opencli_missing(monkeypatch):
    from financial_analyst.data.collectors.opencli import ths_extra
    monkeypatch.setattr(ths_extra, "is_opencli_available", lambda: False)

    def boom(*a, **kw):
        raise RuntimeError("opencli not found on PATH. Install: npm install -g ...")
    monkeypatch.setattr(ths_extra, "run_opencli", boom)
    with pytest.raises(ths_extra.ThsExtraNotInstalled) as ei:
        ths_extra._run_ths_extra("iwencai", "x")
    msg = str(ei.value)
    assert "npm install -g @jackwener/opencli" in msg
    assert "opencli plugin install" in msg


def test_run_ths_extra_raises_friendly_when_plugin_missing(monkeypatch):
    from financial_analyst.data.collectors.opencli import ths_extra
    monkeypatch.setattr(ths_extra, "is_opencli_available", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("opencli exit 1: error: unknown command 'ths-extra'")
    monkeypatch.setattr(ths_extra, "run_opencli", boom)
    with pytest.raises(ths_extra.ThsExtraNotInstalled) as ei:
        ths_extra._run_ths_extra("iwencai", "x")
    assert "plugin 未安装" in str(ei.value)
    assert "opencli plugin install" in str(ei.value)


def test_run_ths_extra_propagates_other_errors(monkeypatch):
    """A genuine runtime error (not a missing-plugin one) propagates as-is."""
    from financial_analyst.data.collectors.opencli import ths_extra
    # opencli present so we don't mis-route to the "opencli missing" branch
    monkeypatch.setattr(ths_extra, "is_opencli_available", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("opencli exit 1: timeout after 60s")
    monkeypatch.setattr(ths_extra, "run_opencli", boom)
    with pytest.raises(RuntimeError) as ei:
        ths_extra._run_ths_extra("iwencai", "x")
    # Not wrapped as ThsExtraNotInstalled
    assert not isinstance(ei.value, ths_extra.ThsExtraNotInstalled)
    assert "timeout" in str(ei.value)


def test_iwencai_passes_question_and_limit():
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=[]) as mock:
        IWencaiCollector().fetch("PE 最低的 10 只", limit=15)
    args = mock.call_args.args
    assert "ths-extra" in args and "iwencai" in args
    assert "PE 最低的 10 只" in args
    assert "15" in args


def test_iwencai_normalises_rows():
    fake = [
        {"columns": "代码|名称|PE", "cells": "000995|皇台|14.39"},
        {"columns": "代码|名称|PE", "cells": "600199|金种子酒|34.67"},
    ]
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=fake):
        out = IWencaiCollector().fetch("白酒 PE 最低", limit=10)
    assert len(out) == 2
    for i, r in enumerate(out):
        assert r["question"] == "白酒 PE 最低"
        assert r["row_index"] == i
        assert "snapshot_ts" in r
        assert "cells" in r


def test_fund_flow_normalises_and_keeps_snapshot_ts():
    fake = [
        {"code": "688055", "name": "龙腾光电", "price": "5.39",
         "change_pct": "20.04%", "turnover_pct": "0.96%",
         "inflow": "7058万", "outflow": "9817万",
         "main_net": "-2759万", "total_amount": "1.69亿"},
    ]
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=fake):
        out = THSFundFlowCollector().fetch(limit=10)
    assert len(out) == 1
    assert out[0]["code"] == "688055"
    assert out[0]["main_net"] == "-2759万"
    assert "snapshot_ts" in out[0]


def test_fund_flow_filters_rows_without_code():
    """opencli sometimes emits a placeholder row with no code; drop it."""
    fake = [
        {"code": "", "name": "--"},
        {"code": "688055", "name": "龙腾光电"},
    ]
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=fake):
        out = THSFundFlowCollector().fetch()
    assert len(out) == 1
    assert out[0]["code"] == "688055"


@pytest.mark.parametrize("target", ["gegu", "gainian", "hangye", "ddzz"])
def test_fund_flow_target_passes_through(target):
    """Each target sends correct --target arg to opencli."""
    from financial_analyst.data.collectors.opencli.ths_extra import THSFundFlowCollector
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=[]) as mock:
        THSFundFlowCollector().fetch(target=target, limit=5)
    args = mock.call_args.args
    assert "--target" in args
    assert target in args


def test_fund_flow_target_rejects_invalid():
    from financial_analyst.data.collectors.opencli.ths_extra import THSFundFlowCollector
    with pytest.raises(ValueError):
        THSFundFlowCollector().fetch(target="bogus")


def test_fund_flow_gainian_drops_placeholder(tmp_db):
    """concept rows without code in cells (only in URL) must still
    pass the filter via `name`."""
    from financial_analyst.data.collectors.opencli.ths_extra import THSFundFlowCollector
    fake = [
        {"code": "309152", "name": "AI眼镜", "change_pct": "0.21%", "main_net": "-64.07"},
        {"code": "", "name": "--"},   # placeholder
        {"code": "", "name": "", "main_net": ""},  # totally empty
    ]
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=fake):
        out = THSFundFlowCollector().fetch(target="gainian")
    assert len(out) == 1
    assert out[0]["name"] == "AI眼镜"


def test_upsert_ths_fund_flow_target_partition(tmp_db):
    """Same code/name across different targets stay separate."""
    tmp_db.upsert_ths_fund_flow([
        {"target": "gegu", "code": "600519", "name": "茅台", "main_net": "1.5亿"},
        {"target": "gainian", "code": "309152", "name": "AI眼镜", "main_net": "-64亿"},
    ])
    gegu = tmp_db.query_ths_fund_flow(target="gegu")
    gainian = tmp_db.query_ths_fund_flow(target="gainian")
    assert len(gegu) == 1 and gegu[0]["code"] == "600519"
    assert len(gainian) == 1 and gainian[0]["name"] == "AI眼镜"


def test_concept_board_passes_mode():
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=[]) as mock:
        THSConceptBoardCollector().fetch(mode="rank", limit=5)
    args = mock.call_args.args
    assert "concept-board" in args
    assert "rank" in args
    assert "5" in args


def test_concept_board_normalises():
    fake = [
        {"board_code": "309265", "board_name": "2026一季报预增",
         "release_date": "2026-04-02", "num_stocks": "101",
         "change_pct": "", "board_url": "http://x"},
    ]
    with patch("financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
               return_value=fake):
        out = THSConceptBoardCollector().fetch(mode="new")
    assert len(out) == 1
    assert out[0]["board_name"] == "2026一季报预增"
    assert out[0]["mode"] == "new"


# ----- DB integration --------------------------------------------------------


def test_upsert_iwencai_then_query(tmp_db):
    items = [
        {"question": "白酒", "row_index": 0, "columns": "code|name|pe",
         "cells": "000995|皇台|14.39", "snapshot_ts": "2026-05-21 09:00:00"},
        {"question": "白酒", "row_index": 1, "columns": "code|name|pe",
         "cells": "600199|金种子酒|34.67", "snapshot_ts": "2026-05-21 09:00:00"},
    ]
    n = tmp_db.upsert_iwencai(items)
    assert n == 2
    rows = tmp_db.query_iwencai("白酒")
    assert len(rows) == 2
    assert rows[0]["cells"].startswith("000995")


def test_query_iwencai_returns_latest_snapshot(tmp_db):
    """Two snapshots of same question; query gets the most recent only."""
    tmp_db.upsert_iwencai([
        {"question": "x", "row_index": 0, "columns": "", "cells": "old",
         "snapshot_ts": "2026-05-20 09:00:00"},
    ])
    tmp_db.upsert_iwencai([
        {"question": "x", "row_index": 0, "columns": "", "cells": "new",
         "snapshot_ts": "2026-05-21 09:00:00"},
    ])
    rows = tmp_db.query_iwencai("x")
    assert len(rows) == 1
    assert rows[0]["cells"] == "new"


def test_upsert_ths_fund_flow_then_query(tmp_db):
    items = [
        {"snapshot_ts": "2026-05-21 09:00:00", "code": "688055",
         "name": "龙腾光电", "main_net": "-2759万"},
    ]
    tmp_db.upsert_ths_fund_flow(items)
    rows = tmp_db.query_ths_fund_flow()
    assert len(rows) == 1
    assert rows[0]["code"] == "688055"


def test_upsert_ths_concept_boards_filters_mode(tmp_db):
    tmp_db.upsert_ths_concept_boards([
        {"mode": "new", "board_code": "309265", "board_name": "X"},
        {"mode": "rank", "board_code": "1", "board_name": "Y"},
    ])
    new_rows = tmp_db.query_ths_concept_boards(mode="new")
    rank_rows = tmp_db.query_ths_concept_boards(mode="rank")
    assert len(new_rows) == 1
    assert len(rank_rows) == 1
    assert new_rows[0]["board_code"] == "309265"
    assert rank_rows[0]["board_code"] == "1"


# ----- buddy tools -----------------------------------------------------------


def test_buddy_registry_has_3_new_tools():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    names = {t.name for t in TOOL_REGISTRY}
    assert "iwencai_search" in names
    assert "ths_fund_flow" in names
    assert "ths_concept_board" in names


def test_iwencai_tool_graceful_when_plugin_missing(tmp_db, monkeypatch):
    """When opencli or ths-extra plugin isn't installed, the tool
    returns is_error=True with an install hint, not a stack trace."""
    class _NoCloseDB:
        def __init__(self, real): self._real = real
        def __getattr__(self, n): return getattr(self._real, n)
        def close(self): pass

    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("opencli not on PATH")),
    )
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB",
                        lambda *a, **kw: _NoCloseDB(tmp_db))
    from financial_analyst.buddy.tools import _tool_iwencai_search
    result = _tool_iwencai_search("test query")
    assert result.is_error
    assert "ths-extra" in result.content or "plugin" in result.content.lower()


def test_iwencai_tool_uses_cache_when_requested(tmp_db, monkeypatch):
    """use_cache=True should read DB without calling run_opencli."""
    class _NoCloseDB:
        def __init__(self, real): self._real = real
        def __getattr__(self, n): return getattr(self._real, n)
        def close(self): pass

    # Seed cache
    tmp_db.upsert_iwencai([
        {"question": "cached q", "row_index": 0, "columns": "", "cells": "abc",
         "snapshot_ts": "2026-05-21 09:00:00"},
    ])

    sentinel = {"called": False}
    def boom(*a, **kw):
        sentinel["called"] = True
        raise AssertionError("should not be called when use_cache=True")

    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.ths_extra.run_opencli",
        boom,
    )
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB",
                        lambda *a, **kw: _NoCloseDB(tmp_db))
    from financial_analyst.buddy.tools import _tool_iwencai_search
    result = _tool_iwencai_search("cached q", use_cache=True)
    assert not result.is_error
    assert "cached" in result.content.lower() or "abc" in result.content
    assert sentinel["called"] is False
