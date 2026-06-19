"""Latency microbenchmark: saidso firewall overhead vs the hand-rolled checks it replaced.

Everything saidso does at a tool call is in-process, deterministic Python — no
network, no LLM. This measures per-call wall time so we can compare BEFORE (the
old hand-rolled helpers) vs AFTER (saidso) and put both next to a single voice
pipeline step.
"""

from __future__ import annotations

import re
import statistics
import time

from saidso import Policy, Transcript, call_context, from_tool, grounded_outputs
from saidso.grounding import grounded
from saidso.provenance import ToolLedger, reconcile


def bench(label, fn, iters=50_000, warmup=2_000):
    for _ in range(warmup):
        fn()
    s = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        s.append(time.perf_counter_ns() - t0)
    s.sort()
    us = lambda ns: ns / 1000.0
    print(
        f"{label:<46} "
        f"mean {us(statistics.mean(s)):7.2f}us  "
        f"p50 {us(s[len(s)//2]):7.2f}us  "
        f"p95 {us(s[int(len(s)*0.95)]):7.2f}us  "
        f"p99 {us(s[int(len(s)*0.99)]):7.2f}us"
    )


# --------------------------- realistic fixtures --------------------------- #

SLOTS = [
    {"slot_start": f"2026-05-{d:02d}T{h:02d}:00:00+05:30"}
    for d in (22, 23) for h in (9, 11, 14, 17)
]  # 8 candidate slots across 2 days
SLOT_VALUES = [s["slot_start"] for s in SLOTS]
RECON = "2026-05-22T17:00:00-05:00"  # model rebuilt the tz wrong

APPTS = [{"appointment_id": f"appt_{i}"} for i in range(3)]

TRANSCRIPT = Transcript()
for u in [
    "Hi, this is Maria Gomez calling.",
    "My date of birth is June 5th, 1990.",
    "I'd like to book a cleaning sometime next week.",
    "Yes that's right.",
    "Thank you so much.",
]:
    TRANSCRIPT.add_user(u)
    TRANSCRIPT.add_agent("Sure, let me help with that.")


# ------------------------------- BEFORE ----------------------------------- #
# Reimplementations of the deleted hand-rolled helpers, for an apples-to-apples
# before/after.

def old_resolve_slot():
    given, starts = RECON, SLOT_VALUES
    if not given or given in starts:
        return given
    minute = given[:16]
    for s in starts:
        if s[:16] == minute:
            return s
    return given


_MONTH_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)

def old_assert_dob_spoken():
    text = TRANSCRIPT.user_text().lower()
    year = "1990"
    return year in text or bool(_MONTH_RE.search(text))


# -------------------------------- AFTER ----------------------------------- #

def after_reconcile_slot():
    return reconcile(RECON, SLOT_VALUES, normalize="datetime-minute")

def after_reconcile_id():
    return reconcile("appt_1", [a["appointment_id"] for a in APPTS])


# Full decorated tool-call paths (context setup + check + arg rewrite), sync to
# exclude event-loop overhead that isn't saidso's.

@grounded_outputs(slot_start=from_tool("get_slots", "slot_start", normalize="datetime-minute"))
def book(slot_start):
    return slot_start

_LEDGER = ToolLedger()
_LEDGER.record("get_slots", SLOTS)

def after_provenance_tool_call():
    with call_context(tools=_LEDGER):
        return book(slot_start=RECON)


@grounded(given_name=Policy.SPOKEN, date_of_birth=Policy.SPOKEN)
def register(given_name, date_of_birth):
    return True

def after_transcript_tool_call():
    with call_context(TRANSCRIPT):
        return register(given_name="Maria", date_of_birth="1990-06-05")


if __name__ == "__main__":
    try:
        import rapidfuzz  # noqa: F401
        backend = "rapidfuzz"
    except ImportError:
        backend = "difflib (stdlib fallback)"
    print(f"fuzzy backend: {backend}\n")

    print("--- BEFORE (hand-rolled, in-process) ---")
    bench("old _resolve_slot (timestamp recovery)", old_resolve_slot)
    bench("old _assert_dob_spoken (regex)", old_assert_dob_spoken)

    print("\n--- AFTER (saidso engine only) ---")
    bench("reconcile slot_start (datetime-minute)", after_reconcile_slot)
    bench("reconcile appointment_id (exact)", after_reconcile_id)

    print("\n--- AFTER (saidso full decorated tool call) ---")
    bench("@provenance_tool book_appointment", after_provenance_tool_call)
    bench("@grounded register_patient (transcript)", after_transcript_tool_call)
