# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for financial-analyst CLI.

把整个 financial-analyst Python 模块 + venv 依赖打成 single-file .exe,
给 Tauri sidecar 调用. 用户最终装的 desktop app 里只有这一个 .exe.

用法:
    cd G:/financial-analyst
    G:/financial-analyst/.venv/Scripts/pyinstaller.exe \\
        packaging/pyinstaller/financial-analyst.spec \\
        --distpath packaging/dist \\
        --workpath packaging/build \\
        --noconfirm

产出:
    packaging/dist/financial-analyst.exe   (~200-500 MB, single file)

打完跑:
    packaging/dist/financial-analyst.exe data status   # 应能正常工作

后续 Tauri 配置里 sidecar 引用这个 exe.
"""
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent   # spec is packaging/pyinstaller/
SRC = PROJECT_ROOT / "src" / "financial_analyst"

# ─────────────────── Hidden imports ───────────────────
# PyInstaller 静态分析找不到的: dynamic imports / plugins / typer subcommands
hidden_imports = [
    # CLI subcommand modules
    "financial_analyst.cli",
    "financial_analyst.data_cli",
    "financial_analyst.init_cli",
    "financial_analyst.tui",
    "financial_analyst.mcp_server",
    # agents (all auto-registered via _ensure_registered)
    "financial_analyst.agent.tier1.quote_fetcher",
    "financial_analyst.agent.tier1.factor_computer",
    "financial_analyst.agent.tier1.model_predictor",
    "financial_analyst.agent.tier1.news_reader",
    "financial_analyst.agent.tier1.f10_reader",
    "financial_analyst.agent.tier2.fundamental_analyst",
    "financial_analyst.agent.tier2.technical_analyst",
    "financial_analyst.agent.tier2.whale_analyst",
    "financial_analyst.agent.tier2.quant_analyst",
    "financial_analyst.agent.tier3.bull_advocate",
    "financial_analyst.agent.tier3.bear_advocate",
    "financial_analyst.agent.tier3.risk_officer",
    "financial_analyst.agent.tier3.report_writer",
    "financial_analyst.agent.tier3.introspector",
    # data layer (updaters added in v1.9.5)
    "financial_analyst.data.bin_writer",
    "financial_analyst.data.updaters.pytdx_pool",
    "financial_analyst.data.updaters.pytdx_kline",
    "financial_analyst.data.updaters.tencent_basic",
    "financial_analyst.data.loaders.qlib_binary",
    "financial_analyst.data.loaders.tushare",
    "financial_analyst.data.collectors.tencent_quote",
    # LLM providers (litellm dispatches dynamically)
    "litellm.llms.openai_like.chat.transformation",
    "litellm.llms.dashscope.chat.transformation",
    "litellm.llms.anthropic.chat.transformation",
    "litellm.llms.deepseek.chat.transformation",
    # serve mode
    "fastapi",
    "uvicorn",
    "uvicorn.lifespan.on",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    # pytdx 配置
    "pytdx.config.hosts",
]

# ─────────────────── Data files ───────────────────
# bundle yaml configs / memories markdown (用户可在 ~/.financial-analyst/ override)
datas = [
    (str(PROJECT_ROOT / "config"), "config"),
    (str(PROJECT_ROOT / "memories"), "memories"),
    (str(SRC / "_resources"), "financial_analyst/_resources") if (SRC / "_resources").exists() else None,
]
datas = [d for d in datas if d is not None]

# ─────────────────── 排除的依赖 (节省 size) ───────────────────
# 这些是 dev/test only, 不需要打进 .exe
excludes = [
    "pytest", "pytest_asyncio", "pytest_mock",
    "ruff", "black", "mypy",
    "ipython", "jupyter", "notebook",
    "matplotlib", "plotly",   # 我们 CLI 不画图; UI 走前端
    "tensorflow", "torch",     # 量化模型走 LightGBM, 没用到 deep learning
]

# ─────────────────── Analysis ───────────────────
ENTRY = PROJECT_ROOT / "packaging" / "pyinstaller" / "fa_entry.py"

a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC.parent)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ─────────────────── Single-file EXE ───────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="financial-analyst",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 压缩偶尔被杀软误报, 先关
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # CLI 工具, 保留 console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,           # 之后可指向 packaging/src-tauri/icons/icon.ico
)
