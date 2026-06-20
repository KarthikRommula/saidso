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
from typing import Any, Callable, Dict, List, Optional, Union

from ._matching import matcher
from .context import CallContext, get_context
from .policy import DEFAULT_THRESHOLDS, Policy
from .result import ArgFinding, GroundingResult, SteerBack

logger = logging.getLogger("saidso")

_OVERRIDE_KEYS = ("_context", "_transcript")


@dataclass
class GroundingConfig:
    """Tunables for the firewall."""

    thresholds: Optional[Dict[Policy, float]] = None
    raise_on_block: bool = False  # default: return SteerBack (slots into tool loops)
    warn_on_missing_context: bool = True

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
    _config: Optional[GroundingConfig] = None,
    **arg_policies: Union[Policy, str],
) -> Callable:
    """Decorator factory. Map argument names to :class:`Policy` values.

    Example::

        @grounded(name=Policy.SPOKEN, dob=Policy.SPOKEN, phone=Policy.CALLER_ID)
        async def register_patient(name, dob, phone): ...
    """
    config = _config or GroundingConfig()
    if not arg_policies:
        raise ValueError("@grounded requires at least one argument policy")
    policies: Dict[str, Policy] = {}
    for name, value in arg_policies.items():
        try:
            policies[name] = value if isinstance(value, Policy) else Policy(value)
        except ValueError as exc:  # unknown policy string
            raise ValueError(
                f"@grounded: unknown policy {value!r} for argument {name!r}"
            ) from exc

    def decorate(fn: Callable) -> Callable:
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
                    call_id=ctx.call_id, ledger=ctx.ledger,
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

            failed: List[ArgFinding] = []
            passed: List[ArgFinding] = []
            for name, policy in policies.items():
                value = resolve(name)
                try:
                    result = matcher.check(
                        value, policy, ctx.transcript, ctx, config.threshold_for(policy)
                    )
                except Exception as exc:  # fail closed: never let a crash open the gate
                    logger.exception(
                        "saidso: grounding check errored for %s.%s; blocking.",
                        fn.__name__, name,
                    )
                    result = GroundingResult(
                        grounded=False, confidence=0.0, policy=policy.value,
                        value=value, reason=f"grounding check errored: {exc}",
                    )
                finding = ArgFinding(name=name, result=result)
                (passed if result.grounded else failed).append(finding)

            if failed:
                steer = SteerBack(action=fn.__name__, failed=failed, grounded=passed)
                logger.info(
                    "blocked %s: ungrounded %s",
                    fn.__name__, [f.name for f in failed],
                    extra={"saidso_event": "block", "saidso_action": fn.__name__,
                           "saidso_args": [f.name for f in failed]},
                )
                return steer

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

            awrapper.__grounded_policies__ = policies
            return awrapper

        @functools.wraps(fn)
        def swrapper(*args, **kwargs):
            outcome = evaluate(args, kwargs)
            if isinstance(outcome, SteerBack):
                if config.raise_on_block:
                    raise GroundingBlocked(outcome)
                return outcome
            return fn(*outcome.args, **outcome.kwargs)

        swrapper.__grounded_policies__ = policies
        return swrapper

    return decorate


@dataclass
class _Pass:
    """Internal: the (possibly override-stripped) args to forward to the body."""

    args: tuple
    kwargs: dict
