"""测试全局卫生:模块收集前先建立进程级输出根，再由 autouse 对可独立
逐测试的记忆与 Events 路径细分到 ``tmp_path``，杜绝测试读写生产 ``var/``。

进程级根专门服务模块级共享 ``guanlan_v2.server.app``：它在测试收集期即构造，
不能绑定函数级 ``tmp_path``，否则后续测试会共享一个已经失效/换根的 app。
各测试自己的显式 monkeypatch.setattr(ct, "_MEMORY_PATH", ...) 在本 fixture 之后生效,优先级更高。
"""
# ── 钉 in-repo engine fork 到 sys.path 最前 + 预热缓存 ──
# 本机 venv 有 financial_analyst 的 editable 安装指向旧外部 src(G:/financial-analyst/src),
# 与仓内 engine fork(guanlan-v2/engine/financial_analyst,含 buddy / data.news_pulse /
# agent.tier1.news_sentiment 等外部 src 已缺失的模块)同名打架。全量套件里前序测试若先以外部版
# import 了 financial_analyst,之后 news/buddy 系测试拿到缓存的外部版 → 收集期 ImportError
# (单独跑各文件却正常 = 纯导入顺序串扰)。pytest 先加载本 conftest 再收集 tests/ → 在此模块顶层
# prepend engine 并预 import,锁定整套件解析到 engine fork(与生产 server 启动 prepend engine 同口径)。
# 这是 test-only 卫生；生产 server 只识别显式 GUANLAN_TESTING + 安全绝对根，
# 正常部署不设置这两个变量时不会改变任何生产路径。
import os as _os
import sys as _sys
import tempfile as _tempfile
from pathlib import Path as _Path

_REPO_DIR = _Path(__file__).resolve().parent.parent
_ENGINE_DIR = str(_REPO_DIR / "engine")
if _Path(_ENGINE_DIR).is_dir():
    if _ENGINE_DIR in _sys.path:
        _sys.path.remove(_ENGINE_DIR)
    _sys.path.insert(0, _ENGINE_DIR)

# Must run before test-module collection imports ``guanlan_v2.server`` or any
# store with a module-level default path.  Keep the TemporaryDirectory object
# alive for the interpreter lifetime: the shared app also lives that long.
_TEST_RUNTIME_TEMP = _tempfile.TemporaryDirectory(prefix="guanlan-pytest-runtime-")
_TEST_RUNTIME_ROOT = _Path(_TEST_RUNTIME_TEMP.name).resolve()
_os.environ["GUANLAN_TESTING"] = "1"
_os.environ["GUANLAN_TEST_ROOT"] = str(_TEST_RUNTIME_ROOT)
from guanlan_v2.runtime_paths import configure_test_output_environment as _configure_test_outputs  # noqa: E402,E501

_configure_test_outputs(repo_root=_REPO_DIR)

if _Path(_ENGINE_DIR).is_dir() and "financial_analyst" not in _sys.modules:
    import financial_analyst  # noqa: F401,E402  # 预热为 engine 版,杜绝后续外部版抢占缓存

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _disable_probe_worker(monkeypatch):
    """常驻 probe worker 默认在测试里禁用:桩 lc._run_probe/subprocess.run 的既有测试
    绝不能落到真 worker 子进程。worker 自身的测试显式 setenv GUANLAN_PROBE_WORKER=1
    并把 _STOCKS_WORKER 指到假 worker 脚本。"""
    monkeypatch.setenv("GUANLAN_PROBE_WORKER", "0")


@pytest.fixture(autouse=True)
def _disable_server_background(monkeypatch):
    """Entering a TestClient lifespan must never start production writers.

    Individual scheduler/worker tests exercise their owning functions
    directly.  App integration tests need routes and lifespans, not the real
    watchdog, pre-warm network calls, market refresh, regen or Events loop.
    """
    monkeypatch.setenv("GUANLAN_SERVER_BACKGROUND", "0")


@pytest.fixture(autouse=True)
def _disable_live_credentials(monkeypatch):
    """Sealed tests must never inherit workstation credentials or call vendors.

    A test that exercises credential parsing can set its own synthetic value
    after this fixture.  Production-data checks belong behind ``realdata``.
    """
    for name in (
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "KIMI_API_KEY", "DASHSCOPE_API_KEY", "TUSHARE_TOKEN",
        "FA_TUSHARE_TOKEN", "GUANLAN_MCP_BEARER_TOKEN",
        "GUANLAN_TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_events_store(tmp_path, monkeypatch):
    """Events 默认关写闸并绑定逐测试临时库，隔绝导入顺序与外部环境污染。

    需要验证默认 ``db_path`` 的少数测试必须在测试体内显式 ``delenv``，且只检查
    路径、绝不连接；需要验证入库的测试可显式把 gate 改回 1，但仍继承临时 DB。
    """
    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "0")
    monkeypatch.setenv(
        "GUANLAN_EVENTS_DB",
        str(tmp_path / "_isolated_events" / "events.db"),
    )


@pytest.fixture(autouse=True)
def _isolate_console_memory(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "_isolated_console" / "memory.md")


@pytest.fixture(autouse=True)
def _isolate_screen_archives(tmp_path, monkeypatch):
    """测试永不写真 var/ 选股档案(picks/rescore runs;2026-07-10 缺陷B同类事故护栏)。
    各测试自己的显式 monkeypatch.setattr(pk/rs, ...) 在本 fixture 之后生效,优先级更高。"""
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "_isolated_screen" / "picks.jsonl")
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "_isolated_screen" / "runs.jsonl")


@pytest.fixture(autouse=True)
def _isolate_sentiment_store(tmp_path, monkeypatch):
    """统一情绪 store 隔离:任何测试对 judgments/market 的写入落 tmp,绝不碰生产
    var/sentiment(2026-07-12 事故:test_console_tools 桩数据经 _sentiment_write_through
    写真档案,大盘判读 as_of 被冻在桩值 2026-06-13)。"""
    from guanlan_v2.datafeed import sentiment as sm
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")


@pytest.fixture(autouse=True)
def _clear_bg_inflight():
    """_bg_inflight 是模块级全局:断言失败/异常路径可能留残键,跨测试串扰 dedup/搭车判定 → 每测后清空。"""
    yield
    import guanlan_v2.console.api as _capi
    _capi._bg_inflight.clear()


def pytest_configure(config):
    """注册自定义标记(未注册标记会产生 PytestUnknownMarkWarning,测试输出须洁净)。"""
    config.addinivalue_line(
        "markers",
        "realdata: 读取真实生产产物的非密封测试（默认门禁排除；显式 -m realdata 运行）",
    )
    config.addinivalue_line(
        "markers",
        "slow: 运行时间较长但仍可使用密封 fixture 的测试",
    )
