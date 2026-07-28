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

# 引擎 mainline-classifier(研报 DAG 节点)dev fallback 硬编码指向 G:/stocks 里的月度主线
# 面板(34 天陈旧,作者机路径)。create_app() 用 FA_MAINLINE_PANEL env 把它指到仓内 vendor
# 产物(guanlan_v2/strategy/vendor/artifacts/,随 regen 刷新),子进程(研报/glmcp)继承。
_MAINLINE_PANEL_PATH = (Path(__file__).resolve().parent.parent / "guanlan_v2" / "strategy"
                        / "vendor" / "artifacts" / "monthly_mainlines_panel.parquet")

# var/secrets.env(gitignored, KEY=VALUE 每行一条):API key 的文件兜底。
# 为什么需要:9999 由 check_9999.ps1 代际链拉起,链上环境是首次登录时的快照——
# setx 的新 key(如 KIMI_API_KEY, 2026-07-03)在看门狗复活的进程里不存在,
# kimi 调用会 401。文件兜底让任意代际拉起的 server 都能拿到 key(不覆盖已有 env)。
_SECRETS_ENV = Path(__file__).resolve().parent.parent / "var" / "secrets.env"

# 仓根引导:生产由 check_9999.ps1 以**脚本**形式拉起(`python guanlan_v2\server.py`),
# CPython 于是把 sys.path[0] 设成本文件所在的**包目录**,仓根不在路径上 —— 任何模块层的
# `import guanlan_v2.*` 都会 ModuleNotFoundError。文档里的 `python -m guanlan_v2.server`
# 和 pytest 都自带仓根,碰不到这条路径(2026-07-26 全套件绿而 9999 起不来,就是这么来的)。
# 必须在本文件**第一个** guanlan_v2 导入之前无条件执行。守护测试:
# tests/test_server_script_launch.py。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# orchestration durable-store 的绑定实况(R23+R24)。记录本体住在零依赖叶子模块
# guanlan_v2/orch_store_status.py —— 那里是 state 词表(not_attempted / bound /
# unavailable / corrupt / failed)与 /data/health provider 的**唯一**定义处,故此处不再
# 复制一份字面量(避免两份漂移)。本文件只负责写入 "unavailable"(唯一只有 server 能观察到
# 的状态:连 startup 模块本身都导不进来)与兜底的 "failed",并由只读探针
# GET /orchestration/store_status 暴露。叶子模块无任何 import,导它不会失败。
from guanlan_v2 import orch_store_status as _orch_status  # noqa: E402


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

    # 引擎 mainline-classifier(研报 DAG 节点)原读 G:/stocks 里 34 天陈旧的主线面板(dev
    # fallback 硬编码,作者机路径)。setdefault 后子进程(研报 CLI / glmcp detached 子进程)
    # 继承此 env,改吃仓内新鲜产物 —— 34 天陈旧根治。见 MARKET_STATUS_PATH 同款先例(上一行)。
    os.environ.setdefault("FA_MAINLINE_PANEL", str(_MAINLINE_PANEL_PATH))

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
    # Phase 9 · Task 10 · carry C-C(server 侧一半):把 Phase 7 的 PlanApprovalCoordinator
    # 经**耐久 replay() 通路**在启动期绑一次(崩溃切口的决策行 → 一次终态 admission 效果),
    # 并桥好 approvals_sink(协调器构造的那一条权威 PlanApproval 存进 admission 自己的
    # approvals 库 → record_approval 载入的权威 == 耐久决策 → 审批真正抵达编排事件面)。
    # 未接 admission provider 时诚实返回 None → 控制台 plan-approval 端点保持既有 503,
    # 生产行为逐字不变(Task 12 接上 provider 后重启即自愈)。签名探测:console/api.py 由
    # 并发 session 持有,kwarg 不在时退回原调用,绝不因此拖垮启动。
    _p7_coord = None
    try:
        from guanlan_v2.orchestration.adapters.api import (
            bind_process_plan_approval_coordinator,
            plan_approval_console_kwargs,
        )

        _p7_coord = bind_process_plan_approval_coordinator()
    except Exception as _e:  # noqa: BLE001 — P7 绑定是加法项,失败不阻断启动
        print(f"[guanlan_v2] plan-approval coordinator skipped "
              f"({type(_e).__name__}: {_e})", file=sys.stderr)
        plan_approval_console_kwargs = None  # type: ignore[assignment]

    from guanlan_v2.console import build_console_router

    _console_kw = {}
    if plan_approval_console_kwargs is not None:
        import inspect as _inspect
        _console_kw = plan_approval_console_kwargs(coordinator=_p7_coord)
        # R21 §11 / R22 §3 carry:没有任何东西生产「操作者凭证材料」,故 console/api.py 的
        # _resolve_actor(None) 一律拒。这里补上唯一诚实的来源 —— identity.declared_operator_actor
        # (读 config/orchestration/operators.json 的那条声明,与 ConfigOperatorVerifier 同源,
        # 因此它给出的 id 按构造就是 verifier 接受的 id)。传**可调用**而非取值:决策时刻的
        # 声明才是权威,撤销一个操作者立即生效、无需重启。sealed 的 plan_approval_console_kwargs
        # 只递协调器,所以这一行只能写在这里。诚实声明:控制台本身没有认证 —— 绑上它意味着
        # **任何能访问本机控制台的调用方都以那位被声明的操作者身份审批**,这是单用户本机
        # 工作台 + 明文 allowlist 的真实描述,不是新增弱点,只是不再假装材料来自别处。
        if _console_kw:
            try:
                from guanlan_v2.orchestration.adapters.identity import (
                    declared_operator_actor as _declared_operator_actor,
                )

                _console_kw["plan_approval_actor"] = _declared_operator_actor
            except Exception as _e:  # noqa: BLE001 — 拿不到声明就退回既有的 403,不阻断启动
                print(f"[guanlan_v2] plan-approval actor material unavailable "
                      f"({type(_e).__name__}: {_e}); /console/plan/approvals/decide "
                      f"keeps refusing", file=sys.stderr)
        _accepted = _inspect.signature(build_console_router).parameters
        _kept = {k: v for k, v in _console_kw.items() if k in _accepted}
        # 一旦有东西被探测过滤掉,必须**大声**报出来:静默丢弃协调器 = 把一次响亮的启动失败
        # 换成一次无声的能力丢失(plan-approval 端点继续 503 且无任何诊断)。
        for _k in sorted(set(_console_kw) - set(_kept)):
            print(f"[guanlan_v2] WARNING plan-approval wiring DROPPED: "
                  f"build_console_router() does not accept {_k!r} — the Phase-7 "
                  f"coordinator is bound but unreachable; console plan-approval "
                  f"endpoints will keep returning 503.", file=sys.stderr)
        _console_kw = _kept
    app.include_router(build_console_router(**_console_kw))

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
                    # Phase 10 Task 7 加法缝:GUANLAN_SEATS_DEEP=1 才尝试装配编排深
                    # 研判包装器(lease 门控,fast 永远照跑);env 未设 ⇒ decide_fn=None
                    # ⇒ run_loop/tick 行为逐位不变。装配失败(今日:Phase-9 catalog 无
                    # 生产 material source,Task 11 收口)→ 响亮记日志,维持 fast-only,
                    # 绝不伪造深研判通道。
                    _seats_deep_fn = None
                    if os.environ.get("GUANLAN_SEATS_DEEP") == "1":
                        try:
                            from guanlan_v2.orchestration.pipeline import (
                                live_decide as _live_decide,
                            )
                            _seats_deep_fn = _live_decide.build_production_decide_fn()
                            print("[guanlan_v2] GUANLAN_SEATS_DEEP=1:编排深研判 "
                                  "decide_fn 已装配", file=sys.stderr)
                        except Exception as _deep_exc:  # noqa: BLE001 — 诚实降级 fast-only
                            print(f"[guanlan_v2] GUANLAN_SEATS_DEEP=1 但深研判装配失败,"
                                  f"维持 fast-only:{type(_deep_exc).__name__}: {_deep_exc}",
                                  file=sys.stderr)
                    _seats_watch = _aio.create_task(
                        _seats_watcher.run_loop(decide_fn=_seats_deep_fn))
                # 事件库入库 tick(小 phase 乙;opt-in:GUANLAN_EVENTS_INGEST=1 才起)。
                # maybe_ingest_tick 内三门(env 闸/30min 节奏/EOD 兜底)自吞异常;run_loop
                # 每拍 to_thread 调它,取数/写盘全在工作线程,绝不堵事件循环。
                _events_ingest = None
                if os.environ.get("GUANLAN_EVENTS_INGEST") == "1":
                    from guanlan_v2.events import ingest as _events_ingest_mod
                    _events_ingest = _aio.create_task(_events_ingest_mod.run_loop())
                try:
                    yield
                finally:
                    if _seats_watch is not None:
                        _seats_watch.cancel()
                    if _events_ingest is not None:
                        _events_ingest.cancel()
                    _revive.cancel()

    app.router.lifespan_context = _composed_lifespan
    app.mount("/gl-mcp", _gl_mcp_app)

    # ── orchestration 持久化存储 + 诚实的启动期中断标记(Phase 9 · Task 1b / R23+R24)──
    # 把 Phase 2 store ABI 的 durable jsonl/file 实现绑定一次(root 默认 var/orchestration/,
    # env GUANLAN_ORCH_STORE_ROOT 覆盖供 9998 验证),折叠日志重建持久化状态;启动扫描把任何
    # 「已受理但未完成」的节点执行 attempt 经既有 RunResult 记录通路标为 interrupted(绝不重跑/
    # 重受理/伪造进度),被 park 的 WAITING_FOR_MATURITY head(state cell)原样保留。
    #
    # R23+R24:这里**必须**是全进程第一次也是唯一一次绑定 —— bind_process_durable_stores_
    # and_scan 每进程幂等(第二次带 kwargs 调用是静默 no-op),且 RuntimeStores 在构造时就把
    # allowed_cell_namespaces 冻成 frozenset(只读属性、无 setter)。所以 resolver(Phase-1 +
    # Phase-2 + Phase-9 累积注册表)、时钟、以及 14 条 state-cell 命名空间全集,全部在
    # guanlan_v2.orchestration.startup 里派生后一次性传进去(细节见该模块 docstring)。
    #
    # 诚实失败:绑定结果落进一份可查询的 status 记录(下面 /orchestration/store_status 只读
    # 暴露),损坏(DurableStoreCorrupt)会带稳定标记 ORCH-STORE-CORRUPT 打 CRITICAL + stderr。
    # 默认仍放行启动 —— 9999 进程承载整个产品 UI,不能被一个加法子系统的损坏日志拖垮 —— 但是
    # 「本进程没有 orchestration store」与「一切正常」在 status 里是两个不同的 state,绝不混同。
    # 运维要更硬的保证:GUANLAN_ORCH_STORE_STRICT=1 → 直接拒绝启动。
    #
    # 红线(复核 Fix 1):**唯一**能让本段拒启动的路径是 strict 模式主动抛的
    # OrchestrationStoreBootRefused。除它之外的任何异常(orchestration 包缺席、durable.py
    # 坏了、接线 bug……)都必须降级成一个被记录的 state —— 绝不能让一个加法子系统的
    # ImportError 从 create_app() 逃出去,把整个 9999(选股/落子/帷幄/datafeed/MCP)带崩。
    # startup.bind_orchestration_stores 内部已把每一步都包进 guard;这里的两层 except 是
    # 结构性的第二道保险,保证「只有 strict 能拒启动」这条性质由调用点本身兜住。
    #
    # strict 的两条支路必须同构(复核第三轮 Fix):记录成同一个 state="unavailable" 却在同一个
    # 开关下有相反的启动行为,是最糟的半吊子。运维打开 GUANLAN_ORCH_STORE_STRICT=1 断言的是
    # 「本进程必须有可用的 orchestration store」,而「包整个不在」恰恰是这个断言最彻底的失败,
    # 绝不该反而放行。_BootRefused / STRICT_ENV 都住在零依赖叶子里,正是为了让这条支路
    # ——startup 自己都导不进来的那条—— 也能拿到它们。
    _strict = os.environ.get(_orch_status.STRICT_ENV) == "1"
    try:
        from guanlan_v2.orchestration.startup import (
            bind_orchestration_stores as _bind_orch_stores,
        )
    except Exception as _e:  # noqa: BLE001 — orchestration 包缺席/导入失败:诚实跳过,不阻断启动
        _orch_status.record_status(dict(
            _orch_status.blank_status(), state="unavailable", strict=_strict,
            error_type=type(_e).__name__, error=str(_e)))
        print(f"[guanlan_v2][ORCH-STORE-UNAVAILABLE] orchestration durable stores not "
              f"wired — this process has NO orchestration store "
              f"({type(_e).__name__}: {_e}). Set {_orch_status.STRICT_ENV}=1 to refuse "
              f"the boot instead.", file=sys.stderr)
        # strict:与 startup 内部那条支路同一个拒启动定义(叶子里的 refuse_if_strict)。
        _orch_status.refuse_if_strict(_strict, "ORCH-STORE-UNAVAILABLE", _e)
    else:
        try:
            _bind_orch_stores()
        except _orch_status.OrchestrationStoreBootRefused:
            raise                       # strict 模式:唯一被允许拒启动的路径
        except Exception as _e:  # noqa: BLE001 — 防御纵深:startup 自身出 bug 也不许崩启动
            # strict=_strict 必须如实盖章:记录里写 "strict": false 而开关其实是开的,
            # 就是一句小谎 —— 本项目不出这种货。三条支路(startup 内 corrupt/failed/
            # unavailable、server 的 unavailable、以及这条兜底 failed)自此**全部**
            # 走叶子里同一个 refuse_if_strict,不存在「记同一个 state 却有相反启动行为」。
            _orch_status.record_status(dict(
                _orch_status.blank_status(), state="failed", strict=_strict,
                error_type=type(_e).__name__, error=str(_e)))
            print(f"[guanlan_v2][ORCH-STORE-FAILED] orchestration durable store bind "
                  f"raised unexpectedly — this process has NO orchestration store "
                  f"({type(_e).__name__}: {_e}). Set {_orch_status.STRICT_ENV}=1 to "
                  f"refuse the boot instead.", file=sys.stderr)
            _orch_status.refuse_if_strict(_strict, "ORCH-STORE-FAILED", _e)

    @app.get("/orchestration/store_status")
    def _orchestration_store_status():  # noqa: ANN202 — 只读运维探针
        """本进程 orchestration durable store 的绑定实况(唯一健康值 state=="bound")。"""
        return _orch_status.orchestration_store_status()

    # ── R3:把适配层路由接到本进程已绑定的 durable stores(opt-in)────────────────
    # 在此之前 set_adapters_router_deps 在生产里**从无调用方**,于是 /orchestration/* 六端点
    # 全部诚实 503(*_unwired)。这一行是那个调用方。默认关(GUANLAN_ORCH_LAUNCHER=1 才开):
    # 打开等于让一个原本一直在拒绝的面在同时服务 选股/落子/帷幄 的进程里真的活起来,而 launcher
    # 的 admission 半边尚未完工(见 R3 报告),半个子系统上线必须是一次决定,不能是重启的副作用。
    # 绑定内部全程 guard 且**从不抛**——出 bug 只记 failed、路由继续 503,启动照常。
    _orch_launcher_status = {"state": "not_attempted"}
    try:
        from guanlan_v2.orchestration.startup import (
            bind_orchestration_launcher as _bind_orch_launcher,
        )

        _orch_launcher_status = _bind_orch_launcher()
    except Exception as _e:  # noqa: BLE001 — 加法项:绑定失败绝不阻断启动
        _orch_launcher_status = {"state": "unavailable", "error_type": type(_e).__name__,
                                 "error": str(_e)}
        print(f"[guanlan_v2][ORCH-LAUNCHER-UNAVAILABLE] adapters-router binding "
              f"skipped ({type(_e).__name__}: {_e}); /orchestration/* keeps its "
              f"honest *_unwired 503s", file=sys.stderr)

    @app.get("/orchestration/launcher_status")
    def _orchestration_launcher_status():  # noqa: ANN202 — 只读运维探针
        """本进程适配层路由的接线实况(state=="bound" 才是真接上)。"""
        try:
            from guanlan_v2.orchestration.startup import orchestration_launcher_status

            return orchestration_launcher_status()
        except Exception:  # noqa: BLE001 — 包缺席时回落到启动期记下的那份
            return dict(_orch_launcher_status)

    # ── orchestration 适配层薄路由(Phase 9 · Task 10)/orchestration/* 六端点 ────────
    # 影子/草稿专用:replay start/state/curves/wakeup + weiwo start/state。start 只开
    # REQUIRED 人审请求(绝不自审、绝不落预算/run),两个 GET 只读,未成熟曲线诚实回
    # status/resume_after 而非编造曲线;任何端点都不写委托/信号。依赖未接线 → 诚实 503。
    # 加法挂载(与上面十六个 build_*_router 同形);后端改动须重启 9999 才生效,验证用 9998。
    try:
        from guanlan_v2.orchestration.adapters.api import build_adapters_router

        app.include_router(build_adapters_router())
    except Exception as _e:  # noqa: BLE001 — 适配层路由是加法项,失败不阻断启动
        print(f"[guanlan_v2] orchestration adapters router skipped "
              f"({type(_e).__name__}: {_e})", file=sys.stderr)

    # ── orchestration pipeline 薄路由(Phase 10 · Task 9)/orchestration/pipeline/* ──
    # 五端点:start(goal|preset_id|source_kind 三模式,都只开 REQUIRED 人审候选,绝不
    # 自批/冻结/执行/写单)+ state/runs/screening-latest 三只读投影 + D7 TA 收件箱。
    # 依赖未绑定 → 各端点诚实 *_unwired 503;生产绑定 GUARDED(store 未 bound / 链装配
    # 失败只 stderr 记录,路由照常挂)。加法挂载(与上面 build_*_router 同形)。
    try:
        from guanlan_v2.orchestration.pipeline.api import (
            bind_production_pipeline_deps,
            build_pipeline_router,
        )

        app.include_router(build_pipeline_router())
        bind_production_pipeline_deps()   # guarded inside — never raises
    except Exception as _e:  # noqa: BLE001 — 加法项:失败不阻断启动
        print(f"[guanlan_v2] orchestration pipeline router skipped "
              f"({type(_e).__name__}: {_e})", file=sys.stderr)

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
