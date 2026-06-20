"""Deterministic grounding matcher.

Given a value, a policy, the transcript and call context, decide whether the
value traces back to something real — and return a span proving it.

This layer is intentionally *deterministic-first*: normalize, then exact/fuzzy
match with conservative guards against false positives. A verifier-model
escalation hook is left for the roadmap (see ``ROADMAP.md``); the common cases
never need it.

Design rules that keep this production-safe:

* **No silent over-matching.** Short needles require exact word matches; numbers
  must appear as whole values, never as a digit substring (``"2"`` must not be
  grounded by ``"20"``).
* **Type-correct.** ``date``/``datetime``/``int``/``float``/``bool`` values are
  coerced deterministically before comparison.
* **Always returns a verdict.** Never raises for ordinary input; the decorator
  additionally wraps calls to fail closed on the unexpected.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional, Sequence

from . import normalize as N
from .fuzz import partial_ratio, ratio
from ..policy import Policy
from ..result import GroundingResult, Span
from ..transcript import Transcript, Turn

# Tunables (conservative by default).
_MIN_FUZZY_TOKEN = 0.85  # per-token fuzzy floor for name/text matching
_MIN_TOKEN_LEN_FOR_FUZZY = 4  # shorter tokens must match exactly
_MAX_USER_TURNS_AFTER_READBACK = 3  # how far to look for a confirmation

# Words that count as the caller affirming a read-back.
_AFFIRM = {
    "yes", "yeah", "yep", "yup", "correct", "right", "sure", "ok", "okay",
    "confirmed", "confirm", "exactly", "perfect", "absolutely", "definitely",
    "mhm", "that's right", "thats right", "that is correct", "that's correct",
    "thats correct", "go ahead", "sounds good", "affirmative", "it is",
}
_DENY = {
    "no", "nope", "nah", "wrong", "incorrect", "not right", "that's wrong",
    "thats wrong", "not correct", "negative", "that's not", "thats not",
}
# Pure backchannel/filler turns to skip when looking for a confirmation.
_FILLER = {
    "um", "umm", "uh", "uhh", "hmm", "er", "erm", "well", "so", "like",
    "mm", "uhm", "ah", "oh", "hold on", "one sec", "one second", "let me think",
}


# --------------------------------------------------------------------------- #
# Value coercion + type sniffing
# --------------------------------------------------------------------------- #


def to_text(value: Any) -> str:
    """Deterministically render a value to the string we match against."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


_DATE_LIKE = re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}\s*$|^\s*\d{1,2}[/]\d{1,2}[/]\d{2,4}\s*$")


def _looks_like_date(value: Any, text: str) -> bool:
    if isinstance(value, (date, datetime)):
        return True
    return bool(_DATE_LIKE.match(text)) or N.normalize_date(text) is not None


def _looks_like_phone(value: Any, text: str) -> bool:
    # An int/float is a quantity, not a phone number.
    if isinstance(value, (int, float, bool, Decimal)):
        return False
    return len(N.normalize_phone(text)) >= 7


def _is_number(value: Any, text: str) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    return bool(re.fullmatch(r"[\d.,]*\d[\d.,]*", text.strip()))


# --------------------------------------------------------------------------- #
# Word-level helpers
# --------------------------------------------------------------------------- #


def _wordwise_contains(haystack: str, needle: str) -> bool:
    """True if normalized ``needle`` appears in ``haystack`` on word boundaries."""
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _best_word_score(token: str, words: Sequence[str]) -> float:
    best = 0.0
    for w in words:
        if w == token:
            return 1.0
        if len(token) >= _MIN_TOKEN_LEN_FOR_FUZZY and len(w) >= _MIN_TOKEN_LEN_FOR_FUZZY:
            r = ratio(token, w)
            if r > best:
                best = r
    return best


# --------------------------------------------------------------------------- #
# SPOKEN
# --------------------------------------------------------------------------- #


def check_spoken(value: Any, transcript: Transcript, ctx, threshold: float) -> GroundingResult:
    text = to_text(value)
    if not text:
        return _miss(Policy.SPOKEN, value, "empty value")

    if _looks_like_date(value, text):
        return _spoken_date(value, text, transcript, ctx)
    if _looks_like_phone(value, text):
        return _spoken_phone(value, text, transcript)
    if _is_number(value, text):
        return _spoken_number(value, text, transcript)
    return _spoken_text(value, text, transcript, threshold, Policy.SPOKEN)


def _spoken_text(value, text, transcript, threshold, policy, turns=None) -> GroundingResult:
    needle = N.normalize_text(text)
    if not needle:
        return _miss(policy, value, "empty after normalization")
    tokens = needle.split()
    turns = turns if turns is not None else transcript.user_turns()

    best_score, best_turn = 0.0, None
    for turn in turns:
        hay = N.normalize_text(turn.text)
        if not hay:
            continue

        # 1) exact phrase on word boundaries — strongest signal.
        if _wordwise_contains(hay, needle):
            return _hit(policy, value, needle, 0.99, "exact match in caller speech", turn)

        # 2) every token present (exact word, or fuzzy for long-enough tokens).
        words = hay.split()
        scores = [_best_word_score(tok, words) for tok in tokens]
        if scores and all(s >= _MIN_FUZZY_TOKEN for s in scores):
            conf = sum(scores) / len(scores)
            if conf >= threshold and conf > best_score:
                best_score, best_turn = conf, turn

    if best_turn is not None and best_score >= threshold:
        return _hit(policy, value, needle, best_score, "fuzzy match in caller speech", best_turn)
    return GroundingResult(
        grounded=False, confidence=best_score, policy=policy.value, value=value,
        normalized=needle, reason="not found in caller speech",
        span=Span.from_turn(best_turn) if best_turn else None,
    )


def _spoken_date(value, text, transcript, ctx) -> GroundingResult:
    now = getattr(ctx, "now", None)
    iso = N.normalize_date(text, now)
    if not iso:
        return _miss(Policy.SPOKEN, value, "value is not a parseable date")
    for turn in transcript.user_turns():
        if N.normalize_date(turn.text, now) == iso:
            return _hit(Policy.SPOKEN, value, iso, 0.98, "date spoken by caller", turn)
        if N.date_components_present(iso, turn.text):
            return _hit(Policy.SPOKEN, value, iso, 0.9, "date components spoken by caller", turn)
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.SPOKEN.value, value=value,
        normalized=iso, reason="date not found in caller speech",
    )


def _spoken_phone(value, text, transcript) -> GroundingResult:
    for turn in transcript.user_turns():
        if N.phones_match(text, turn.text):
            return _hit(
                Policy.SPOKEN, value, N.normalize_phone(text), 0.97,
                "phone digits spoken by caller", turn,
            )
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.SPOKEN.value, value=value,
        normalized=N.normalize_phone(text), reason="phone not found in caller speech",
    )


def _spoken_number(value, text, transcript) -> GroundingResult:
    want = _to_number(text)
    if want is None:
        return _miss(Policy.SPOKEN, value, "value is not a clean number")
    for turn in transcript.user_turns():
        if want in N.find_numbers(turn.text):  # whole-value match only
            return _hit(Policy.SPOKEN, value, want, 0.95, "number spoken by caller", turn)
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.SPOKEN.value, value=value,
        normalized=want, reason="number not found in caller speech",
    )


def _to_number(text: str) -> Optional[int]:
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", text)
    cleaned = cleaned.split(".")[0]  # integer part only
    digits = N.digits_only(cleaned)
    return int(digits) if digits else None


# --------------------------------------------------------------------------- #
# CONFIRMED
# --------------------------------------------------------------------------- #


def check_confirmed(value: Any, transcript: Transcript, ctx, threshold: float) -> GroundingResult:
    text = to_text(value)
    if not text:
        return _miss(Policy.CONFIRMED, value, "empty value")

    turns = transcript.turns
    for i, turn in enumerate(turns):
        if turn.speaker != "agent" or not _value_in_turn_text(value, text, turn.text, ctx):
            continue
        # Agent read the value back; inspect the next few caller turns.
        seen = 0
        for follow in turns[i + 1 :]:
            if follow.speaker != "user":
                continue
            if _is_filler(follow.text):
                continue
            verdict = _affirmation(follow.text)
            if verdict is True:
                return _hit(
                    Policy.CONFIRMED, value, N.normalize_text(text), 0.95,
                    "agent read back and caller confirmed", follow,
                )
            if verdict is False:
                return GroundingResult(
                    grounded=False, confidence=0.0, policy=Policy.CONFIRMED.value,
                    value=value, reason="caller rejected the read-back",
                    span=Span.from_turn(follow),
                )
            seen += 1
            if seen >= _MAX_USER_TURNS_AFTER_READBACK:
                break
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.CONFIRMED.value, value=value,
        reason="no read-back + confirmation found",
    )


def _value_in_turn_text(value, text, turn_text, ctx) -> bool:
    if _looks_like_date(value, text):
        iso = N.normalize_date(text, getattr(ctx, "now", None))
        return bool(iso) and (
            N.normalize_date(turn_text, getattr(ctx, "now", None)) == iso
            or N.date_components_present(iso, turn_text)
        )
    if _looks_like_phone(value, text):
        return N.phones_match(text, turn_text)
    if _is_number(value, text):
        want = _to_number(text)
        return want is not None and want in N.find_numbers(turn_text)
    needle = N.normalize_text(text)
    hay = N.normalize_text(turn_text)
    return bool(needle) and (_wordwise_contains(hay, needle) or partial_ratio(needle, hay) >= 0.9)


def _is_filler(text: str) -> bool:
    norm = N.normalize_text(text)
    if not norm:
        return True
    return all(tok in _FILLER for tok in norm.split())


def _affirmation(text: str) -> Optional[bool]:
    norm = N.normalize_text(text)
    if not norm:
        return None
    for phrase in _DENY:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm):
            return False
    for phrase in _AFFIRM:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm):
            return True
    return None


# --------------------------------------------------------------------------- #
# CALLER_ID
# --------------------------------------------------------------------------- #


def check_caller_id(value: Any, transcript: Transcript, ctx, threshold: float) -> GroundingResult:
    meta = getattr(ctx, "metadata", {}) or {}
    cid = meta.get("caller_id") or meta.get("ani") or meta.get("from")
    if not cid:
        return GroundingResult(
            grounded=False, confidence=0.0, policy=Policy.CALLER_ID.value, value=value,
            reason="no caller_id present in call metadata",
        )
    text = to_text(value)
    if N.phones_match(text, str(cid)):
        return GroundingResult(
            grounded=True, confidence=1.0, policy=Policy.CALLER_ID.value, value=value,
            normalized=N.normalize_phone(text), reason="matches trusted caller_id metadata",
        )
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.CALLER_ID.value, value=value,
        reason="value does not match caller_id metadata",
    )


# --------------------------------------------------------------------------- #
# INFERABLE
# --------------------------------------------------------------------------- #


def check_inferable(value: Any, transcript: Transcript, ctx, threshold: float) -> GroundingResult:
    text = to_text(value)
    if not text:
        return _miss(Policy.INFERABLE, value, "empty value")

    now = getattr(ctx, "now", None) or date.today()
    iso = N.normalize_date(text, now)
    if iso:
        for turn in transcript.user_turns():
            if N.normalize_date(turn.text, now) == iso:
                return _hit(
                    Policy.INFERABLE, value, iso, 0.9,
                    "resolved from caller's relative date + clock", turn,
                )
    spoken = check_spoken(value, transcript, ctx, threshold)
    if spoken.grounded:
        spoken.policy = Policy.INFERABLE.value
        spoken.reason = "inferable: " + spoken.reason
        return spoken
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.INFERABLE.value, value=value,
        normalized=iso, reason="not inferable from context or speech",
    )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

_CHECKERS = {
    Policy.SPOKEN: check_spoken,
    Policy.CONFIRMED: check_confirmed,
    Policy.CALLER_ID: check_caller_id,
    Policy.INFERABLE: check_inferable,
}


def check(value: Any, policy: Policy, transcript: Transcript, ctx, threshold: float) -> GroundingResult:
    checker = _CHECKERS.get(policy)
    if checker is None:
        raise ValueError(f"unknown policy: {policy!r}")
    return checker(value, transcript, ctx, threshold)


# --------------------------------------------------------------------------- #
# Small constructors
# --------------------------------------------------------------------------- #


def _hit(policy: Policy, value, normalized, conf: float, reason: str, turn: Turn) -> GroundingResult:
    return GroundingResult(
        grounded=True, confidence=conf, policy=policy.value, value=value,
        normalized=normalized, reason=reason, span=Span.from_turn(turn),
    )


def _miss(policy: Policy, value, reason: str) -> GroundingResult:
    return GroundingResult(
        grounded=False, confidence=0.0, policy=policy.value, value=value, reason=reason
    )
