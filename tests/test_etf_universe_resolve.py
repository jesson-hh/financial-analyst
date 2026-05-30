from financial_analyst.data.universe import resolve_universe_codes


def test_resolve_etf_returns_seed_codes():
    codes = resolve_universe_codes("etf")
    assert "SH510300" in codes and "SZ159915" in codes
    assert all(c[:2] in ("SH", "SZ") for c in codes)
