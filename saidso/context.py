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
from typing import Any, Dict, Optional

from .transcript import Transcript


@dataclass
class CallContext:
    """Everything the firewall needs to judge one call."""

    transcript: Transcript = field(default_factory=Transcript)
    metadata: Dict[str, Any] = field(default_factory=dict)
    now: Optional[date] = None
    call_id: Optional[str] = None
    ledger: Any = None  # AttestationLog | None (avoid import cycle)
    tools: Any = None  # provenance.ToolLedger | None (avoid import cycle)


_CURRENT: contextvars.ContextVar[Optional[CallContext]] = contextvars.ContextVar(
    "saidso_call_context", default=None
)


def get_context() -> Optional[CallContext]:
    return _CURRENT.get()


def set_context(ctx: Optional[CallContext]) -> contextvars.Token:
    return _CURRENT.set(ctx)


def reset_context(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


@contextmanager
def call_context(
    transcript: Optional[Transcript] = None,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    now: Optional[date] = None,
    call_id: Optional[str] = None,
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
