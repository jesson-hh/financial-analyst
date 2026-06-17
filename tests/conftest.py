"""测试全局卫生:autouse 把帷幄全局记忆路径指到 tmp_path,
杜绝任何测试(尤其 API 集成测试)读写开发机真实 var/console/memory.md。
各测试自己的显式 monkeypatch.setattr(ct, "_MEMORY_PATH", ...) 在本 fixture 之后生效,优先级更高。
"""
# ── 钉 in-repo engine fork 到 sys.path 最前 + 预热缓存 ──
# 本机 venv 有 financial_analyst 的 editable 安装指向旧外部 src(G:/financial-analyst/src),
# 与仓内 engine fork(guanlan-v2/engine/financial_analyst,含 buddy / data.news_pulse /
# agent.tier1.news_sentiment 等外部 src 已缺失的模块)同名打架。全量套件里前序测试若先以外部版
# import 了 financial_analyst,之后 news/buddy 系测试拿到缓存的外部版 → 收集期 ImportError
# (单独跑各文件却正常 = 纯导入顺序串扰)。pytest 先加载本 conftest 再收集 tests/ → 在此模块顶层
# prepend engine 并预 import,锁定整套件解析到 engine fork(与生产 server 启动 prepend engine 同口径)。
# 注:test-only 卫生,不碰生产 guanlan_v2/server.py。
import sys as _sys
from pathlib import Path as _Path

_ENGINE_DIR = str(_Path(__file__).resolve().parent.parent / "engine")
if _Path(_ENGINE_DIR).is_dir():
    if _ENGINE_DIR in _sys.path:
        _sys.path.remove(_ENGINE_DIR)
    _sys.path.insert(0, _ENGINE_DIR)
    if "financial_analyst" not in _sys.modules:
        import financial_analyst  # noqa: F401,E402  # 预热为 engine 版,杜绝后续外部版抢占缓存

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_console_memory(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "_isolated_console" / "memory.md")


@pytest.fixture(autouse=True)
def _clear_bg_inflight():
    """_bg_inflight 是模块级全局:断言失败/异常路径可能留残键,跨测试串扰 dedup/搭车判定 → 每测后清空。"""
    yield
    import guanlan_v2.console.api as _capi
    _capi._bg_inflight.clear()
