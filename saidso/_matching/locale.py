"""Locale tables for grounding multilingual / multi-tenant calls.

saidso's normalization (spoken-date -> ISO, month names, the affirmation/denial
vocabulary used by ``CONFIRMED``) is English-centric by default. A tenant whose
calls run in another language — driven from ``call_context(metadata={"locale": ...})``
— needs the matcher to read that language's month names and "yes"/"no" words.

A :class:`Locale` bundles those tables. The registry ships **English** (the default,
byte-for-byte the historical behavior) and **Spanish**; :func:`get_locale` resolves a
BCP-47 tag (``"es-ES"`` -> ``"es"``) to the closest entry, falling back to English.
Add a locale by constructing one more :class:`Locale` and registering it — the matcher
and ``normalize`` helpers consume it without further changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Locale:
    """Language-specific tables the matcher consults for one call's locale."""

    language: str
    months: dict[str, int]
    ordinals: dict[str, int]
    relative: dict[str, str]  # spoken word -> "today" | "tomorrow" | "yesterday"
    affirmations: frozenset[str]
    deny: frozenset[str]
    retract_words: frozenset[str] = field(default_factory=frozenset)


# --------------------------------------------------------------------------- #
# English (default) — mirrors the historical tables in normalize.py / matcher.py
# so locale=None and locale="en" behave identically.
# --------------------------------------------------------------------------- #

_EN_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_EN_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "twenty-first": 21, "twenty-second": 22, "twenty-third": 23,
    "twenty-fourth": 24, "twenty-fifth": 25, "twenty-sixth": 26,
    "twenty-seventh": 27, "twenty-eighth": 28, "twenty-ninth": 29,
    "thirtieth": 30, "thirty-first": 31,
}

_EN_AFFIRM = frozenset({
    "yes", "yeah", "yep", "yup", "correct", "right", "sure", "ok", "okay",
    "confirmed", "confirm", "exactly", "perfect", "absolutely", "definitely",
    "mhm", "that's right", "thats right", "that is correct", "that's correct",
    "thats correct", "go ahead", "sounds good", "affirmative", "it is",
})
_EN_DENY = frozenset({
    "no", "nope", "nah", "wrong", "incorrect", "not right", "that's wrong",
    "thats wrong", "not correct", "negative", "that's not", "thats not",
})

EN = Locale(
    language="en",
    months=_EN_MONTHS,
    ordinals=_EN_ORDINALS,
    relative={"today": "today", "tomorrow": "tomorrow", "yesterday": "yesterday"},
    affirmations=_EN_AFFIRM,
    deny=_EN_DENY,
)


# --------------------------------------------------------------------------- #
# Spanish — months, basic ordinals, relative dates, yes/no vocabulary.
# --------------------------------------------------------------------------- #

_ES_MONTHS = {
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
    "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "setiembre": 9,
    "sep": 9, "set": 9, "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12,
}

_ES_ORDINALS = {
    "primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "cuarto": 4,
    "quinto": 5, "sexto": 6, "septimo": 7, "octavo": 8, "noveno": 9,
    "decimo": 10,
}

_ES_AFFIRM = frozenset({
    "si", "claro", "correcto", "exacto", "exactamente", "vale", "perfecto",
    "de acuerdo", "afirmativo", "eso es", "asi es", "esta bien", "esta correcto",
    "por supuesto", "confirmo", "confirmado",
})
_ES_DENY = frozenset({
    "no", "incorrecto", "equivocado", "negativo", "para nada", "esta mal",
    "eso no", "no es correcto",
})

ES = Locale(
    language="es",
    months=_ES_MONTHS,
    ordinals=_ES_ORDINALS,
    relative={"hoy": "today", "manana": "tomorrow", "ayer": "yesterday"},
    affirmations=_ES_AFFIRM,
    deny=_ES_DENY,
)


LOCALES: dict[str, Locale] = {"en": EN, "es": ES}


def get_locale(tag: str | None) -> Locale:
    """Resolve a BCP-47 tag (``"es-ES"``) to a :class:`Locale`, defaulting to English.

    Matches on the primary language subtag, case-insensitively; an unknown or empty
    tag returns :data:`EN` so grounding never hard-fails on an unsupported language.
    """
    if not tag:
        return EN
    base = str(tag).replace("_", "-").split("-", 1)[0].lower()
    return LOCALES.get(base, EN)
