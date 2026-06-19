# Changelog

## 0.3.0

Multi-source provenance + a best-effort speech monitor for the residual
"agent said a name it made up" gap.

### New
- **Multi-source `from_tool`** — a provenance-grounded argument may come from any
  of several tools: `from_tool(("list_doctors","doctor_id"),
  ("list_appointments","doctor_id"))`. Candidates from all sources are pooled.
  The single-source form `from_tool(tool, key, ...)` is unchanged.
- **`saidso.speech`** (PARTIAL, best-effort) — `find_ungrounded_names` /
  `check_spoken_names`: a *post-turn* check that flags honorific+name mentions
  ("Dr. X") in the agent's transcript that aren't in the ground-truth set a tool
  returned. Reactive and heuristic — pair it with provenance grounding, which
  makes the *action* safe deterministically. Not a guarantee.

## 0.2.1

Performance. No API or behavior change.

- `datetime-minute` normalizer uses a regex fast-path instead of full
  `datetime.fromisoformat` parsing per candidate — ~4.5x faster slot
  reconciliation (≈24us → ≈5us), and now handles a trailing `Z` / any offset
  without requiring Python 3.11. Falls back to a real parse for non-standard
  shapes. Provenance-grounded tool calls drop to ≈13us end-to-end.

## 0.2.0

Tool-output provenance grounding — ground a tool argument against what an
**earlier tool returned** this call, not just against what the caller said.
Absorbs the two most common realtime voice-agent bugs: the model inventing an
opaque id, or reconstructing a value (a timestamp from "5 PM", a phone number
from digits) instead of echoing the canonical one a tool handed it.

### New
- `@grounded_outputs(arg=from_tool("list_doctors", "doctor_id"))` decorator:
  blocks-and-steers when an argument doesn't trace to a real tool output, and
  rewrites a passing argument to its canonical value before the body runs.
- `ToolLedger`: records what tools returned this call (`record` / `candidates`);
  passed to `call_context(..., tools=ledger)`.
- `reconcile()` engine + `from_tool` / `FromTool` specs. Type-aware normalizers:
  `exact`, `casefold`, `e164`, `datetime-minute`, `money`.
- Fail-closed contract: a value passes only via raw-exact, unique-normalized, or
  single-candidate resolution — the firewall never forwards a non-tool value.

## 0.1.0

First release. A grounding firewall for action-taking agents.

### Core
- `@grounded` decorator: per-argument grounding policies, block-and-steer on
  failure, attestation on success. Sync **and** async tools.
- Policies: `SPOKEN`, `CONFIRMED`, `CALLER_ID`, `INFERABLE`.
- `Transcript` buffer, `call_context` plumbing (contextvars).
- Deterministic matcher with number-word / year / date / phone / text
  normalization. Uses `rapidfuzz` if installed, stdlib `difflib` otherwise
  (zero required dependencies).
- `AttestationLog`: in-memory + optional JSONL provenance ledger.
- `SteerBack` return contract with auto-generated re-ask messages.
- `saidso.testing.GroundingCase`: replay harness for CI gates.

### Production hardening
- **Fail-closed**: a matcher exception blocks the call and logs, never crashes
  or lets it through.
- **Decoration-time validation**: guarding a non-existent parameter raises
  immediately (typos can't leave real args unguarded); unknown policy strings
  and empty policy sets raise.
- **No digit-substring over-matching**: numbers match as whole values only
  (`"2"` is not grounded by `"20"`).
- **No short-string fuzzy over-matching**: tokens shorter than 4 chars require
  exact word matches; multi-token values require every token to match.
- **Type coercion**: `date` / `datetime` / `int` / `float` / `bool` / `Decimal`
  arguments are rendered deterministically before comparison.
- **`CONFIRMED` tolerates filler/backchannel** turns between read-back and the
  caller's "yes".
- **Comma-grouped numbers** (`1,250`) parse correctly.
- `VAR_KEYWORD` (`**kwargs`) functions: guarded args resolved from the kwargs
  dict.
- Observability via the `saidso` logger; `py.typed` ships type information.
