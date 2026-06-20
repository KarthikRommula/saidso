"""Return contracts: grounding verdicts, the steer-back message, attestations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReasonCode(str, Enum):
    """Machine-readable reason on every grounding decision (pass *and* block).

    Surfaced on :class:`GroundingResult.code`, in :meth:`SteerBack.to_dict`, and in
    the attestation record so observability (PostHog, dashboards) can route on a
    stable code instead of parsing prose. ``OK_*`` codes are passes; the rest block.
    """

    OK_EXACT = "ok_exact"
    OK_FUZZY = "ok_fuzzy"
    OK_NORMALIZED = "ok_normalized"
    OK_SINGLE = "ok_single"
    OK_CONFIRMED = "ok_confirmed"
    OK_CALLER_ID = "ok_caller_id"
    OK_INFERRED = "ok_inferred"
    NO_VALUE = "no_value"
    NOT_IN_TRANSCRIPT = "not_in_transcript"
    BELOW_THRESHOLD = "below_threshold"
    WRONG_TOOL_SOURCE = "wrong_tool_source"
    NORMALIZE_MISMATCH = "normalize_mismatch"
    RETRACTED = "retracted"
    REJECTED_READBACK = "rejected_readback"
    NO_CONFIRMATION = "no_confirmation"
    AMBIGUOUS = "ambiguous"
    DUPLICATE = "duplicate"
    STALE_PROVENANCE = "stale_provenance"
    CHECK_ERROR = "check_error"


@dataclass
class Span:
    """A pointer into the transcript that grounds (or fails to ground) a value."""

    turn_id: int
    ts: float
    speaker: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "ts": self.ts,
            "speaker": self.speaker,
            "text": self.text,
        }

    @classmethod
    def from_turn(cls, turn) -> Span:
        return cls(turn_id=turn.id, ts=turn.ts, speaker=turn.speaker, text=turn.text)


@dataclass
class GroundingResult:
    """Verdict for a single argument against a single policy."""

    grounded: bool
    confidence: float
    policy: str
    value: Any
    reason: str = ""
    normalized: Any = None
    span: Span | None = None
    code: str = ""  # machine-readable ReasonCode (set on pass and block)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grounded": self.grounded,
            "confidence": round(self.confidence, 4),
            "policy": self.policy,
            "value": self.value,
            "normalized": self.normalized,
            "reason": self.reason,
            "code": self.code,
            "span": self.span.to_dict() if self.span else None,
        }


@dataclass
class ArgFinding:
    """A per-argument finding attached to an action's outcome."""

    name: str
    result: GroundingResult

    def to_dict(self) -> dict[str, Any]:
        return {"arg": self.name, **self.result.to_dict()}


# Human-friendly re-ask phrasing for common argument names.
_REASK_PHRASES = {
    "name": "your name",
    "full_name": "your full name",
    "dob": "your date of birth",
    "date_of_birth": "your date of birth",
    "birthday": "your date of birth",
    "phone": "your phone number",
    "email": "your email address",
    "address": "your address",
    "amount": "the amount",
    "account": "which account",
    "visit_date": "the date you'd like to come in",
    "appointment_date": "the appointment date",
    "consent": "your confirmation",
}


def _phrase_for(arg: str) -> str:
    return _REASK_PHRASES.get(arg, arg.replace("_", " "))


@dataclass
class SteerBack:
    """Returned instead of running the action when grounding fails.

    This is *not* a dead error. Hand ``message`` back to the agent as the tool
    result and it will re-ask the caller in-conversation, then try again.
    """

    action: str
    blocked: bool = True
    failed: list[ArgFinding] = field(default_factory=list)
    grounded: list[ArgFinding] = field(default_factory=list)
    message: str = ""
    style: str = "default"  # "default" (developer-facing) | "spoken" (caller-facing)
    code: str = ""  # machine-readable ReasonCode for the block (routing/observability)

    def __post_init__(self) -> None:
        if not self.message:
            self.message = (
                self._build_spoken_message()
                if self.style == "spoken"
                else self._build_message()
            )

    def _ask_phrase(self) -> str:
        phrases = [_phrase_for(f.name) for f in self.failed]
        if not phrases:
            return ""
        if len(phrases) == 1:
            return phrases[0]
        if len(phrases) == 2:
            return f"{phrases[0]} and {phrases[1]}"
        return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"

    def _build_message(self) -> str:
        ask = self._ask_phrase()
        if not ask:
            return f"Could not run {self.action}: an argument was not grounded."
        return (
            f"I don't have {ask} from what the caller said. "
            f"Ask the caller for {ask}, then try again. "
            f"Do not guess or fill in placeholder values."
        )

    def _build_spoken_message(self) -> str:
        """Caller-facing re-ask: no tool/id/internal vocabulary, safe to say aloud."""
        ask = self._ask_phrase()
        if not ask:
            return "Sorry, could you say that again?"
        return f"Sorry, could you give me {ask} again?"

    # -- adapters -------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "blocked": self.blocked,
            "message": self.message,
            "code": self.code,
            "failed": [f.to_dict() for f in self.failed],
            "grounded": [f.to_dict() for f in self.grounded],
        }

    def to_tool_message(self) -> str:
        """String to return as the tool-call result so the agent self-corrects."""
        return self.message

    def __bool__(self) -> bool:  # truthy-but-blocked guard for callers
        return False
