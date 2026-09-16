"""落子复盘真跑的停止/汇总一致性回归检查。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


APP = Path(__file__).resolve().parents[1] / "ui" / "seats" / "luozi-app.jsx"


def _source() -> str:
    return APP.read_text(encoding="utf-8")


def _locked_runner(src: str) -> str:
    start = src.index("const runRealThinkLocked = async () => {")
    end = src.index("// 切页/切票 → 清选中与触发标记", start)
    return src[start:end]


def test_stop_only_blocks_the_next_request_and_run_header_uses_persisted_rows() -> None:
    src = _source()
    runner = _locked_runner(src)

    # /decide 是先落盘后响应：await 后不能再因 stop 丢掉这一笔在途响应。
    assert runner.count("if (realStopRef.current) break;") == 1
    assert runner.index("if (realStopRef.current) break;") < runner.index(
        "res = await window.lzSeatDecide"
    )

    # run header 必须在服务端事实回读之后注册，且所有汇总字段都来自回读摘要。
    reconcile = runner.index("persistedRows = await lzReadRunRowsStrict(runId, codeNow, sid);")
    register = runner.index("+ '/seats/runs'")
    assert reconcile < register
    for field in (
        "start_date: summary.firstDate",
        "end_date: summary.lastDate",
        "n_buy: summary.nBuy",
        "n_sell: summary.nSell",
        "n_watch: summary.nWatch",
        "model: summary.lastModel",
    ):
        assert field in runner


def test_double_click_has_a_synchronous_ref_lock_and_reconciliation_is_bounded() -> None:
    src = _source()
    assert "if (realRunActiveRef.current)" in src
    assert "realRunActiveRef.current = true;" in src
    assert "realRunActiveRef.current = false;" in src
    assert "if (realRun.running)" not in _locked_runner(src)
    assert "const LZ_RUN_RECONCILE_LIMIT = 300;" in src
    assert "runBars.length > LZ_RUN_RECONCILE_LIMIT" in src
    strict_reader = src[src.index("async function lzReadRunRowsStrict"):src.index("// ② 五档盘口")]
    assert "row.kind === 'decide'" in strict_reader
    assert "String(row.code || '').replace(/\\D/g, '') === codeCore" in strict_reader
    assert "String(row.strategy_id || '').trim() === wantStrategy" in strict_reader


def test_persisted_row_summary_counts_and_dates_actual_rows() -> None:
    """用 Node 直接执行页面中的纯摘要函数，覆盖乱序、买卖观和截止日。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js 不可用；静态一致性检查仍会执行")

    src = _source()
    start = src.index("function lzSummarizeRunRows(rows) {")
    end = src.index("\nasync function lzReadRunRowsStrict", start)
    fn = src[start:end]
    rows = [
        {"run_id": "r", "asof": "2026-02-27", "direction": "观望", "model_name": "m3"},
        {"run_id": "r", "asof": "2026-02-25", "direction": "买入", "model_name": "m1"},
        {"run_id": "r", "asof": "2026-02-26", "direction": "卖出", "model_name": "m2"},
    ]
    script = (
        "const fs = require('fs');\n"
        + fn
        + "\nconst rows = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        + "process.stdout.write(JSON.stringify(lzSummarizeRunRows(rows)));\n"
    )
    completed = subprocess.run(
        [node, "-e", script],
        input=json.dumps(rows, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    summary = json.loads(completed.stdout)
    assert summary == {
        "total": 3,
        "nBuy": 1,
        "nSell": 1,
        "nWatch": 1,
        "firstDate": "2026-02-25",
        "lastDate": "2026-02-27",
        "lastModel": "m3",
    }
