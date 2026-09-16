from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ui" / "fundflow" / "fundflow-app.jsx"
HTML = ROOT / "ui" / "fundflow" / "观澜 · 资金流向.html"
CSS = ROOT / "ui" / "fundflow" / "fundflow.css"


def test_fundflow_page_loads_dedicated_visual_layer():
    html = HTML.read_text(encoding="utf-8")
    assert 'href="fundflow.css?v=1"' in html
    assert 'src="fundflow-app.jsx?v=9"' in html
    assert CSS.is_file()


def test_fundflow_visual_hierarchy_and_dual_ranking_are_present():
    app = APP.read_text(encoding="utf-8")
    for marker in (
        "ff-hero",
        "ff-overview-grid",
        "MARKET STRUCTURE",
        "INTRADAY TRACE",
        "CAPITAL RANKING",
        "净流入与净流出双向对照",
    ):
        assert marker in app


def test_intraday_chart_limits_visual_noise_without_mutating_data_contract():
    app = APP.read_text(encoding="utf-8")
    assert ".slice(0, 4)" in app
    assert "market_series" in app
    assert "glFetchFundflowLive" in app
    assert "glFetchFundflowHistory" in app
    assert "window.glSaveFundflowMemory" in app
