"""Normalization helpers: turn messy spoken values into comparable forms.

The grounding matcher is *deterministic-first*: before anything fancy, we
normalize numbers, dates, phones and names so that "January first, nineteen
ninety" and "1990-01-01" become the same thing.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

__all__ = [
    "assemble_spelled",
    "date_components_present",
    "digits_only",
    "find_numbers",
    "find_years",
    "normalize_date",
    "normalize_phone",
    "normalize_text",
    "phones_match",
    "soundex",
    "strip_accents",
    "words_to_int",
]


def strip_accents(s: str) -> str:
    """Drop combining diacritics so "sí"/"mañana" fold to "si"/"manana".

    ASR transcribes accented words inconsistently; folding makes locale yes/no and
    relative-date matching robust to that. ASCII text is returned unchanged.
    """
    if not s:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )

# --------------------------------------------------------------------------- #
# Basic text
# --------------------------------------------------------------------------- #

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    if not s:
        return ""
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def digits_only(s: str) -> str:
    """Every digit in ``s``, concatenated (number words first via caller)."""
    return re.sub(r"\D", "", s or "")


def assemble_spelled(s: str) -> str:
    """Join runs of single spelled-out letters into words.

    A native-audio caller spells a surname letter-by-letter ("R O M U L A"); ASR
    transcribes the letters separately, so the assembled word the model commits
    ("Romula") never string-matches the transcript. This collapses consecutive
    single-letter tokens into one token so the surname grounds::

        assemble_spelled("my name is r o m u l a")  # -> "my name is romula"

    Non-letter and multi-character tokens pass through unchanged. Used by the
    ``normalize="spelled-name"`` SPOKEN normalizer; English-letter oriented.
    """
    toks = normalize_text(s).split()
    out: list[str] = []
    run: list[str] = []
    for t in toks:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(t)
    if run:
        out.append("".join(run))
    return " ".join(out)


_SOUNDEX_MAP = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def soundex(s: str) -> str:
    """Classic Soundex code for a token (deterministic phonetic key).

    Near-homophones share a code, so the ``normalize="phonetic"`` SPOKEN normalizer
    can ground an ASR homophone the model silently corrected — e.g. the caller's
    transcribed "mail" against the committed "male" (both ``M400``). English-oriented.
    Empty / non-alpha input returns ``""``.
    """
    letters = re.sub(r"[^a-z]", "", (s or "").lower())
    if not letters:
        return ""
    first = letters[0].upper()
    out = first
    prev = _SOUNDEX_MAP.get(letters[0], "")
    for ch in letters[1:]:
        code = _SOUNDEX_MAP.get(ch, "")
        if code and code != prev:
            out += code
            if len(out) == 4:
                break
        # 'h'/'w' are transparent (don't reset the previous code); vowels do reset.
        if ch not in "hw":
            prev = code
    return (out + "000")[:4]


# --------------------------------------------------------------------------- #
# Number words
# --------------------------------------------------------------------------- #

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "twenty-first": 21, "twenty-second": 22, "twenty-third": 23,
    "twenty-fourth": 24, "twenty-fifth": 25, "twenty-sixth": 26,
    "twenty-seventh": 27, "twenty-eighth": 28, "twenty-ninth": 29,
    "thirtieth": 30, "thirty-first": 31,
}

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _tokens(s: str) -> list[str]:
    return re.split(r"[\s,]+", s.lower().strip()) if s else []


def words_to_int(s: str) -> int | None:
    """Convert a cardinal number phrase to an int.

    Handles forms like ``"forty two"``, ``"two thousand five"``,
    ``"one hundred twenty three"``. Returns ``None`` if nothing numeric found.
    """
    if s is None:
        return None
    toks = [t for t in re.split(r"[\s-]+", s.lower().strip()) if t]
    if not toks:
        return None

    total = 0
    current = 0
    seen = False
    for tok in toks:
        if tok == "and":
            continue
        if tok in _UNITS:
            current += _UNITS[tok]
            seen = True
        elif tok in _TENS:
            current += _TENS[tok]
            seen = True
        elif tok in _SCALES:
            scale = _SCALES[tok]
            seen = True
            if scale == 100:
                current = (current or 1) * 100
            else:
                current = (current or 1) * scale
                total += current
                current = 0
        elif tok.isdigit():
            current += int(tok)
            seen = True
        else:
            return None  # unknown token -> not a clean number phrase
    return total + current if seen else None


def find_numbers(s: str) -> set[int]:
    """All integers mentioned in ``s`` as digits or words."""
    out: set[int] = set()
    if not s:
        return out
    # join comma-grouped digits ("1,234" -> "1234") before extraction
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    for m in re.findall(r"\d+", s):
        out.add(int(m))
    # word runs
    toks = _tokens(normalize_text(s))
    run: list[str] = []
    numbery = set(_UNITS) | set(_TENS) | set(_SCALES) | {"and"}
    for tok in [*toks, "<end>"]:
        if tok in numbery:
            run.append(tok)
        else:
            if run:
                val = words_to_int(" ".join(run))
                if val is not None:
                    out.add(val)
                run = []
    return out


# --------------------------------------------------------------------------- #
# Years (spoken year colloquialisms)
# --------------------------------------------------------------------------- #


def find_years(s: str) -> set[int]:
    """Plausible 4-digit years in ``s`` (digits or spoken)."""
    out: set[int] = set()
    if not s:
        return out
    norm = normalize_text(s)
    for m in re.findall(r"\b(\d{4})\b", norm):
        y = int(m)
        if 1900 <= y <= 2100:
            out.add(y)

    # "nineteen ninety", "nineteen eighty four"
    for m in re.finditer(r"\bnineteen\s+([a-z\s-]+)", norm):
        tail = m.group(1).split()
        # consume up to two words ("eighty four")
        for take in (2, 1):
            val = words_to_int(" ".join(tail[:take]))
            if val is not None and 0 <= val <= 99:
                out.add(1900 + val)
                break

    # "twenty twenty", "twenty twenty four"
    for m in re.finditer(r"\btwenty\s+([a-z\s-]+)", norm):
        tail = m.group(1).split()
        for take in (2, 1):
            val = words_to_int(" ".join(tail[:take]))
            if val is not None and 0 <= val <= 99:
                out.add(2000 + val)
                break

    # "two thousand", "two thousand five", "two thousand and twelve"
    for m in re.finditer(r"\btwo thousand(?:\s+and)?\s*([a-z\s-]*)", norm):
        tail = m.group(1).strip()
        add = words_to_int(tail) if tail else 0
        out.add(2000 + (add or 0))
    return out


# --------------------------------------------------------------------------- #
# Phone
# --------------------------------------------------------------------------- #


def normalize_phone(s: str) -> str:
    """Digits of a phone number (number words converted first)."""
    if not s:
        return ""
    # convert spelled-out digits like "five five five" -> "555"
    toks = _tokens(normalize_text(s))
    pieces: list[str] = []
    for tok in toks:
        if tok in _UNITS and _UNITS[tok] <= 9:
            pieces.append(str(_UNITS[tok]))
        else:
            pieces.append(tok)
    joined = "".join(pieces)
    return digits_only(joined)


def phones_match(a: str, b: str, min_digits: int = 7) -> bool:
    """True if two phone numbers share a long-enough suffix."""
    da, db = normalize_phone(a), normalize_phone(b)
    if not da or not db:
        return False
    n = min(len(da), len(db))
    if n < min_digits:
        return da == db
    return da[-n:] == db[-n:]


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_SLASH_RE = re.compile(r"\b(\d{1,2})[/](\d{1,2})[/](\d{2,4})\b")


def _strip_ordinal_suffix(tok: str) -> str:
    return re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", tok)


def normalize_date(s: str, now: date | None = None, locale=None) -> str | None:
    """Best-effort parse of a date expression to ISO ``YYYY-MM-DD``.

    Returns ``None`` if no confident date can be extracted. ``locale`` (a
    :class:`saidso._matching.locale.Locale`) selects the month / ordinal / relative
    tables; ``None`` uses the English defaults, byte-for-byte the prior behavior.
    """
    if not s:
        return None
    months = locale.months if locale is not None else _MONTHS
    ordinals = locale.ordinals if locale is not None else _ORDINALS
    raw = s.strip().lower()

    m = _ISO_RE.search(raw)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return _iso(y, mo, d)

    m = _SLASH_RE.search(raw)
    if m:
        mo, d, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000 if y < 50 else 1900
        return _iso(y, mo, d)

    # relative
    rel = _relative_date(raw, now, locale)
    if rel:
        return rel.isoformat()

    # textual: month + day + year. Accent-fold so "septiembre" matches even when ASR
    # drops/keeps diacritics inconsistently.
    norm = strip_accents(normalize_text(_strip_ordinal_suffix(raw)))
    toks = norm.split()

    month = None
    for t in toks:
        if t in months:
            month = months[t]
            break
    if month is None:
        return None

    day = None
    for t in toks:
        if t in ordinals:
            day = ordinals[t]
            break
        if t.isdigit() and 1 <= int(t) <= 31:
            day = int(t)
            break
    # ordinal phrases like "twenty first"
    if day is None:
        for ord_word, val in ordinals.items():
            if "-" in ord_word and ord_word.replace("-", " ") in norm:
                day = val
                break

    years = find_years(raw)
    year = next(iter(sorted(years)), None) if years else None

    if month and day and year:
        return _iso(year, month, day)
    return None


def date_components_present(iso: str, text: str, locale=None) -> bool:
    """True if the year, month and day of ``iso`` all appear in ``text``.

    Robust fallback when full date parsing of the transcript is too brittle:
    we just confirm each piece was actually spoken. ``locale`` selects the month /
    ordinal names; ``None`` uses English.
    """
    try:
        y, mo, d = (int(x) for x in iso.split("-"))
    except Exception:
        return False
    months = locale.months if locale is not None else _MONTHS
    ordinals = locale.ordinals if locale is not None else _ORDINALS
    norm = strip_accents(normalize_text(_strip_ordinal_suffix(text)))

    year_ok = y in find_years(text)
    if not year_ok:
        year_ok = re.search(rf"\b{y}\b", norm) is not None

    month_names = {strip_accents(k) for k, v in months.items() if v == mo}
    month_ok = any(re.search(rf"\b{name}\b", norm) for name in month_names)
    if not month_ok:
        month_ok = re.search(rf"\b0?{mo}\b", norm) is not None

    day_ok = re.search(rf"\b0?{d}\b", norm) is not None
    if not day_ok:
        day_words = {strip_accents(k) for k, v in ordinals.items() if v == d}
        day_ok = any(w.replace("-", " ") in norm for w in day_words)
        if not day_ok and locale is None:
            cardinal = _int_to_words(d)
            day_ok = cardinal in norm

    return year_ok and month_ok and day_ok


def _relative_date(raw: str, now: date | None, locale=None) -> date | None:
    base = now or date.today()
    if locale is None:  # English default — exact historical substring behavior
        if "today" in raw:
            return base
        if "tomorrow" in raw:
            return base + timedelta(days=1)
        if "yesterday" in raw:
            return base - timedelta(days=1)
        return None
    folded = strip_accents(raw)
    delta = {"today": 0, "tomorrow": 1, "yesterday": -1}
    for word, kind in locale.relative.items():
        w = strip_accents(word)
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", folded):
            return base + timedelta(days=delta.get(kind, 0))
    return None


_INT_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
    13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    21: "twenty one", 22: "twenty two", 23: "twenty three", 24: "twenty four",
    25: "twenty five", 26: "twenty six", 27: "twenty seven", 28: "twenty eight",
    29: "twenty nine", 30: "thirty", 31: "thirty one",
}


def _int_to_words(n: int) -> str:
    return _INT_WORDS.get(n, str(n))


def _iso(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None
