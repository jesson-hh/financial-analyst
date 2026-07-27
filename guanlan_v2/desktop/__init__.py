# -*- coding: utf-8 -*-
"""观澜桌面壳。

真实逻辑住在 supervisor.py 与 bridge.py —— 两者都不 import pywebview,故可无头测试。
shell.py 只做 GUI 接线。本 __init__ 刻意不 import 任何子模块:导入本包不应把
pywebview 拖进来(测试与 CI 都没有 GUI)。
"""
from __future__ import annotations
