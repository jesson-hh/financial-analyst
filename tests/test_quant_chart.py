from types import SimpleNamespace

from financial_analyst.data import loader_factory, paths
from financial_analyst.data.loaders import qlib_binary

from guanlan_v2.console import quant_chart


def test_stock_daily_is_pinned_to_local_qlib_loader(monkeypatch):
    provider_uri = {"day": "G:/trusted/local/qlib"}
    marker = object()
    seen = {}

    class StubQlibBinaryLoader:
        def __init__(self, configured_uri):
            seen["provider_uri"] = configured_uri

        def fetch_quote(self, code, start, end, freq):
            seen["fetch_quote"] = (code, start, end, freq)
            return marker

    def default_loader_must_not_run():
        raise AssertionError("quant chart must not consult the configurable default loader")

    monkeypatch.setattr(qlib_binary, "QlibBinaryLoader", StubQlibBinaryLoader)
    monkeypatch.setattr(
        paths, "get_data_paths", lambda: SimpleNamespace(qlib_uri=provider_uri),
    )
    monkeypatch.setattr(loader_factory, "get_default_loader", default_loader_must_not_run)

    result = quant_chart._load_stock_daily(
        "SH600000", "2026-01-01", "2026-08-28",
    )

    assert result is marker
    assert seen == {
        "provider_uri": provider_uri,
        "fetch_quote": ("SH600000", "2026-01-01", "2026-08-28", "day"),
    }
