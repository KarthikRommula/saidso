"""The provenance ledger: proof that every committed argument was grounded."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .result import ArgFinding


@dataclass
class Attestation:
    """A receipt: this action ran, and here is what grounded every argument.

    ``status`` is ``"ok"`` for a committed write, ``"shadow_block"`` for a decision
    recorded under non-enforcing (shadow) mode, or any caller-supplied value via
    :func:`attest_action`. ``metadata`` carries free-form audit detail (e.g. the
    destination of a ``transfer_to_human``).
    """

    action: str
    ts: float
    call_id: str | None
    args: list[ArgFinding] = field(default_factory=list)
    status: str = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ts": self.ts,
            "call_id": self.call_id,
            "status": self.status,
            "metadata": self.metadata,
            "args": [
                {
                    "arg": f.name,
                    "policy": f.result.policy,
                    "value": f.result.value,
                    "confidence": round(f.result.confidence, 4),
                    "code": f.result.code,
                    "span": f.result.span.to_dict() if f.result.span else None,
                }
                for f in self.args
            ],
        }


class AttestationLog:
    """Collects attestations in memory and (optionally) appends them as JSONL.

    Pass ``path=`` to persist an audit trail; otherwise records are kept in
    memory and reachable via :attr:`records`.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._records: list[Attestation] = []
        self._lock = threading.Lock()

    def record(self, attestation: Attestation) -> Attestation:
        with self._lock:
            self._records.append(attestation)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(attestation.to_dict()) + "\n")
        return attestation

    def build(
        self,
        action: str,
        findings: list[ArgFinding],
        call_id: str | None = None,
        *,
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> Attestation:
        return self.record(
            Attestation(
                action=action,
                ts=time.time(),
                call_id=call_id,
                args=list(findings),
                status=status,
                metadata=metadata or {},
            )
        )

    def has(
        self,
        action: str,
        *,
        status: str = "ok",
        call_id: str | None = None,
        since_ts: float | None = None,
    ) -> bool:
        """True if an attestation for ``action`` with ``status`` is on file.

        Filters by ``call_id`` and/or a ``since_ts`` floor when given. Used by
        :func:`saidso.render_spoken`'s ``requires_write`` and by
        :func:`saidso.reconcile_turn` to confirm a spoken completion claim is
        backed by a real, successful action this call/turn.
        """
        with self._lock:
            for a in self._records:
                if a.action != action or a.status != status:
                    continue
                if call_id is not None and a.call_id != call_id:
                    continue
                if since_ts is not None and a.ts < since_ts:
                    continue
                return True
        return False

    @property
    def records(self) -> list[Attestation]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def export(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._records]


@dataclass(frozen=True)
class AttestedWrite:
    """Declares that a spoken completion claim must be backed by a successful write.

    Built by :func:`attested` and passed to :func:`saidso.render_spoken` as
    ``requires_write=`` so the rendered line asserts not just grounded *nouns* but
    that the named action actually succeeded this call (the *verb*).
    """

    action: str
    status: str = "ok"


def attested(action: str, status: str = "ok") -> AttestedWrite:
    """Require that ``action`` succeeded (reconciled against the AttestationLog).

    Example::

        render_spoken(
            "You have an appointment with {doctor} at {time}.",
            attestations=attestation_log,
            requires_write=attested("book_appointment", status="ok"),
            doctor=fact(...), time=fact(...),
        )
        # -> UnattestedAction if book_appointment did not succeed this call.
    """
    return AttestedWrite(action=action, status=status)


def attest_action(
    action: str,
    *,
    status: str = "ok",
    metadata: dict[str, Any] | None = None,
    call_id: str | None = None,
    ledger: AttestationLog | None = None,
) -> Attestation | None:
    """Record a consequential, *argument-less* action in the AttestationLog.

    ``end_call`` / ``transfer_to_human`` take no grounded arguments, so the firewall
    never records them. Call this at the action boundary to complete the audit trail
    and let :func:`saidso.reconcile_turn` and ``requires_write`` catch
    "I'm transferring you now" / "goodbye" claims with no matching action.

    Writes to ``ledger`` if given, else the active ``call_context``'s ledger. Returns
    the recorded :class:`Attestation`, or ``None`` if no ledger is available.
    """
    if ledger is None:
        from .context import get_context

        ctx = get_context()
        ledger = getattr(ctx, "ledger", None) if ctx else None
        if call_id is None and ctx is not None:
            call_id = ctx.call_id
    if ledger is None:
        return None
    return ledger.build(action, [], call_id=call_id, status=status, metadata=metadata)
