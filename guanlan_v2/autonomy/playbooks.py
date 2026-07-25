# -*- coding: utf-8 -*-
"""playbook 注册表:名字 -> callable(JobCtx)->{ok,error?,report?}。
review_officer(盘后复盘官)+ Phase 9 Task 6 的 shadow_replay_wakeup(成熟唤醒)/
lane0_bootstrap(每日 Lane 0 自举)。

红线(Task 6 两 playbook 共同遵守):
- 写域仅限编排 stores + job 事件:成熟唤醒只经 luozi.mature_shadow_replay 动编排 stores;
  Lane 0 自举经注入的 service 端口(生产:Phase 5 自举 + Phase 7 lease + 准入;测试:录制假件);
  绝不写 picks/信号/seats/记忆(复盘官同域红线)。
- 无上下文/无 active lease ⇒ **诚实跳过**(report 标 skipped),绝不当错误、绝不编造。
- 依赖全收敛为模块级可打桩 provider(`_shadow_wakeup_context` / `_lane0_bootstrap_service`),
  生产未接线 ⇒ 默认返回 None ⇒ 诚实跳过;测试注入假件。
"""
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

PLAYBOOKS: Dict[str, Callable[..., Dict[str, Any]]] = {}

# 模块底注册(runtime._playbooks() 是延迟 import,此处直接注册即可,无循环导入风险)。
from guanlan_v2.autonomy.review_officer import run_review_officer  # noqa: E402

PLAYBOOKS["review_officer"] = run_review_officer


# =========================================================================== #
# Phase 9 · Task 6 — shadow-replay 成熟唤醒 playbook                              #
# =========================================================================== #
def _shadow_wakeup_context() -> Optional[Tuple[Any, Any, Any]]:
    """生产接线钩子(测试打桩靶点):返回 (ReplayStateStore, ReplayRuntimeBindings, now) 或 None。

    默认返回 None——生产尚未接线(Task 10/12 负责用进程级 durable stores + 绑定装配),故本
    playbook 诚实跳过;测试 monkeypatch 本函数注入假件。"""
    return None


def _run_shadow_replay_wakeup(ctx: Any) -> Dict[str, Any]:
    """遍历所有 WAITING_FOR_MATURITY 的 head,逐 key 调 mature_shadow_replay,汇报逐 key 结局。

    无上下文 ⇒ 诚实跳过。写域仅编排 stores(经 mature_shadow_replay)+ job 事件——绝不写
    picks/信号/seats/记忆。单 key 失败诚实记 error 不中断其余(诚实显形)。"""
    provider = _shadow_wakeup_context()
    if provider is None:
        return {"ok": True, "report": {"skipped": "no shadow-replay context wired"}}
    store, bindings, now = provider
    from guanlan_v2.orchestration.adapters.luozi import mature_shadow_replay

    outcomes = []
    for state in store.waiting_states():
        key = state.wakeup_key
        try:
            receipt = mature_shadow_replay(key, bindings=bindings, now=now)
            outcomes.append({
                "experiment_id": state.experiment_id, "outcome": receipt.outcome,
                "matured_points": receipt.matured_points,
            })
        except Exception as exc:  # noqa: BLE001 — 单 key 失败诚实记录不中断其余
            outcomes.append({
                "experiment_id": state.experiment_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {"ok": True, "report": {"wakeups": outcomes, "count": len(outcomes)}}


PLAYBOOKS["shadow_replay_wakeup"] = _run_shadow_replay_wakeup


# =========================================================================== #
# Phase 9 · Task 6 — 每日 Lane 0 自举 playbook(2026-07-18 集成修正)             #
# =========================================================================== #
class Lane0BootstrapService(Protocol):
    """Lane 0 每日自举的注入端口(生产:Phase 5 自举 + Phase 7 lease + 准入;测试:录制假件)。

    `try_lease` 走 Phase 7 `register_and_try_lease`,返回 (是否取得 active lease, lease_id)。
    `admit_and_run` 仅在取得 lease 后调:以 actor `lease:<id>` 走真 `PlanApproval` →
    `admit_after_approval` → `run_plan`(BOOTSTRAP profile,durable stores),提交 ContextSnapshot
    并追加当日 case seeds,返回其产物摘要。红线:无 active lease 绝不 admit 任何东西。
    """

    def try_lease(self, *, now: Any) -> Tuple[bool, Optional[str]]: ...

    def admit_and_run(self, *, actor: str, now: Any) -> Dict[str, Any]: ...


def orchestrate_lane0_bootstrap(*, service: Lane0BootstrapService, now: Any) -> Dict[str, Any]:
    """Lane 0 每日自举编排:lease 门 → 准入 → 运行(可测的纯编排形)。

    先 `try_lease`;无 active lease ⇒ 诚实跳过(`skipped: no active lease`,零准入,不 admit
    任何东西);有 lease ⇒ 以 actor `lease:<id>` 调 `admit_and_run`(真 PlanApproval →
    admit_after_approval → run_plan → 提交快照 + 追加 case seeds)并回填产物摘要。"""
    leased, lease_id = service.try_lease(now=now)
    if not leased or not lease_id:
        return {"admitted": False, "skipped": "no active lease"}
    actor = f"lease:{lease_id}"
    produced = service.admit_and_run(actor=actor, now=now)
    out: Dict[str, Any] = {"admitted": True, "actor": actor}
    out.update(produced or {})
    return out


def _lane0_bootstrap_service() -> Optional[Lane0BootstrapService]:
    """生产接线钩子(测试打桩靶点):返回 Lane0BootstrapService 或 None(未接线 ⇒ 诚实跳过)。"""
    return None


def _run_lane0_bootstrap(ctx: Any) -> Dict[str, Any]:
    """每日 Lane 0 自举 playbook:无 service ⇒ 诚实跳过;有 ⇒ 走 lease 门编排。

    写域仅限注入 service 端口内的编排 stores + job 事件——绝不写 picks/信号/seats/记忆。"""
    service = _lane0_bootstrap_service()
    if service is None:
        return {"ok": True, "report": {"skipped": "no lane0 bootstrap service wired"}}
    import datetime as _dt
    report = orchestrate_lane0_bootstrap(service=service, now=_dt.datetime.now(_dt.timezone.utc))
    return {"ok": True, "report": report}


PLAYBOOKS["lane0_bootstrap"] = _run_lane0_bootstrap
