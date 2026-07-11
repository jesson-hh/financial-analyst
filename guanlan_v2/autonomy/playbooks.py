# -*- coding: utf-8 -*-
"""playbook 注册表:名字 -> callable(JobCtx)->{ok,error?,report?}。v1 只有 review_officer。"""
from typing import Any, Callable, Dict

PLAYBOOKS: Dict[str, Callable[..., Dict[str, Any]]] = {}

# 模块底注册(runtime._playbooks() 是延迟 import,此处直接注册即可,无循环导入风险)。
from guanlan_v2.autonomy.review_officer import run_review_officer  # noqa: E402

PLAYBOOKS["review_officer"] = run_review_officer
