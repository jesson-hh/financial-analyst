"""Playwright e2e: backtest panel P0+P1+P2 UX 真浏览器烟测.

跑专属 fa serve + http.server (OS 分配空闲端口, 不和 user dev 9999/5173 抢);
浏览器里用 page.route 把 quant.html 硬编码的 127.0.0.1:9999 请求转到测试 backend
(quant.html inline script 覆盖了 add_init_script, 走 route 拦截才能跳过).

验:
* P0.1 banner Mock 文案出现, 切 real → 文案切 Real LLM + qwen3.5-plus
* P0.2 高级控件展开 → 改 pool=csi_fast, 跑 mock 完成 → 横条 chip 显示 池: csi_fast
* P1.3 点池 chip → popover 出 "候选池构造流程"
* P1.1 KPI Calmar tooltip title 含 "年化收益"
* P0.3 点击交易行 → modal 出 "当日 market_view" + "本笔 reason"

不依赖 e2e 跑的 chromium 安装 → playwright chromium 在 repo 其它 e2e 已装,
缺浏览器会 fail → 视为可见报错 (不 skip).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest


pytestmark = pytest.mark.slow


def _alloc_port() -> int:
    """OS-assigned free port. 关 socket 后立即 spawn 基本不会被抢."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _probe(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_for(url: str, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _probe(url, timeout=2.0):
            return True
        time.sleep(0.5)
    return False


def _schema_has_pool(backend_url: str) -> bool:
    """验后端 BacktestRunReq 含 P2 字段 (pool/hold_days). 缺 = stale 进程 / install."""
    try:
        with urllib.request.urlopen(f"{backend_url}/openapi.json", timeout=5) as r:
            spec = json.load(r)
        props = (
            spec.get("components", {})
            .get("schemas", {})
            .get("BacktestRunReq", {})
            .get("properties", {})
        )
        return "pool" in props and "hold_days" in props
    except Exception:
        return False


@pytest.fixture(scope="module")
def stack():
    """启动专属 fa serve + http.server 各占一个 OS 分配的空闲端口; teardown 全 kill."""
    # Pre-flight: csi_fast 必须可解析 (100 只成分股), 否则 mock 回测无候选可选
    try:
        from financial_analyst.data.universe import resolve_universe_codes
        if not resolve_universe_codes("csi_fast"):
            pytest.skip(
                "csi_fast 池子未解析 (缺 universes/csi_fast.txt 或 index_constituents.parquet); "
                "无 100 只成分股 e2e 烟测无法跑"
            )
    except Exception as e:
        pytest.skip(f"data layer 缺: {e}")

    backend_port = _alloc_port()
    ui_port = _alloc_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    ui_url = f"http://127.0.0.1:{ui_port}"

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    spawned: list[subprocess.Popen] = []

    # 拉 fa serve. fa CLI 不在 PATH 时回退到 python -m financial_analyst.cli
    try:
        p_be = subprocess.Popen(
            ["fa", "serve", "--port", str(backend_port)],
            cwd="G:/financial-analyst", env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        p_be = subprocess.Popen(
            ["python", "-m", "financial_analyst.cli", "serve", "--port", str(backend_port)],
            cwd="G:/financial-analyst", env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    spawned.append(p_be)

    p_ui = subprocess.Popen(
        ["python", "-m", "http.server", str(ui_port)],
        cwd="G:/financial-analyst/src/financial_analyst/ui", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    spawned.append(p_ui)

    def _cleanup():
        for p in spawned:
            try:
                p.terminate()
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            except Exception:
                pass

    try:
        if not _wait_for(f"{backend_url}/health", timeout=120.0):
            _cleanup()
            pytest.skip(f"fa serve 120s 内未起 ({backend_port}), 跳过 e2e")

        if not _wait_for(f"{ui_url}/", timeout=15.0):
            _cleanup()
            pytest.skip(f"http.server 15s 内未起 ({ui_port}), 跳过 e2e")

        # 验后端是最新 schema (含 P2 字段); 缺 = editable install 没生效, 真 bug 应失败而非 skip
        if not _schema_has_pool(backend_url):
            _cleanup()
            pytest.fail(
                f"测试 backend {backend_url} BacktestRunReq 缺 P2 字段 (pool/hold_days). "
                f"financial-analyst editable install 没装? src/ 改动没生效?"
            )

        time.sleep(5)  # chromadb / lazy imports 暖机

        yield (f"{ui_url}/quant.html", backend_url)
    finally:
        _cleanup()


def test_backtest_panel_ux(stack, tmp_path):
    """E2E 真浏览器: 切 backtest tab → 验 banner / 高级控件 / 横条 / popover / KPI tooltip / 交易 modal."""
    from playwright.sync_api import sync_playwright

    quant_url, backend_url = stack

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})

            # quant.html 里 inline script 硬编码 window.GUANLAN_BACKEND='http://127.0.0.1:9999';
            # 用 ctx.add_init_script 会被 inline 覆盖. 走 page.route 拦截 9999 转测试 backend,
            # 既不依赖改 jsx 也不和 user dev 9999 抢占.
            def _route_to_test_backend(route):
                req = route.request
                parsed = urllib.parse.urlparse(req.url)
                new_url = req.url.replace(
                    f"{parsed.scheme}://{parsed.netloc}", backend_url, 1
                )
                resp = ctx.request.fetch(
                    new_url, method=req.method,
                    headers={k: v for k, v in req.headers.items() if k.lower() != "host"},
                    data=req.post_data_buffer,
                )
                route.fulfill(
                    status=resp.status,
                    headers={
                        k: v for k, v in resp.headers.items()
                        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
                    },
                    body=resp.body(),
                )

            page = ctx.new_page()
            page.route("http://127.0.0.1:9999/**", _route_to_test_backend)
            page.route("http://localhost:9999/**", _route_to_test_backend)

            # 收集 console + 失败时落盘
            console_logs: list[str] = []
            page.on("console", lambda m: console_logs.append(f"[{m.type}] {m.text}"))
            page.on("pageerror", lambda e: console_logs.append(f"[pageerror] {e}"))
            sent_payload: list[str] = []
            page.on("request", lambda r: (
                sent_payload.append(r.post_data or "(empty)")
                if "/backtest/run" in r.url and r.method == "POST" else None
            ))

            page.goto(quant_url, wait_until="networkidle", timeout=30_000)

            # 切到 Agent 回测 tab
            page.click("text=Agent 回测", timeout=10_000)

            # ─── P0.1 banner Mock 文案 ───
            page.wait_for_selector("text=Mock 模式", timeout=10_000)
            page.wait_for_selector("text=不是盈利策略", timeout=3_000)

            # 切 Real LLM → banner 文案变 (验 banner 切换)
            page.click("text=真 LLM(慢)", timeout=5_000)
            page.wait_for_selector("text=Real LLM", timeout=5_000)
            page.wait_for_selector("text=qwen3.5-plus", timeout=3_000)

            # 切回 Mock
            page.click("text=Mock(秒级)", timeout=5_000)
            page.wait_for_selector("text=Mock 模式", timeout=5_000)

            # ─── P2.4 展开高级控件 + 选 pool=csi_fast ───
            page.click("text=高级 ▾", timeout=5_000)
            pool_select = page.locator("select").filter(has_text="csi_fast").first
            pool_select.wait_for(state="attached", timeout=3_000)
            pool_select.select_option("csi_fast")
            assert pool_select.input_value() == "csi_fast", \
                f"select_option 失败, 当前 value: {pool_select.input_value()!r}"

            # ─── 起回测 (mock + csi_fast, 等 backend done) ───
            page.click("text=起回测 ▶", timeout=5_000)
            # mock + 100 只 rev_20 计算 + 14 日窗口, 给 4min 兜底 (cold start 慢)
            page.wait_for_selector("text=组合表现", timeout=240_000)

            # ─── P0.2 横条 chip 显示 csi_fast ───
            # 注意: 高级控件里的 <option value="csi_fast"> hidden 也含此文本, 必须限定可见 chip;
            # filter 用 'span:has-text("池:")' 把范围锁到 SummaryChips 的 chip span
            pool_chip = page.locator("span:has-text('池:')").filter(has_text="csi_fast").first
            try:
                pool_chip.wait_for(state="visible", timeout=10_000)
            except Exception:
                # 失败时落盘截图 + 关键调试信息以便 root cause
                dbg = tmp_path / "debug.txt"
                chip_html = "(no chip)"
                try:
                    if page.locator("span:has-text('池:')").count() > 0:
                        chip_html = page.locator("span:has-text('池:')").first.inner_html()
                except Exception:
                    pass
                dbg.write_text(
                    f"POST /backtest/run payload(s): {sent_payload!r}\n\n"
                    f"chip html: {chip_html}\n\n"
                    f"console:\n" + "\n".join(console_logs[:50]),
                    encoding="utf-8",
                )
                page.screenshot(path=str(tmp_path / "fail.png"))
                raise AssertionError(
                    f"pool chip 'csi_fast' 未渲染. chip={chip_html!r}; "
                    f"payload={sent_payload!r}; 调试: {dbg}"
                )
            page.wait_for_selector("text=持有:", timeout=3_000)

            # ─── P1.3 点池 chip → popover 显示 "候选池构造流程" ───
            pool_chip.click()
            page.wait_for_selector("text=候选池构造流程", timeout=5_000)
            page.click("text=关闭", timeout=3_000)

            # ─── P1.1 KPI Calmar tooltip ───
            # Kpi DOM: <div title="..."><div class="mono">Calmar</div><div class="mono">{value}</div></div>
            # xpath 找含 title attr 且子树有 "Calmar" 文本节点的 div
            calmar_title = page.locator(
                "xpath=//div[@title][.//*[contains(text(), 'Calmar')]]"
            ).first.get_attribute("title")
            assert calmar_title and "年化收益" in calmar_title, \
                f"Calmar tooltip 缺 '年化收益': {calmar_title!r}"

            # ─── P0.3 交易行 modal (有交易才验) ───
            # 交易表 div.hover-row 内含 mono span "buy"/"sell"; 用 has= filter 把范围锁到交易行
            trade_rows = page.locator("div.hover-row").filter(
                has=page.locator("span.mono", has_text="buy")
            ).or_(
                page.locator("div.hover-row").filter(
                    has=page.locator("span.mono", has_text="sell")
                )
            )
            if trade_rows.count() > 0:
                trade_rows.first.click()
                page.wait_for_selector("text=当日 market_view", timeout=5_000)
                page.wait_for_selector("text=本笔 reason", timeout=3_000)
                # 关 modal (点 × 按钮, 失败容忍 — 测试主流程已验过)
                try:
                    page.locator("button", has_text="×").first.click(timeout=3_000)
                except Exception:
                    pass
            else:
                # 短窗口可能无交易, 至少验 "交易记录" panel 出现 (空状态也算 panel 渲染)
                page.wait_for_selector("text=交易记录", timeout=5_000)

        finally:
            browser.close()
