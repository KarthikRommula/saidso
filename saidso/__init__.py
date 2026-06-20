"""saidso — a grounding firewall for action-taking AI agents.

Sit between an agent and its consequential tools. Refuse to let the agent
commit any argument that isn't grounded in what the user actually said — and
keep a transcript-linked audit trail for every action that does run.

Quick start::

    from saidso import grounded, Policy, Transcript, call_context, AttestationLog

    @grounded(name=Policy.SPOKEN, dob=Policy.SPOKEN)
    def register_patient(name, dob): ...

    tr = Transcript()
    tr.add_user("Hi, this is Maria Gomez.")
    log = AttestationLog()

    with call_context(tr, ledger=log):
        result = register_patient(name="John Doe", dob="1990-01-01")
        # -> SteerBack(blocked=True): nothing was said about John Doe / that DOB
"""

from __future__ import annotations

from .attestation import Attestation, AttestationLog
from .context import CallContext, call_context, get_context, set_context, reset_context
from .grounding import GroundingBlocked, GroundingConfig, grounded
from .policy import DEFAULT_THRESHOLDS, Policy
from .provenance import (
    FromTool,
    Resolution,
    Status,
    ToolLedger,
    from_tool,
    grounded_outputs,
    reconcile,
)
from .observe import EventRecorder, enable_pretty_logging, summary
from .result import ArgFinding, GroundingResult, Span, SteerBack
from .speech import (
    BlockedFact,
    Fact,
    SpokenName,
    UngroundedSpeech,
    check_spoken_names,
    fact,
    find_name_mentions,
    find_ungrounded_names,
    render_spoken,
    try_render_spoken,
)
from .transcript import AGENT, SYSTEM, USER, Transcript, Turn

__version__ = "0.4.0"

__all__ = [
    "grounded",
    "Policy",
    "Transcript",
    "Turn",
    "USER",
    "AGENT",
    "SYSTEM",
    "call_context",
    "CallContext",
    "get_context",
    "set_context",
    "reset_context",
    "SteerBack",
    "GroundingResult",
    "GroundingConfig",
    "GroundingBlocked",
    "grounded_outputs",
    "from_tool",
    "FromTool",
    "ToolLedger",
    "reconcile",
    "Resolution",
    "Status",
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
    "Span",
    "ArgFinding",
    "Attestation",
    "AttestationLog",
    "enable_pretty_logging",
    "summary",
    "EventRecorder",
    "DEFAULT_THRESHOLDS",
    "__version__",
]
