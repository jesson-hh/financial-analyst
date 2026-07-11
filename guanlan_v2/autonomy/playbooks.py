# -*- coding: utf-8 -*-
"""playbook 注册表:名字 -> callable(JobCtx)->{ok,error?,report?}。v1 只有 review_officer。"""
from typing import Any, Callable, Dict

PLAYBOOKS: Dict[str, Callable[..., Dict[str, Any]]] = {}
