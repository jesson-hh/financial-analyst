"""pytdx 主站连接池 + 自动 failover.

pytdx 主站 (公开通达信服务器) 是替代 Tushare 的免费数据源, 无 token, 不限速.
通用 ``pytdx.config.hosts.hq_hosts`` 列表 104 个主站中约半数已下线, 实测
10/20 通率 (见 docs/research/2026-05-23-direct-data-stability.md), 因此我们
**静态固化已验证可用的主站**, 不再每次扫完整列表.

设计:
- 单连接 + 自动重连. 大部分使用场景是串行 (fa data update), 不需要并行池.
- 高频压测 100 次单连接 0 失败, QPS 39 — 单连接够用.
- 调用 ``call("get_security_bars", ...)`` 而不是直接拿 api 实例, 失败时
  自动换 host 重连 + 重试.

Example::

    client = PytdxClient()
    bars = client.call("get_security_bars",
                        TDXParams.KLINE_TYPE_DAILY, 1, "600519", 0, 30)
    client.close()
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, List, Tuple

from pytdx.hq import TdxHq_API

log = logging.getLogger(__name__)


# 实测可用主站 (2026-05-23 probe_direct_data.py 验证). 顺序按测得的握手延迟排序.
# 后续要加新 host 跑 scripts/probe_direct_data.py 看哪些 ✓.
KNOWN_GOOD_HOSTS: List[Tuple[str, int]] = [
    ("180.153.18.172", 80),     # 80ms
    ("115.238.56.198", 7709),   # 79ms
    ("218.75.126.9", 7709),     # 82ms
    ("60.191.117.167", 7709),   # 85ms
    ("180.153.18.170", 7709),   # 86ms
    ("115.238.90.165", 7709),   # 90ms
    ("218.6.170.47", 7709),     # 99ms
    ("60.12.136.250", 7709),    # 99ms
    ("123.125.108.14", 7709),   # 102ms
    ("202.108.253.139", 80),    # 108ms
]


class PytdxClient:
    """Single persistent connection with multi-host failover.

    使用方式:
      client = PytdxClient()
      bars = client.call("get_security_bars", category, mkt, code, start, count)
      client.close()

    或:
      with PytdxClient() as client:
          ...

    线程安全: 内部 lock 保护 connect/call. 单连接吞吐 ~39 QPS 已经够全市场用
    (5500 只 × 27ms ≈ 2.5 min). 若真需并发, 实例化多个 PytdxClient.
    """

    def __init__(self, max_retries: int = 3, connect_timeout: float = 3.0) -> None:
        self._api: TdxHq_API | None = None
        self._current_host: Tuple[str, int] | None = None
        self._lock = threading.Lock()
        self._max_retries = max_retries
        self._connect_timeout = connect_timeout

    # ─────────────────────────────── 内部 ───────────────────────────────

    def _connect(self) -> None:
        """从 KNOWN_GOOD_HOSTS 顺序试连, 第一个成功的就用."""
        last_err = None
        for host, port in KNOWN_GOOD_HOSTS:
            api = TdxHq_API(heartbeat=False, auto_retry=True)
            try:
                if api.connect(host, port, time_out=self._connect_timeout):
                    self._api = api
                    self._current_host = (host, port)
                    log.debug("pytdx connected: %s:%s", host, port)
                    return
            except Exception as e:
                last_err = e
                try: api.disconnect()
                except Exception: pass
                continue
        raise RuntimeError(
            f"All {len(KNOWN_GOOD_HOSTS)} known-good pytdx hosts failed. "
            f"Last error: {last_err}. 网络问题? 跑 scripts/probe_direct_data.py 重新探."
        )

    def _disconnect(self) -> None:
        if self._api is not None:
            try: self._api.disconnect()
            except Exception: pass
        self._api = None
        self._current_host = None

    # ─────────────────────────────── 公开 ───────────────────────────────

    @property
    def host(self) -> str:
        """当前连接的 host:port 字符串 (debugging 用)."""
        if self._current_host is None: return "(disconnected)"
        return f"{self._current_host[0]}:{self._current_host[1]}"

    def call(self, method_name: str, *args, **kwargs) -> Any:
        """调 pytdx API method, 失败自动换 host 重试 max_retries 次.

        ``method_name`` 是 TdxHq_API 上的方法名, 例如 ``"get_security_bars"``
        / ``"get_xdxr_info"`` / ``"get_company_info_category"`` etc.
        """
        with self._lock:
            last_err = None
            for attempt in range(self._max_retries):
                if self._api is None:
                    self._connect()
                try:
                    method = getattr(self._api, method_name)
                    result = method(*args, **kwargs)
                    return result
                except (OSError, ConnectionError, TimeoutError) as e:
                    log.warning("pytdx call %s failed on %s (attempt %d/%d): %s",
                                method_name, self.host, attempt + 1,
                                self._max_retries, e)
                    last_err = e
                    self._disconnect()   # 标死 + 下次重连换 host
                    if attempt < self._max_retries - 1:
                        time.sleep(0.5 * (attempt + 1))   # 指数退避
                except Exception as e:
                    # 业务错误 (例如非法代码), 直接抛
                    raise
            raise RuntimeError(
                f"pytdx {method_name}{args} failed after {self._max_retries} retries: "
                f"{last_err}"
            )

    def close(self) -> None:
        with self._lock:
            self._disconnect()

    def __enter__(self) -> "PytdxClient":
        self._connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ────────────────────────── 工具 ──────────────────────────


def qlib_code_to_pytdx(code: str) -> Tuple[int, str]:
    """SH600519 → (1, '600519'); SZ300750 → (0, '300750'); BJ830779 → (2, '830779').

    pytdx 用 market code 区分: 1=沪, 0=深, 2=北.
    """
    code = code.upper().strip()
    if code.startswith("SH"): return 1, code[2:]
    if code.startswith("SZ"): return 0, code[2:]
    if code.startswith("BJ"): return 2, code[2:]
    # 兼容裸 6 位代码
    if code.isdigit() and len(code) == 6:
        if code[0] == "6": return 1, code        # 沪
        if code[0] in "03": return 0, code       # 深
        if code[0] in "84": return 2, code       # 北
    raise ValueError(f"Unrecognized stock code: {code!r}")


def pytdx_to_qlib_code(market: int, code: str) -> str:
    """反向. (1, '600519') → 'SH600519'."""
    prefix = {1: "SH", 0: "SZ", 2: "BJ"}.get(market)
    if prefix is None:
        raise ValueError(f"Unknown pytdx market: {market}")
    return prefix + code
