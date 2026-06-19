"""Best-effort speech-grounding monitor (PARTIAL — not a guarantee).

For a realtime speech-to-speech agent, saidso cannot block what the model *says*
before the caller hears it — only what it *does* (tool calls). This module is the
honest middle ground: a **post-turn** check. Given the agent's transcribed turn
and the ground-truth set of names a tool actually returned this call, it flags
titled-name mentions ("Dr. X") that aren't grounded — so the caller can inject a
spoken correction and/or log the slip.

It is reactive (the words were already spoken), heuristic (honorific + name
extraction over ASR text), and English-leaning. Use it alongside provenance
grounding, which makes the consequential *action* safe deterministically; this
only reduces the residual "purely-spoken wrong name" gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from . import normalize as N
from ._fuzz import ratio

_DEFAULT_TITLES = ("dr", "doctor", "prof", "professor")
_MIN_TOKEN_LEN_FOR_FUZZY = 4
_FUZZY = 0.85


@dataclass
class SpokenName:
    """A name the agent spoke after an honorific, judged against the allowed set."""

    text: str            # the name as spoken, e.g. "Jones"
    grounded: bool
    best_match: str = ""
    score: float = 0.0


def find_name_mentions(spoken: str, titles: Sequence[str] = _DEFAULT_TITLES) -> List[str]:
    """Extract names spoken right after an honorific (Dr / Doctor / Prof ...)."""
    if not spoken:
        return []
    title_re = "|".join(re.escape(t) for t in titles)
    # Case-insensitivity is scoped to the title only — the name's second word must
    # stay capital-anchored so we don't swallow a trailing lowercase word ("and").
    pat = re.compile(rf"\b(?i:{title_re})\.?\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)")
    return [m.group(1).strip() for m in pat.finditer(spoken)]


def _allowed_tokens(allowed: Iterable[str]) -> set:
    toks = set()
    for a in allowed:
        for t in N.normalize_text(a).split():
            if t:
                toks.add(t)
    return toks


def check_spoken_names(
    spoken: str,
    allowed: Iterable[str],
    *,
    titles: Sequence[str] = _DEFAULT_TITLES,
    threshold: float = _FUZZY,
) -> List[SpokenName]:
    """Judge each titled name in ``spoken`` against the ``allowed`` ground-truth names.

    Grounding is decided on the surname (last token of the mention): an exact
    token match, or a fuzzy match >= ``threshold`` for tokens long enough to fuzz.
    """
    allowed = list(allowed)
    tokens = _allowed_tokens(allowed)
    out: List[SpokenName] = []
    for raw in find_name_mentions(spoken, titles):
        ntoks = N.normalize_text(raw).split()
        surname = ntoks[-1] if ntoks else ""
        grounded, best, score = False, "", 0.0
        if surname:
            if surname in tokens:
                grounded, best, score = True, surname, 1.0
            elif len(surname) >= _MIN_TOKEN_LEN_FOR_FUZZY:
                for at in tokens:
                    if len(at) >= _MIN_TOKEN_LEN_FOR_FUZZY:
                        r = ratio(surname, at)
                        if r > score:
                            score, best = r, at
                if score >= threshold:
                    grounded = True
        out.append(SpokenName(text=raw, grounded=grounded, best_match=best,
                              score=round(score, 3)))
    return out


def find_ungrounded_names(
    spoken: str,
    allowed: Iterable[str],
    *,
    titles: Sequence[str] = _DEFAULT_TITLES,
    threshold: float = _FUZZY,
) -> List[SpokenName]:
    """Convenience: just the ungrounded titled-name mentions in ``spoken``."""
    return [
        m for m in check_spoken_names(spoken, allowed, titles=titles, threshold=threshold)
        if not m.grounded
    ]
