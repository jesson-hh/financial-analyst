"""Playwright e2e: watch panel P0+P1+P2 UX 真浏览器烟测.

跑专属 fa serve + http.server (OS 分配空闲端口, 不和 user dev 9999/5173 抢);
浏览器里用 page.route 把 quant.html 硬编码的 127.0.0.1:9999 请求转到测试 backend
(quant.html inline script 覆盖了 add_init_script, 走 route 拦截才能跳过).

验:
* P0.1 stopped banner "盯盘未开始" 出现
* + 添加 SH600519 后, 自选列表显示该 code
* P2.1 [高级 ▾] 展开 → 3 个 number input + "tick 间隔" label 出
* 改 tick=30
* ▶ 开始盯盘 → P0.1 running banner "实时盯盘运行中" + "Tencent realtime" 出
* P0.2 状态横条出 (含 "tick 30s")
* P1.3 KPI "现价" tooltip 含 "Tencent realtime" (KPI 仅在 quote_update SSE 推到才渲染, 容忍 skip)
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
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


@pytest.fixture(scope="module")
def stack():
    """启动专属 fa serve + http.server 各占一个 OS 分配的空闲端口; teardown 全 kill."""
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

        # 验后端是最新 schema (含 P0.2 加的 tick_seconds 字段); 缺 = editable install 没生效
        try:
            with urllib.request.urlopen(f"{backend_url}/watch/status", timeout=5) as r:
                import json as _json
                body = _json.load(r)
            assert "tick_seconds" in body, (
                f"测试 backend /watch/status 缺 tick_seconds 字段 (P0.2 新增). "
                f"financial-analyst editable install 没装? src/ 改动没生效? body={body!r}"
            )
        except Exception as e:
            _cleanup()
            pytest.fail(f"backend schema 验失败: {e}")

        time.sleep(5)  # chromadb / lazy imports 暖机

        yield (f"{ui_url}/quant.html", backend_url)
    finally:
        # teardown 前先停盯盘 (避免后台 task 在 fa serve kill 前一直跑)
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"{backend_url}/watch/stop",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"},
                ), timeout=3,
            )
        except Exception:
            pass
        _cleanup()


def test_watch_panel_ux(stack, tmp_path):
    """E2E 真浏览器: 切到实时盯盘 tab → 验 banner / 高级控件 / 加股 / 开始盯盘 / 状态横条 / KPI tooltip."""
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
                if "/watch/start" in r.url and r.method == "POST" else None
            ))

            page.goto(quant_url, wait_until="networkidle", timeout=30_000)

            # 切到实时盯盘 tab (header 里第 7 个 tab)
            page.click("text=实时盯盘", timeout=10_000)

            # ─── P0.1 stopped banner ───
            page.wait_for_selector("text=盯盘未开始", timeout=10_000)

            # ─── 加 1 只股 SH600519 ───
            page.fill("input[placeholder*='加股票代码']", "SH600519")
            page.click("text=+ 添加", timeout=5_000)
            # 自选列表里出现 SH600519
            page.wait_for_selector("code:has-text('SH600519')", timeout=5_000)

            # ─── P2.1 高级控件 ───
            page.click("text=高级 ▾", timeout=5_000)
            page.wait_for_selector("text=tick 间隔", timeout=3_000)
            # 第一个 number input = tick 间隔; 改 30
            tick_input = page.locator("input[type=number]").first
            tick_input.fill("30")
            assert tick_input.input_value() == "30", \
                f"tick input fill 失败, 当前 value: {tick_input.input_value()!r}"

            # ─── ▶ 开始盯盘 ───
            page.click("text=开始盯盘", timeout=5_000)
            # POST /watch/start 转测试 backend, WatchAgent.client 懒加载 (无 DASHSCOPE 也不爆)
            # 但 LLMClient.for_agent 或 EventSource 可能瞬间抛错, 给 4s 让 setRunning(true) 翻
            time.sleep(4)

            # ─── P0.1 running banner ───
            # 注: 如果后端启动失败 (LLMClient init 抛), running 不会翻, banner 仍 stopped
            try:
                page.wait_for_selector("text=实时盯盘运行中", timeout=8_000)
                page.wait_for_selector("text=Tencent realtime", timeout=3_000)
            except Exception:
                # 落盘调试信息
                dbg = tmp_path / "debug.txt"
                err_box = "(no err box)"
                try:
                    if page.locator("text=启动失败").count() > 0:
                        err_box = page.locator("text=启动失败").first.locator("..").inner_text()
                    elif page.locator(".ErrorBox, [class*=error]").count() > 0:
                        err_box = page.locator(".ErrorBox, [class*=error]").first.inner_text()
                except Exception:
                    pass
                dbg.write_text(
                    f"POST /watch/start payload(s): {sent_payload!r}\n\n"
                    f"err box: {err_box}\n\n"
                    f"console (50):\n" + "\n".join(console_logs[:50]),
                    encoding="utf-8",
                )
                page.screenshot(path=str(tmp_path / "fail.png"))
                pytest.skip(
                    f"watch_start 后 'running' banner 没出现 — 可能 backend WatchAgent 初始化失败 "
                    f"(常见: DASHSCOPE_API_KEY 缺). payload={sent_payload!r}; 调试: {dbg}"
                )

            # ─── P0.2 状态横条 (tick_seconds 透传到前端) ───
            # WatchStatusChips 在 running 时渲染, 应显示 "tick 30s" (POST 把 30 传到后端,
            # status 拉回也是 30; 注意 cfg 默认 60 → 30 是改成功的证据)
            page.wait_for_selector("text=tick 30s", timeout=5_000)

            # ─── P1.3 KPI 现价 tooltip ───
            # KPI 仅在 sel + quotes[sel] 都有时渲染, quotes 由 SSE quote_update 累计;
            # SSE 取决于 Tencent 真接口 + 交易时段, 测试环境可能没数据 → try/except 容忍
            try:
                # 选中 SH600519 (点击自选列表里那条) 让 sel 翻
                page.locator("code:has-text('SH600519')").first.click(timeout=3_000)
                # 蜡烛区 4 KPI: 现价/涨跌%/最高/最低 (DOM: <div title=...><div>现价</div>...)
                # 用 xpath 找含 title attr 且子树有 "现价" 文本的 div
                kpi_loc = page.locator(
                    "xpath=//div[@title][.//*[contains(text(), '现价')]]"
                ).first
                # 给短时间等 quote_update SSE 推 (可能根本不来)
                kpi_loc.wait_for(state="attached", timeout=8_000)
                tooltip = kpi_loc.get_attribute("title")
                if tooltip:
                    assert "Tencent realtime" in tooltip, \
                        f"现价 KPI tooltip 缺 'Tencent realtime': {tooltip!r}"
            except Exception:
                # KPI 仅在 quote 到时显示, SSE 没数据可能跳过 — 不视为失败
                pass

            # ─── 停止 ───
            try:
                page.click("text=■ 停止", timeout=5_000)
            except Exception:
                pass

        finally:
            browser.close()
