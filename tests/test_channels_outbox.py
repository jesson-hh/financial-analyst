from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import httpx

from guanlan_v2.channels.models import OutboundPart, OutboxState
from guanlan_v2.channels.outbox import OutboxWorker
from guanlan_v2.channels.pairing import derive_pairing_digest
from guanlan_v2.channels.runtime import ChannelRuntime, ChannelRuntimeError
from guanlan_v2.channels.store import ChannelConflict, ChannelStore
from guanlan_v2.channels.telegram import (
    TelegramAdapter,
    TelegramCredentialInvalid,
    TelegramProtocolError,
    TelegramRetryable,
    TelegramSendResult,
    TelegramTargetUnavailable,
    TelegramUncertain,
)
from guanlan_v2.console.store import ConsoleStore


UTC = timezone.utc
CREDENTIAL_A = "cred_" + "a" * 32
CREDENTIAL_B = "cred_" + "b" * 32


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += timedelta(seconds=seconds)


class Secrets:
    def __init__(self):
        self.values = {
            CREDENTIAL_A: "123456:abcdefghijklmnopqrstuvwxyz_ABCD",
            CREDENTIAL_B: "123457:abcdefghijklmnopqrstuvwxyz_ABCD",
        }

    def get(self, credential_id: str):
        return self.values[credential_id]


class Adapter:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    async def send_message(self, _token: str, *, chat_id: str, text: str):
        self.calls.append((chat_id, text))
        outcome = self.outcomes.pop(0) if self.outcomes else TelegramSendResult("1")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class NeverReadSecrets:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, credential_id: str):
        self.calls.append(credential_id)
        raise AssertionError("legacy-secret fence must run before credential access")


class NeverCalledAdapter:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def send_message(self, _token: str, *, chat_id: str, text: str):
        self.calls.append((chat_id, text))
        raise AssertionError("legacy-secret fence must run before Telegram")


def _seed(tmp_path, *, clock: Clock | None = None, suffix: str = "a"):
    clock = clock or Clock()
    store = ChannelStore(tmp_path / "channels.sqlite3", clock=clock)
    sid = f"cs_{suffix}"
    bot = store.create_bot(f"Bot {suffix}", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id=CREDENTIAL_A,
        telegram_bot_id=str(100 + len(store.bots())),
    )
    binding = store.bind_for_test(
        sid,
        bot,
        user_id=str(10 + len(store.live_bindings())),
        chat_id=str(10 + len(store.live_bindings())),
        sync_from_event_id=0,
    )
    return store, binding, clock


def _project(store: ChannelStore, binding, event_id: int, text: str = "消息"):
    return store.project_event_for_test(
        binding.id,
        source_event_id=event_id,
        parts=[OutboundPart(kind="text", payload_part=0, text=text)],
    )[0]


def _claim(store: ChannelStore, binding, clock: Clock):
    return store.claim_next_outbox(
        binding.id,
        owner="fixed-process-name",
        now=clock(),
        lease_until=clock() + timedelta(seconds=30),
    )


def _finish(store: ChannelStore, claim, state, clock: Clock, **kwargs):
    return store.finish_outbox_claim(
        claim.id,
        lease_owner=str(claim.lease_owner),
        state=state,
        bot_account_id=claim.bot_account_id,
        telegram_chat_id=claim.telegram_chat_id,
        credential_id=claim.credential_id,
        credential_revision=claim.credential_revision,
        now=clock(),
        **kwargs,
    )


def _remember_pairing_code(store, binding, clock, code, *, update_id="901"):
    salt = b"h" * 16
    return store.create_pairing_request(
        bot_account_id=binding.bot_account_id,
        session_id=binding.session_id,
        telegram_update_id=update_id,
        telegram_user_id=binding.telegram_user_id,
        telegram_chat_id=binding.telegram_chat_id,
        code_salt=salt,
        code_digest=derive_pairing_digest(code, salt),
        code_hint=code[-2:],
        expires_at=clock() + timedelta(hours=1),
    )


def test_claim_is_strictly_ordered_unique_and_stale_owner_cannot_complete(tmp_path):
    store, binding, clock = _seed(tmp_path)
    first = _project(store, binding, 1, "一")
    second = _project(store, binding, 2, "二")

    claim_one = _claim(store, binding, clock)
    assert claim_one.id == first.id
    assert _claim(store, binding, clock) is None
    store.mark_outbox_claim_uncertain(
        first.id,
        lease_owner=str(claim_one.lease_owner),
        error_class="test_uncertain",
        now=clock(),
    )
    store.resolve_outbox(first.id, "resend")
    claim_two = _claim(store, binding, clock)
    assert claim_two.id == first.id
    assert claim_two.lease_owner != claim_one.lease_owner

    assert _finish(
        store,
        claim_one,
        OutboxState.SENT,
        clock,
        telegram_message_id="90",
    ) is None
    assert _finish(
        store,
        claim_two,
        OutboxState.SENT,
        clock,
        telegram_message_id="91",
    ).state is OutboxState.SENT
    assert _claim(store, binding, clock).id == second.id


def test_finish_and_renew_reject_expired_or_changed_credential_generation(tmp_path):
    store, binding, clock = _seed(tmp_path)
    _project(store, binding, 1)
    claim = _claim(store, binding, clock)
    with store.connect() as conn:
        conn.execute(
            "UPDATE bot_accounts SET credential_id = ?, credential_revision = "
            "credential_revision + 1 WHERE id = ?",
            (CREDENTIAL_B, binding.bot_account_id),
        )

    assert not store.outbox_claim_is_current(claim, now=clock())
    assert not store.renew_outbox_claim(
        claim.id,
        lease_owner=str(claim.lease_owner),
        lease_until=clock() + timedelta(seconds=30),
        bot_account_id=claim.bot_account_id,
        telegram_chat_id=claim.telegram_chat_id,
        credential_id=claim.credential_id,
        credential_revision=claim.credential_revision,
        now=clock(),
    )
    assert _finish(
        store, claim, OutboxState.SENT, clock, telegram_message_id="10"
    ) is None
    store.mark_outbox_claim_uncertain(
        claim.id,
        lease_owner=str(claim.lease_owner),
        error_class="credential_generation_changed",
        now=clock(),
    )
    assert store.outbox(claim.id).state is OutboxState.UNCERTAIN


def test_startup_recovers_all_sending_even_paused_disconnected_and_future_lease(tmp_path):
    store, binding, clock = _seed(tmp_path)
    first = _project(store, binding, 1)
    second = _project(store, binding, 2)
    with store.connect() as conn:
        conn.execute(
            "UPDATE outbox SET state = 'sending', lease_owner = 'claim_a', "
            "lease_until = ? WHERE id = ?",
            ((clock() + timedelta(days=1)).isoformat(), first.id),
        )
        conn.execute(
            "UPDATE outbox SET state = 'sending', lease_owner = 'claim_b', "
            "lease_until = NULL WHERE id = ?",
            (second.id,),
        )
        conn.execute(
            "UPDATE channel_bindings SET status = 'disconnected' WHERE id = ?",
            (binding.id,),
        )

    assert store.recover_sending_outbox(now=clock()) == 2
    assert {item.state for item in store.outbox_for(binding.id)} == {OutboxState.UNCERTAIN}


def test_legacy_rows_without_recipient_fence_migrate_fail_closed(tmp_path):
    store, binding, _clock = _seed(tmp_path)
    pending = _project(store, binding, 1)
    sending = _project(store, binding, 2)
    terminal = _project(store, binding, 3)
    with store.connect() as conn:
        conn.execute("DELETE FROM outbox_route_fences")
        conn.execute("UPDATE outbox SET state='sending' WHERE id=?", (sending.id,))
        conn.execute("UPDATE outbox SET state='terminal' WHERE id=?", (terminal.id,))

    migrated = ChannelStore(store.path)
    assert migrated.outbox(pending.id).state is OutboxState.CANCELLED
    assert migrated.outbox(sending.id).state is OutboxState.UNCERTAIN
    assert migrated.outbox(terminal.id).state is OutboxState.TERMINAL
    with pytest.raises(ChannelConflict):
        migrated.resolve_outbox(terminal.id, "retry")
    assert migrated.resolve_outbox(terminal.id, "drop").state is OutboxState.CANCELLED


def test_historic_recipient_cannot_retry_but_remains_visible_for_resolution(tmp_path):
    store, binding, clock = _seed(tmp_path)
    item = _project(store, binding, 1)
    claim = _claim(store, binding, clock)
    _finish(store, claim, OutboxState.UNCERTAIN, clock, error_class="uncertain")
    with store.connect() as conn:
        conn.execute(
            "UPDATE channel_bindings SET status = 'disconnected', disconnected_at = ? "
            "WHERE id = ?",
            (clock().isoformat(), binding.id),
        )

    with pytest.raises(ChannelConflict, match="recipient generation"):
        store.resolve_outbox(item.id, "resend")
    assert [row.id for row in store.outbox_for_session(binding.session_id)] == [item.id]
    assert store.resolve_outbox(item.id, "assume_sent").state is OutboxState.SENT


@pytest.mark.anyio
async def test_pairing_shaped_ordinary_text_enters_outbox_and_is_delivered(tmp_path):
    store, binding, clock = _seed(tmp_path)
    text = "BACKTEST TELEGRAM ABCDEFGH 23456789"
    item = _project(store, binding, 1, text)
    adapter = Adapter([TelegramSendResult("70")])
    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        clock=clock,
    )

    assert store.outbox(item.id).text == text
    assert await worker.run_once() == 1
    assert adapter.calls == [(binding.telegram_chat_id, text)]
    assert store.outbox(item.id).state is OutboxState.SENT


@pytest.mark.anyio
@pytest.mark.parametrize("initial_state", [OutboxState.PENDING, OutboxState.RETRY_WAIT])
@pytest.mark.parametrize("secret_kind", ["token", "exact_pairing_code"])
async def test_fenced_legacy_secret_is_blocked_before_credentials_or_network(
    tmp_path,
    initial_state,
    secret_kind,
):
    store, binding, clock = _seed(tmp_path)
    if secret_kind == "token":
        secret = Secrets().values[CREDENTIAL_A]
        item = _project(store, binding, 1, "legacy safe placeholder")
        # Simulate a fenced row written by an old version whose admission rule
        # did not recognize the Token. Current projection intentionally rejects it.
        with store.connect() as conn:
            conn.execute("UPDATE outbox SET text = ? WHERE id = ?", (secret, item.id))
    else:
        secret = "23456789"
        _remember_pairing_code(store, binding, clock, secret)
        item = _project(store, binding, 1, secret)

    if initial_state is OutboxState.RETRY_WAIT:
        store.transition_outbox_for_test(
            item.id,
            OutboxState.SENDING,
            lease_owner="fixture_claim",
            lease_until=clock() + timedelta(seconds=30),
        )
        store.transition_outbox_for_test(
            item.id,
            OutboxState.RETRY_WAIT,
            next_attempt_at=clock(),
            error_class="fixture_retry",
        )

    secrets = NeverReadSecrets()
    adapter = NeverCalledAdapter()
    worker = OutboxWorker(
        channel_store=store,
        secret_store=secrets,
        telegram_adapter=adapter,
        clock=clock,
    )

    assert await worker.run_once() == 1
    blocked = store.outbox(item.id)
    assert blocked.state is OutboxState.TERMINAL
    assert blocked.last_error_class == "outbound_secret_blocked"
    assert secrets.calls == []
    assert adapter.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("secret_kind", ["token", "exact_pairing_code"])
async def test_secret_across_batch_parts_blocks_later_part_after_first_was_sent(
    tmp_path,
    secret_kind,
):
    store, binding, clock = _seed(tmp_path)
    if secret_kind == "token":
        secret = Secrets().values[CREDENTIAL_A]
        split_at = secret.index(":") + 1
    else:
        secret = "23456789"
        split_at = 4
        _remember_pairing_code(store, binding, clock, secret)
    items = store.project_event_for_test(
        binding.id,
        source_event_id=1,
        parts=[
            OutboundPart(kind="text", payload_part=0, text=secret[:split_at]),
            OutboundPart(kind="text", payload_part=1, text=secret[split_at:]),
        ],
    )
    first_claim = _claim(store, binding, clock)
    assert first_claim.id == items[0].id
    assert _finish(
        store,
        first_claim,
        OutboxState.SENT,
        clock,
        telegram_message_id="701",
    ).state is OutboxState.SENT

    secrets = NeverReadSecrets()
    adapter = NeverCalledAdapter()
    worker = OutboxWorker(
        channel_store=store,
        secret_store=secrets,
        telegram_adapter=adapter,
        clock=clock,
    )

    assert await worker.run_once() == 1
    assert store.outbox(items[0].id).state is OutboxState.SENT
    blocked = store.outbox(items[1].id)
    assert blocked.state is OutboxState.TERMINAL
    assert blocked.last_error_class == "outbound_secret_blocked"
    assert secrets.calls == []
    assert adapter.calls == []


@pytest.mark.anyio
async def test_worker_success_retry_uncertain_and_protocol_terminal_are_explicit(tmp_path):
    store, binding, clock = _seed(tmp_path)
    first = _project(store, binding, 1, "成功")
    adapter = Adapter([TelegramSendResult("71")])
    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        clock=clock,
        random_uniform=lambda _a, _b: 0.1,
    )
    assert await worker.run_once() == 1
    assert store.outbox(first.id).state is OutboxState.SENT
    assert store.outbox(first.id).telegram_message_id == "71"

    second = _project(store, binding, 2, "重试")
    adapter.outcomes.append(TelegramRetryable())
    assert await worker.run_once() == 1
    retrying = store.outbox(second.id)
    assert retrying.state is OutboxState.RETRY_WAIT
    assert datetime.fromisoformat(retrying.next_attempt_at) == clock() + timedelta(seconds=2.2)
    clock.advance(3)
    adapter.outcomes.append(TelegramUncertain())
    assert await worker.run_once() == 1
    assert store.outbox(second.id).state is OutboxState.UNCERTAIN

    store.resolve_outbox(second.id, "drop")
    third = _project(store, binding, 3, "终态")
    adapter.outcomes.append(TelegramProtocolError())
    assert await worker.run_once() == 1
    assert store.outbox(third.id).state is OutboxState.TERMINAL


@pytest.mark.anyio
async def test_cancelled_inflight_send_becomes_uncertain(tmp_path):
    store, binding, clock = _seed(tmp_path)
    item = _project(store, binding, 1)
    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=Adapter(),
        clock=clock,
    )
    await worker.startup_recover()
    claim = _claim(store, binding, clock)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_token: str, *, chat_id: str, text: str):
        entered.set()
        await release.wait()
        return TelegramSendResult("8")

    worker.telegram_adapter.send_message = blocked
    task = asyncio.create_task(worker.deliver_claim(claim))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.outbox(item.id).state is OutboxState.UNCERTAIN


def test_retry_backoff_and_half_open_probe_never_deadlock():
    clock = Clock()
    worker = OutboxWorker(
        channel_store=object(),
        secret_store=object(),
        telegram_adapter=object(),
        clock=clock,
        random_uniform=lambda _a, _b: 0.2,
    )
    assert worker.retry_delay(1) == pytest.approx(2.4)
    assert worker.retry_delay(99) == pytest.approx(360.0)
    assert worker.retry_delay(3, retry_after=17) == 17

    for _ in range(5):
        worker._circuit_retryable("bot_a", clock())
    assert not worker._circuit_allows("bot_a", clock())
    clock.advance(60)
    assert worker._circuit_allows("bot_a", clock())
    assert not worker._circuit_allows("bot_a", clock())
    worker._cancel_unused_probe("bot_a")
    assert worker._circuit_allows("bot_a", clock())

    worker._circuit_retryable("bot_a", clock())
    snapshot = worker.circuit_snapshot("bot_a")
    assert snapshot["cooldown_seconds"] == 120
    clock.advance(120)
    assert worker._circuit_allows("bot_a", clock())
    worker._circuit_retryable("bot_a", clock())
    assert worker.circuit_snapshot("bot_a")["cooldown_seconds"] == 240
    for expected in (480, 900, 900):
        clock.advance(worker.circuit_snapshot("bot_a")["cooldown_seconds"])
        assert worker._circuit_allows("bot_a", clock())
        worker._circuit_retryable("bot_a", clock())
        assert worker.circuit_snapshot("bot_a")["cooldown_seconds"] == expected
    worker._circuit_success("bot_a")
    assert worker.circuit_snapshot("bot_a")["failures"] == 0
    assert worker._circuit_allows("bot_a", clock())


@pytest.mark.anyio
async def test_two_workers_only_claim_once_and_two_bindings_send_in_parallel(tmp_path):
    store, first_binding, clock = _seed(tmp_path, suffix="a")
    _store_again, second_binding, _clock = _seed(tmp_path, clock=clock, suffix="b")
    _project(store, first_binding, 1, "甲")
    _project(store, second_binding, 1, "乙")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    class BlockingAdapter:
        async def send_message(self, _token: str, *, chat_id: str, text: str):
            calls.append(chat_id)
            if len(calls) == 2:
                entered.set()
            await release.wait()
            return TelegramSendResult(str(len(calls) + 10))

    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=BlockingAdapter(),
        clock=clock,
    )
    run = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert len(calls) == 2
    assert sum(item.state is OutboxState.SENDING for item in store.outbox_for(first_binding.id)) == 1
    assert sum(item.state is OutboxState.SENDING for item in store.outbox_for(second_binding.id)) == 1
    release.set()
    assert await run == 2

    third = _project(store, first_binding, 2, "只发一次")
    entered.clear()
    release.clear()
    calls.clear()
    first_worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=BlockingAdapter(),
        clock=clock,
    )
    second_worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=BlockingAdapter(),
        clock=clock,
    )
    task_a = asyncio.create_task(first_worker.run_once())
    task_b = asyncio.create_task(second_worker.run_once())
    while not calls:
        await asyncio.sleep(0)
    assert len(calls) == 1
    assert store.outbox(third.id).state is OutboxState.SENDING
    release.set()
    assert sorted(await asyncio.gather(task_a, task_b)) == [0, 1]


@pytest.mark.anyio
async def test_late_worker_does_not_recover_or_clobber_live_http_lease(tmp_path):
    store, binding, clock = _seed(tmp_path)
    item = _project(store, binding, 1)
    entered = asyncio.Event()
    release = asyncio.Event()
    sends = 0

    class BlockedAdapter:
        async def send_message(self, _token: str, *, chat_id: str, text: str):
            nonlocal sends
            sends += 1
            entered.set()
            await release.wait()
            return TelegramSendResult("88")

    first_worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=BlockedAdapter(),
        clock=clock,
    )
    late_worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=BlockedAdapter(),
        clock=clock,
    )
    first_run = asyncio.create_task(first_worker.run_once())
    await asyncio.wait_for(entered.wait(), timeout=2)
    live_claim = store.outbox(item.id)
    assert live_claim.state is OutboxState.SENDING

    assert await late_worker.run_once() == 0
    unchanged = store.outbox(item.id)
    assert unchanged.state is OutboxState.SENDING
    assert unchanged.lease_owner == live_claim.lease_owner
    assert sends == 1

    release.set()
    assert await first_run == 1
    assert store.outbox(item.id).state is OutboxState.SENT


@pytest.mark.anyio
async def test_later_binding_claim_failure_does_not_strand_earlier_claim(tmp_path):
    store, first_binding, clock = _seed(tmp_path, suffix="a")
    _store_again, second_binding, _clock = _seed(tmp_path, clock=clock, suffix="b")
    first = _project(store, first_binding, 1, "先发送")
    second = _project(store, second_binding, 1, "故障绑定")
    real_claim = store.claim_next_outbox

    def flaky_claim(binding_id, **kwargs):
        if binding_id == second_binding.id:
            raise RuntimeError("injected claim fault")
        return real_claim(binding_id, **kwargs)

    store.claim_next_outbox = flaky_claim
    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=Adapter([TelegramSendResult("81")]),
        clock=clock,
    )

    assert await worker.run_once() == 1
    assert store.outbox(first.id).state is OutboxState.SENT
    assert store.outbox(second.id).state is OutboxState.PENDING


@pytest.mark.parametrize("blocking_state", ["sending", "retry_wait", "uncertain", "terminal"])
def test_every_nonterminal_head_blocks_later_sequence(tmp_path, blocking_state):
    store, binding, clock = _seed(tmp_path / blocking_state)
    first = _project(store, binding, 1)
    second = _project(store, binding, 2)
    sending = store.transition_outbox_for_test(
        first.id,
        OutboxState.SENDING,
        lease_owner="manual_claim",
        lease_until=clock() + timedelta(seconds=30),
        now=clock(),
    )
    if blocking_state != "sending":
        kwargs = {"error_class": "test_failure", "now": clock()}
        if blocking_state == "retry_wait":
            kwargs["next_attempt_at"] = clock() + timedelta(seconds=60)
        store.transition_outbox_for_test(
            sending.id,
            OutboxState(blocking_state),
            **kwargs,
        )
    assert _claim(store, binding, clock) is None
    assert store.outbox(second.id).state is OutboxState.PENDING


def test_expired_lease_becomes_uncertain_before_any_new_claim(tmp_path):
    store, binding, clock = _seed(tmp_path)
    first = _project(store, binding, 1)
    _project(store, binding, 2)
    claim = _claim(store, binding, clock)
    assert claim.id == first.id
    clock.advance(31)
    assert _claim(store, binding, clock) is None
    expired = store.outbox(first.id)
    assert expired.state is OutboxState.UNCERTAIN
    assert expired.last_error_class == "lease_expired"


def test_cancelled_head_is_the_only_other_state_that_can_be_passed(tmp_path):
    store, binding, clock = _seed(tmp_path)
    first = _project(store, binding, 1)
    second = _project(store, binding, 2)
    store.transition_outbox_for_test(first.id, OutboxState.CANCELLED, now=clock())
    assert _claim(store, binding, clock).id == second.id


@pytest.mark.anyio
async def test_cancel_during_claim_settles_thread_and_leaves_no_sending(tmp_path):
    store, binding, clock = _seed(tmp_path)
    item = _project(store, binding, 1)
    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=Adapter(),
        clock=clock,
    )
    real_claim = store.claim_next_outbox
    entered = asyncio.Event()

    def blocked_claim(*args, **kwargs):
        claim = real_claim(*args, **kwargs)
        entered_loop.call_soon_threadsafe(entered.set)
        release_thread.wait(timeout=5)
        return claim

    import threading

    entered_loop = asyncio.get_running_loop()
    release_thread = threading.Event()
    store.claim_next_outbox = blocked_claim
    task = asyncio.create_task(worker._claim_binding(binding.id, clock()))
    await entered.wait()
    task.cancel()
    release_thread.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.outbox(item.id).state is OutboxState.UNCERTAIN


class _NoopPairing:
    async def start(self):
        return None

    async def stop(self):
        return None

    def signal(self):
        return None


@pytest.mark.anyio
async def test_runtime_each_generation_recovers_all_sending_and_stop_is_a_fence(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="runtime")["id"]
    clock = Clock()
    store = ChannelStore(tmp_path / "channels.sqlite3", clock=clock)
    bot = store.create_bot("runtime", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id=CREDENTIAL_A,
        telegram_bot_id="100",
    )
    binding = store.bind_for_test(sid, bot, user_id="1", chat_id="1", sync_from_event_id=0)
    item = _project(store, binding, 1)
    with store.connect() as conn:
        conn.execute(
            "UPDATE outbox SET state='sending', lease_owner='old', lease_until=? WHERE id=?",
            ((clock() + timedelta(days=1)).isoformat(), item.id),
        )
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=Adapter(),
    )
    runtime.pairing = _NoopPairing()

    await runtime.start()
    assert store.outbox(item.id).state is OutboxState.UNCERTAIN
    await runtime.stop()
    with store.connect() as conn:
        conn.execute(
            "UPDATE outbox SET state='sending', lease_owner='new', lease_until=? WHERE id=?",
            ((clock() + timedelta(days=1)).isoformat(), item.id),
        )
    await runtime.start()
    assert store.outbox(item.id).state is OutboxState.UNCERTAIN
    await runtime.stop()


@pytest.mark.anyio
async def test_runtime_stop_cancels_blocked_http_and_leaves_zero_sending(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="blocked")["id"]
    clock = Clock()
    store = ChannelStore(tmp_path / "channels.sqlite3", clock=clock)
    bot = store.create_bot("blocked", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id=CREDENTIAL_A,
        telegram_bot_id="100",
    )
    binding = store.bind_for_test(sid, bot, user_id="1", chat_id="1", sync_from_event_id=0)
    item = _project(store, binding, 1)
    entered = asyncio.Event()

    class BlockedAdapter:
        async def send_message(self, _token: str, *, chat_id: str, text: str):
            entered.set()
            await asyncio.Future()

    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=BlockedAdapter(),
    )
    runtime.pairing = _NoopPairing()
    await runtime.start()
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert store.outbox(item.id).state is OutboxState.SENDING

    await runtime.stop()

    assert store.outbox(item.id).state is OutboxState.UNCERTAIN
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE state='sending'"
        ).fetchone()[0] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ok": False, "error_code": 401}, TelegramCredentialInvalid),
        ({"ok": False, "error_code": 403}, TelegramTargetUnavailable),
        (
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 13}},
            TelegramRetryable,
        ),
        ({"ok": False, "error_code": 500}, TelegramRetryable),
        ({"ok": False, "error_code": 400}, TelegramProtocolError),
        ({"ok": True, "result": {}}, TelegramUncertain),
    ],
)
async def test_http_200_provider_failures_never_count_as_sent(payload, expected):
    async def handler(_request):
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramAdapter(client=client)
    try:
        with pytest.raises(expected):
            await adapter.send_message(
                "123456:abcdefghijklmnopqrstuvwxyz_ABCD",
                chat_id="1",
                text="消息",
            )
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        ({"ok": False, "error_code": 401}, OutboxState.TERMINAL),
        ({"ok": False, "error_code": 403}, OutboxState.TERMINAL),
        (
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 13}},
            OutboxState.RETRY_WAIT,
        ),
        ({"ok": False, "error_code": 500}, OutboxState.RETRY_WAIT),
        ({"ok": False, "error_code": 400}, OutboxState.TERMINAL),
        ({"ok": True, "result": {}}, OutboxState.UNCERTAIN),
    ],
)
async def test_worker_persists_http_200_provider_failure_state(
    tmp_path,
    payload,
    expected_state,
):
    store, binding, clock = _seed(tmp_path)
    item = _project(store, binding, 1, "状态分类")

    async def handler(_request):
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramAdapter(client=client)
    worker = OutboxWorker(
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        clock=clock,
        random_uniform=lambda _a, _b: 0.0,
    )
    try:
        assert await worker.run_once() == 1
    finally:
        await client.aclose()

    persisted = store.outbox(item.id)
    assert persisted.state is expected_state
    assert persisted.telegram_message_id is None
    if payload.get("error_code") == 429:
        assert datetime.fromisoformat(persisted.next_attempt_at) == (
            clock() + timedelta(seconds=13)
        )


def test_same_recipient_reapproval_preserves_queue_and_different_recipient_rotates(tmp_path):
    store, binding, clock = _seed(tmp_path)
    items = store.project_event_for_test(
        binding.id,
        source_event_id=1,
        parts=[
            OutboundPart(kind="text", payload_part=index, text=f"状态{index}")
            for index in range(5)
        ],
    )
    pending, retrying, terminal, uncertain, sending = items
    with store.connect() as conn:
        conn.execute(
            "UPDATE outbox SET state='retry_wait', next_attempt_at=? WHERE id=?",
            ((clock() + timedelta(seconds=60)).isoformat(), retrying.id),
        )
        conn.execute(
            "UPDATE outbox SET state='terminal', last_error_class='protocol_error' WHERE id=?",
            (terminal.id,),
        )
        conn.execute(
            "UPDATE outbox SET state='uncertain', last_error_class='uncertain' WHERE id=?",
            (uncertain.id,),
        )
        conn.execute(
            "UPDATE outbox SET state='sending', lease_owner='claim_old', lease_until=? WHERE id=?",
            ((clock() + timedelta(seconds=30)).isoformat(), sending.id),
        )
    same = store.create_pairing_request(
        bot_account_id=binding.bot_account_id,
        session_id=binding.session_id,
        telegram_update_id=1,
        telegram_user_id=binding.telegram_user_id,
        telegram_chat_id=binding.telegram_chat_id,
        code_salt=b"s" * 16,
        code_digest=b"d" * 32,
        code_hint="AA",
        expires_at=clock() + timedelta(hours=1),
    )
    same_binding = store.approve_pairing_request(
        same.id,
        session_id=binding.session_id,
        sync_from_event_id=99,
        now=clock(),
    )
    assert same_binding.id == binding.id
    assert same_binding.projection_cursor_event_id == 1
    assert [store.outbox(item.id).state for item in items] == [
        OutboxState.PENDING,
        OutboxState.RETRY_WAIT,
        OutboxState.TERMINAL,
        OutboxState.UNCERTAIN,
        OutboxState.UNCERTAIN,
    ]
    queued = _project(store, binding, 2)
    changed = store.create_pairing_request(
        bot_account_id=binding.bot_account_id,
        session_id=binding.session_id,
        telegram_update_id=2,
        telegram_user_id="999",
        telegram_chat_id="999",
        code_salt=b"t" * 16,
        code_digest=b"e" * 32,
        code_hint="BB",
        expires_at=clock() + timedelta(hours=1),
    )
    rotated = store.approve_pairing_request(
        changed.id,
        session_id=binding.session_id,
        sync_from_event_id=50,
        now=clock(),
    )
    assert rotated.id != binding.id
    assert rotated.sync_from_event_id == rotated.projection_cursor_event_id == 50
    assert store.binding(binding.id).status.value == "disconnected"
    assert store.outbox(pending.id).state is OutboxState.CANCELLED
    assert store.outbox(retrying.id).state is OutboxState.CANCELLED
    assert store.outbox(terminal.id).state is OutboxState.TERMINAL
    assert store.outbox(uncertain.id).state is OutboxState.UNCERTAIN
    assert store.outbox(queued.id).state is OutboxState.CANCELLED
    with pytest.raises(ChannelConflict):
        store.resolve_outbox(terminal.id, "retry")
    assert store.resolve_outbox(terminal.id, "drop").state is OutboxState.CANCELLED


def test_runtime_status_keeps_historic_unresolved_delivery_visible(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="status")["id"]
    clock = Clock()
    store = ChannelStore(tmp_path / "channels.sqlite3", clock=clock)
    bot = store.create_bot("status", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id=CREDENTIAL_A,
        telegram_bot_id="100",
    )
    old_binding = store.bind_for_test(sid, bot, user_id="1", chat_id="1", sync_from_event_id=0)
    item = _project(store, old_binding, 1)
    claim = _claim(store, old_binding, clock)
    _finish(store, claim, OutboxState.UNCERTAIN, clock, error_class="uncertain")
    store.transition_binding(old_binding.id, "disconnected")
    clock.advance(1)
    current_binding = store.bind_for_test(
        sid, bot, user_id="2", chat_id="2", sync_from_event_id=1
    )
    later = []
    for event_id in range(2, 27):
        clock.advance(1)
        later.append(_project(store, current_binding, event_id))
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=Secrets(),
        telegram_adapter=Adapter(),
    )

    status = runtime.status_for_session(sid)
    assert status["counts"]["uncertain"] == 1
    assert status["counts"]["pending"] == len(later)
    visible_ids = [row["outbox_id"] for row in status["recent_deliveries"]]
    assert len(visible_ids) == 20
    assert visible_ids[0] == item.id
    assert later[-1].id in visible_ids
    assert later[0].id not in visible_ids
    with pytest.raises(ChannelRuntimeError):
        runtime.resolve_delivery(sid, item.id, "resend")
    assert runtime.resolve_delivery(sid, item.id, "assume_sent")["counts"]["uncertain"] == 0
