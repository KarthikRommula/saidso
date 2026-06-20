"""Fuzzy string matching with a zero-dependency fallback.

Uses ``rapidfuzz`` when it is installed (fast, C-backed) and transparently
falls back to the stdlib ``difflib`` so ``saidso`` works with no required
third-party dependencies.
"""

from __future__ import annotations

try:  # pragma: no cover - exercised indirectly
    from rapidfuzz import fuzz as _rf

    _HAVE_RAPIDFUZZ = True
except Exception:  # pragma: no cover
    _HAVE_RAPIDFUZZ = False
    import difflib


def ratio(a: str, b: str) -> float:
    """Whole-string similarity in ``[0, 1]``."""
    if not a or not b:
        return 0.0
    if _HAVE_RAPIDFUZZ:
        return _rf.ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def partial_ratio(needle: str, haystack: str) -> float:
    """How well ``needle`` appears *inside* ``haystack``, in ``[0, 1]``.

    This is the workhorse for "did the caller roughly say this?" checks.
    """
    if not needle or not haystack:
        return 0.0
    if _HAVE_RAPIDFUZZ:
        return _rf.partial_ratio(needle, haystack) / 100.0

    # difflib fallback: slide a window the size of ``needle`` across ``haystack``.
    n = len(needle)
    if n >= len(haystack):
        return difflib.SequenceMatcher(None, needle, haystack).ratio()
    best = 0.0
    step = max(1, n // 4)
    for i in range(0, len(haystack) - n + 1, step):
        window = haystack[i : i + n]
        r = difflib.SequenceMatcher(None, needle, window).ratio()
        if r > best:
            best = r
    return best
