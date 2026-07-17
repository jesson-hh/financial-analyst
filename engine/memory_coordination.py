# -*- coding: utf-8 -*-
"""Owner-neutral canonical-root lease / factory (Phase 3 · Task 9).

The ONE concrete process-shared lock primitive for cutover, AgentMemory and
console memory roots. This module deliberately imports **neither** ``guanlan_v2``
nor any financial-analyst/console domain model: orchestration coordinators,
``memory_ops`` owner coordinators, console writers and legacy entry-point
factories all receive/import this same primitive rather than defining local
locks.

Semantics:

* the lease resolves and validates the canonical absolute root FIRST, derives an
  OS/process-shared exclusive lock identity from that root, and records
  owner/operation metadata for diagnosis;
* it fails closed (:class:`LeaseHeldError`) when another process — or another
  independently constructed coordinator in the SAME process — owns the lease; an
  in-process mutex alone is never sufficient (the lock is a real OS file lock,
  held per open handle);
* lease/coordination files live in the reserved non-memory namespace
  ``<root>/_coordination/`` and are excluded from adapter scans / cutover
  manifests / capture digests (adapters never scan ``_``-prefixed directories
  other than ``_shared``); they can never be interpreted as accepted memory or
  proposal evidence.

The reviewed global acquisition order is owned by the caller (coordinator):
``global-cutover lease → canonical root leases in logical-root order → optional
target-file locks``; :class:`RootLeaseFactory.acquire_ordered` enforces the
root-lease segment of that order and rejects reverse acquisition.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

__all__ = [
    "COORDINATION_DIRNAME",
    "RootLeaseError",
    "LeaseHeldError",
    "ProcessSharedRootLease",
    "RootLeaseFactory",
]

#: reserved non-memory namespace for lease/coordination files.
COORDINATION_DIRNAME = "_coordination"


class RootLeaseError(Exception):
    """A root-lease invariant was violated (bad root, double release, ordering)."""


class LeaseHeldError(RootLeaseError):
    """The canonical root lease is held by another process/coordinator."""


if sys.platform == "win32":  # pragma: no cover - platform split
    import msvcrt

    def _lock_handle(fh) -> bool:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock_handle(fh) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:  # pragma: no cover - platform split
    import fcntl

    def _lock_handle(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock_handle(fh) -> None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class ProcessSharedRootLease:
    """One exclusive OS/process-shared advisory lease over one canonical root."""

    def __init__(self, root: os.PathLike | str, *, owner: str, operation: str) -> None:
        if not str(root).strip():
            raise RootLeaseError("root must be a non-empty path")
        canonical = Path(root).resolve()
        if not canonical.is_dir():
            raise RootLeaseError(f"canonical root {canonical} is not an existing directory")
        if not owner.strip() or not operation.strip():
            raise RootLeaseError("owner and operation metadata must be non-empty")
        self.root = canonical
        self.owner = owner
        self.operation = operation
        self._dir = canonical / COORDINATION_DIRNAME
        self._lock_path = self._dir / "root.lock"
        self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> "ProcessSharedRootLease":
        if self._fh is not None:
            raise RootLeaseError(f"lease for {self.root} is already held by this object")
        self._dir.mkdir(parents=True, exist_ok=True)
        fh = open(self._lock_path, "a+b")  # noqa: SIM115 — held for the lease lifetime
        fh.seek(0)
        if not _lock_handle(fh):
            fh.close()
            raise LeaseHeldError(
                f"root lease {self._lock_path} is held by another process/coordinator "
                f"(requested by owner={self.owner!r} operation={self.operation!r})"
            )
        self._fh = fh
        # best-effort diagnostic metadata (never authorization, never memory).
        try:
            (self._dir / "root.lock.info").write_text(
                f"owner={self.owner}\noperation={self.operation}\npid={os.getpid()}\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return self

    def release(self) -> None:
        if self._fh is None:
            raise RootLeaseError(f"lease for {self.root} is not held")
        _unlock_handle(self._fh)
        self._fh.close()
        self._fh = None

    def __enter__(self) -> "ProcessSharedRootLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            self.release()


class RootLeaseFactory:
    """The one factory every owner receives — it validates roots and ordering."""

    def lease_for(self, root: os.PathLike | str, *, owner: str, operation: str) -> ProcessSharedRootLease:
        return ProcessSharedRootLease(root, owner=owner, operation=operation)

    def acquire_ordered(
        self,
        roots: Iterable[tuple[str, os.PathLike | str]],
        *,
        owner: str,
        operation: str,
    ) -> tuple[ProcessSharedRootLease, ...]:
        """Acquire root leases in canonical logical-root order, or none.

        ``roots`` is an iterable of ``(logical_root_id, path)``. Reverse/unsorted
        acquisition is rejected; on any failure every already-acquired lease is
        released before the error propagates.
        """
        items = list(roots)
        ids = [rid for rid, _ in items]
        if ids != sorted(ids):
            raise RootLeaseError(
                "root leases must be acquired in canonical logical-root order; got "
                + ", ".join(ids)
            )
        if len(set(ids)) != len(ids):
            raise RootLeaseError("duplicate logical root id in ordered acquisition")
        acquired: list[ProcessSharedRootLease] = []
        try:
            for rid, path in items:
                lease = self.lease_for(path, owner=owner, operation=f"{operation}:{rid}")
                lease.acquire()
                acquired.append(lease)
        except Exception:
            for lease in reversed(acquired):
                try:
                    lease.release()
                except RootLeaseError:
                    pass
            raise
        return tuple(acquired)
