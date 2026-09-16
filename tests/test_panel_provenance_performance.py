"""800-code provenance must not scale pandas __finalize__/clip copy work."""
from copy import deepcopy
import json
from time import perf_counter

import numpy as np
import pandas as pd
from pandas.testing import assert_series_equal, assert_frame_equal

from financial_analyst.data.loaders.technical import TECH_DEFINITIONS, TECH_VERSION, technical_definition_fingerprint
from financial_analyst.factors.zoo.panel import PanelData, _merge_tech, _technical_provenance_summary
from financial_analyst.factors.eval.preprocess import winsorize, zscore


def manifest(n=800):
    return {"by_code": {f"SH{i:06}": {
        "schema": TECH_VERSION, "definition_hash": technical_definition_fingerprint(),
        "code": f"SH{i:06}", "definitions": dict(TECH_DEFINITIONS),
        "source_file_signatures": {f"features/sh{i:06}/{f}.day.bin": {"size": 34004, "mtime_ns": 1777777777777777777}
                                   for f in ("open", "high", "low", "close", "volume", "amount")},
        "first_source_date": "2005-01-04", "last_source_date": "2026-09-04",
        "price_adjustment": "provider inherited; not independently PIT-certified",
        "requested_start": "2010-01-01", "requested_end": "2026-09-04", "cache": "hit",
    } for i in range(n)}, "errors": {}}


def numeric_frame(n=800, days=40):
    idx = pd.MultiIndex.from_product([pd.bdate_range("2026-07-01", periods=days), [f"SH{i:06}" for i in range(n)]], names=["datetime", "code"])
    r = np.random.default_rng(7)
    close = r.lognormal(3., .6, len(idx))
    return pd.DataFrame({"open": close*.999, "high": close*1.02, "low": close*.98,
                         "close":close, "volume":r.uniform(1e5,1e7,len(idx))},index=idx)


def test_summary_is_bounded_and_preserves_definition_coverage_pit_and_digest():
    full = manifest()
    summary = _technical_provenance_summary(full, 802)
    assert "by_code" not in summary and "errors" not in summary and "definitions" not in summary
    assert summary["provenance_code_count"] == 800
    assert summary["requested_code_count"] == 802
    assert summary["missing_provenance_code_count"] == 2
    assert summary["definition_hash_code_count"] == 800
    assert summary["definition_hash"] == technical_definition_fingerprint()
    assert summary["technical_version"] == TECH_VERSION
    assert summary["pit_certified"] is False
    assert summary["last_source_date_min"] == summary["last_source_date_max"] == "2026-09-04"
    assert "not finite feature coverage" in summary["coverage_scope"]
    assert len(json.dumps(summary)) < 2000
    small = _technical_provenance_summary(manifest(2), 2)
    assert abs(len(json.dumps(summary)) - len(json.dumps(small))) < 100
    reverse = {"errors": {}, "by_code":dict(reversed(list(full["by_code"].items())))}
    assert _technical_provenance_summary(reverse,802)["manifest_sha256"] == summary["manifest_sha256"]


def test_digest_changes_for_individual_source_version_definition_error():
    full = manifest(2)
    old = _technical_provenance_summary(full)["manifest_sha256"]
    variants = []
    x = deepcopy(full)
    x["by_code"]["SH000000"]["source_file_signatures"]["features/sh000000/close.day.bin"]["mtime_ns"] += 1
    variants.append(x)
    x = deepcopy(full)
    x["by_code"]["SH000000"]["schema"] = "next_version"
    variants.append(x)
    x = deepcopy(full)
    x["by_code"]["SH000000"]["definition_hash"] = "a"*64
    variants.append(x)
    x = deepcopy(full)
    x["errors"]["SH000001"] = "source changed"
    variants.append(x)
    for item in variants:
        assert _technical_provenance_summary(item)["manifest_sha256"] != old
    multi = _technical_provenance_summary(variants[2])
    assert multi["definition_hash"] is None and multi["definition_hash_count"] == 2
    assert len(multi["definition_hashes_sha256"]) == 64
    malformed = manifest(1)
    malformed["by_code"]["SH000000"]["definition_hash"] = "x"*100000
    bounded = _technical_provenance_summary(malformed)
    assert bounded["definition_hash_code_count"] == 0 and len(json.dumps(bounded)) < 2000


def test_800_code_legacy_manifest_never_deepcopied_by_pandas_and_numeric_unchanged():
    class CopyTrap(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("Per-code audit tree leaked into pandas deepcopy")

    full = manifest()
    full["by_code"] = CopyTrap(full["by_code"])
    source = numeric_frame()
    expected = zscore(winsorize(source.close, .01))
    source.attrs["technical_provenance"] = full
    source.attrs["owner_note"] = "must preserve"
    start = perf_counter()
    wrapped = PanelData(source)
    got = zscore(winsorize(wrapped.close, .01))
    elapsed = perf_counter()-start
    assert_series_equal(got, expected)
    assert wrapped.technical_provenance_manifest is full
    assert source.attrs["technical_provenance"] is full
    assert wrapped.df.attrs["owner_note"] == "must preserve"
    assert "by_code" not in wrapped.df.attrs["technical_provenance"]
    assert len(json.dumps(got.attrs)) < 2100
    assert len(json.dumps(deepcopy(wrapped.df.attrs))) < 2100
    # Wide tolerance against runaway metadata scaling; exact CopyTrap test is
    # the deterministic gate (timing depends on concurrent CPU/OS load).
    assert elapsed < 10., f"800 stocks x 40 sessions unexpectedly took {elapsed:.2f}s"
    derived = PanelData(wrapped.df.assign(idx_ret=0.001))
    assert derived.technical_provenance_manifest is None  # summary is not a full manifest
    assert derived.df.attrs["technical_provenance"]["manifest_sha256"] == wrapped.df.attrs["technical_provenance"]["manifest_sha256"]
    assert_frame_equal(pd.DataFrame(source, copy=False), numeric_frame(), check_flags=False)  # numeric source never changed


def test_merge_800_codes_returns_manifest_separately_and_keeps_attrs_small():
    full = manifest()
    frame = numeric_frame(days=1)
    day = frame.index.get_level_values("datetime")[0]

    class Stub:
        def fetch_tech(self, code, start, end):
            out = pd.DataFrame({"trade_date":[day], "amihud_20":[1e-11]})
            out.attrs["technical_provenance"] = full["by_code"][code]
            return out

    result = _merge_tech(frame,Stub(),list(full["by_code"]),str(day),str(day))
    assert len(result["by_code"]) == 800
    assert len(json.dumps(frame.attrs)) < 2100
    assert frame.amihud_20.notna().sum() == 800
    panel = PanelData(frame, technical_provenance_manifest=result)
    assert panel.technical_provenance_manifest is result
    assert panel.df.attrs["technical_provenance"]["error_code_count"] == 0


def test_unserializable_custom_provenance_does_not_pretend_to_have_digest():
    full = {"by_code":{"A":{"schema":"custom", "opaque":object()}}, "errors":{}}
    out = _technical_provenance_summary(full,2)
    assert out["manifest_sha256"] is None
    assert out["manifest_sha256_status"] == "unavailable_non_json_values"
    assert out["definition_hash_code_count"] == 0
    assert out["missing_provenance_code_count"] == 1
