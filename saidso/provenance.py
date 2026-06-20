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
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from .context import CallContext, get_context
from .result import ArgFinding, GroundingResult, ReasonCode, SteerBack

logger = logging.getLogger("saidso")


# --------------------------------------------------------------------------- #
# Normalizers — type-aware equality for "the model rebuilt the value" cases.
# Each maps a raw value to a comparable key, or None if it can't be normalized.
# --------------------------------------------------------------------------- #


def _n_exact(v: Any) -> str | None:
    return None if v is None else str(v).strip()


def _n_casefold(v: Any) -> str | None:
    return None if v is None else str(v).strip().casefold()


def _n_e164(v: Any) -> str | None:
    if v is None:
        return None
    digits = "".join(c for c in str(v) if c.isdigit())
    return ("+" + digits) if digits else None


# Fast path: the wall-clock minute as written, for standard ISO-8601 strings
# (`YYYY-MM-DDTHH:MM...` or with a space). Avoids the cost of full datetime
# parsing on every candidate, and handles 'Z' / any offset without Python 3.11.
_ISO_MINUTE_RE = re.compile(r"\s*(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")


def _n_datetime_minute(v: Any) -> str | None:
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


def _n_money(v: Any) -> str | None:
    if v is None:
        return None
    cleaned = "".join(c for c in str(v) if c.isdigit() or c in ".-")
    try:
        return f"{float(cleaned):.2f}"
    except ValueError:
        return None


_NORMALIZERS: dict[str, Callable[[Any], str | None]] = {
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

    sources: tuple[tuple[str, str], ...]  # of (tool_name, key)
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
    pairs: tuple[tuple[str, str], ...]
    if isinstance(sources[0], str):  # single-source form: from_tool("tool", "key")
        if len(sources) != 2 or not all(isinstance(s, str) for s in sources):
            raise ValueError("single-source form is from_tool(tool, key)")
        pairs = ((sources[0], sources[1]),)
    else:  # multi-source form: from_tool(("t","k"), ("t2","k2"), ...)
        for s in sources:
            if len(s) != 2 or not all(isinstance(x, str) for x in s):
                raise ValueError("each source must be a (tool, key) pair of strings")
        pairs = tuple((s[0], s[1]) for s in sources)
    if normalize not in _NORMALIZERS:
        raise ValueError(
            f"unknown normalizer {normalize!r}; choose from {sorted(_NORMALIZERS)}"
        )
    return FromTool(sources=pairs, normalize=normalize,
                    allow_single_candidate=allow_single_candidate)


@dataclass
class _LedgerEntry:
    """Rows a tool returned, plus when and from where (for freshness/audit)."""

    rows: list[dict[str, Any]]
    ts: float
    ttl_s: float | None = None
    source: str | None = None

    def is_stale(self, now: float) -> bool:
        return self.ttl_s is not None and (now - self.ts) > self.ttl_s


class ToolLedger:
    """Records what tools returned this call, so arguments can be grounded in it.

    The adapter calls :meth:`record` after a read tool returns; provenance
    grounding reads candidates from it. Only the latest result per tool is kept
    (matching how a realtime agent references "the list I was just shown").

    Each entry carries a timestamp and optional ``ttl_s`` / ``source`` so grounding
    can flag provenance that may be stale (e.g. pre-seeded from dispatch metadata or
    rebuilt from a cache hit). See :meth:`is_stale` and ``GroundingConfig.on_stale``.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LedgerEntry] = {}

    def record(
        self,
        tool: str,
        rows: Any,
        *,
        ttl_s: float | None = None,
        source: str | None = None,
    ) -> None:
        out: list[dict[str, Any]] = []
        for r in rows or []:
            if isinstance(r, dict):
                out.append(dict(r))
        self._entries[tool] = _LedgerEntry(
            rows=out, ts=time.time(), ttl_s=ttl_s, source=source
        )

    def candidates(self, tool: str, key: str) -> list[Any]:
        out: list[Any] = []
        entry = self._entries.get(tool)
        for r in entry.rows if entry else ():  # single dict.get per row (hot path)
            v = r.get(key)
            if v is not None:
                out.append(v)
        return out

    def is_stale(self, tool: str, now: float | None = None) -> bool:
        """True if ``tool``'s recorded output is past its TTL (never if no TTL set)."""
        entry = self._entries.get(tool)
        if entry is None:
            return False
        return entry.is_stale(time.time() if now is None else now)

    def source_of(self, tool: str) -> str | None:
        entry = self._entries.get(tool)
        return entry.source if entry else None

    def __len__(self) -> int:
        return sum(len(e.rows) for e in self._entries.values())


# --------------------------------------------------------------------------- #
# Reconciliation engine — the deterministic, fail-closed core.
# --------------------------------------------------------------------------- #


class Status(str, Enum):
    # "pass_*" are resolution outcomes, not credentials (bandit B105 false positive).
    PASS_EXACT = "pass_exact"  # nosec B105
    PASS_NORMALIZED = "pass_normalized"  # nosec B105
    PASS_SINGLE = "pass_single"  # nosec B105
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
    candidates: list[Any] = field(default_factory=list)
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
        return Resolution(
            Status.PASS_EXACT, canonical=value, candidates=cands, reason="exact match"
        )
    sval = str(value).strip()
    for c in cands:
        if str(c).strip() == sval:
            return Resolution(
                Status.PASS_EXACT, canonical=c, candidates=cands, reason="exact match"
            )

    # 2. unique normalized match
    nv = norm(value)
    if nv is not None:
        matches: list[Any] = []
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

    return Resolution(
        Status.BLOCK_NO_MATCH, candidates=cands, reason="value matches no tool output"
    )


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


def _outputs_block_code(failed: list[ArgFinding]) -> str:
    """The machine-readable code for a provenance block (first failing arg's code)."""
    for f in failed:
        if f.result.code:
            return f.result.code
    return ReasonCode.WRONG_TOOL_SOURCE.value


_STATUS_CODE = {
    Status.PASS_EXACT: ReasonCode.OK_EXACT.value,
    Status.PASS_NORMALIZED: ReasonCode.OK_NORMALIZED.value,
    Status.PASS_SINGLE: ReasonCode.OK_SINGLE.value,
    Status.BLOCK_NO_VALUE: ReasonCode.NO_VALUE.value,
    Status.BLOCK_NO_CANDIDATES: ReasonCode.WRONG_TOOL_SOURCE.value,
    Status.BLOCK_NO_MATCH: ReasonCode.WRONG_TOOL_SOURCE.value,
    Status.BLOCK_AMBIGUOUS: ReasonCode.AMBIGUOUS.value,
}


def grounded_outputs(
    _config: Any = None, **specs: FromTool
) -> Callable[..., Any]:
    """Ground tool arguments against prior tool output (see module docstring).

    Apply *inside* the platform's tool decorator. On a block the body never runs
    and a :class:`SteerBack` is returned; on a pass each guarded argument is
    rewritten to its canonical tool value before the body runs.

    Pass a :class:`~saidso.GroundingConfig` as the first positional argument to opt
    into ``enforce`` (shadow mode), ``idempotency_key`` (double-write guard),
    ``on_stale`` (provenance-freshness policy) and ``steer_style`` — e.g.
    ``@grounded_outputs(GroundingConfig(idempotency_key=lambda a: a["slot_start"]),
    slot_start=from_tool("get_slots", "slot_start"))``.
    """
    if not specs:
        raise ValueError("@grounded_outputs requires at least one from_tool() spec")
    for name, spec in specs.items():
        if not isinstance(spec, FromTool):
            raise TypeError(
                f"@grounded_outputs: {name!r} must be from_tool(...), got {type(spec).__name__}"
            )
    # Duck-typed config read (avoids importing GroundingConfig — no import cycle).
    enforce = bool(getattr(_config, "enforce", True))
    idem = getattr(_config, "idempotency_key", None)
    on_stale = getattr(_config, "on_stale", "warn")
    steer_style = getattr(_config, "steer_style", "default")

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
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
            failed: list[ArgFinding] = []
            passed: list[ArgFinding] = []
            rewrites: dict[str, Any] = {}
            for name, spec in spec_items:
                value = get_value(name)
                cands: list[Any] = []
                stale = False
                if ledger is not None:
                    for tool, key in spec.sources:
                        cands.extend(ledger.candidates(tool, key))
                        if getattr(ledger, "is_stale", None) and ledger.is_stale(tool):
                            stale = True
                if stale and on_stale == "block":
                    gr = GroundingResult(
                        grounded=False, confidence=0.0,
                        policy=f"from_tool:{spec.label}", value=value,
                        reason="provenance is stale (past TTL)",
                        code=ReasonCode.STALE_PROVENANCE.value,
                    )
                    failed.append(ArgFinding(name=name, result=gr))
                    continue
                if stale and on_stale == "warn":
                    logger.warning(
                        "saidso: grounding %s.%s against stale provenance "
                        "(ledger entry past TTL).", fn.__name__, name,
                    )
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
                    code=_STATUS_CODE.get(res.status, ""),
                )
                if res.passed:
                    rewrites[name] = res.canonical  # rewrite to canonical
                    passed.append(ArgFinding(name=name, result=gr))
                else:
                    failed.append(ArgFinding(name=name, result=gr))
            return failed, passed, rewrites

        def _finish(ctx, failed, passed, args_dict):
            # Hard block — enforcing mode only.
            if failed and enforce:
                logger.info(
                    "blocked %s: ungrounded tool args %s",
                    fn.__name__, [f.name for f in failed],
                    extra={"saidso_event": "block", "saidso_action": fn.__name__,
                           "saidso_args": [f.name for f in failed]},
                )
                return SteerBack(
                    action=fn.__name__, failed=failed, grounded=passed,
                    style=steer_style, code=_outputs_block_code(failed),
                )
            # Idempotency: refuse a repeat of an already-committed write this call.
            if idem is not None:
                try:
                    key = idem(dict(args_dict))
                except Exception:
                    logger.exception(
                        "saidso: idempotency_key raised for %s; skipping dedupe.",
                        fn.__name__,
                    )
                    key = None
                if key is not None:
                    if key in ctx.seen_keys:
                        logger.info(
                            "blocked duplicate %s", fn.__name__,
                            extra={"saidso_event": "block",
                                   "saidso_action": fn.__name__,
                                   "saidso_args": ["<duplicate>"]},
                        )
                        msg = (
                            "You're already set — I won't repeat that."
                            if steer_style == "spoken"
                            else f"{fn.__name__} was already committed on this call "
                            "(idempotency key seen); not running it again."
                        )
                        return SteerBack(
                            action=fn.__name__, failed=[], message=msg,
                            style=steer_style, code=ReasonCode.DUPLICATE.value,
                        )
                    ctx.seen_keys.add(key)
            if failed:  # shadow mode: record the would-block, then run the body
                if ctx.ledger is not None:
                    ctx.ledger.build(
                        fn.__name__, failed + passed, call_id=ctx.call_id,
                        status="shadow_block",
                    )
                logger.info(
                    "shadow-blocked %s: ungrounded tool args %s (enforce=False)",
                    fn.__name__, [f.name for f in failed],
                    extra={"saidso_event": "shadow_block",
                           "saidso_action": fn.__name__,
                           "saidso_args": [f.name for f in failed]},
                )
                return None
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
                result_kwargs = {**kwargs, **rewrites} if rewrites else kwargs
                steer = _finish(ctx, failed, passed, result_kwargs)
                if steer is not None:
                    return steer
                return _Call(args, result_kwargs)

            # Slow path: positional guarded args / defaults — bind the signature.
            try:
                bound = sig.bind(*args, **kwargs)
            except TypeError:
                return None  # let the real function raise its own clear error
            bound.apply_defaults()
            failed, passed, rewrites = _reconcile_args(bound.arguments.get, ledger)
            for name, canonical in rewrites.items():
                bound.arguments[name] = canonical
            steer = _finish(ctx, failed, passed, dict(bound.arguments))
            if steer is not None:
                return steer
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

            awrapper.__provenance_specs__ = specs  # type: ignore[attr-defined]
            return awrapper

        @functools.wraps(fn)
        def swrapper(*args, **kwargs):
            outcome = evaluate(args, kwargs)
            if outcome is None:
                return fn(*args, **kwargs)
            if isinstance(outcome, SteerBack):
                return outcome
            return fn(*outcome.args, **outcome.kwargs)

        swrapper.__provenance_specs__ = specs  # type: ignore[attr-defined]
        return swrapper

    return decorate
