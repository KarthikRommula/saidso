"""Speech grounding — the "reads" side of the firewall.

Two complementary tools:

* :mod:`saidso.speech.render` — *deterministic* grounded speech: build a spoken
  line whose every dynamic fact is verified against tool output, and refuse to
  produce anything if a fact is ungrounded. This is the 100% guarantee.
* :mod:`saidso.speech.monitor` — *best-effort* post-turn detection: flag titled
  names the agent spoke that aren't in the ground-truth set. A safety net, not a
  guarantee.
"""

from __future__ import annotations

from .monitor import (
    SpokenName,
    check_spoken_names,
    find_name_mentions,
    find_ungrounded_names,
)
from .render import (
    BlockedFact,
    Fact,
    UngroundedSpeech,
    fact,
    render_spoken,
    try_render_spoken,
)

__all__ = [
    "render_spoken",
    "try_render_spoken",
    "fact",
    "Fact",
    "UngroundedSpeech",
    "BlockedFact",
    "find_ungrounded_names",
    "check_spoken_names",
    "find_name_mentions",
    "SpokenName",
]
