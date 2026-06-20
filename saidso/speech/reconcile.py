"""Turn-level completion-claim reconciler — the reactive backstop for native audio.

A native-audio model speaks directly, so saidso can't gate its mouth; the best it can
do post-utterance is **detect** a spoken completion claim that no successful action
backs, and let the app drive a correction. :func:`saidso.find_ungrounded_names` only
catches titled names ("Dr. X"); this catches *action-completion* hallucinations that
carry no name and no fact — "you're all set", "you're registered", "okay, you're
booked" — by reconciling the agent's turn against the AttestationLog.

It is heuristic (English phrase patterns over ASR text) and reactive (the words were
already spoken). Pair it with the deterministic write side; this only reduces the
residual "claimed a completed action that never ran" gap. The default
:data:`COMPLETION_CLAIMS` is meant to be extended/replaced per deployment.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("saidso")


@dataclass(frozen=True)
class ClaimPattern:
    """A spoken completion claim and the action(s) that would back it.

    ``pattern`` is matched case-insensitively against the agent's turn. The claim is
    *backed* if **any** action in ``actions`` has a successful attestation this turn.
    """

    label: str
    pattern: re.Pattern[str]
    actions: tuple[str, ...]


def _claim(label: str, regex: str, *actions: str) -> ClaimPattern:
    return ClaimPattern(label, re.compile(regex, re.IGNORECASE), tuple(actions))


# Default claim vocabulary (clinic-receptionist shaped; extend per deployment).
COMPLETION_CLAIMS: tuple[ClaimPattern, ...] = (
    _claim("registered",
           r"\b(you(?:'re| are)\s+(?:all set|registered|signed up)"
           r"|got you (?:registered|set up)|you(?:'re| are) in the system)\b",
           "register_patient"),
    _claim("booked",
           r"\b(booked|scheduled|you(?:'ve| have)\s+(?:an?\s+)?appointment"
           r"|i(?:'m| am)\s+scheduling|all set for|you(?:'re| are)\s+booked)\b",
           "book_appointment"),
    _claim("transferred",
           r"\b(transferring you|i(?:'ll| will)\s+transfer|connecting you"
           r"|transferred you|putting you through)\b",
           "transfer_to_human"),
    _claim("ended",
           r"\b(good\s?bye|have a (?:great|good|nice) (?:day|one)|hanging up now)\b",
           "end_call"),
)


@dataclass
class UnbackedClaim:
    """A spoken completion claim with no successful action backing it this turn."""

    claim: str
    expected_action: tuple[str, ...]
    matched_text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "expected_action": list(self.expected_action),
            "matched_text": self.matched_text,
            "metadata": self.metadata,
        }


def _backed(attestations: Any, actions: Sequence[str], call_id, since_ts) -> bool:
    """True if any of ``actions`` has a successful attestation in ``attestations``.

    Accepts an :class:`~saidso.AttestationLog` (uses ``.has``) or a plain iterable of
    attestation dicts (e.g. ``log.export()``).
    """
    has = getattr(attestations, "has", None)
    if callable(has):
        return any(
            has(a, status="ok", call_id=call_id, since_ts=since_ts) for a in actions
        )
    if isinstance(attestations, Iterable):
        wanted = set(actions)
        for rec in attestations:
            if not isinstance(rec, dict):
                continue
            if rec.get("status", "ok") != "ok" or rec.get("action") not in wanted:
                continue
            if call_id is not None and rec.get("call_id") != call_id:
                continue
            if since_ts is not None and rec.get("ts", 0) < since_ts:
                continue
            return True
    return False


def reconcile_turn(
    agent_text: str,
    *,
    attestations: Any,
    claim_patterns: Sequence[ClaimPattern] = COMPLETION_CLAIMS,
    call_id: str | None = None,
    since_ts: float | None = None,
) -> list[UnbackedClaim]:
    """Return the completion claims in ``agent_text`` that no successful action backs.

    Reconciles the agent's spoken turn against ``attestations`` (an
    :class:`~saidso.AttestationLog` or an iterable of attestation dicts). For every
    matched claim pattern whose backing action(s) have no successful attestation this
    turn, an :class:`UnbackedClaim` is returned — so the app can inject a spoken
    correction instead of leaving a fabricated "you're all set" standing.

    Scope claims to the current turn with ``call_id`` / ``since_ts`` (e.g. pass the
    timestamp the agent turn began). Makes a bespoke regex watchdog deletable.
    """
    if not agent_text:
        return []
    out: list[UnbackedClaim] = []
    for cp in claim_patterns:
        m = cp.pattern.search(agent_text)
        if not m:
            continue
        if _backed(attestations, cp.actions, call_id, since_ts):
            continue
        out.append(UnbackedClaim(
            claim=cp.label, expected_action=cp.actions, matched_text=m.group(0),
        ))
    if out:
        logger.info(
            "unbacked completion claims: %s", [c.claim for c in out],
            extra={"saidso_event": "block", "saidso_action": "reconcile_turn",
                   "saidso_args": [c.claim for c in out]},
        )
    return out
