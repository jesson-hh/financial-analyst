from __future__ import annotations

import asyncio
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

import guanlan_v2.channels.pairing as pairing_module
from guanlan_v2.channels.models import BindingStatus, BotStatus, PairingStatus
from guanlan_v2.channels.runtime import ChannelRuntime
from guanlan_v2.channels.pairing import (
    PAIRING_ALPHABET,
    PAIRING_CODE_LENGTH,
    POLL_LEASE_SECONDS,
    POLL_RENEW_SECONDS,
    PairingError,
    TelegramPairingService,
    derive_pairing_digest,
    generate_pairing_code,
)
from guanlan_v2.channels.store import ChannelStore
from guanlan_v2.channels.telegram import (
    TelegramBotIdentity,
    TelegramCredentialInvalid,
    TelegramInboundMessage,
    TelegramProtocolError,
    TelegramPollConflict,
    TelegramRetryable,
    TelegramSendResult,
    TelegramUncertain,
    TelegramUpdate,
)
from guanlan_v2.console.store import ConsoleStore


UTC = timezone.utc
TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
CREDENTIAL_ID = "cred_" + "a" * 32


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class MemorySecrets:
    def __init__(self):
        self.values = {CREDENTIAL_ID: TOKEN}
        self.get_calls = 0

    def get(self, credential_id: str) -> str:
        self.get_calls += 1
        return self.values[credential_id]


class FakeTelegram:
    def __init__(self):
        self.updates: list[TelegramUpdate] = []
        self.send_effects: list[BaseException | TelegramSendResult] = []
        self.sent: list[tuple[str, str]] = []
        self.get_me_identity = TelegramBotIdentity(bot_id="101", username="research_bot")
        self.get_me_calls = 0
        self.get_updates_calls = 0

    async def get_updates(self, token: str, *, offset: int, timeout: int):
        assert token == TOKEN
        self.get_updates_calls += 1
        return tuple(update for update in self.updates if update.update_id >= offset)

    async def send_message(self, token: str, *, chat_id: str, text: str):
        assert token == TOKEN
        self.sent.append((chat_id, text))
        if self.send_effects:
            effect = self.send_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            return effect
        return TelegramSendResult(message_id=str(len(self.sent)))

    async def get_me(self, token: str):
        assert token == TOKEN
        self.get_me_calls += 1
        return self.get_me_identity


def private_update(
    update_id: int,
    *,
    user_id: str = "501",
    chat_id: str | None = None,
    chat_type: str = "private",
) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramInboundMessage(
            message_id=str(update_id),
            chat_id=chat_id or user_id,
            chat_type=chat_type,
            user_id=user_id,
            username="reader",
            display_name="Reader",
        ),
    )


def seeded_service(tmp_path, *, now: datetime | None = None):
    clock = MutableClock(now or datetime(2026, 8, 23, 3, 0, tzinfo=UTC))
    console = ConsoleStore(tmp_path / "console")
    session = console.create_session("研究")
    sid = session["id"]
    store = ChannelStore(tmp_path / "channels.sqlite3", clock=clock)
    bot_id = store.create_bot("研究助手", draft_session_id=sid)
    store.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=CREDENTIAL_ID,
        telegram_bot_id="101",
        telegram_username="research_bot",
    )
    telegram = FakeTelegram()
    secrets = MemorySecrets()
    service = TelegramPairingService(
        console_store=console,
        channel_store=store,
        secret_store=secrets,
        telegram_adapter=telegram,
        clock=clock,
    )
    return service, store, console, telegram, secrets, sid, bot_id, clock


def extract_code(message: str) -> str:
    matches = re.findall(r"(?<![A-Z2-9])[A-Z2-9]{8}(?![A-Z2-9])", message)
    assert len(matches) == 1
    return matches[0]


async def wait_until(predicate, *, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.01)


def test_pairing_code_uses_exact_readable_alphabet():
    codes = [generate_pairing_code() for _ in range(200)]
    assert all(len(code) == PAIRING_CODE_LENGTH == 8 for code in codes)
    assert all(set(code) <= set(PAIRING_ALPHABET) for code in codes)
    assert not ({"0", "O", "1", "I"} & set("".join(codes)))
    assert (POLL_LEASE_SECONDS, POLL_RENEW_SECONDS) == (60, 20)


@pytest.mark.anyio
async def test_finish_on_cancel_does_not_spin_when_child_cancels_itself():
    async def self_cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            pairing_module._finish_on_cancel(self_cancel()), timeout=1
        )


@pytest.mark.anyio
async def test_success_sends_before_atomic_request_and_offset(tmp_path):
    service, store, _console, telegram, _secrets, sid, bot_id, clock = seeded_service(tmp_path)
    telegram.updates = [private_update(77)]

    assert await service.poll_once(bot_id) is True

    bot = store.bot(bot_id)
    requests = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)
    assert bot.update_offset == 78
    assert len(requests) == 1
    request = requests[0]
    code = extract_code(telegram.sent[0][1])
    assert request.session_id == sid
    assert request.code_hint == code[-2:]
    assert request.code_salt and len(request.code_salt) == 16
    assert request.code_digest == derive_pairing_digest(code, request.code_salt)
    assert datetime.fromisoformat(request.expires_at) - datetime.fromisoformat(
        request.created_at
    ) == timedelta(hours=1)
    assert datetime.fromisoformat(request.created_at) == clock.value
    for sqlite_file in tmp_path.glob("channels.sqlite3*"):
        assert code.encode("ascii") not in sqlite_file.read_bytes()


@pytest.mark.anyio
async def test_retryable_send_keeps_offset_and_creates_no_request(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(10)]
    telegram.send_effects = [TelegramRetryable(3)]

    assert await service.poll_once(bot_id) is False

    assert store.bot(bot_id).update_offset == 0
    assert store.pairing_requests_for(bot_id) == []


@pytest.mark.anyio
async def test_retryable_gap_stops_batch_before_later_updates(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(10), private_update(11, user_id="502")]
    telegram.send_effects = [TelegramRetryable(), TelegramSendResult(message_id="2")]

    assert await service.poll_once(bot_id) is False

    assert store.bot(bot_id).update_offset == 0
    assert len(telegram.sent) == 1
    assert store.pairing_requests_for(bot_id) == []


@pytest.mark.anyio
async def test_ambiguous_send_commits_request_because_user_may_have_received_it(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(11)]
    telegram.send_effects = [TelegramUncertain()]

    assert await service.poll_once(bot_id) is True

    assert store.bot(bot_id).update_offset == 12
    assert len(store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)) == 1


@pytest.mark.anyio
async def test_explicit_send_rejection_advances_without_request(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(12)]
    telegram.send_effects = [TelegramProtocolError()]

    assert await service.poll_once(bot_id) is True

    assert store.bot(bot_id).update_offset == 13
    assert store.pairing_requests_for(bot_id) == []


@pytest.mark.anyio
async def test_non_private_update_is_consumed_without_pairing_request(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(13, chat_id="-99", chat_type="group")]

    assert await service.poll_once(bot_id) is True

    assert store.bot(bot_id).update_offset == 14
    assert store.pairing_requests_for(bot_id) == []
    assert telegram.sent == []


@pytest.mark.anyio
@pytest.mark.parametrize("bound_status", [BindingStatus.ACTIVE, BindingStatus.PAUSED])
async def test_bound_inbound_only_advances_offset_and_never_touches_console(
    tmp_path, bound_status
):
    service, store, console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    store.transition_bot(bot_id, BotStatus.ACTIVE)
    store.bind_for_test(
        sid,
        bot_id,
        user_id="501",
        chat_id="501",
        sync_from_event_id=0,
        status=bound_status,
    )
    if bound_status is BindingStatus.PAUSED:
        store.transition_bot(bot_id, BotStatus.PAUSED)
    before = list(console.read_events(sid))
    telegram.updates = [private_update(14)]

    assert await service.poll_once(bot_id) is True

    assert store.bot(bot_id).update_offset == 15
    assert console.read_events(sid) == before
    assert telegram.sent == []
    assert store.pairing_requests_for(bot_id) == []


@pytest.mark.anyio
async def test_same_chat_gets_new_code_only_after_send_accepts(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(20)]
    await service.poll_once(bot_id)
    first = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]

    telegram.updates = [private_update(21)]
    telegram.send_effects = [TelegramRetryable()]
    assert await service.poll_once(bot_id) is False
    assert store.pairing_request(first.id).status is PairingStatus.PENDING
    assert store.bot(bot_id).update_offset == 21

    telegram.send_effects = []
    assert await service.poll_once(bot_id) is True
    assert store.pairing_request(first.id).status is PairingStatus.DISMISSED
    pending = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)
    assert len(pending) == 1 and pending[0].telegram_update_id == "21"


@pytest.mark.anyio
async def test_consumed_duplicate_update_is_idempotent(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    update = private_update(25)
    telegram.updates = [update]
    assert await service.poll_once(bot_id) is True
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    first_text = telegram.sent[-1][1]

    telegram.updates = [update]
    assert await service.poll_once(bot_id) is False

    assert store.bot(bot_id).update_offset == 26
    assert store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0].id == request.id
    assert telegram.sent == [("501", first_text)]


@pytest.mark.anyio
async def test_fourth_distinct_chat_gets_safe_capacity_notice_not_code(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    for update_id, user_id in enumerate(("501", "502", "503"), start=30):
        telegram.updates = [private_update(update_id, user_id=user_id)]
        await service.poll_once(bot_id)

    telegram.updates = [private_update(33, user_id="504")]
    assert await service.poll_once(bot_id) is True

    assert store.bot(bot_id).update_offset == 34
    assert len(store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)) == 3
    assert "最多 3" in telegram.sent[-1][1]
    assert not re.search(r"(?<![A-Z2-9])[A-Z2-9]{8}(?![A-Z2-9])", telegram.sent[-1][1])


@pytest.mark.anyio
async def test_cancellation_finishes_send_to_store_critical_section(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_send(token: str, *, chat_id: str, text: str):
        telegram.sent.append((chat_id, text))
        entered.set()
        await release.wait()
        return TelegramSendResult(message_id="1")

    telegram.send_message = slow_send
    telegram.updates = [private_update(40)]
    task = asyncio.create_task(service.poll_once(bot_id))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.bot(bot_id).update_offset == 41
    assert len(store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)) == 1


@pytest.mark.anyio
async def test_send_success_then_commit_crash_replays_with_latest_authoritative_code(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(41)]
    original_commit = store.commit_sent_pairing_update
    crashed = False

    def crash_once(**kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("injected crash cut")
        return original_commit(**kwargs)

    store.commit_sent_pairing_update = crash_once
    with pytest.raises(RuntimeError, match="injected crash cut"):
        await service.poll_once(bot_id)
    first_code = extract_code(telegram.sent[-1][1])
    assert store.bot(bot_id).update_offset == 0
    assert store.pairing_requests_for(bot_id) == []

    assert await service.poll_once(bot_id) is True
    latest_code = extract_code(telegram.sent[-1][1])
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    assert request.code_digest == derive_pairing_digest(latest_code, request.code_salt)
    if first_code != latest_code:
        assert request.code_digest != derive_pairing_digest(first_code, request.code_salt)
    assert "以最新一条为准" in telegram.sent[-1][1]


def test_poll_lease_is_owner_and_revision_fenced_and_expires(tmp_path):
    _service, store, _console, _telegram, _secrets, _sid, bot_id, clock = seeded_service(tmp_path)
    revision = store.bot(bot_id).credential_revision

    assert store.acquire_poll_lease(bot_id, owner="owner-a", credential_revision=revision)
    assert not store.acquire_poll_lease(bot_id, owner="owner-b", credential_revision=revision)
    assert not store.renew_poll_lease(bot_id, owner="owner-b", credential_revision=revision)
    clock.value += timedelta(seconds=61)
    assert store.acquire_poll_lease(bot_id, owner="owner-b", credential_revision=revision)
    assert not store.release_poll_lease(bot_id, owner="owner-a", credential_revision=revision)
    assert store.release_poll_lease(bot_id, owner="owner-b", credential_revision=revision)


@pytest.mark.anyio
async def test_secure_approval_checks_code_identity_and_uses_console_tail(tmp_path):
    service, store, console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(50)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[0][1])
    console.append_event(sid, "user_msg", text="批准前事实")

    with pytest.raises(PairingError, match="pairing_code_invalid"):
        await service.approve_pairing(sid, request.id, "22222222")

    binding = await service.approve_pairing(sid, request.id, code)
    assert binding.session_id == sid
    assert binding.telegram_user_id == "501"
    assert binding.sync_from_event_id == 1
    assert binding.projection_cursor_event_id == 1
    assert store.bot(bot_id).status is BotStatus.ACTIVE
    assert store.pairing_request(request.id).status is PairingStatus.APPROVED
    assert telegram.get_me_calls == 2


@pytest.mark.anyio
async def test_approval_boundary_uses_actual_event_tail_across_id_gap(tmp_path):
    service, store, console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(51)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[-1][1])
    console.append_event(sid, "user_msg", text="actual event one")
    meta = console.get_meta(sid)
    meta["next_event_id"] = 8
    console._save_meta(meta)

    binding = await service.approve_pairing(sid, request.id, code)

    assert binding.sync_from_event_id == 1
    assert binding.projection_cursor_event_id == 1


@pytest.mark.anyio
async def test_repeated_approval_still_requires_original_code(tmp_path):
    service, store, _console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(52)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[-1][1])
    first = await service.approve_pairing(sid, request.id, code)

    with pytest.raises(PairingError, match="pairing_code_invalid"):
        await service.approve_pairing(sid, request.id, "22222222")

    repeated = await service.approve_pairing(sid, request.id, code)
    assert repeated.id == first.id
    assert len(store.live_bindings()) == 1


@pytest.mark.anyio
async def test_token_rotation_invalidates_old_generation_challenge(tmp_path):
    service, store, _console, telegram, secrets_store, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(53)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[-1][1])
    replacement_credential = "cred_" + "b" * 32

    store.cas_bot_credential(
        bot_id,
        expected_revision=1,
        credential_id=replacement_credential,
        telegram_bot_id="101",
    )
    secrets_store.values[replacement_credential] = TOKEN

    assert store.pairing_request(request.id).status is PairingStatus.DISMISSED
    with pytest.raises(PairingError, match="pairing_not_pending"):
        await service.approve_pairing(sid, request.id, code)
    assert store.live_bindings() == []


@pytest.mark.anyio
async def test_token_rotation_resets_offset_only_when_bot_identity_changes(tmp_path):
    service, store, _console, telegram, secrets_store, _sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(88)]
    await service.poll_once(bot_id)
    assert store.bot(bot_id).update_offset == 89

    same_identity_credential = "cred_" + "6" * 32
    store.cas_bot_credential(
        bot_id,
        expected_revision=1,
        credential_id=same_identity_credential,
        telegram_bot_id="101",
    )
    secrets_store.values[same_identity_credential] = TOKEN
    assert store.bot(bot_id).update_offset == 89

    changed_identity_credential = "cred_" + "7" * 32
    store.cas_bot_credential(
        bot_id,
        expected_revision=2,
        credential_id=changed_identity_credential,
        telegram_bot_id="202",
    )
    secrets_store.values[changed_identity_credential] = TOKEN
    assert store.bot(bot_id).update_offset == 0


@pytest.mark.anyio
async def test_approval_rejects_get_me_identity_change_and_revision_race(tmp_path):
    service, store, _console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(60)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[0][1])
    telegram.get_me_identity = TelegramBotIdentity(bot_id="999")

    with pytest.raises(PairingError, match="telegram_identity_changed"):
        await service.approve_pairing(sid, request.id, code)

    telegram.get_me_identity = TelegramBotIdentity(bot_id="101")

    async def race_get_me(token: str):
        store.cas_bot_credential(
            bot_id,
            expected_revision=1,
            credential_id="cred_" + "b" * 32,
            telegram_bot_id="101",
        )
        return TelegramBotIdentity(bot_id="101")

    telegram.get_me = race_get_me
    with pytest.raises(PairingError, match="pairing_conflict"):
        await service.approve_pairing(sid, request.id, code)
    assert store.live_bindings() == []


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["archive", "delete"])
async def test_approval_reservation_spans_get_me_and_rechecks_console_before_commit(
    tmp_path, mutation
):
    service, store, console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(605)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[-1][1])
    reserved: set[str] = set()
    entered = asyncio.Event()
    release = asyncio.Event()

    def reserve(session_id: str) -> bool:
        if session_id in reserved:
            return False
        reserved.add(session_id)
        return True

    def unreserve(session_id: str) -> None:
        reserved.discard(session_id)

    async def blocking_get_me(token: str):
        entered.set()
        await release.wait()
        return TelegramBotIdentity(bot_id="101")

    service._reserve_mutation = reserve
    service._release_mutation = unreserve
    telegram.get_me = blocking_get_me
    task = asyncio.create_task(service.approve_pairing(sid, request.id, code))
    await entered.wait()
    assert reserved == {sid}
    if mutation == "archive":
        console.set_archived(sid, True)
    else:
        assert console.delete_session(sid)
    release.set()

    with pytest.raises(PairingError, match="channel_session_read_only|pairing_not_found"):
        await task
    assert reserved == set()
    assert store.live_bindings() == []


@pytest.mark.anyio
async def test_approval_cancellation_keeps_reservation_until_sqlite_worker_finishes(tmp_path):
    service, store, _console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(606)]
    await service.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[-1][1])
    reserved: set[str] = set()
    sqlite_entered = threading.Event()
    sqlite_release = threading.Event()
    original_approve = store.approve_pairing_request

    def reserve(session_id: str) -> bool:
        if session_id in reserved:
            return False
        reserved.add(session_id)
        return True

    def blocking_approve(*args, **kwargs):
        sqlite_entered.set()
        assert sqlite_release.wait(timeout=3)
        return original_approve(*args, **kwargs)

    service._reserve_mutation = reserve
    service._release_mutation = reserved.discard
    store.approve_pairing_request = blocking_approve
    task = asyncio.create_task(service.approve_pairing(sid, request.id, code))
    assert await asyncio.to_thread(sqlite_entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.05)

    assert reserved == {sid}
    assert not task.done()
    sqlite_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert reserved == set()
    assert len(store.live_bindings()) == 1


@pytest.mark.anyio
async def test_runtime_exposes_pairing_approval_in_shared_console_busy_set(tmp_path):
    _service, store, console, telegram, secrets_store, sid, bot_id, _clock = seeded_service(tmp_path)
    service_for_issue = TelegramPairingService(
        console_store=console,
        channel_store=store,
        secret_store=secrets_store,
        telegram_adapter=telegram,
    )
    telegram.updates = [private_update(607)]
    await service_for_issue.poll_once(bot_id)
    request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    code = extract_code(telegram.sent[-1][1])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_get_me(token: str):
        entered.set()
        await release.wait()
        return TelegramBotIdentity(bot_id="101")

    telegram.get_me = blocking_get_me
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=secrets_store,
        telegram_adapter=telegram,
    )
    task = asyncio.create_task(runtime.approve_pairing(sid, request.id, code))
    await entered.wait()
    assert runtime.reserved_secret_session_ids() == frozenset({sid})
    release.set()
    await task
    assert runtime.reserved_secret_session_ids() == frozenset()


@pytest.mark.anyio
async def test_two_pending_requests_cannot_both_win_approval(tmp_path):
    service, store, _console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    request_codes: list[tuple[str, str]] = []
    for update_id, user_id in ((61, "501"), (62, "502")):
        telegram.updates = [private_update(update_id, user_id=user_id)]
        await service.poll_once(bot_id)
        request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[-1]
        request_codes.append((request.id, extract_code(telegram.sent[-1][1])))

    results = await asyncio.gather(
        *(service.approve_pairing(sid, request_id, code) for request_id, code in request_codes),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, PairingError) for result in results) == 1
    assert len(store.live_bindings()) == 1


@pytest.mark.anyio
async def test_code_only_approval_derives_all_candidates_not_hint_match(tmp_path):
    service, store, _console, _telegram, _secrets, sid, bot_id, clock = seeded_service(tmp_path)
    code_a = "222222AA"
    code_b = "333333AA"
    for update_id, user_id, code, salt_byte in (
        (63, "501", code_a, b"a"),
        (64, "502", code_b, b"b"),
    ):
        salt = salt_byte * 16
        store.create_pairing_request(
            bot_account_id=bot_id,
            session_id=sid,
            telegram_update_id=update_id,
            telegram_user_id=user_id,
            telegram_chat_id=user_id,
            code_salt=salt,
            code_digest=derive_pairing_digest(code, salt),
            code_hint="AA",
            expires_at=clock.value + timedelta(hours=1),
        )

    binding = await service.approve_pairing_code(sid, code_b)

    assert binding.telegram_user_id == "502"
    assert len(store.live_bindings()) == 1


@pytest.mark.anyio
async def test_code_only_approval_never_crosses_session_or_accepts_ambiguous_code(tmp_path):
    service, store, console, _telegram, _secrets, sid, bot_id, clock = seeded_service(tmp_path)
    other_sid = console.create_session("其他研究")["id"]
    code = "444444BB"
    for update_id, request_sid, user_id, salt_byte in (
        (65, other_sid, "601", b"c"),
        (66, sid, "602", b"d"),
        (67, sid, "603", b"e"),
    ):
        salt = salt_byte * 16
        store.create_pairing_request(
            bot_account_id=bot_id,
            session_id=request_sid,
            telegram_update_id=update_id,
            telegram_user_id=user_id,
            telegram_chat_id=user_id,
            code_salt=salt,
            code_digest=derive_pairing_digest(code, salt),
            code_hint="BB",
            expires_at=clock.value + timedelta(hours=1),
        )

    with pytest.raises(PairingError, match="pairing_code_invalid"):
        await service.approve_pairing_code(other_sid, code)
    with pytest.raises(PairingError, match="pairing_code_invalid"):
        await service.approve_pairing_code(sid, code)
    assert store.live_bindings() == []


@pytest.mark.anyio
async def test_code_only_skips_expired_request_before_scrypt(tmp_path, monkeypatch):
    service, store, _console, _telegram, _secrets, sid, bot_id, clock = seeded_service(tmp_path)
    code = "555555CC"
    for update_id, user_id, salt_byte, expires_at in (
        (68, "701", b"f", clock.value - timedelta(seconds=1)),
        (69, "702", b"g", clock.value + timedelta(hours=1)),
    ):
        salt = salt_byte * 16
        store.create_pairing_request(
            bot_account_id=bot_id,
            session_id=sid,
            telegram_update_id=update_id,
            telegram_user_id=user_id,
            telegram_chat_id=user_id,
            code_salt=salt,
            code_digest=derive_pairing_digest(code, salt),
            code_hint="CC",
            expires_at=expires_at,
        )
    real_derive = pairing_module.derive_pairing_digest
    calls: list[bytes] = []

    def counting_derive(value: str, salt: bytes):
        calls.append(bytes(salt))
        return real_derive(value, salt)

    monkeypatch.setattr(pairing_module, "derive_pairing_digest", counting_derive)
    binding = await service.approve_pairing_code(sid, code)

    assert binding.telegram_user_id == "702"
    # One derive during code-only resolution and one final secure approval;
    # the expired candidate never invokes scrypt.
    assert calls == [b"g" * 16, b"g" * 16]


@pytest.mark.anyio
async def test_identity_repair_updates_original_binding_and_clears_old_backlog(tmp_path):
    service, store, _console, telegram, _secrets, sid, bot_id, _clock = seeded_service(tmp_path)
    telegram.updates = [private_update(70, user_id="501")]
    await service.poll_once(bot_id)
    first_request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]
    first = await service.approve_pairing(sid, first_request.id, extract_code(telegram.sent[-1][1]))
    store.project_event(
        first.id,
        source_event_id=1,
        parts=[{"kind": "text", "payload_part": 0, "text": "旧目标待发送"}],
    )
    store.cas_bot_credential(
        bot_id,
        expected_revision=1,
        credential_id="cred_" + "b" * 32,
        telegram_bot_id="202",
        pause_live_binding_if_identity_changed=True,
    )
    service.secret_store.values["cred_" + "b" * 32] = TOKEN
    telegram.get_me_identity = TelegramBotIdentity(bot_id="202")
    assert store.bot(bot_id).update_offset == 0
    telegram.updates = [private_update(1, user_id="777")]
    await service.poll_once(bot_id)
    second_request = store.pairing_requests_for(bot_id, status=PairingStatus.PENDING)[0]

    repaired = await service.approve_pairing(
        sid, second_request.id, extract_code(telegram.sent[-1][1])
    )

    assert repaired.id == first.id
    assert repaired.telegram_user_id == "777"
    assert repaired.status is BindingStatus.ACTIVE
    assert len(store.live_bindings()) == 1
    assert store.outbox_for(first.id)[0].state.value == "cancelled"


def test_dismiss_is_scoped_to_current_session_and_current_bot(tmp_path):
    service, store, _console, _telegram, _secrets, sid, bot_id, clock = seeded_service(tmp_path)
    salt = b"s" * 16
    request = store.create_pairing_request(
        bot_account_id=bot_id,
        session_id=sid,
        telegram_update_id=80,
        telegram_user_id="501",
        telegram_chat_id="501",
        code_salt=salt,
        code_digest=derive_pairing_digest("22222222", salt),
        code_hint="22",
        expires_at=clock.value + timedelta(hours=1),
    )

    with pytest.raises(PairingError, match="pairing_not_found"):
        service.dismiss_pairing("cs_wrong", request.id)
    dismissed = service.dismiss_pairing(sid, request.id)
    assert dismissed.status is PairingStatus.DISMISSED
    assert store.pairing_requests_for(bot_id, status=PairingStatus.PENDING) == []


@pytest.mark.anyio
async def test_credential_invalid_stops_only_affected_bot(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)

    async def invalid_updates(token: str, *, offset: int, timeout: int):
        raise TelegramCredentialInvalid()

    telegram.get_updates = invalid_updates
    assert await service.poll_once(bot_id) is False
    assert store.bot(bot_id).status is BotStatus.CREDENTIAL_INVALID
    assert store.bot(bot_id).poll_owner is None


@pytest.mark.anyio
async def test_generic_secret_failure_faults_bot_unavailable(tmp_path):
    service, store, _console, _telegram, secrets_store, _sid, bot_id, _clock = seeded_service(tmp_path)

    def broken_get(_credential_id: str):
        raise RuntimeError("provider account unavailable")

    secrets_store.get = broken_get
    assert await service.poll_once(bot_id) is False
    assert store.bot(bot_id).status is BotStatus.CREDENTIAL_UNAVAILABLE
    assert store.bot(bot_id).poll_owner is None


@pytest.mark.anyio
async def test_supervisor_renews_and_stop_releases_lease(tmp_path):
    service, store, _console, telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    renew_calls = 0
    original_renew = store.renew_poll_lease

    async def blocking_updates(token: str, *, offset: int, timeout: int):
        entered.set()
        await release.wait()
        return ()

    def counting_renew(*args, **kwargs):
        nonlocal renew_calls
        renew_calls += 1
        return original_renew(*args, **kwargs)

    telegram.get_updates = blocking_updates
    store.renew_poll_lease = counting_renew
    service.supervisor.reconcile_seconds = 0.01
    service.supervisor.renew_seconds = 0.02

    await service.start()
    await entered.wait()
    await wait_until(lambda: renew_calls >= 2)
    assert store.bot(bot_id).poll_owner is not None
    release.set()
    await service.stop()

    assert store.bot(bot_id).poll_owner is None
    assert store.bot(bot_id).poll_lease_until is None


@pytest.mark.anyio
async def test_renewer_exception_fences_and_releases_main_poller(tmp_path):
    service, store, _console, _telegram, _secrets, _sid, bot_id, _clock = seeded_service(tmp_path)

    def broken_renew(*args, **kwargs):
        raise RuntimeError("sqlite temporarily unavailable")

    store.renew_poll_lease = broken_renew
    service.supervisor.renew_seconds = 0.02
    task = asyncio.create_task(
        service.supervisor._run_poller(
            bot_id, store.bot(bot_id).credential_revision
        )
    )
    await asyncio.wait_for(task, timeout=1)
    assert store.bot(bot_id).poll_owner is None
    assert store.bot(bot_id).poll_lease_until is None


@pytest.mark.anyio
async def test_supervisor_poll_conflict_isolated_to_one_bot(tmp_path):
    service, store, console, telegram, secrets_store, _sid, first_bot_id, _clock = seeded_service(tmp_path)
    second_sid = console.create_session("第二研究")["id"]
    second_bot_id = store.create_bot("第二助手", draft_session_id=second_sid)
    second_credential = "cred_" + "c" * 32
    second_token = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    store.cas_bot_credential(
        second_bot_id,
        expected_revision=0,
        credential_id=second_credential,
        telegram_bot_id="202",
    )
    secrets_store.values[second_credential] = second_token
    second_polled = asyncio.Event()

    async def split_updates(token: str, *, offset: int, timeout: int):
        if token == TOKEN:
            raise TelegramPollConflict()
        assert token == second_token
        second_polled.set()
        await asyncio.sleep(0.02)
        return ()

    telegram.get_updates = split_updates
    service.supervisor.reconcile_seconds = 0.01
    await service.start()
    await second_polled.wait()
    await wait_until(lambda: store.bot(first_bot_id).status is BotStatus.POLL_CONFLICT)

    assert store.bot(second_bot_id).status is BotStatus.AWAITING_PAIRING
    assert any(key[0] == second_bot_id for key in service.supervisor.poller_keys)
    await service.stop()


@pytest.mark.anyio
async def test_supervisor_signal_reconciles_new_bot_and_credential_revision(tmp_path):
    service, store, console, _telegram, secrets_store, _sid, first_bot_id, _clock = seeded_service(tmp_path)
    service.supervisor.reconcile_seconds = 10.0
    await service.start()
    await wait_until(
        lambda: (first_bot_id, 1) in service.supervisor.poller_keys
    )

    second_sid = console.create_session("第二研究")["id"]
    second_bot_id = store.create_bot("第二助手", draft_session_id=second_sid)
    second_credential = "cred_" + "d" * 32
    store.cas_bot_credential(
        second_bot_id,
        expected_revision=0,
        credential_id=second_credential,
        telegram_bot_id="303",
    )
    secrets_store.values[second_credential] = TOKEN
    service.signal()
    await wait_until(
        lambda: (second_bot_id, 1) in service.supervisor.poller_keys
    )

    replacement = "cred_" + "e" * 32
    store.cas_bot_credential(
        first_bot_id,
        expected_revision=1,
        credential_id=replacement,
        telegram_bot_id="101",
    )
    secrets_store.values[replacement] = TOKEN
    service.signal()
    await wait_until(
        lambda: (first_bot_id, 2) in service.supervisor.poller_keys
        and (first_bot_id, 1) not in service.supervisor.poller_keys
    )
    await service.stop()


def test_missing_workspace_authority_fails_closed(tmp_path):
    service, _store, _console, _telegram, _secrets, sid, _bot_id, _clock = seeded_service(tmp_path)

    class ConsoleWithoutWorkspaceAuthority:
        def get_meta(self, session_id: str):
            return {"id": session_id, "archived": False}

    service.console_store = ConsoleWithoutWorkspaceAuthority()
    with pytest.raises(PairingError, match="channel_session_read_only"):
        service._require_writable_session(sid)
