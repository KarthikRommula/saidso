"""Per-call context: the transcript, metadata, clock and attestation sink.

Adapters set this once per call (via :func:`call_context`); the ``@grounded``
decorator reads it implicitly so action functions stay clean. Values can also
be passed explicitly to the decorated call via ``_transcript=`` / ``_context=``.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .transcript import Transcript


@dataclass
class CallContext:
    """Everything the firewall needs to judge one call."""

    transcript: Transcript = field(default_factory=Transcript)
    metadata: dict[str, Any] = field(default_factory=dict)
    now: date | None = None
    call_id: str | None = None
    ledger: Any = None  # AttestationLog | None (avoid import cycle)
    tools: Any = None  # provenance.ToolLedger | None (avoid import cycle)
    seen_keys: set[Any] = field(default_factory=set)  # idempotency guard (this call)


_CURRENT: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar(
    "saidso_call_context", default=None
)


def get_context() -> CallContext | None:
    return _CURRENT.get()


def set_context(ctx: CallContext | None) -> contextvars.Token[CallContext | None]:
    return _CURRENT.set(ctx)


def reset_context(token: contextvars.Token[CallContext | None]) -> None:
    _CURRENT.reset(token)


@contextmanager
def call_context(
    transcript: Transcript | None = None,
    *,
    metadata: dict[str, Any] | None = None,
    now: date | None = None,
    call_id: str | None = None,
    ledger: Any = None,
    tools: Any = None,
):
    """Scope a :class:`CallContext` for the duration of a call.

    Example::

        with call_context(transcript, metadata={"caller_id": "+1..."}, ledger=log):
            await register_patient(...)
    """
    ctx = CallContext(
        transcript=transcript if transcript is not None else Transcript(),
        metadata=metadata or {},
        now=now,
        call_id=call_id,
        ledger=ledger,
        tools=tools,
    )
    token = _CURRENT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT.reset(token)
