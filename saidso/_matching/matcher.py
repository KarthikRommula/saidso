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
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..policy import Policy
from ..result import GroundingResult, ReasonCode, Span
from ..transcript import Transcript, Turn
from . import normalize as N
from .fuzz import partial_ratio, ratio
from .locale import EN, get_locale


def _ctx_locale(ctx):
    """The call's :class:`Locale` from ``ctx.metadata['locale']`` (None when English).

    Returning ``None`` for English keeps the English matching path byte-for-byte
    identical to before locale support existed.
    """
    meta = getattr(ctx, "metadata", None) or {}
    loc = get_locale(meta.get("locale"))
    return None if loc is EN else loc

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


def _best_word_score(token: str, words: Sequence[str], phonetic: bool = False) -> float:
    best = 0.0
    tcode = N.soundex(token) if phonetic else ""
    for w in words:
        if w == token:
            return 1.0
        # Phonetic mode lets short near-homophones ("mail"~"male") match by Soundex,
        # which the length-gated fuzzy path below would otherwise reject.
        if phonetic and tcode and N.soundex(w) == tcode:
            best = max(best, 0.95)
            continue
        if len(token) >= _MIN_TOKEN_LEN_FOR_FUZZY and len(w) >= _MIN_TOKEN_LEN_FOR_FUZZY:
            r = ratio(token, w)
            if r > best:
                best = r
    return best


# --------------------------------------------------------------------------- #
# Supersession / retraction guard
# --------------------------------------------------------------------------- #
#
# A value may *appear* in caller speech and yet have been explicitly taken back
# ("my OLD number was 555-1234 but use 555-9999", "my name is NOT John"). The
# deterministic matchers below would otherwise ground the retracted value just
# because its characters are present. This guard locates the value's mention(s)
# inside the turn and refuses any mention that is either:
#
#   * preceded by a retraction cue ("old", "previous", "not", "instead of"), or
#   * followed — across a correction pivot ("but", "i mean") — by a competing
#     value of the *same kind*.
#
# It is intentionally conservative: it only *removes* false grounds, so when it
# is unsure it leaves the mention live (the threshold checks still apply). It is
# a heuristic for common self-corrections, not a semantic intent model; an
# ambiguous correction can still slip through (see ROADMAP's verifier hook).

# Clause boundaries between a possibly-retracted value and its replacement:
# explicit correction pivots, plus clause punctuation (commas / sentence ends).
# Punctuation only splits when followed by space/end, so it never breaks a
# grouped number ("1,234") or a dotted value ("555.1234"). Note: "instead" /
# "rather" are NOT pivots — "instead of X" drops X, so they are retraction
# phrases below, not replacement markers.
_PIVOTS = r"\b(?:but|however|i mean|scratch that|no wait|never\s?mind)\b"
_CLAUSE_SPLIT_RE = re.compile(_PIVOTS + r"|[,;.!?]+(?:\s+|$)", re.IGNORECASE)
# Pivot-only split for values that naturally span punctuation — a date is
# spoken as "January first, nineteen ninety", so commas must NOT break it.
_PIVOT_ONLY_RE = re.compile(_PIVOTS, re.IGNORECASE)
# Cues that, sitting just before a value, retract *that* value.
_RETRACT_WORDS = {
    "old", "previous", "former", "not", "no", "isnt", "wasnt", "arent",
    "werent", "wrong", "incorrect", "dont", "doesnt",
}
_RETRACT_PHRASES = (
    "used to be", "used to", "no longer", "instead of", "rather than",
    "get rid of",
)
# A *sentential* rejection ("no") rejects the PRIOR turn (often the agent's wrong
# read-back), not the value the caller then asserts. Distinct from value-negation
# ("not John"), which does retract the value.
_SENTENTIAL_NO = {"no", "nope", "nah"}
# "no, it's X" / "no its X" / "no that's X" / "no actually X" / "no my <field> is X":
# a leading rejection introducing a CORRECTED value. The asserted value stays live.
# Written for NORMALIZED text (lowercase, punctuation -> spaces, so "it's" -> "it s").
_CORRECTION_RE = re.compile(
    r"\b(?:no|nope|nah)\s+"
    r"(?:it\s+s|it\s+is|its|that\s+s|that\s+is|thats|actually|really|"
    r"the\b|my\b.*?\bis)\b"
)


def _is_correction_intro(norm: str) -> bool:
    """True if ``norm`` is "no, it's X"-style: a rejection asserting a new value.

    ``norm`` must be :func:`normalize_text` output (apostrophes already folded to
    spaces, so "it's"/"that's" arrive as "it s"/"that s").
    """
    return _CORRECTION_RE.search(norm) is not None


def _clauses(text: str, splitter: re.Pattern[str] = _CLAUSE_SPLIT_RE) -> list[str]:
    """Split a turn into ordered clauses at correction pivots / clause breaks."""
    parts = splitter.split(text or "")
    return [p for p in (s.strip() for s in parts) if p]


def _retracted_before(clause: str, anchor: re.Pattern[str]) -> bool:
    """True if a retraction cue sits in the few words just before the value."""
    norm = N.normalize_text(clause)
    m = anchor.search(norm)
    if not m:
        return False
    prev = norm[: m.start()].split()[-3:]  # only the adjacent words count
    window = " ".join(prev)
    if any(ph in window for ph in _RETRACT_PHRASES):
        return True
    triggers = {w for w in prev if w in _RETRACT_WORDS}
    if not triggers:
        return False
    # "no, it's X": the leading "no" rejects the prior turn; X is the asserted
    # correction, not retracted. A value-negation ("not X") still retracts.
    return not (triggers <= _SENTENTIAL_NO and _is_correction_intro(norm))


def _clause_retracts(clause: str) -> bool:
    """Clause-level cue (used where the value cannot be character-anchored)."""
    norm = N.normalize_text(clause)
    if any(ph in norm for ph in _RETRACT_PHRASES):
        return True
    triggers = set(norm.split()) & _RETRACT_WORDS
    if not triggers:
        return False
    return not (triggers <= _SENTENTIAL_NO and _is_correction_intro(norm))


def _turn_has_retraction(turn_text: str) -> bool:
    """True if the turn retracts the value (correction-aware, whole-turn).

    The liveness gate for the spelled-name / phonetic normalizers, which match a
    transformed form the needle isn't literally present in. Evaluated over the whole
    turn so "no, it's <correction>" — where a lone "no" lands in its own clause — is
    recognized as a correction rather than a retraction.
    """
    return _clause_retracts(turn_text)


def _has_live_mention(text, contains, competing, retracted, splitter=_CLAUSE_SPLIT_RE) -> bool:
    """True if some clause mentions the value without being retracted/superseded.

    ``contains(clause)`` -> value present; ``competing(clause)`` -> a different
    same-kind value present; ``retracted(clause)`` -> the value is taken back.
    """
    clauses = _clauses(text, splitter)
    present = [i for i, c in enumerate(clauses) if contains(c)]
    if not present:
        return False
    for i in present:
        if retracted(clauses[i]):
            continue
        if any(competing(clauses[j]) for j in range(i + 1, len(clauses))):
            continue
        return True
    return False


def _text_is_live(
    needle: str, turn_text: str, spelled: bool = False, phonetic: bool = False
) -> bool:
    anchor = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")

    def contains(c: str) -> bool:
        norm = N.normalize_text(c)
        if _wordwise_contains(norm, needle):
            return True
        if spelled and _wordwise_contains(N.assemble_spelled(c), needle):
            return True
        if phonetic:
            # The matched form is a homophone, not the needle's literal text, so the
            # mention is "present" when every needle token has a Soundex match here.
            cwords = norm.split()
            ncodes = [N.soundex(t) for t in needle.split()]
            return bool(ncodes) and all(
                code and any(N.soundex(w) == code for w in cwords) for code in ncodes
            )
        return False

    return _has_live_mention(
        turn_text,
        contains=contains,
        competing=lambda c: False,  # a competing free-text name can't be told apart safely
        retracted=lambda c: _retracted_before(c, anchor),
    )


def _date_is_live(iso: str, turn_text: str, now, locale=None) -> bool:
    def contains(c):
        return (N.normalize_date(c, now, locale) == iso
                or N.date_components_present(iso, c, locale))

    def competing(c):
        other = N.normalize_date(c, now, locale)
        return other is not None and other != iso

    return _has_live_mention(
        turn_text, contains, competing, _clause_retracts, splitter=_PIVOT_ONLY_RE
    )


def _phone_is_live(value_text: str, turn_text: str) -> bool:
    def competing(c):
        return len(N.normalize_phone(c)) >= 7 and not N.phones_match(value_text, c)

    return _has_live_mention(
        turn_text,
        contains=lambda c: N.phones_match(value_text, c),
        competing=competing,
        retracted=_clause_retracts,
    )


def _number_is_live(want: int, turn_text: str) -> bool:
    anchor = re.compile(rf"\b{want}\b")
    return _has_live_mention(
        turn_text,
        contains=lambda c: want in N.find_numbers(c),
        competing=lambda c: bool(N.find_numbers(c) - {want}),
        retracted=lambda c: _retracted_before(c, anchor),
    )


# --------------------------------------------------------------------------- #
# SPOKEN
# --------------------------------------------------------------------------- #


def check_spoken(
    value: Any, transcript: Transcript, ctx, threshold: float, normalize: str | None = None
) -> GroundingResult:
    text = to_text(value)
    if not text:
        return _miss(Policy.SPOKEN, value, "empty value", ReasonCode.NO_VALUE.value)

    # An explicit normalizer pins the strategy; otherwise auto-sniff by value shape.
    if normalize == "spoken-date":
        return _spoken_date(value, text, transcript, ctx)
    if normalize in ("spelled-name", "phonetic"):
        return _spoken_text(
            value, text, transcript, threshold, Policy.SPOKEN, normalize=normalize
        )

    if _looks_like_date(value, text):
        return _spoken_date(value, text, transcript, ctx)
    if _looks_like_phone(value, text):
        return _spoken_phone(value, text, transcript)
    if _is_number(value, text):
        return _spoken_number(value, text, transcript)
    return _spoken_text(value, text, transcript, threshold, Policy.SPOKEN)


def _haystacks(turn_text: str, spelled: bool) -> list[str]:
    """Normalized forms of a turn to match against (adds a spelled-letter assembly)."""
    base = N.normalize_text(turn_text)
    if spelled:
        asm = N.assemble_spelled(turn_text)
        if asm != base:
            return [base, asm]
    return [base]


def _spoken_text(
    value, text, transcript, threshold, policy, turns=None, normalize=None
) -> GroundingResult:
    needle = N.normalize_text(text)
    if not needle:
        return _miss(policy, value, "empty after normalization", ReasonCode.NO_VALUE.value)
    tokens = needle.split()
    turns = turns if turns is not None else transcript.user_turns()
    spelled = normalize == "spelled-name"
    phonetic = normalize == "phonetic"
    # The spelled/phonetic normalizers match a *transformed* form (assembled letters,
    # a homophone), so the needle is rarely present literally — the strict
    # literal-presence liveness gate would wrongly drop them. For these opt-in modes
    # we only reject on an explicit retraction cue in the turn.
    relaxed = spelled or phonetic

    def _is_live(turn_text: str) -> bool:
        if relaxed:
            return not _turn_has_retraction(turn_text)
        return _text_is_live(needle, turn_text)

    best_score, best_turn = 0.0, None
    retracted_seen = False
    for turn in turns:
        for hay in _haystacks(turn.text, spelled):
            if not hay:
                continue

            # 1) exact phrase on word boundaries — strongest signal.
            if _wordwise_contains(hay, needle):
                if _is_live(turn.text):
                    return _hit(policy, value, needle, 0.99,
                                "exact match in caller speech", turn,
                                ReasonCode.OK_EXACT.value)
                retracted_seen = True

            # 2) every token present (exact word, fuzzy/phonetic for eligible tokens).
            words = hay.split()
            scores = [_best_word_score(tok, words, phonetic=phonetic) for tok in tokens]
            if scores and all(s >= _MIN_FUZZY_TOKEN for s in scores):
                conf = sum(scores) / len(scores)
                if conf >= threshold and conf > best_score and _is_live(turn.text):
                    best_score, best_turn = conf, turn

    if best_turn is not None and best_score >= threshold:
        return _hit(policy, value, needle, best_score,
                    "fuzzy match in caller speech", best_turn, ReasonCode.OK_FUZZY.value)
    if retracted_seen:
        code = ReasonCode.RETRACTED.value
    elif best_score > 0:
        code = ReasonCode.BELOW_THRESHOLD.value
    else:
        code = ReasonCode.NOT_IN_TRANSCRIPT.value
    return GroundingResult(
        grounded=False, confidence=best_score, policy=policy.value, value=value,
        normalized=needle, reason="not found in caller speech", code=code,
        span=Span.from_turn(best_turn) if best_turn else None,
    )


def _spoken_date(value, text, transcript, ctx) -> GroundingResult:
    now = getattr(ctx, "now", None)
    locale = _ctx_locale(ctx)
    iso = N.normalize_date(text, now, locale)
    if not iso:
        return _miss(Policy.SPOKEN, value, "value is not a parseable date",
                     ReasonCode.NORMALIZE_MISMATCH.value)
    for turn in transcript.user_turns():
        if not _date_is_live(iso, turn.text, now, locale):
            continue
        if N.normalize_date(turn.text, now, locale) == iso:
            return _hit(Policy.SPOKEN, value, iso, 0.98, "date spoken by caller", turn,
                        ReasonCode.OK_EXACT.value)
        if N.date_components_present(iso, turn.text, locale):
            return _hit(Policy.SPOKEN, value, iso, 0.9, "date components spoken by caller",
                        turn, ReasonCode.OK_FUZZY.value)
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.SPOKEN.value, value=value,
        normalized=iso, reason="date not found in caller speech",
        code=ReasonCode.NOT_IN_TRANSCRIPT.value,
    )


def _spoken_phone(value, text, transcript) -> GroundingResult:
    for turn in transcript.user_turns():
        if N.phones_match(text, turn.text) and _phone_is_live(text, turn.text):
            return _hit(
                Policy.SPOKEN, value, N.normalize_phone(text), 0.97,
                "phone digits spoken by caller", turn, ReasonCode.OK_EXACT.value,
            )
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.SPOKEN.value, value=value,
        normalized=N.normalize_phone(text), reason="phone not found in caller speech",
        code=ReasonCode.NOT_IN_TRANSCRIPT.value,
    )


def _spoken_number(value, text, transcript) -> GroundingResult:
    want = _to_number(text)
    if want is None:
        return _miss(Policy.SPOKEN, value, "value is not a clean number",
                     ReasonCode.NORMALIZE_MISMATCH.value)
    for turn in transcript.user_turns():
        if want in N.find_numbers(turn.text) and _number_is_live(want, turn.text):
            return _hit(Policy.SPOKEN, value, want, 0.95, "number spoken by caller", turn,
                        ReasonCode.OK_EXACT.value)
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.SPOKEN.value, value=value,
        normalized=want, reason="number not found in caller speech",
        code=ReasonCode.NOT_IN_TRANSCRIPT.value,
    )


def _to_number(text: str) -> int | None:
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
        return _miss(Policy.CONFIRMED, value, "empty value", ReasonCode.NO_VALUE.value)

    locale = _ctx_locale(ctx)
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
            verdict = _affirmation(follow.text, locale)
            if verdict is True:
                return _hit(
                    Policy.CONFIRMED, value, N.normalize_text(text), 0.95,
                    "agent read back and caller confirmed", follow,
                    ReasonCode.OK_CONFIRMED.value,
                )
            if verdict is False:
                return GroundingResult(
                    grounded=False, confidence=0.0, policy=Policy.CONFIRMED.value,
                    value=value, reason="caller rejected the read-back",
                    span=Span.from_turn(follow), code=ReasonCode.REJECTED_READBACK.value,
                )
            seen += 1
            if seen >= _MAX_USER_TURNS_AFTER_READBACK:
                break
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.CONFIRMED.value, value=value,
        reason="no read-back + confirmation found", code=ReasonCode.NO_CONFIRMATION.value,
    )


def _value_in_turn_text(value, text, turn_text, ctx) -> bool:
    if _looks_like_date(value, text):
        now = getattr(ctx, "now", None)
        locale = _ctx_locale(ctx)
        iso = N.normalize_date(text, now, locale)
        return iso is not None and (
            N.normalize_date(turn_text, now, locale) == iso
            or N.date_components_present(iso, turn_text, locale)
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


def _affirmation(text: str, locale=None) -> bool | None:
    norm = N.normalize_text(text)
    if not norm:
        return None
    if locale is None:
        deny, affirm = _DENY, _AFFIRM
    else:
        # Accent-fold both sides so "sí" matches a folded "si" vocabulary entry.
        norm = N.strip_accents(norm)
        deny = {N.strip_accents(p) for p in locale.deny}
        affirm = {N.strip_accents(p) for p in locale.affirmations}
    for phrase in deny:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm):
            return False
    for phrase in affirm:
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
            reason="no caller_id present in call metadata", code=ReasonCode.NO_VALUE.value,
        )
    text = to_text(value)
    if N.phones_match(text, str(cid)):
        return GroundingResult(
            grounded=True, confidence=1.0, policy=Policy.CALLER_ID.value, value=value,
            normalized=N.normalize_phone(text), reason="matches trusted caller_id metadata",
            code=ReasonCode.OK_CALLER_ID.value,
        )
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.CALLER_ID.value, value=value,
        reason="value does not match caller_id metadata",
        code=ReasonCode.WRONG_TOOL_SOURCE.value,
    )


# --------------------------------------------------------------------------- #
# INFERABLE
# --------------------------------------------------------------------------- #


def check_inferable(
    value: Any, transcript: Transcript, ctx, threshold: float, normalize: str | None = None
) -> GroundingResult:
    text = to_text(value)
    if not text:
        return _miss(Policy.INFERABLE, value, "empty value", ReasonCode.NO_VALUE.value)

    now = getattr(ctx, "now", None) or date.today()
    locale = _ctx_locale(ctx)
    iso = N.normalize_date(text, now, locale)
    if iso:
        for turn in transcript.user_turns():
            if N.normalize_date(turn.text, now, locale) == iso:
                return _hit(
                    Policy.INFERABLE, value, iso, 0.9,
                    "resolved from caller's relative date + clock", turn,
                    ReasonCode.OK_INFERRED.value,
                )
    spoken = check_spoken(value, transcript, ctx, threshold, normalize=normalize)
    if spoken.grounded:
        spoken.policy = Policy.INFERABLE.value
        spoken.reason = "inferable: " + spoken.reason
        spoken.code = ReasonCode.OK_INFERRED.value
        return spoken
    return GroundingResult(
        grounded=False, confidence=0.0, policy=Policy.INFERABLE.value, value=value,
        normalized=iso, reason="not inferable from context or speech",
        code=ReasonCode.NOT_IN_TRANSCRIPT.value,
    )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

_CHECKERS = {
    Policy.CONFIRMED: check_confirmed,
    Policy.CALLER_ID: check_caller_id,
}


def check(
    value: Any,
    policy: Policy,
    transcript: Transcript,
    ctx,
    threshold: float,
    normalize: str | None = None,
) -> GroundingResult:
    """Dispatch to the policy's checker.

    ``normalize`` is a SPOKEN-side normalizer name (``"spelled-name"`` ·
    ``"phonetic"`` · ``"spoken-date"``); it is honored by SPOKEN and the SPOKEN
    fallback of INFERABLE, and ignored by CONFIRMED / CALLER_ID.
    """
    if policy is Policy.SPOKEN:
        return check_spoken(value, transcript, ctx, threshold, normalize=normalize)
    if policy is Policy.INFERABLE:
        return check_inferable(value, transcript, ctx, threshold, normalize=normalize)
    checker = _CHECKERS.get(policy)
    if checker is None:
        raise ValueError(f"unknown policy: {policy!r}")
    return checker(value, transcript, ctx, threshold)


# --------------------------------------------------------------------------- #
# Small constructors
# --------------------------------------------------------------------------- #


def _hit(
    policy: Policy, value, normalized, conf: float, reason: str, turn: Turn,
    code: str = ReasonCode.OK_EXACT.value,
) -> GroundingResult:
    return GroundingResult(
        grounded=True, confidence=conf, policy=policy.value, value=value,
        normalized=normalized, reason=reason, span=Span.from_turn(turn), code=code,
    )


def _miss(
    policy: Policy, value, reason: str, code: str = ReasonCode.NOT_IN_TRANSCRIPT.value
) -> GroundingResult:
    return GroundingResult(
        grounded=False, confidence=0.0, policy=policy.value, value=value, reason=reason,
        code=code,
    )
