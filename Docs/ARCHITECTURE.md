# saidso — Architecture & Vocabulary

A grounding firewall for action-taking AI agents. It sits between an agent and its
consequences and enforces one rule: **nothing is committed (a tool argument) or
spoken (a fact) unless it traces back to something real — what the user said, or
what a tool returned.** Anything ungrounded is blocked (fail-closed).

---

## Package layout

```
saidso/
├─ __init__.py        Public API surface (everything below is re-exported here)
├─ policy.py          Policy enum + default confidence thresholds
├─ context.py         CallContext, call_context() — the per-call scope
├─ transcript.py      Transcript, Turn — what was said this call
├─ result.py          GroundingResult, ArgFinding, SteerBack, Span — verdicts
├─ attestation.py     Attestation, AttestationLog — the audit trail of passes
│
├─ grounding.py       @grounded            — ground args against the CONVERSATION
├─ provenance.py      @grounded_outputs    — ground args against TOOL OUTPUT
│                     from_tool, ToolLedger, reconcile, Resolution, Status
│
├─ speech/            The "reads" side — guarding what the agent SAYS
│  ├─ render.py       render_spoken, fact — DETERMINISTIC grounded speech (the guarantee)
│  └─ monitor.py      find_ungrounded_names — BEST-EFFORT post-turn detection
│
├─ _matching/         (private) the SPOKEN/CONFIRMED matching engine
│  ├─ matcher.py      policy checkers
│  ├─ normalize.py    number / date / phone / name normalization
│  └─ fuzz.py         rapidfuzz with a stdlib difflib fallback
│
├─ observe.py         enable_pretty_logging, EventRecorder, summary — observability
└─ testing.py         GroundingCase, replay — CI replay harness
```

**Two layers, on purpose.** The top-level modules are the framework's *first-class
concepts* (flat, because each is a public idea). `speech/` groups the two reads
tools that form one domain. `_matching/` hides the fuzzy-matching machinery behind a
leading underscore — internal, may change without notice.

---

## The mental model

An agent can lie in exactly two places. saidso defends both:

| It lies about… | Example | Defense | Module |
|---|---|---|---|
| **What it DOES** (a tool argument) | books slot `#99999` that doesn't exist | ground the argument | `grounding`, `provenance` |
| **What it SAYS** (a spoken fact) | "booked with Dr. House" (no such doctor) | ground the spoken text | `speech` |

---

## Vocabulary

### Decorators (wrap your tool functions)

| Name | Meaning | Grounds against |
|---|---|---|
| `@grounded(arg=Policy.X)` | the argument must satisfy a policy | the **conversation** (transcript / metadata) |
| `@grounded_outputs(arg=from_tool(...))` | the argument must trace to a prior tool result | the **ToolLedger** (tool output) |

Both block-and-steer on failure, and rewrite a passing argument to its canonical
value. They work on sync *and* async functions, positional *and* keyword args.

### Policies (the rule for an argument, used by `@grounded`)

| Policy | Plain meaning |
|---|---|
| `SPOKEN` | the value appears in what the caller said (numbers/dates/names normalized + fuzzy-matched) |
| `CONFIRMED` | the agent read it back **and** the caller affirmed it |
| `CALLER_ID` | the value comes from trusted call metadata, not the model's mouth |
| `INFERABLE` | the value is derivable from context (e.g. "tomorrow" + clock) or was spoken |

### Provenance (the engine behind `@grounded_outputs`)

| Term | Meaning |
|---|---|
| `ToolLedger` | records what each read tool returned this call (`record` / `candidates`) |
| `from_tool(tool, key, normalize=...)` | declares an argument must originate from a tool's output (one or many sources) |
| `reconcile(value, candidates, ...)` | the judge: raw-exact → unique-normalized → single-candidate → block; returns the canonical value |
| `Resolution` / `Status` | the verdict object + its enum (`PASS_EXACT`, `BLOCK_NO_MATCH`, …) |
| normalizers | `exact`, `casefold`, `e164`, `datetime-minute`, `money` — ignore harmless differences |

### Reads (`saidso.speech`)

| Term | Meaning |
|---|---|
| `render_spoken(template, ledger=..., **facts)` | build a spoken line; every fact is verified, ungrounded → `UngroundedSpeech` (speak nothing) |
| `fact(value, *sources, render=...)` | one interpolated value + its provenance + an optional deterministic formatter |
| `try_render_spoken(...)` | same, but returns `None` instead of raising |
| `UngroundedSpeech` | raised when a fact can't be grounded — fail-closed |
| `find_ungrounded_names(...)` | best-effort post-turn detector for spoken names not in the ground-truth set (a safety net, not a guarantee) |

saidso never produces audio — `render_spoken` returns the verified **string**; your
own TTS speaks it (TTS-agnostic).

### Core types & scope

| Term | Meaning |
|---|---|
| `Transcript` / `Turn` | the conversation buffer (`add_user`, `add_agent`) |
| `call_context(transcript, ledger=..., tools=..., metadata=...)` | scopes one call so the decorators find what they need (contextvars; async-safe) |
| `SteerBack` | the "blocked → re-ask" result: `.message`, `.failed`, `.grounded` |
| `Attestation` / `AttestationLog` | a receipt per passing action; optional JSONL audit trail |
| `GroundingResult` / `ArgFinding` / `Span` | per-argument verdict detail + transcript location |
| `GroundingConfig` / `GroundingBlocked` | tuning knobs; the exception raised when `raise_on_block=True` |

### Observability (`saidso.observe`)

| Term | Meaning |
|---|---|
| `enable_pretty_logging()` | colored ✓/✗ live stream (auto-off when not a TTY) |
| `EventRecorder` | captures the structured event stream (`.passed`, `.blocked`) |
| `summary(audit, recorder)` | end-of-run counts + one row per decision |

Every decision emits one structured log event on the `saidso` logger
(`saidso_event` = `pass`/`block`, with `saidso_action` / `saidso_args`).

---

## Design principles

- **Fail-closed** — a check that errors blocks; a broken metal detector locks the door.
- **Deterministic & fast** — pure Python, in-process; a write check is ~12µs.
- **Zero required dependencies** — `rapidfuzz` optional (stdlib `difflib` fallback).
- **Model- & platform-agnostic** — no model SDK is imported anywhere; saidso operates on
  text, function arguments, and recorded tool outputs, which every stack has.
