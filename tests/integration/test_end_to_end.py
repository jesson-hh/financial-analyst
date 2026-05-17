# tests/integration/test_end_to_end.py
import os
import pytest
from pathlib import Path

pytestmark = pytest.mark.skipif(
    os.environ.get("FA_E2E") != "1",
    reason="set FA_E2E=1 + ANTHROPIC_API_KEY + TUSHARE_TOKEN to enable"
)


@pytest.mark.asyncio
async def test_real_report_sh600519(tmp_path):
    from financial_analyst.tui import run_report_oneshot
    out_dir = tmp_path / "out"
    await run_report_oneshot(code="SH600519", asof="2026-05-16", out_dir=out_dir)
    files = list(out_dir.glob("SH600519_*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "贵州茅台" in text or "600519" in text
    assert "综合评级" in text
