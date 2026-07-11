# -*- coding: utf-8 -*-
"""guanlan_v2 backend base.

Thin shell over the financial_analyst engine. Imports ``build_app()`` (the full
~50-endpoint buddy SSE bridge, already wired to real data via
``get_data_paths``) and serves the V2 multi-page UI (``ui/``) as static files.

Run (dev)::

    set GUANLAN_FA_SRC=G:/fa-watch-wt/src
    G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.server
    # http://127.0.0.1:9999/ui/

The point of Phase 0 is that the backend base IS the proven engine (imported,
not re-implemented) — so every existing endpoint (/run, /factor/*, /watch/*,
/concepts, /upload, /quotes, …) is live and real from day one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ui/ lives one level up from this package (guanlan-v2/ui/, guanlan-v2/guanlan_v2/).
_UI_DIR = Path(__file__).resolve().parent.parent / "ui"


# The engine (financial_analyst) is now FORKED INTO this repo under ``engine/``
# — guanlan-v2 is self-contained: the full buddy SSE backend + tools + recipes +
# report multi-agents live in-repo, and ONLY data is external (resolved by the
# engine's ``get_data_paths`` → env / config/loaders.yaml / G:/stocks fallback).
# ``engine/`` is the directory placed on sys.path so ``import financial_analyst``
# resolves to the vendored copy. Override via GUANLAN_FA_SRC (e.g. point back at
# G:/fa-watch-wt/src to A/B against upstream before it's deleted).
_ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
_FA_SRC_DEFAULT = str(_ENGINE_DIR)

# In-repo engine config (config/llm.yaml → deepseek, config/loaders.yaml →
# G:/stocks data). Forced via FA_CONFIG_DIR in create_app() so the engine reads
# THIS repo's config instead of an external workspace/user config (which would
# otherwise shadow it via find_config's workspace lookup). Keeps guanlan-v2
# self-contained for everything except data (which stays external, in stocks).
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# guanlan 原生 market_status.json 落点(仓内 data/, 不写 G:/stocks)。create_app() 用
# MARKET_STATUS_PATH env 把引擎 default_market_status_path() 指到这里 → 大盘/主线/regime
# 走 guanlan 自包含生成器(guanlan_v2/strategy/market_status.py)的产物。
_MARKET_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "market_status.json"

# var/secrets.env(gitignored, KEY=VALUE 每行一条):API key 的文件兜底。
# 为什么需要:9999 由 check_9999.ps1 代际链拉起,链上环境是首次登录时的快照——
# setx 的新 key(如 KIMI_API_KEY, 2026-07-03)在看门狗复活的进程里不存在,
# kimi 调用会 401。文件兜底让任意代际拉起的 server 都能拿到 key(不覆盖已有 env)。
_SECRETS_ENV = Path(__file__).resolve().parent.parent / "var" / "secrets.env"


def _load_secrets_env() -> None:
    try:
        if not _SECRETS_ENV.exists():
            return
        for line in _SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    except Exception:  # noqa: BLE001 — 兜底失败不阻断启动;缺 key 的调用会诚实 401
        pass


_load_secrets_env()


def _ensure_engine_importable() -> str:
    """Make ``financial_analyst`` importable, with GUANLAN_FA_SRC AUTHORITATIVE.

    The requested engine source (``GUANLAN_FA_SRC`` env, else the dev default
    ``G:/fa-watch-wt/src``) is PREPENDED to ``sys.path`` BEFORE import, so it
    wins over any installed ``financial_analyst`` (e.g. an editable install of
    an older branch — which on this machine shadows the engine we actually
    built unless we prepend first). Returns the resolved
    ``financial_analyst.__file__`` for boot logging / sanity checks.
    """
    fa_src = os.environ.get("GUANLAN_FA_SRC", _FA_SRC_DEFAULT)
    if fa_src and Path(fa_src).is_dir() and "financial_analyst" not in sys.modules:
        sys.path.insert(0, str(Path(fa_src)))
    import financial_analyst  # noqa: F401

    resolved = getattr(financial_analyst, "__file__", "") or ""
    if fa_src and Path(fa_src).is_dir() and resolved:
        try:
            Path(resolved).resolve().relative_to(Path(fa_src).resolve())
        except ValueError:
            import warnings

            warnings.warn(
                f"guanlan_v2: GUANLAN_FA_SRC={fa_src} requested but financial_analyst "
                f"resolved to {resolved} — a conflicting install shadowed it. "
                f"The recipe layer / market_status / signal_pack endpoints may be missing.",
                stacklevel=2,
            )
    return resolved


def create_app():
    """Build the V2 app: full financial_analyst engine + V2 static UI.

    Returns the FastAPI instance. Kept as a factory (not just module-level)
    so tests / verifiers can construct a fresh app with TestClient.
    """
    resolved = _ensure_engine_importable()

    # Self-contained config: make the engine read guanlan-v2/config/ (in-repo)
    # unless the operator explicitly points FA_CONFIG_DIR elsewhere. Without this
    # the engine's find_config() can resolve an external workspace config (e.g.
    # G:/financial-analyst/config) and shadow our llm.yaml/loaders.yaml — which
    # is why /models showed the stale qwen provider before this was added.
    if _CONFIG_DIR.is_dir():
        os.environ.setdefault("FA_CONFIG_DIR", str(_CONFIG_DIR))

    # 大盘/主线/regime 走 guanlan 自包含生成器的产物(仓内 data/market_status.json)。
    # 引擎 default_market_status_path() 见此 env 且文件存在即读它, 否则回退老的 stocks
    # parquet(向后兼容)。生成/刷新: python -m guanlan_v2.strategy.market_status。
    os.environ.setdefault("MARKET_STATUS_PATH", str(_MARKET_STATUS_PATH))

    # 双 wisdom 合流(互通审计 P1⑩):引擎 WisdomStore / buddy wisdom_search 的根
    # 指到 guanlan 卡片库(.data/wisdom)。此前两套存储互不相通 —— 卡片页 approve 的
    # 经验卡,对话/研报 agent 的 wisdom_search 永远搜不到(引擎默认根是空目录)。
    # 合流后:promote/approve 落 approved/ 即可被检索;subprocess(run_report)继承本 env。
    _WISDOM_ROOT = Path(__file__).resolve().parent.parent / ".data" / "wisdom"
    if _WISDOM_ROOT.is_dir():
        os.environ.setdefault("FA_WISDOM_ROOT", str(_WISDOM_ROOT))

    from fastapi.responses import RedirectResponse
    from fastapi.staticfiles import StaticFiles

    class _UIStatic(StaticFiles):
        """StaticFiles 子类:对 .html(text/html)回 ``Cache-Control: no-cache``,
        强制浏览器每次向服务器复验(ETag/Last-Modified → 未改回 304,改了回 200)。

        缘由:页面 URL 不带 cache-buster(不像 jsx/css 带 ?v=),普通刷新会吃到浏览器
        缓存的旧 .html → 看不到 bump 后的 ?v= 脚本标签。no-cache 让普通刷新即可拿到
        新版,无需 Ctrl+Shift+R / ?cb=。仅作用于 /ui 静态层(不碰引擎 API / SSE 流式
        响应,故用子类而非全局 middleware —— 后者会缓冲 SSE)。jsx/css 仍走默认缓存
        (它们的 ?v= 已足够 bust)。
        """

        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            try:
                is_html = str(path).endswith(".html") or (
                    "text/html" in (getattr(resp, "media_type", "") or ""))
                if is_html:
                    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            except Exception:  # noqa: BLE001 — 复验头是增强项,失败也不能拦住发文件
                pass
            return resp

    from financial_analyst.buddy.server import build_app

    app = build_app()  # all real endpoints, real data via get_data_paths
    print(f"[guanlan_v2] engine source: {resolved}", file=sys.stderr)

    # guanlan 自有 cards 后端(经验卡 /cards/*)。卡是 UI 量化形状(cat/verdict/conf/
    # ic/expr),属 guanlan 应用层,与引擎 wisdom 的定性卡是不同领域,故落在 guanlan_v2/
    # cards/ 而非 vendored engine。见 docs/superpowers/specs/2026-06-04-cards-backend-wiring-design.md。
    from guanlan_v2.cards.api import build_cards_router

    app.include_router(build_cards_router())

    # GL 档案总线后端影子库(P2-C):strategy/research/decision 三类真物料防清缓存丢失。
    from guanlan_v2.archive.api import build_archive_router

    app.include_router(build_archive_router())

    # guanlan 自有 seats 后端(落子 /seats/*):日线真 K(读 stock_data 经引擎 loader),
    # 供「复盘」在真实价格上逐 bar 推演。其余证据层(因子/研报/regime)仍 mock,待上游。
    from guanlan_v2.seats.api import build_seats_router

    app.include_router(build_seats_router())

    # guanlan 自有因子库(factorlib):启动时把 base/ + mined/ 因子(引擎 zoo-DSL 表达式,
    # 由 stocks 的 Qlib-DSL 挖掘产物译写并校验而来)经引擎 primitive 编译→注册进引擎
    # 运行期 zoo registry(进程级全局 dict,不改 engine/ 文件),使其立即出现在引擎
    # /factor/list 的 registered;并暴露 /factorlib/* 自有端点。register 必须在
    # _ensure_engine_importable() 之后(此处天然满足)——其内部先 import zoo 触发内置族注册。
    from guanlan_v2.factorlib import build_factorlib_router, register_library_factors

    try:
        _lib = register_library_factors()  # 幂等:内部 unregister→register;单条失败记台账不崩
        print(f"[guanlan_v2] factorlib: registered {_lib.get('registered', 0)} / "
              f"{_lib.get('total', 0)} factors into zoo "
              f"(skipped {_lib.get('skipped', 0)})", file=sys.stderr)
    except Exception as _e:  # noqa: BLE001  —— 注册失败不阻断启动,/factorlib/list 仍可用
        print(f"[guanlan_v2] factorlib: register skipped ({type(_e).__name__}: {_e})",
              file=sys.stderr)
    app.include_router(build_factorlib_router())

    # guanlan 自有工作流节点(workflow):P2 /feature/build —— 收特征公式 + 标签公式
    # (或前向收益 horizon)→ 在 universe 面板上物化真 X/y → 真统计(n_dates/n_codes/
    # coverage/特征-标签 IC/预览)+ 可复算 fe spec(供 P3 ML 节点重建训练集)。复用
    # /factor/report 同一条 universe→panel→eval 链,引擎 primitive 全部函数体内延迟
    # import(不改 engine/);诚实失败 ok:False + reason(HTTP 200)。一个 router 容纳
    # P2-P5 自有节点端点(P3 /model/*、P5 /backtest/* 往同一 router 追加)。
    from guanlan_v2.workflow import build_workflow_router

    app.include_router(build_workflow_router())

    # guanlan 自有选股(screen):POST /screen/run —— 把前端约束(因子+权重+TopN+行业中性+
    # 流动性/剔除)编译成「最新截面打分→约束→行业中性→TopN→分布统计」,返回与前端
    # window.xgBuild 同形结果。复用 workflow/factor 同一条 universe→panel→eval 链(引擎
    # primitive 全延迟 import,不改 engine/);价量类因子已接真 DSL,北向/PEAD/消息面因子
    # 与 ST/停牌/涨跌停/次新 排除属字段缺口,诚实标 unsupported(见
    # docs/superpowers/specs/2026-06-04-screen-backend-wiring-design.md §7)。
    from guanlan_v2.screen import build_screen_router

    app.include_router(build_screen_router())

    # guanlan 自有报告库(reports):POST /report/save、GET /report/list、GET /report/get/{id}、
    # POST /report/delete —— 把工作流 run 结果存盘 + 浏览/重看/删除。落 guanlan_v2/reports/store/*.json
    # (仓内自有报告 JSON,非 engine、不拷 stocks 数据)。
    from guanlan_v2.reports import build_reports_router

    app.include_router(build_reports_router())

    # ── 帷幄 console(单核心对话总控台,一期)─────────────────────
    from guanlan_v2.console import build_console_router

    app.include_router(build_console_router())

    # ── P2:自主研究回路(提案→求值→批判→改进 后台单飞;零开关零定时器,
    #     只能被显式 POST /research/loop/start 发起 → 合并零行为变化)──────
    from guanlan_v2.research import build_research_router

    app.include_router(build_research_router())

    # ── P5:选股池再打分(产业链分+情绪分+综合;展示型,零信号回写)─────────────
    from guanlan_v2.screen.rescore import build_rescore_router

    app.include_router(build_rescore_router())

    # guanlan 自有大盘状态刷新(market):POST /market_status/refresh —— 后台线程重生成
    # 仓内 data/market_status.json(guanlan_v2/strategy/market_status.py 原生生成器,
    # 直读引擎 day 二进制现算 regime/涨停/主线,不依赖 qlib/fa-watch-wt)。读仍走引擎
    # GET /watch/market_status(经上面 setdefault 的 MARKET_STATUS_PATH env 读仓内 json)。
    from guanlan_v2.market import build_market_router, start_market_status_scheduler

    app.include_router(build_market_router())
    # 收盘后自动刷新调度(进程内后台 daemon 线程, 幂等只起一次):启动期按需刷一次 +
    # 每日本地 18:00 后(env MARKET_STATUS_REFRESH_HOUR 可调)自动重生成 market_status.json,
    # 免手动点「数据」按钮 / 跑 CLI。盘中守卫在 generate 内, 早触发也只落上一完整收盘日。
    start_market_status_scheduler()

    # ── AI投研看板(industry):GET /industry/board、/industry/segment/{id}、
    #     /industry/doc/{doc_id}、POST /industry/ingest、GET /industry/ingest_state ──
    from guanlan_v2.industry import build_industry_router  # AI投研看板(2026-07-02 spec)
    app.include_router(build_industry_router())

    # ── 全球情绪温度计(macro):GET /macro/pulse、/macro/history ──
    # PM+Kalshi 全球宏观预期概率 × A股本土打板温度(2026-07-06 spec,纯展示层)
    from guanlan_v2.macro import build_macro_router
    app.include_router(build_macro_router())

    # ── 板块资金流向(fundflow):GET /fundflow/live、/fundflow/history ──
    # 纯展示层(2026-07-08 spec)。盘中多线由东财分钟线直出(fflow/kline klt=1,当日 240 点),
    # 开盘即完整、进程重启不断线,故无 poller、无自累快照;两端点各带 SWR 缓存。
    from guanlan_v2.fundflow import build_fundflow_router
    app.include_router(build_fundflow_router())

    # ── 盘后自主复盘官(autonomy):GET /autonomy/jobs、/autonomy/report/latest、
    # POST /autonomy/run(帷幄智能体化一期·单元二 Task 5)。调度钩子挂在 rescore 落定后
    # (opt-in GUANLAN_REVIEW_DAILY,默认关),此处仅挂端点——注册失败不阻断启动。
    try:
        from guanlan_v2.autonomy import build_autonomy_router
        app.include_router(build_autonomy_router())
    except Exception as exc:  # noqa: BLE001 — autonomy 注册失败不阻断启动
        print(f"[guanlan_v2] autonomy router skipped: {exc}", file=sys.stderr)

    # ── 数据健康总闸(datafeed):GET /data/health ──
    # 全仓数据新鲜度一处可见(2026-07-07 中台③;收编 T5 断供/停摆)
    from guanlan_v2.datafeed.api import build_datafeed_router
    app.include_router(build_datafeed_router())

    # P1:regen 每日 EOD 自动再生(opt-in;GUANLAN_REGEN_DAILY=1 才启;
    # 定时器随本进程存亡,非 24/7 保证——进程死定时即停,health.regen_scheduler 显形)
    from guanlan_v2.screen.api import start_regen_daily_scheduler
    start_regen_daily_scheduler()

    # ── guanlan 自有 MCP(挂 /gl-mcp,与引擎 /mcp 并存)──────────────
    # build_app() 已挂引擎 MCP 于 /mcp 并为其设了 app lifespan。Starlette 不会自动
    # 跑被挂子应用的 lifespan,故这里把 guanlan MCP 的 session-manager lifespan
    # **叠加**进现有 lifespan(wrap app.router.lifespan_context:先进原,再进 guanlan MCP)。
    import contextlib as _ctxlib
    from guanlan_v2.glmcp.http import build_mcp_http_app as _build_gl_mcp
    _gl_mcp_app = _build_gl_mcp()
    _prev_lifespan = app.router.lifespan_context

    @_ctxlib.asynccontextmanager
    async def _composed_lifespan(_app):
        async with _prev_lifespan(_app):
            async with _gl_mcp_app.router.lifespan_context(_gl_mcp_app):
                import asyncio as _aio
                _revive = _aio.create_task(_checker_revive_loop())   # 互拉守望(见函数 docstring)
                # 后端定时盯盘(2026-07-11 落子改造 Task 1;opt-in:GUANLAN_SEATS_WATCH=1 才起)。
                # enabled 开关持久化在 var/seats_watch.json → 重启自恢复;tick 内 LLM/取数全走
                # to_thread(run_loop 只 await asyncio.to_thread(tick)),绝不堵事件循环。
                _seats_watch = None
                if os.environ.get("GUANLAN_SEATS_WATCH") == "1":
                    from guanlan_v2.seats import watcher as _seats_watcher
                    _seats_watch = _aio.create_task(_seats_watcher.run_loop())
                try:
                    yield
                finally:
                    if _seats_watch is not None:
                        _seats_watch.cancel()
                    _revive.cancel()

    app.router.lifespan_context = _composed_lifespan
    app.mount("/gl-mcp", _gl_mcp_app)

    if not _UI_DIR.is_dir():
        raise RuntimeError(f"guanlan_v2 UI dir missing: {_UI_DIR}")

    # Serve the V2 multi-page UI under /ui (html=True → /ui/ serves index.html,
    # which redirects to the 研究图谱 home screen). Chinese filenames are
    # URL-encoded by the browser and decoded by Starlette's StaticFiles.
    app.mount("/ui", _UIStatic(directory=str(_UI_DIR), html=True), name="guanlan-ui")

    # Convenience root redirect → UI home. Best-effort: if the engine already
    # registers "/", that one wins (FastAPI matches first-registered); harmless.
    @app.get("/", include_in_schema=False)
    def _root_redirect():
        return RedirectResponse(url="/ui/")

    return app


async def _checker_revive_loop() -> None:
    """互拉守望:检查器心跳(var/check_9999.heartbeat)陈旧 >600s → detached 拉起新代际。
    检查器守 server、server 守检查器;双死才需登录 Run key/人工。绝不用 schtasks(本机
    Schedule 服务派生进程冻死在 loader init,见 scripts/register_watchdog_9999.ps1 头注)。"""
    import asyncio as _aio
    import subprocess
    import time
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    heart = repo / "var" / "check_9999.heartbeat"
    script = repo / "scripts" / "check_9999.ps1"
    cmd = ["C:\\Windows\\System32\\conhost.exe", "--headless", "powershell.exe",
           "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    while True:
        try:
            stale = (not heart.exists()) or (time.time() - heart.stat().st_mtime > 600)
            if stale and script.exists():
                subprocess.Popen(cmd, creationflags=0x00000008 | 0x00000200)  # DETACHED
                print("[revive] check_9999 心跳陈旧,已拉起新代际", flush=True)
                await _aio.sleep(300)     # 给新代际时间写心跳,防派生风暴
        except Exception:  # noqa: BLE001 — 守望绝不拖垮 server
            pass
        await _aio.sleep(60)


app = create_app()


def main(host: str = "127.0.0.1", port: int = 9999) -> None:
    """Launch uvicorn. host/port overridable via GUANLAN_HOST / GUANLAN_PORT."""
    host = os.environ.get("GUANLAN_HOST", host)
    port = int(os.environ.get("GUANLAN_PORT", port))
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
