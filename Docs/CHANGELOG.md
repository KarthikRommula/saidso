# Changelog

## 0.4.0

Deterministic grounded speech — the production side of "reads". Make a
consequential *spoken* fact 100% accurate by never letting the model say it:
build the line from grounded data and speak it with your own TTS.

### New
- **`saidso.say`** — `render_spoken(template, ledger=..., **facts)` renders a
  spoken line in which every dynamic fact is reconciled against real tool output
  (the same fail-closed engine as `grounded_outputs`) and substituted with its
  *canonical* value. If any fact can't be grounded, nothing is returned —
  `UngroundedSpeech` is raised — so a fabricated value can never be spoken.
  `try_render_spoken(...)` returns `None` instead of raising.
- **`fact(value, *sources, normalize=..., render=...)`** declares an interpolated
  value, its tool-output provenance, and an optional deterministic renderer
  (e.g. ISO timestamp -> "5:00 PM"). Unlike writes, `allow_single_candidate`
  defaults to **False**: speaking the only name on file in place of one that was
  never returned is the silent error reads must avoid.
- **TTS-agnostic.** saidso never produces audio — it returns the verified string;
  you speak it with whatever TTS you bring. This is the deterministic complement
  to the best-effort `find_ungrounded_names` post-turn monitor.

### Project structure
- Reorganized the package for clarity (public API unchanged — `from saidso import …`
  is identical). Reads moved under a `speech/` subpackage (`render` = the
  deterministic guarantee, `monitor` = best-effort detection); the fuzzy-matching
  engine moved to a private `_matching/` subpackage (`matcher`, `normalize`,
  `fuzz`). Added `Docs/ARCHITECTURE.md` (layout + vocabulary reference). The
  quickstart demo now lives in `examples/quickstart.py` (it previously shadowed
  the package as a root-level `saidso.py`).

### Observability
- Every decision now emits one structured event on the `saidso` logger
  (`saidso_event` = `pass`/`block`, with `saidso_action`/`saidso_args`).
- **`saidso.observe`** (zero-dependency) — `enable_pretty_logging()` for a
  colored ✓/✗ live stream (auto-disables off a TTY / under `NO_COLOR`, enables
  Windows VT mode), `EventRecorder` to capture the stream, and `summary()` for an
  end-of-run counts + per-decision box.

## 0.3.1

Hot-path latency. No API or behaviour change — the fail-closed guarantee is
identical.

- **`@grounded_outputs` keyword fast path** — the realtime model passes tool
  arguments by keyword, so the common call now reconciles directly against
  `kwargs` and skips `inspect.Signature.bind` / `apply_defaults` entirely,
  falling back to a full bind only for positional or defaulted guarded args.
- **`ToolLedger.candidates`** does a single `dict.get` per row instead of two.
- Net: a provenance-grounded write call drops to ≈12us end-to-end (p50 ≈11us)
  — ~1/2000th of a single backend round trip.

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
