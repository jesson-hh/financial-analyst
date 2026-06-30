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
                yield

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


app = create_app()


def main(host: str = "127.0.0.1", port: int = 9999) -> None:
    """Launch uvicorn. host/port overridable via GUANLAN_HOST / GUANLAN_PORT."""
    host = os.environ.get("GUANLAN_HOST", host)
    port = int(os.environ.get("GUANLAN_PORT", port))
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
