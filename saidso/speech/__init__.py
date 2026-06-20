"""Speech grounding — the "reads" side of the firewall.

Two complementary tools:

* :mod:`saidso.speech.render` — *deterministic* grounded speech: build a spoken
  line whose every dynamic fact is verified against tool output, and refuse to
  produce anything if a fact is ungrounded. This is the 100% guarantee.
* :mod:`saidso.speech.monitor` — *best-effort* post-turn detection: flag titled
  names the agent spoke that aren't in the ground-truth set. A safety net, not a
  guarantee.
* :mod:`saidso.speech.reconcile` — *best-effort* turn-level reconciler: flag spoken
  completion claims ("you're all set") that no successful action backs.
"""

from __future__ import annotations

from .monitor import (
    SpokenName,
    check_spoken_names,
    find_name_mentions,
    find_ungrounded_names,
)
from .reconcile import (
    COMPLETION_CLAIMS,
    ClaimPattern,
    UnbackedClaim,
    reconcile_turn,
)
from .render import (
    BlockedFact,
    Fact,
    UnattestedAction,
    UngroundedSpeech,
    fact,
    render_spoken,
    try_render_spoken,
)

__all__ = [
    "COMPLETION_CLAIMS",
    "BlockedFact",
    "ClaimPattern",
    "Fact",
    "SpokenName",
    "UnattestedAction",
    "UnbackedClaim",
    "UngroundedSpeech",
    "check_spoken_names",
    "fact",
    "find_name_mentions",
    "find_ungrounded_names",
    "reconcile_turn",
    "render_spoken",
    "try_render_spoken",
]
