# -*- coding: utf-8 -*-
"""R21 — the ONE production ``verify(actor) -> principal`` port (operator allowlist).

Why this module exists
----------------------
:meth:`~guanlan_v2.orchestration.approval.PlanApprovalCoordinator.decide` (and
``issue_lease`` / ``revoke_lease``) are fail-closed: with ``verifier=None`` there
is no decision path at all, and with a verifier the coordinator calls
``self._verifier.verify(actor)`` and stamps ``principal.actor`` onto the durable
``PlanApproval``. Until this module existed, **every** verifier in the repository
was a test double, so no ``PlanApproval`` could ever be recorded and the whole
nine-phase orchestration framework refused at its first human decision point.
This module supplies the missing production implementation — nothing more.

WHAT THIS VERIFIER PROVES — and what it does NOT
------------------------------------------------
It proves exactly ONE thing:

    the actor id handed to ``verify`` is character-for-character equal to an id
    declared on the operator list in this repository's local config file
    (``config/orchestration/operators.json``).

That is the entire claim. Read the following as literal limitations, not caveats:

* it does **NOT authenticate a human**. Nobody proved they are the person behind
  ``human:ops``; a caller merely typed a string that is on a list.
* it does **NOT prove possession of a credential**. There is no password, no
  token, no signature, no challenge, no session, no second factor. The "secret",
  such as it is, is a plaintext id sitting in a committed config file.
* it does **NOT survive anyone who can write the local config**. Anyone (or any
  process) able to edit ``config/orchestration/operators.json`` can name
  themselves an operator and approve plans. Filesystem write access to this repo
  IS approval authority. There is no tamper seal on the declaration and there
  deliberately is none — a digest here would only pretend to add a property the
  file's own mutability contradicts.
* it therefore does **NOT** defend against a compromised workstation, a hostile
  local process, or a malicious code change. It is not a security boundary
  against any attacker who already runs code as this user.

What it *is* good for is the honest job a single-user local workbench actually
needs: making "a decision was taken under an operator id that this installation
declared in advance" a checkable, durable, non-forgeable-by-accident fact, and
making every other actor id — typos, service ids, model-supplied strings, lease
ids, blanks — a hard refusal instead of a silent pass. If this system ever grows
real multi-party authority, this module is the seam to replace; the coordinator
above it needs no change.

Fail-closed, everywhere
-----------------------
There is **no default operator, no wildcard, and no "allow when the list is
empty"**. Every one of the following raises a typed :class:`OperatorIdentityError`
and returns nothing:

* an actor that is not a ``str`` (``None`` / bytes / int / list / …);
* an empty or whitespace-only actor; a near-miss (leading/trailing space, a
  different case) — matching is exact, with no trimming and no case folding;
* an actor prefixed ``lease:`` (see the lease decision below);
* an actor absent from the declaration;
* a missing, unreadable, BOM-carrying, non-UTF-8, non-JSON, wrong-shaped,
  extra-keyed or empty declaration; a duplicate declared id; or a single
  unusable declared row — a bad row is never skipped, the WHOLE declaration is
  refused (no partial trust, mirroring the approval journal's fold).

The declaration is re-read on **every** ``verify`` call (it is a handful of
bytes). The list in force at *decision* time is the authority, so removing an
operator takes effect immediately, without a process restart — and a config that
is deleted or corrupted after start-up refuses the next decision rather than
serving a stale in-memory copy. Construction itself never touches the disk, so a
broken declaration breaks the *decision*, never the wiring.

The lease decision: ``lease:*`` actors are REFUSED here
-------------------------------------------------------
Phase 7 stamps lease-authorized approvals with ``actor_id="lease:<lease_id>"``
(``approval._LEASE_ACTOR_PREFIX``), and ``autonomy.playbooks.orchestrate_lane0_bootstrap``
calls its service port with ``actor=f"lease:{lease_id}"``. Those actors must NOT
verify here, for three reasons:

1. **No production path needs it.** The lease channel is deliberately
   verifier-free: ``register_and_try_lease`` -> ``_admit_under_lease`` calls the
   shared ``_record_terminal_decision`` directly — "the lease IS the standing
   authority", so ``verify`` is never consulted at consume time. Refusing
   ``lease:*`` here costs Lane 0 nothing.
2. **Accepting it would open a real hole.** ``decide(actor="lease:<id>")`` would
   mint a lease-signed ``PlanApproval`` with *no* lease, *no* ``lease_consumed``
   row and *no* envelope drawn — bypassing the validity window, ``max_admissions``
   and the LLM budget cap that make a lease bounded. Worse, ``register_and_try_lease``
   reads back ``existing.actor_id.startswith("lease:")`` and reports
   ``outcome="lease_admitted", lease_id=<suffix>``, so a forged actor would make
   the coordinator report an admission under a lease that never existed.
3. **The verified actors are humans by construction.** ``issue_lease`` and
   ``revoke_lease`` verify the *human* who issues or revokes (stamped
   ``issued_by`` / ``revocation.actor_id``), never a lease. The one place a lease
   id legitimately becomes an actor id is inside the coordinator, past this port.

So ``lease:`` is refused both as a submitted actor and as a *declarable* id — you
cannot smuggle one onto the list either.

Config convention
-----------------
``config/orchestration/operators.json``, located the same way every other
orchestration component locates its config: a module-level constant built from
``Path(__file__).resolve().parents[3] / "config" / "orchestration" / ...`` (cf.
``catalog_runtime._CONFIG_ROOT``, ``presets._CONFIG_ROOT``, ``bootstrap``,
``phase7_registry``, ``lane_catalog``). It is loaded with the same strictness as
``plan_presets.load_preset_registry`` loads ``config/orchestration/presets/*.json``:
UTF-8, byte-order mark rejected, ``extra="forbid"`` strict validation, and a typed
error — never a silent skip — for anything malformed. The allowlist is a
**declaration, not a secret**: it belongs in committed config and must never be
read from, or written to, ``var/secrets.env``. There is no environment-variable
override of the path (an env var that can redirect the authority list would
weaken the one claim this module makes); a caller who needs another location
passes ``allowlist_path`` explicitly.

This module is a **service port, not a contract**: it defines no public
``ContractModel``, registers nothing in any schema registry, and therefore adds
nothing to the cumulative registry chain or its frozen goldens — matching how the
other Phase-9 service ports (``ReplayPointClock``, ``PitReaderRawSource``,
``LiveClientSource``, the ``*RuntimeBindings`` carriers) are classified.
"""
from __future__ import annotations

import codecs
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal

__all__ = [
    "DEFAULT_OPERATOR_ALLOWLIST_PATH",
    "VERIFIED_BY",
    "OperatorIdentityError",
    "OperatorAllowlistError",
    "OperatorNotAllowed",
    "ConfigOperatorVerifier",
    "load_operator_allowlist",
]

#: the house-convention home of the declaration (repo root / config / orchestration).
DEFAULT_OPERATOR_ALLOWLIST_PATH: Path = (
    Path(__file__).resolve().parents[3] / "config" / "orchestration" / "operators.json"
)

#: what a principal produced here was verified *by* — named after the mechanism so a
#: reader of an audit trail is never misled into thinking a human was authenticated.
VERIFIED_BY = "config-operator-allowlist"

#: the actor-id prefix Phase 7 reserves for lease-authorized decisions
#: (``approval._LEASE_ACTOR_PREFIX``). Never a human operator — see the module
#: docstring's lease decision. Compared case-insensitively.
_LEASE_ACTOR_PREFIX = "lease:"


# --------------------------------------------------------------------------- #
# typed refusals (there is no other outcome besides a principal)               #
# --------------------------------------------------------------------------- #
class OperatorIdentityError(Exception):
    """Base: this verifier refused. It never returns a principal on any failure."""


class OperatorAllowlistError(OperatorIdentityError):
    """The declaration itself is unusable — missing, malformed, or empty.

    A configuration fault, not a denial of a particular actor: nobody can be
    verified at all until the declaration is fixed. Distinct from
    :class:`OperatorNotAllowed` so an operator reading the failure can tell "my
    config is broken" from "that id is not on the list".
    """


class OperatorNotAllowed(OperatorIdentityError):
    """The submitted actor is not a declared operator (or is not a usable id).

    Covers a non-string / blank / near-miss actor, a reserved ``lease:`` actor, and
    an id simply absent from the declaration. The message never echoes the whole
    allowlist back to the caller.
    """


# --------------------------------------------------------------------------- #
# the declaration file (private models: a service port defines no contract)    #
# --------------------------------------------------------------------------- #
class _OperatorRow(BaseModel):
    """One declared operator. ``note`` is documentation for humans, never authority."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    actor_id: str
    note: str | None = None


class _OperatorAllowlistFile(BaseModel):
    """The whole declaration: a version tag plus the reviewed operator rows."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["1"]
    operators: list[_OperatorRow]


def _reject_unusable_id(actor_id: str, *, where: str) -> None:
    """Raise unless ``actor_id`` is a usable identity (shared by both sides)."""
    if not actor_id or actor_id != actor_id.strip():
        raise OperatorAllowlistError(
            f"{where}: an operator id must be non-empty with no surrounding "
            f"whitespace (got {actor_id!r})")
    if any(ch.isspace() for ch in actor_id):
        raise OperatorAllowlistError(
            f"{where}: an operator id must contain no whitespace (got {actor_id!r})")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in actor_id):
        raise OperatorAllowlistError(
            f"{where}: an operator id must contain no control characters")
    if "*" in actor_id:
        raise OperatorAllowlistError(
            f"{where}: there is no wildcard operator — {actor_id!r} is not an identity")
    if actor_id.lower().startswith(_LEASE_ACTOR_PREFIX):
        raise OperatorAllowlistError(
            f"{where}: {actor_id!r} is a reserved lease actor id, never a human "
            "operator (the lease channel is verifier-free by design)")


def load_operator_allowlist(path: Path | str | None = None) -> tuple[str, ...]:
    """Load + validate the declared operator ids, or raise :class:`OperatorAllowlistError`.

    Strict, in the same shape as ``plan_presets.load_preset_registry``: UTF-8 with
    no byte-order mark, ``extra="forbid"`` strict validation, every declared id
    checked for usability, duplicates refused, and an empty list refused. There is
    no partial trust — one bad row rejects the whole declaration. The returned
    tuple preserves declaration order and is never empty on success.
    """
    target = Path(path) if path is not None else DEFAULT_OPERATOR_ALLOWLIST_PATH
    try:
        raw = target.read_bytes()
    except FileNotFoundError as exc:
        raise OperatorAllowlistError(
            f"no operator declaration at {target} — approval is disabled until one "
            "is declared (fail closed; there is no default operator)") from exc
    except OSError as exc:
        raise OperatorAllowlistError(
            f"the operator declaration at {target} cannot be read: {exc}") from exc

    if raw.startswith(codecs.BOM_UTF8):
        raise OperatorAllowlistError(
            f"the operator declaration at {target} must be UTF-8 with no byte-order mark")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OperatorAllowlistError(
            f"the operator declaration at {target} is not valid UTF-8: {exc}") from exc
    try:
        doc = _OperatorAllowlistFile.model_validate_json(text)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise OperatorAllowlistError(
            f"the operator declaration at {target} is not a valid operator "
            f"allowlist: {exc}") from exc

    seen: set[str] = set()
    ids: list[str] = []
    for row in doc.operators:
        _reject_unusable_id(row.actor_id, where=f"operator declaration at {target}")
        if row.actor_id in seen:
            raise OperatorAllowlistError(
                f"the operator declaration at {target} declares {row.actor_id!r} twice")
        seen.add(row.actor_id)
        ids.append(row.actor_id)

    if not ids:
        raise OperatorAllowlistError(
            f"the operator declaration at {target} declares no operators — an empty "
            "list means nobody can approve, never everybody (fail closed)")
    return tuple(ids)


# --------------------------------------------------------------------------- #
# the verifier                                                                 #
# --------------------------------------------------------------------------- #
class ConfigOperatorVerifier:
    """The production ``verify(actor) -> AuthenticatedAdminPrincipal`` port.

    Structurally satisfies Phase 3's ``AdminReviewVerifier`` protocol and is what
    :class:`~guanlan_v2.orchestration.approval.PlanApprovalCoordinator` needs for
    ``decide`` / ``issue_lease`` / ``revoke_lease``. The returned principal exposes
    ``.actor`` — the attribute ``approval.py`` actually reads (NOT ``.actor_id``;
    the long-standing test stub in ``test_adapters_api.py`` gets this wrong and is
    only ever used on construction paths, so nothing caught it).

    Read the module docstring before trusting this for anything: it proves list
    membership, not identity.
    """

    def __init__(
        self,
        *,
        allowlist_path: Path | str | None = None,
        verified_by: str = VERIFIED_BY,
    ) -> None:
        self._path = Path(allowlist_path) if allowlist_path is not None \
            else DEFAULT_OPERATOR_ALLOWLIST_PATH
        self._verified_by = verified_by
        # deliberately no eager load and no cache: see the module docstring.

    @property
    def allowlist_path(self) -> Path:
        """The declaration this verifier reads (for diagnostics / console display)."""
        return self._path

    def verify(self, credential: Any) -> AuthenticatedAdminPrincipal:
        """Return a principal for a declared operator id; raise otherwise.

        ``credential`` is the actor id itself — this mechanism has no separate
        secret to present, which is precisely its documented limitation. The
        declaration is re-read here, so the list in force at decision time governs.
        """
        if not isinstance(credential, str):
            raise OperatorNotAllowed(
                "an operator id must be a string; refusing "
                f"{type(credential).__name__} (fail closed)")
        if not credential.strip():
            raise OperatorNotAllowed(
                "an empty operator id is never verified (fail closed)")
        if credential.lower().startswith(_LEASE_ACTOR_PREFIX):
            raise OperatorNotAllowed(
                "a lease actor id is never a human operator: the lease channel "
                "records its own decision without this verifier, so signing a "
                "decision with a lease id here would forge a lease-authorized "
                "approval with no lease and no envelope (fail closed)")

        declared = load_operator_allowlist(self._path)
        if credential not in declared:  # exact match: no trim, no case folding
            raise OperatorNotAllowed(
                f"actor {credential!r} is not a declared operator in {self._path.name} "
                "(fail closed; there is no default operator and no wildcard)")
        return AuthenticatedAdminPrincipal(
            actor=credential, verified_by=self._verified_by)
