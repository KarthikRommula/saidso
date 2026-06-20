"""Deterministic grounded speech — the production side of "reads".

A realtime speech-to-speech model can't have its mouth gated: by the time you see
the transcript, the words are already out. So the only way to make a *consequential
spoken fact* 100% accurate is to never let the model say it — have code speak it
instead, verbatim, from grounded data.

This module is the verification half of that pattern, and it is **TTS-agnostic**:
saidso never produces audio. You give it a template plus the facts you intend to
speak (each tagged with its tool-output provenance); it reconciles every fact
against what a tool actually returned this call (the same fail-closed engine as
:func:`saidso.grounded_outputs`), substitutes the *canonical* value, and hands you
back a string. Speak that string with whatever TTS you (or your users) bring.

**The reads guarantee.** A string returned by :func:`render_spoken` contains only
facts that trace to real tool output. If any fact can't be grounded, nothing is
returned — :class:`UngroundedSpeech` is raised — so a fabricated value can never be
spoken. The rendered form is a deterministic function of the canonical tool value.

Boundaries (honest): the template's static text is author-written (trusted, not
model output), and a custom ``render`` callable is assumed deterministic. Within
those, no ungrounded dynamic fact can reach the returned line.
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass
from typing import Any, Callable

from ..context import get_context
from ..provenance import FromTool, from_tool, reconcile

logger = logging.getLogger("saidso")


@dataclass(frozen=True)
class Fact:
    """One interpolated value in a spoken line, with its provenance + optional renderer."""

    value: Any
    spec: FromTool
    render: Callable[[Any], str] | None = None


def fact(
    value: Any,
    *sources,
    normalize: str = "exact",
    render: Callable[[Any], str] | None = None,
    allow_single_candidate: bool = False,
) -> Fact:
    """Declare a spoken fact and where it must come from.

    ``sources`` is the same shape as :func:`saidso.from_tool` — a single
    ``("tool", "key")`` or several ``(("t1","k1"), ("t2","k2"))`` pooled together.
    ``render`` is an optional deterministic formatter applied to the *canonical* tool
    value to get the spoken form (e.g. an ISO timestamp -> "5:00 PM"); the default is
    ``str``::

        fact(slot_start, ("get_slots", "slot_start"),
             normalize="datetime-minute", render=to_clock)

    ``allow_single_candidate`` defaults to ``False`` — unlike writes. For an *action*,
    a lone candidate is the only possible target, so coercing to it is safe; but
    *speaking* a value the situation doesn't actually support (substituting the only
    name on file for one that was never returned) is exactly the silent error reads
    must avoid. A spoken fact must genuinely match a tool value.
    """
    spec = from_tool(*sources, normalize=normalize, allow_single_candidate=allow_single_candidate)
    return Fact(value=value, spec=spec, render=render)


@dataclass
class BlockedFact:
    """A fact that could not be grounded, so it was refused for speech."""

    name: str
    value: Any
    reason: str


class UngroundedSpeech(Exception):
    """Raised when a spoken line would include a fact not grounded in tool output.

    Carries the offending facts on ``.blocked`` so the caller can log/fall back. The
    point is fail-closed: rather than speak a possibly-fabricated value, speak nothing.
    """

    def __init__(self, blocked: list[BlockedFact]) -> None:
        self.blocked = blocked
        names = ", ".join(b.name for b in blocked) or "(none)"
        super().__init__(f"refusing to speak ungrounded fact(s): {names}")


class UnattestedAction(UngroundedSpeech):
    """Raised when a spoken completion claim is not backed by a successful write.

    ``render_spoken(..., requires_write=attested("book_appointment"))`` grounds the
    *verb* of the line ("you *have* an appointment") against the AttestationLog, not
    just the nouns against the ToolLedger. If the required action did not succeed this
    call, the line is refused even when every ``fact(...)`` placeholder is grounded —
    because the sentence asserts a completed action that never happened.
    """

    def __init__(self, action: str, status: str) -> None:
        self.action = action
        self.status = status
        # Reuse the UngroundedSpeech contract: a single synthetic blocked "fact".
        blocked = [BlockedFact(
            name=f"requires_write:{action}", value=action,
            reason=f"no attested {action!r} with status {status!r} this call",
        )]
        Exception.__init__(
            self, f"refusing to speak: completion claim requires a successful "
            f"{action!r} ({status!r}) but none is attested this call"
        )
        self.blocked = blocked


def _field_names(template: str) -> list[str]:
    """The simple ``{name}`` placeholders in ``template`` (rejects ``{a.b}`` / ``{0}``)."""
    names: list[str] = []
    for _, field, _, _ in string.Formatter().parse(template):
        if field is None:
            continue
        if not field.isidentifier():
            raise ValueError(
                f"grounded-speech template fields must be simple names; got {field!r}"
            )
        names.append(field)
    return names


def render_spoken(
    template: str,
    *,
    ledger: Any = None,
    attestations: Any = None,
    requires_write: Any = None,
    **facts: Fact,
) -> str:
    """Render ``template`` using only facts verified against tool output.

    Every ``{name}`` placeholder must have a matching ``name=fact(...)`` keyword, and
    every fact must be used. Each fact's value is reconciled against ``ledger`` (a
    :class:`~saidso.ToolLedger`; falls back to the active ``call_context``'s tools) with
    the fail-closed provenance engine. On a pass the *canonical* tool value is rendered
    and substituted; if ANY fact fails, :class:`UngroundedSpeech` is raised and nothing
    is returned. The result is safe to hand to any TTS — saidso never speaks.

    ``requires_write=attested("book_appointment")`` additionally grounds the line's
    *completion claim*: the named action must have a matching attestation in
    ``attestations`` (an :class:`~saidso.AttestationLog`; falls back to the active
    ``call_context``'s ledger) this call, else :class:`UnattestedAction` is raised —
    even when every fact is grounded. Use it for lines that assert a write succeeded
    ("you *have* an appointment"), so saidso owns the verb, not just the nouns.
    """
    ctx = None
    if ledger is None:
        ctx = get_context()
        ledger = getattr(ctx, "tools", None) if ctx else None

    # Completion-claim gate (the "verb"): checked before the facts (the "nouns").
    if requires_write is not None:
        log = attestations
        if log is None:
            ctx = ctx if ctx is not None else get_context()
            log = getattr(ctx, "ledger", None) if ctx else None
        ok = bool(
            log is not None
            and getattr(log, "has", None)
            and log.has(requires_write.action, status=requires_write.status)
        )
        if not ok:
            logger.info(
                "refused unattested completion claim: %s", requires_write.action,
                extra={"saidso_event": "block", "saidso_action": "speak",
                       "saidso_args": [f"requires_write:{requires_write.action}"]},
            )
            raise UnattestedAction(requires_write.action, requires_write.status)

    fields = _field_names(template)
    missing = [f for f in fields if f not in facts]
    if missing:
        raise ValueError(f"template placeholders with no grounded fact: {missing}")
    unused = [k for k in facts if k not in fields]
    if unused:
        raise ValueError(f"facts not referenced by the template: {unused}")

    rendered: dict[str, str] = {}
    blocked: list[BlockedFact] = []
    for name in fields:
        f = facts[name]
        if not isinstance(f, Fact):
            raise TypeError(f"{name!r} must be a fact(...), got {type(f).__name__}")
        cands: list[Any] = []
        if ledger is not None:
            for tool, key in f.spec.sources:
                cands.extend(ledger.candidates(tool, key))
        try:
            res = reconcile(f.value, cands, f.spec.normalize, f.spec.allow_single_candidate)
        except Exception as exc:  # fail closed: a crashing check refuses, never speaks
            logger.exception("saidso: grounded-speech reconcile errored for %s; blocking.", name)
            blocked.append(BlockedFact(name, f.value, f"errored: {exc}"))
            continue
        if not res.passed:
            blocked.append(BlockedFact(name, f.value, res.reason))
            continue
        try:
            rendered[name] = f.render(res.canonical) if f.render else str(res.canonical)
        except Exception as exc:  # a renderer that throws must not leak a raw value
            logger.exception("saidso: grounded-speech render failed for %s; blocking.", name)
            blocked.append(BlockedFact(name, f.value, f"render failed: {exc}"))

    if blocked:
        logger.info(
            "refused ungrounded speech: %s", [b.name for b in blocked],
            extra={"saidso_event": "block", "saidso_action": "speak",
                   "saidso_args": [b.name for b in blocked]},
        )
        raise UngroundedSpeech(blocked)
    out = template.format_map(rendered)
    logger.info(
        "spoke: %s", out,
        extra={"saidso_event": "pass", "saidso_action": "speak",
               "saidso_args": list(fields)},
    )
    return out


def try_render_spoken(
    template: str,
    *,
    ledger: Any = None,
    attestations: Any = None,
    requires_write: Any = None,
    **facts: Fact,
) -> str | None:
    """Like :func:`render_spoken`, but return ``None`` instead of raising on a block.

    Convenient for "speak the deterministic line if every fact is grounded (and the
    required write succeeded), otherwise fall back" — e.g. let the model phrase it, or
    re-ask. Catches both :class:`UngroundedSpeech` and :class:`UnattestedAction`.
    """
    try:
        return render_spoken(
            template, ledger=ledger, attestations=attestations,
            requires_write=requires_write, **facts,
        )
    except UngroundedSpeech:
        return None
