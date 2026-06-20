"""The grounding policies: what makes an argument legitimate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Policy(str, Enum):
    """Per-argument grounding rules.

    - ``SPOKEN``    : the value must appear in the caller's speech
                      (digits/dates/names normalized, fuzzy-matched).
    - ``CONFIRMED`` : the agent read the value back AND the caller affirmed it.
    - ``CALLER_ID`` : the value comes from trusted call metadata, not the mouth.
    - ``INFERABLE`` : the value is derivable from context (e.g. "tomorrow" + clock)
                      or was spoken explicitly.

    A bare policy uses the default threshold and the auto-sniffing matcher. Call a
    member to attach **per-argument** tuning — a SPOKEN-side normalizer and/or a
    threshold override — without touching the global :class:`GroundingConfig`::

        @grounded(
            family_name=Policy.SPOKEN(normalize="spelled-name"),
            gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6),
        )
        def register(family_name, gender): ...

    ``Policy.SPOKEN(...)`` returns a :class:`PolicySpec`; ``Policy("spoken")`` (the
    enum value lookup) is unchanged.
    """

    SPOKEN = "spoken"
    CONFIRMED = "confirmed"
    CALLER_ID = "caller_id"
    INFERABLE = "inferable"

    def __call__(
        self, *, normalize: str | None = None, threshold: float | None = None
    ) -> PolicySpec:
        """Attach per-argument tuning to this policy. See :class:`Policy`."""
        return PolicySpec(policy=self, normalize=normalize, threshold=threshold)


@dataclass(frozen=True)
class PolicySpec:
    """A policy plus optional per-argument tuning.

    Produced by calling a :class:`Policy` member (``Policy.SPOKEN(normalize=...)``).
    ``normalize`` selects a SPOKEN-side normalizer (``"spelled-name"`` ·
    ``"phonetic"`` · ``"spoken-date"``); ``threshold`` overrides the per-policy
    confidence floor for this one argument. Both default to ``None`` — the bare
    policy's behavior.
    """

    policy: Policy
    normalize: str | None = None
    threshold: float | None = None


def as_spec(value: Policy | str | PolicySpec) -> PolicySpec:
    """Coerce any accepted policy input to a :class:`PolicySpec`.

    Accepts a bare :class:`Policy`, a policy string (``"spoken"``), or an already
    built :class:`PolicySpec`. Raises ``ValueError`` for an unknown policy string.
    """
    if isinstance(value, PolicySpec):
        return value
    if isinstance(value, Policy):
        return PolicySpec(policy=value)
    return PolicySpec(policy=Policy(value))


# Default confidence thresholds per policy (override via GroundingConfig or a
# per-argument PolicySpec.threshold).
DEFAULT_THRESHOLDS = {
    Policy.SPOKEN: 0.82,
    Policy.CONFIRMED: 0.82,
    Policy.CALLER_ID: 0.99,
    Policy.INFERABLE: 0.82,
}
