"""The ``@grounded`` decorator: the firewall around your action tools.

Wrap a consequential function, declare a policy per argument, and the call is
intercepted: every guarded argument is verified against the transcript before
the body runs. Ungrounded? The body never executes and a :class:`SteerBack` is
returned so the agent re-asks the caller. Grounded? An attestation is written.

Production guarantees:

* **Validated at decoration time** — a policy naming a non-existent parameter
  raises immediately, so a typo can never silently leave a real argument
  unguarded.
* **Fail-closed** — if a grounding check raises unexpectedly, the argument is
  treated as ungrounded (blocked) and the error is logged; a firewall must
  never let a call through because the check crashed.
"""

from __future__ import annotations

import functools
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from ._matching import matcher
from .context import CallContext, get_context
from .policy import DEFAULT_THRESHOLDS, Policy, PolicySpec, as_spec
from .result import ArgFinding, GroundingResult, ReasonCode, SteerBack

logger = logging.getLogger("saidso")

_OVERRIDE_KEYS = ("_context", "_transcript")


@dataclass
class GroundingConfig:
    """Tunables for the firewall.

    - ``thresholds`` / ``raise_on_block`` / ``warn_on_missing_context`` — as before.
    - ``enforce`` — when ``False``, run in **shadow mode**: a would-block is recorded
      to the AttestationLog (``status="shadow_block"``) and logged, but the body runs
      anyway. Calibrate thresholds against real traffic before enforcing.
    - ``steer_style`` — ``"default"`` (developer-facing SteerBack) or ``"spoken"``
      (caller-facing re-ask with no tool/id jargon, safe to say on a voice channel).
    - ``idempotency_key`` — ``callable(args: dict) -> hashable``. After a guarded call
      passes, a repeat with the same key **this call** is blocked as a duplicate,
      de-risking recovery-injection loops that might double-fire a write.
    - ``on_stale`` — how provenance grounding treats candidates from a ledger entry
      past its TTL: ``"warn"`` (default), ``"block"``, or ``"ignore"``.
    """

    thresholds: dict[Policy, float] | None = None
    raise_on_block: bool = False  # default: return SteerBack (slots into tool loops)
    warn_on_missing_context: bool = True
    enforce: bool = True
    steer_style: str = "default"
    idempotency_key: Callable[[dict[str, Any]], Any] | None = None
    on_stale: str = "warn"

    def threshold_for(self, policy: Policy) -> float:
        if self.thresholds and policy in self.thresholds:
            return self.thresholds[policy]
        return DEFAULT_THRESHOLDS[policy]


class GroundingBlocked(Exception):
    """Raised instead of returning a SteerBack when ``raise_on_block=True``."""

    def __init__(self, steer: SteerBack) -> None:
        super().__init__(steer.message)
        self.steer = steer


def grounded(
    _config: GroundingConfig | None = None,
    **arg_policies: Policy | str | PolicySpec,
) -> Callable[..., Any]:
    """Decorator factory. Map argument names to :class:`Policy` values.

    A bare policy uses the default threshold; a :class:`PolicySpec` (from calling a
    member, ``Policy.SPOKEN(normalize=..., threshold=...)``) adds per-argument tuning::

        @grounded(
            name=Policy.SPOKEN,
            family_name=Policy.SPOKEN(normalize="spelled-name"),
            gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6),
            phone=Policy.CALLER_ID,
        )
        async def register_patient(name, family_name, gender, phone): ...
    """
    config = _config or GroundingConfig()
    if not arg_policies:
        raise ValueError("@grounded requires at least one argument policy")
    policies: dict[str, PolicySpec] = {}
    for name, value in arg_policies.items():
        try:
            policies[name] = as_spec(value)
        except ValueError as exc:  # unknown policy string
            raise ValueError(
                f"@grounded: unknown policy {value!r} for argument {name!r}"
            ) from exc

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(fn)
        params = sig.parameters
        var_kw_name = next(
            (n for n, p in params.items() if p.kind is inspect.Parameter.VAR_KEYWORD),
            None,
        )
        accepts_var_kw = var_kw_name is not None
        # Validate at decoration time: every guarded name must be a real param.
        if not accepts_var_kw:
            unknown = [n for n in policies if n not in params]
            if unknown:
                raise ValueError(
                    f"@grounded on {fn.__name__}{sig}: these guarded arguments are "
                    f"not parameters of the function: {unknown}. Check for typos."
                )
        # An override key only collides if the function genuinely declares it.
        strip_keys = [k for k in _OVERRIDE_KEYS if k not in params]

        def evaluate(args, kwargs):
            override_ctx = kwargs.pop("_context", None) if "_context" in strip_keys else None
            override_tr = kwargs.pop("_transcript", None) if "_transcript" in strip_keys else None

            ctx = override_ctx or get_context()
            if ctx is None:
                if config.warn_on_missing_context:
                    logger.warning(
                        "saidso: no call_context active for %s; treating transcript "
                        "as empty (all guarded args will block).", fn.__name__,
                    )
                ctx = CallContext()
            if override_tr is not None:
                ctx = CallContext(
                    transcript=override_tr, metadata=ctx.metadata, now=ctx.now,
                    call_id=ctx.call_id, ledger=ctx.ledger, tools=ctx.tools,
                    seen_keys=ctx.seen_keys,
                )

            try:
                bound = sig.bind_partial(*args, **kwargs)
            except TypeError:
                # Let the real function raise its own clear TypeError.
                return _Pass(args, kwargs)
            bound.apply_defaults()

            def resolve(arg_name):
                if arg_name in bound.arguments and arg_name != var_kw_name:
                    return bound.arguments[arg_name]
                if var_kw_name and var_kw_name in bound.arguments:
                    return bound.arguments[var_kw_name].get(arg_name)
                return None

            failed: list[ArgFinding] = []
            passed: list[ArgFinding] = []
            for name, spec in policies.items():
                policy = spec.policy
                value = resolve(name)
                threshold = (
                    spec.threshold if spec.threshold is not None
                    else config.threshold_for(policy)
                )
                try:
                    result = matcher.check(
                        value, policy, ctx.transcript, ctx, threshold,
                        normalize=spec.normalize,
                    )
                except Exception as exc:  # fail closed: never let a crash open the gate
                    logger.exception(
                        "saidso: grounding check errored for %s.%s; blocking.",
                        fn.__name__, name,
                    )
                    result = GroundingResult(
                        grounded=False, confidence=0.0, policy=policy.value,
                        value=value, reason=f"grounding check errored: {exc}",
                        code=ReasonCode.CHECK_ERROR.value,
                    )
                finding = ArgFinding(name=name, result=result)
                (passed if result.grounded else failed).append(finding)

            # Hard block — enforcing mode only. Shadow mode records and proceeds.
            if failed and config.enforce:
                steer = SteerBack(
                    action=fn.__name__, failed=failed, grounded=passed,
                    style=config.steer_style, code=_block_code(failed),
                )
                logger.info(
                    "blocked %s: ungrounded %s",
                    fn.__name__, [f.name for f in failed],
                    extra={"saidso_event": "block", "saidso_action": fn.__name__,
                           "saidso_args": [f.name for f in failed]},
                )
                return steer

            # Idempotency: a repeat of an already-completed call is refused before
            # the body runs again (de-risks recovery-injection double-fires).
            dup = _idempotency_block(config, ctx, bound.arguments, fn.__name__)
            if dup is not None:
                return dup

            if failed:  # shadow mode: record the would-block, then run the body
                if ctx.ledger is not None:
                    ctx.ledger.build(
                        fn.__name__, failed + passed, call_id=ctx.call_id,
                        status="shadow_block",
                    )
                logger.info(
                    "shadow-blocked %s: ungrounded %s (enforce=False, running anyway)",
                    fn.__name__, [f.name for f in failed],
                    extra={"saidso_event": "shadow_block", "saidso_action": fn.__name__,
                           "saidso_args": [f.name for f in failed]},
                )
                return _Pass(args, kwargs)

            if ctx.ledger is not None:
                ctx.ledger.build(fn.__name__, passed, call_id=ctx.call_id)
            logger.info(
                "grounded %s: %s", fn.__name__, [f.name for f in passed],
                extra={"saidso_event": "pass", "saidso_action": fn.__name__,
                       "saidso_args": [f.name for f in passed]},
            )
            return _Pass(args, kwargs)

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                outcome = evaluate(args, kwargs)
                if isinstance(outcome, SteerBack):
                    if config.raise_on_block:
                        raise GroundingBlocked(outcome)
                    return outcome
                return await fn(*outcome.args, **outcome.kwargs)

            # Expose the policy map for introspection (e.g. the test harness).
            awrapper.__grounded_policies__ = policies  # type: ignore[attr-defined]
            return awrapper

        @functools.wraps(fn)
        def swrapper(*args, **kwargs):
            outcome = evaluate(args, kwargs)
            if isinstance(outcome, SteerBack):
                if config.raise_on_block:
                    raise GroundingBlocked(outcome)
                return outcome
            return fn(*outcome.args, **outcome.kwargs)

        swrapper.__grounded_policies__ = policies  # type: ignore[attr-defined]
        return swrapper

    return decorate


def _block_code(failed: list[ArgFinding]) -> str:
    """The machine-readable code for a multi-arg block (first failing arg's code)."""
    for f in failed:
        if f.result.code:
            return f.result.code
    return ReasonCode.NOT_IN_TRANSCRIPT.value


def _idempotency_block(
    config: GroundingConfig, ctx: CallContext, arguments: dict[str, Any], action: str
) -> SteerBack | None:
    """Refuse a repeat of an already-completed call (see GroundingConfig)."""
    if config.idempotency_key is None:
        return None
    try:
        key = config.idempotency_key(dict(arguments))
    except Exception:  # a broken key function must not crash the call — skip dedupe
        logger.exception("saidso: idempotency_key raised for %s; skipping dedupe.", action)
        return None
    if key is None:
        return None
    if key in ctx.seen_keys:
        msg = (
            "You're already set — I won't repeat that."
            if config.steer_style == "spoken"
            else f"{action} was already completed on this call (idempotency key seen); "
            "not running it again."
        )
        logger.info(
            "blocked duplicate %s", action,
            extra={"saidso_event": "block", "saidso_action": action,
                   "saidso_args": ["<duplicate>"]},
        )
        return SteerBack(
            action=action, failed=[], message=msg,
            style=config.steer_style, code=ReasonCode.DUPLICATE.value,
        )
    ctx.seen_keys.add(key)
    return None


@dataclass
class _Pass:
    """Internal: the (possibly override-stripped) args to forward to the body."""

    args: tuple[Any, ...]
    kwargs: dict[str, Any]
