"""Tool-output provenance grounding (saidso feature #1, prototype).

Where :func:`saidso.grounded` checks an argument against what the *caller said*
(the transcript), provenance grounding checks an argument against what a *tool
returned* earlier in the same call. It absorbs the two most common realtime
voice-agent bugs:

* the model invents an opaque id it was never given, and
* the model *reconstructs* a value (a timestamp from "5 PM", a phone number from
  digits) instead of echoing the canonical one a tool handed it.

**Fail-closed contract (the 100% guarantee).** A value is forwarded to the tool
body ONLY when it resolves to exactly one real candidate the tool actually
returned — by raw-exact match, by a unique normalized match, or because there
was only one candidate. In every passing case the value handed to the body is a
*genuine tool output* (rewritten to its canonical form), never the model's
string. Anything ambiguous or unmatched blocks with a :class:`SteerBack`. The
firewall therefore never forwards a value that did not come from a tool.
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .context import CallContext, get_context
from .result import ArgFinding, GroundingResult, SteerBack

logger = logging.getLogger("saidso")


# --------------------------------------------------------------------------- #
# Normalizers — type-aware equality for "the model rebuilt the value" cases.
# Each maps a raw value to a comparable key, or None if it can't be normalized.
# --------------------------------------------------------------------------- #


def _n_exact(v: Any) -> Optional[str]:
    return None if v is None else str(v).strip()


def _n_casefold(v: Any) -> Optional[str]:
    return None if v is None else str(v).strip().casefold()


def _n_e164(v: Any) -> Optional[str]:
    if v is None:
        return None
    digits = "".join(c for c in str(v) if c.isdigit())
    return ("+" + digits) if digits else None


# Fast path: the wall-clock minute as written, for standard ISO-8601 strings
# (`YYYY-MM-DDTHH:MM...` or with a space). Avoids the cost of full datetime
# parsing on every candidate, and handles 'Z' / any offset without Python 3.11.
_ISO_MINUTE_RE = re.compile(r"\s*(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")


def _n_datetime_minute(v: Any) -> Optional[str]:
    """Wall-clock minute, ignoring seconds and timezone-offset formatting.

    The Live model rebuilds an ISO timestamp from a spoken time, getting the
    offset/seconds wrong; comparing at minute granularity recovers the slot.
    """
    if v is None:
        return None
    s = str(v).strip()
    m = _ISO_MINUTE_RE.match(s)
    if m:
        return f"{m.group(1)}T{m.group(2)}"
    try:  # non-standard shape (e.g. date only) — fall back to a real parse
        return datetime.fromisoformat(s).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _n_money(v: Any) -> Optional[str]:
    if v is None:
        return None
    cleaned = "".join(c for c in str(v) if c.isdigit() or c in ".-")
    try:
        return f"{float(cleaned):.2f}"
    except ValueError:
        return None


_NORMALIZERS: Dict[str, Callable[[Any], Optional[str]]] = {
    "exact": _n_exact,
    "casefold": _n_casefold,
    "e164": _n_e164,
    "datetime-minute": _n_datetime_minute,
    "money": _n_money,
}


# --------------------------------------------------------------------------- #
# Spec + ledger
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FromTool:
    """Declares that an argument must be grounded in prior tool output.

    ``sources`` is one or more ``(tool, key)`` pairs; candidates from all of them
    are pooled, so an argument may legitimately come from any of several tools
    (e.g. a ``doctor_id`` from ``list_doctors`` OR from an existing appointment).
    """

    sources: tuple  # tuple[tuple[str, str], ...] of (tool_name, key)
    normalize: str = "exact"
    allow_single_candidate: bool = True

    @property
    def label(self) -> str:
        return "|".join(f"{t}.{k}" for t, k in self.sources)


def from_tool(
    *sources,
    normalize: str = "exact",
    allow_single_candidate: bool = True,
) -> FromTool:
    """Build a :class:`FromTool` provenance spec for ``@grounded_outputs``.

    Single source::

        slot_start=from_tool("get_slots", "slot_start", normalize="datetime-minute")

    Multiple sources (the value may come from any of them)::

        doctor_id=from_tool(("list_doctors", "doctor_id"),
                            ("list_appointments", "doctor_id"))
    """
    if not sources:
        raise ValueError("from_tool requires at least one (tool, key) source")
    if isinstance(sources[0], str):  # single-source form: from_tool("tool", "key")
        if len(sources) != 2 or not all(isinstance(s, str) for s in sources):
            raise ValueError("single-source form is from_tool(tool, key)")
        pairs = ((sources[0], sources[1]),)
    else:  # multi-source form: from_tool(("t","k"), ("t2","k2"), ...)
        pairs = tuple(tuple(s) for s in sources)
        for s in pairs:
            if len(s) != 2 or not all(isinstance(x, str) for x in s):
                raise ValueError("each source must be a (tool, key) pair of strings")
    if normalize not in _NORMALIZERS:
        raise ValueError(
            f"unknown normalizer {normalize!r}; choose from {sorted(_NORMALIZERS)}"
        )
    return FromTool(sources=pairs, normalize=normalize,
                    allow_single_candidate=allow_single_candidate)


class ToolLedger:
    """Records what tools returned this call, so arguments can be grounded in it.

    The adapter calls :meth:`record` after a read tool returns; provenance
    grounding reads candidates from it. Only the latest result per tool is kept
    (matching how a realtime agent references "the list I was just shown").
    """

    def __init__(self) -> None:
        self._rows: Dict[str, List[dict]] = {}

    def record(self, tool: str, rows: Any) -> None:
        out: List[dict] = []
        for r in rows or []:
            if isinstance(r, dict):
                out.append(dict(r))
        self._rows[tool] = out

    def candidates(self, tool: str, key: str) -> List[Any]:
        out: List[Any] = []
        for r in self._rows.get(tool, ()):  # single dict.get per row (hot path)
            v = r.get(key)
            if v is not None:
                out.append(v)
        return out

    def __len__(self) -> int:
        return sum(len(v) for v in self._rows.values())


# --------------------------------------------------------------------------- #
# Reconciliation engine — the deterministic, fail-closed core.
# --------------------------------------------------------------------------- #


class Status(str, Enum):
    PASS_EXACT = "pass_exact"
    PASS_NORMALIZED = "pass_normalized"
    PASS_SINGLE = "pass_single"
    BLOCK_NO_VALUE = "block_no_value"
    BLOCK_NO_CANDIDATES = "block_no_candidates"
    BLOCK_NO_MATCH = "block_no_match"
    BLOCK_AMBIGUOUS = "block_ambiguous"


_PASS = {Status.PASS_EXACT, Status.PASS_NORMALIZED, Status.PASS_SINGLE}


@dataclass
class Resolution:
    """Verdict for one argument against a tool's candidate values."""

    status: Status
    canonical: Any = None
    candidates: List[Any] = field(default_factory=list)
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status in _PASS


def reconcile(
    value: Any,
    candidates: Any,
    normalize: str = "exact",
    allow_single_candidate: bool = True,
) -> Resolution:
    """Resolve ``value`` against tool ``candidates``. Never forwards a non-candidate.

    Order: raw-exact -> unique normalized -> single-candidate fallback -> block.
    The returned ``canonical`` (when passed) is always one of ``candidates``.
    """
    norm = _NORMALIZERS[normalize]
    cands = list(candidates)

    if value is None or (isinstance(value, str) and not value.strip()):
        return Resolution(Status.BLOCK_NO_VALUE, candidates=cands, reason="no value supplied")
    if not cands:
        return Resolution(
            Status.BLOCK_NO_CANDIDATES, candidates=cands,
            reason="no tool output to ground against (was the lookup tool called?)",
        )

    # 1. raw-exact (object equality, then string equality for int/str drift)
    if value in cands:
        return Resolution(Status.PASS_EXACT, canonical=value, candidates=cands, reason="exact match")
    sval = str(value).strip()
    for c in cands:
        if str(c).strip() == sval:
            return Resolution(Status.PASS_EXACT, canonical=c, candidates=cands, reason="exact match")

    # 2. unique normalized match
    nv = norm(value)
    if nv is not None:
        matches: List[Any] = []
        seen = set()
        for c in cands:
            if norm(c) == nv:
                k = str(c)
                if k not in seen:
                    seen.add(k)
                    matches.append(c)
        if len(matches) == 1:
            return Resolution(
                Status.PASS_NORMALIZED, canonical=matches[0], candidates=cands,
                reason=f"unique '{normalize}' match",
            )
        if len(matches) > 1:
            return Resolution(
                Status.BLOCK_AMBIGUOUS, candidates=cands,
                reason=f"{len(matches)} candidates match under '{normalize}'",
            )

    # 3. single-candidate fallback (the only valid target is the one row shown)
    if allow_single_candidate and len(cands) == 1:
        return Resolution(
            Status.PASS_SINGLE, canonical=cands[0], candidates=cands,
            reason="only one candidate was returned",
        )

    return Resolution(Status.BLOCK_NO_MATCH, candidates=cands, reason="value matches no tool output")


# --------------------------------------------------------------------------- #
# Decorator
# --------------------------------------------------------------------------- #


class _Call:
    """A resolved call: positional args + keyword args ready to invoke the body.

    Lighter than ``inspect.BoundArguments`` for the hot path; exposes the same
    ``.args`` / ``.kwargs`` the wrappers splat into the wrapped function.
    """

    __slots__ = ("args", "kwargs")

    def __init__(self, args, kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


def grounded_outputs(**specs: FromTool) -> Callable:
    """Ground tool arguments against prior tool output (see module docstring).

    Apply *inside* the platform's tool decorator. On a block the body never runs
    and a :class:`SteerBack` is returned; on a pass each guarded argument is
    rewritten to its canonical tool value before the body runs.
    """
    if not specs:
        raise ValueError("@grounded_outputs requires at least one from_tool() spec")
    for name, spec in specs.items():
        if not isinstance(spec, FromTool):
            raise TypeError(
                f"@grounded_outputs: {name!r} must be from_tool(...), got {type(spec).__name__}"
            )

    def decorate(fn: Callable) -> Callable:
        sig = inspect.signature(fn)
        params = sig.parameters
        has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        if not has_var_kw:
            unknown = [n for n in specs if n not in params]
            if unknown:
                raise ValueError(
                    f"@grounded_outputs on {fn.__name__}{sig}: these guarded arguments are "
                    f"not parameters of the function: {unknown}. Check for typos."
                )
        # Frozen at decoration time so the hot path touches tuples, not the dict.
        spec_items = tuple(specs.items())
        spec_names = tuple(specs)

        def _reconcile_args(get_value, ledger):
            """Reconcile every guarded arg via ``get_value(name)``.

            Returns ``(failed, passed, rewrites)`` — ``rewrites`` maps each
            passing arg to its canonical tool value. Shared by both call paths.
            """
            failed: List[ArgFinding] = []
            passed: List[ArgFinding] = []
            rewrites: Dict[str, Any] = {}
            for name, spec in spec_items:
                value = get_value(name)
                cands: List[Any] = []
                if ledger is not None:
                    for tool, key in spec.sources:
                        cands.extend(ledger.candidates(tool, key))
                try:
                    res = reconcile(value, cands, spec.normalize, spec.allow_single_candidate)
                except Exception as exc:  # fail closed
                    logger.exception(
                        "saidso: provenance check errored for %s.%s; blocking.",
                        fn.__name__, name,
                    )
                    res = Resolution(Status.BLOCK_NO_MATCH, reason=f"errored: {exc}")
                gr = GroundingResult(
                    grounded=res.passed,
                    confidence=1.0 if res.passed else 0.0,
                    policy=f"from_tool:{spec.label}",
                    value=value,
                    normalized=res.canonical,
                    reason=res.reason,
                )
                if res.passed:
                    rewrites[name] = res.canonical  # rewrite to canonical
                    passed.append(ArgFinding(name=name, result=gr))
                else:
                    failed.append(ArgFinding(name=name, result=gr))
            return failed, passed, rewrites

        def _finish(ctx, failed, passed):
            if failed:
                logger.info(
                    "blocked %s: ungrounded tool args %s",
                    fn.__name__, [f.name for f in failed],
                    extra={"saidso_event": "block", "saidso_action": fn.__name__,
                           "saidso_args": [f.name for f in failed]},
                )
                return SteerBack(action=fn.__name__, failed=failed, grounded=passed)
            if ctx.ledger is not None:
                ctx.ledger.build(fn.__name__, passed, call_id=ctx.call_id)
            logger.info(
                "grounded %s: %s", fn.__name__, [f.name for f in passed],
                extra={"saidso_event": "pass", "saidso_action": fn.__name__,
                       "saidso_args": [f.name for f in passed]},
            )
            return None  # passed

        def evaluate(args, kwargs):
            ctx = get_context() or CallContext()
            ledger = getattr(ctx, "tools", None)

            # Fast path: the realtime model passes tool args by keyword, so every
            # guarded arg is in kwargs — reconcile in place, skip signature binding.
            if kwargs and all(n in kwargs for n in spec_names):
                failed, passed, rewrites = _reconcile_args(kwargs.__getitem__, ledger)
                steer = _finish(ctx, failed, passed)
                if steer is not None:
                    return steer
                if rewrites:
                    kwargs = {**kwargs, **rewrites}
                return _Call(args, kwargs)

            # Slow path: positional guarded args / defaults — bind the signature.
            try:
                bound = sig.bind(*args, **kwargs)
            except TypeError:
                return None  # let the real function raise its own clear error
            bound.apply_defaults()
            failed, passed, rewrites = _reconcile_args(bound.arguments.get, ledger)
            steer = _finish(ctx, failed, passed)
            if steer is not None:
                return steer
            for name, canonical in rewrites.items():
                bound.arguments[name] = canonical
            return _Call(bound.args, bound.kwargs)

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                outcome = evaluate(args, kwargs)
                if outcome is None:
                    return await fn(*args, **kwargs)
                if isinstance(outcome, SteerBack):
                    return outcome
                return await fn(*outcome.args, **outcome.kwargs)

            awrapper.__provenance_specs__ = specs
            return awrapper

        @functools.wraps(fn)
        def swrapper(*args, **kwargs):
            outcome = evaluate(args, kwargs)
            if outcome is None:
                return fn(*args, **kwargs)
            if isinstance(outcome, SteerBack):
                return outcome
            return fn(*outcome.args, **outcome.kwargs)

        swrapper.__provenance_specs__ = specs
        return swrapper

    return decorate
