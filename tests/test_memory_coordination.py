# -*- coding: utf-8 -*-
"""Task 9 — owner-neutral canonical-root lease tests (engine/memory_coordination).

The one concrete process-shared lock: real OS file lock (an in-process mutex is
never sufficient), canonical-root validation, ordered acquisition, reserved
``_coordination`` namespace, and REAL two-process exclusion via a subprocess.

Run: ``pytest tests/test_memory_coordination.py -v``
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

import memory_coordination as mc


@pytest.fixture()
def root(tmp_path) -> Path:
    d = tmp_path / "memories"
    d.mkdir()
    return d


def test_module_imports_no_domain_model():
    """Owner-neutral: it imports neither guanlan_v2 nor financial_analyst."""
    import inspect
    import re as _re

    imports = [ln for ln in inspect.getsource(mc).splitlines()
               if _re.match(r"\s*(from|import)\s", ln)]
    joined = "\n".join(imports)
    assert "guanlan_v2" not in joined
    assert "financial_analyst" not in joined


def test_two_independent_coordinators_exclude_each_other_in_process(root):
    a = mc.ProcessSharedRootLease(root, owner="a", operation="op-a")
    b = mc.ProcessSharedRootLease(root, owner="b", operation="op-b")
    a.acquire()
    try:
        with pytest.raises(mc.LeaseHeldError):
            b.acquire()
    finally:
        a.release()
    # released -> the other coordinator can now enter.
    with b:
        assert b.held


def test_two_process_exclusion_via_subprocess(root):
    """A second OS process holding the lease excludes this one (and vice versa)."""
    flag = root.parent / "held.flag"
    stop = root.parent / "stop.flag"
    child = subprocess.Popen([
        sys.executable, "-c",
        (
            "import sys, time, pathlib\n"
            f"sys.path.insert(0, {str(Path(mc.__file__).parent)!r})\n"
            "import memory_coordination as mc\n"
            f"lease = mc.ProcessSharedRootLease({str(root)!r}, owner='child', operation='hold')\n"
            "lease.acquire()\n"
            f"pathlib.Path({str(flag)!r}).write_text('held')\n"
            f"while not pathlib.Path({str(stop)!r}).exists(): time.sleep(0.05)\n"
            "lease.release()\n"
        ),
    ])
    try:
        for _ in range(100):
            if flag.exists():
                break
            time.sleep(0.05)
        assert flag.exists(), "child never acquired the lease"
        mine = mc.ProcessSharedRootLease(root, owner="parent", operation="try")
        with pytest.raises(mc.LeaseHeldError):
            mine.acquire()
    finally:
        stop.write_text("stop")
        child.wait(timeout=10)
    # after the child released, this process can acquire.
    with mc.ProcessSharedRootLease(root, owner="parent", operation="after") as lease:
        assert lease.held


def test_canonical_root_is_validated_first(tmp_path):
    with pytest.raises(mc.RootLeaseError):
        mc.ProcessSharedRootLease(tmp_path / "does-not-exist", owner="x", operation="y")
    with pytest.raises(mc.RootLeaseError):
        mc.ProcessSharedRootLease("", owner="x", operation="y")
    with pytest.raises(mc.RootLeaseError):
        mc.ProcessSharedRootLease(tmp_path, owner="  ", operation="y")


def test_ordered_acquisition_rejects_reverse_and_duplicate_order(tmp_path):
    for name in ("a-root", "b-root"):
        (tmp_path / name).mkdir()
    factory = mc.RootLeaseFactory()
    with pytest.raises(mc.RootLeaseError, match="order"):
        factory.acquire_ordered(
            (("b", tmp_path / "b-root"), ("a", tmp_path / "a-root")),
            owner="x", operation="y")
    with pytest.raises(mc.RootLeaseError, match="duplicate"):
        factory.acquire_ordered(
            (("a", tmp_path / "a-root"), ("a", tmp_path / "b-root")),
            owner="x", operation="y")
    leases = factory.acquire_ordered(
        (("a", tmp_path / "a-root"), ("b", tmp_path / "b-root")),
        owner="x", operation="y")
    assert all(lease.held for lease in leases)
    for lease in reversed(leases):
        lease.release()


def test_ordered_acquisition_releases_everything_on_failure(tmp_path):
    for name in ("a-root", "b-root"):
        (tmp_path / name).mkdir()
    factory = mc.RootLeaseFactory()
    blocker = factory.lease_for(tmp_path / "b-root", owner="blocker", operation="hold")
    blocker.acquire()
    try:
        with pytest.raises(mc.LeaseHeldError):
            factory.acquire_ordered(
                (("a", tmp_path / "a-root"), ("b", tmp_path / "b-root")),
                owner="x", operation="y")
        # the partially acquired 'a' lease was rolled back.
        with factory.lease_for(tmp_path / "a-root", owner="probe", operation="p") as p:
            assert p.held
    finally:
        blocker.release()


def test_coordination_files_live_in_the_reserved_namespace(root):
    with mc.ProcessSharedRootLease(root, owner="a", operation="op"):
        pass
    coord = root / mc.COORDINATION_DIRNAME
    assert coord.is_dir()
    assert (coord / "root.lock").exists()
    # nothing outside the reserved dir was created.
    assert [p.name for p in root.iterdir()] == [mc.COORDINATION_DIRNAME]


def test_double_acquire_and_double_release_fail_loud(root):
    lease = mc.ProcessSharedRootLease(root, owner="a", operation="op")
    lease.acquire()
    with pytest.raises(mc.RootLeaseError):
        lease.acquire()
    lease.release()
    with pytest.raises(mc.RootLeaseError):
        lease.release()
