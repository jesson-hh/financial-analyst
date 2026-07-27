# -*- coding: utf-8 -*-
"""掉线状态机。

硬约束:**一次掉线只拉一次看门狗 —— 按壳进程计,不按窗口计。** 看门狗自己有
30s 周期;壳再叠一层重试就会变成派生风暴,与 2026-07-20 那个「重启 → 撞端口
→ 死 → 重启」的循环是同一个形状。
"""
from __future__ import annotations

from guanlan_v2.desktop.supervisor import ConnectionMonitor


def test_healthy_stays_quiet():
    m = ConnectionMonitor()
    d = m.observe(True)
    assert d.connected and not d.show_overlay and not d.hide_overlay and not d.spawn_watchdog


def test_failures_below_threshold_do_not_alarm():
    m = ConnectionMonitor(failure_threshold=3)
    assert not m.observe(False).show_overlay
    d = m.observe(False)
    assert not d.show_overlay and d.consecutive_failures == 2


def test_third_failure_shows_overlay_and_spawns_once():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False)
    d = m.observe(False)
    assert d.show_overlay and d.spawn_watchdog and not d.connected


def test_further_failures_never_spawn_again():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False); m.observe(False)
    for _ in range(10):
        d = m.observe(False)
        assert not d.spawn_watchdog, "派生风暴 —— 正是 07-20 那个循环"
        assert not d.show_overlay, "浮层只在进入降级时推一次"


def test_recovery_hides_overlay():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False); m.observe(False)
    d = m.observe(True)
    assert d.connected and d.hide_overlay and d.consecutive_failures == 0


def test_a_second_outage_may_spawn_again():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False); m.observe(False)
    m.observe(True)                       # 恢复 → 标记复位
    m.observe(False); m.observe(False)
    assert m.observe(False).spawn_watchdog is True


def test_blip_below_threshold_leaves_no_trace():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False)
    d = m.observe(True)
    assert not d.hide_overlay, "从没显形过的浮层不该被撤"
