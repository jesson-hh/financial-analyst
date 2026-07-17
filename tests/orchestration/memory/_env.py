# -*- coding: utf-8 -*-
"""Shared Stage-B memory test world (private helper module — not collected).

Builds, over pytest tmp roots only: two real store layouts (agent ``memories/``
+ console ``var/console``), the sealed FULL registry, RuntimeStores carrying the
exact seven-name memory namespace union, the real lease factory, a completed
legacy cutover and a wired :class:`MemoryContextPreparationService`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import memory_coordination  # engine-side neutral lease module (conftest path)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.memory.adapters import (
    AgentMemoryAdapter,
    ConsoleMemoryAdapter,
)
from guanlan_v2.orchestration.memory.catalog import resolved_memory_policy
from guanlan_v2.orchestration.memory.models import (
    PHASE3_MEMORY_STATE_CELL_NAMESPACES,
)
from guanlan_v2.orchestration.memory.schema_registry import build_phase3_full_registry
from guanlan_v2.orchestration.memory.store import (
    MemoryContextPreparationService,
    MemoryCutoverCoordinator,
    MemoryCutoverRepository,
    MemorySourceStateHeadRepository,
)
from guanlan_v2.orchestration.worker import PROMPT_CELL_NAMESPACE

UTC = timezone.utc
T0 = datetime(2026, 7, 16, 1, 30, tzinfo=UTC)


class SteppingClock:
    """A deterministic advancing clock (each ``now()`` +1s; settable)."""

    def __init__(self, start: datetime = T0) -> None:
        self.current = start

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


def write_agent_root(root: Path) -> None:
    (root / "_shared").mkdir(parents=True)
    (root / "_shared" / "market.md").write_text(
        "A股涨跌停制度: main board 10% limit.", encoding="utf-8")
    own = root / "dec.pm"
    own.mkdir()
    (own / "lesson.md").write_text("momentum lesson: respect the stop.", encoding="utf-8")
    (own / "crit.md").write_text("critical playbook: size before entry.", encoding="utf-8")
    (own / "always_include.txt").write_text("crit.md\n", encoding="utf-8")
    other = root / "mkt.macro"
    other.mkdir()
    (other / "regime.md").write_text("macro regime notes: liquidity first.", encoding="utf-8")
    staging = root / "_proposed" / "dec.pm"
    staging.mkdir(parents=True)
    (staging / "pending.md").write_text("NOT accepted memory", encoding="utf-8")
    (root / "_buddy").mkdir()
    (root / "_buddy" / "b.md").write_text("buddy staging", encoding="utf-8")


def write_console_root(root: Path, *, session_id: str = "cs.demo") -> None:
    root.mkdir(parents=True)
    (root / "memory.md").write_text(
        "- [2026-07-10] (pref) always answer in Chinese\n"
        "- [2026-07-11] checked momentum factor yesterday\n"
        "- [2026-07-12] liquidity looked thin on Friday\n",
        encoding="utf-8")
    (root / "memory.archive.md").write_text(
        "## 归档于 2026-07-01T00:00:00\n"
        "- [2026-06-20] old archived observation on momentum\n",
        encoding="utf-8")
    notes = root / "sessions" / session_id
    notes.mkdir(parents=True)
    (notes / "notes.md").write_text(
        "- [2026-07-15] session note: user prefers brief tables\n", encoding="utf-8")
    other = root / "sessions" / "cs.other"
    other.mkdir(parents=True)
    (other / "notes.md").write_text(
        "- [2026-07-15] FOREIGN session secret note\n", encoding="utf-8")


@dataclass
class MemoryWorld:
    agent_root: Path
    console_root: Path
    stores: RuntimeStores
    registry_digest: str
    clock: SteppingClock
    policy: object
    agent_adapter: AgentMemoryAdapter
    console_factory: object
    cutover: object
    service: MemoryContextPreparationService
    lease_factory: object
    extras: dict = field(default_factory=dict)


def build_memory_world(
    tmp_path: Path,
    *,
    session_id: str = "cs.demo",
    extra_namespaces: tuple[str, ...] = (PROMPT_CELL_NAMESPACE,),
    required_registry: str | None = None,
    required_catalog: str = "c" * 64,
    skip_service: bool = False,
    stores: RuntimeStores | None = None,
    resolver: SchemaRegistryResolver | None = None,
    clock: SteppingClock | None = None,
    post_cutover_time: datetime | None = datetime(2026, 7, 17, 9, 0, tzinfo=UTC),
) -> MemoryWorld:
    agent_root = tmp_path / "memories"
    console_root = tmp_path / "var" / "console"
    write_agent_root(agent_root)
    write_console_root(console_root, session_id=session_id)

    from guanlan_v2.orchestration.data.schema_registry import (
        PHASE3_DATA_REGISTRY_DIGEST,
    )

    full = build_phase3_full_registry(PHASE3_DATA_REGISTRY_DIGEST)
    if resolver is None:
        resolver = SchemaRegistryResolver()
    registry_digest = resolver.register(full)
    clock = clock or SteppingClock()
    if stores is None:
        stores = RuntimeStores(
            resolver=resolver, clock=clock,
            allowed_cell_namespaces=tuple(PHASE3_MEMORY_STATE_CELL_NAMESPACES)
            + tuple(extra_namespaces))

    policy = resolved_memory_policy()
    agent_adapter = AgentMemoryAdapter(agent_root)

    def console_factory(sid):
        return ConsoleMemoryAdapter(
            console_root, session_id=sid,
            archive_tail_chars=policy.policy.archive_tail_chars)

    lease_factory = memory_coordination.RootLeaseFactory()
    repo = MemoryCutoverRepository(stores, registry_digest=registry_digest)
    source_repo = MemorySourceStateHeadRepository(stores, registry_digest=registry_digest)
    coordinator = MemoryCutoverCoordinator(
        repository=repo, source_repository=source_repo, stores=stores,
        registry_digest=registry_digest, lease_factory=lease_factory,
        global_lease_root=tmp_path,
        logical_roots=(("agent", agent_root), ("console", console_root)))

    def scan():
        return tuple(sorted(
            agent_adapter.scan_units() + console_factory(session_id).scan_units(),
            key=lambda u: (u.source_id, u.locator)))

    cutover = coordinator.perform_cutover(
        admin_operation_key="adopt-2026-07-16",
        admin_actor="ops-admin",
        scan=scan,
        cutover_policy_digest=policy.policy.policy_digest,
        clock_now=lambda: clock.now())
    # a run's frozen as_of precedes its ONLINE capture time: jump the
    # deterministic clock past the cutoff, mirroring reality.
    if post_cutover_time is not None:
        clock.current = post_cutover_time

    service = None
    if not skip_service:
        service = MemoryContextPreparationService(
            stores=stores, registry_digest=registry_digest, policy=policy,
            agent_adapter=agent_adapter, console_adapter_factory=console_factory,
            clock=clock, cutover=cutover,
            required_schema_registry_digest=required_registry or full.registry_digest,
            required_catalog_digest=required_catalog)

    return MemoryWorld(
        agent_root=agent_root, console_root=console_root, stores=stores,
        registry_digest=registry_digest, clock=clock, policy=policy,
        agent_adapter=agent_adapter, console_factory=console_factory,
        cutover=cutover, service=service, lease_factory=lease_factory,
        extras={"coordinator": coordinator, "scan": scan,
                "source_repo": source_repo, "cutover_repo": repo,
                "session_id": session_id})


def make_data_context(*, as_of: datetime, built_at: datetime | None = None):
    from guanlan_v2.orchestration.context import ClockSpec, DataContext
    from guanlan_v2.orchestration.enums import DataBackend, DataMode

    clock = ClockSpec(as_of=as_of, timezone="Asia/Shanghai", calendar_id="cn_a_share")
    return DataContext(
        as_of=as_of, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share",
        resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest="a" * 64, source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64, data_snapshot_id="snap-1",
        data_snapshot_content_digest="d" * 64, built_at=built_at or as_of,
    )
