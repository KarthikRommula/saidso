# Changelog

## 0.5.2

Documentation and metadata — no code changes.

### Fixed
- Updated PyPI package description to cover both guarantees (writes and reads).
- Fixed all `Docs/` path references to `docs/` (case-sensitive on GitHub/Linux) across README, CONTRIBUTING, PULL_REQUEST_TEMPLATE, issue templates, GETTING_STARTED, and changelog.
- Removed stale `saidso[fast]` extra reference from bug report template (extra was removed in 0.5.x).
- Fixed Changelog URL in `pyproject.toml` (`Docs/CHANGELOG.md` → `docs/CHANGELOG.md`).

## 0.5.1

CLI polish — a quieter, self-cleaning `saidso upgrade` / `saidso uninstall`.

### Fixed
- **No more spurious pip warning on Windows self-upgrade.** When `saidso upgrade`
  replaced the running package, pip printed `WARNING: Failed to remove contents in a
  temporary directory '…pip-uninstall-…'. You can safely remove it manually.` — a
  benign artifact of the running `saidso.exe` launcher locking its own old files
  (the install always succeeded). The CLI now streams pip's output through a filter
  that drops *only* that specific benign line (all real errors pass through), and
  sweeps stale `pip-uninstall-*` backup dirs from `TEMP` after each run so they never
  accumulate. Applies to both `saidso upgrade` and `saidso uninstall`.

## 0.5.0

Twelve gaps from a native-audio (Gemini 3.1 Live) deployment's incident report,
closed as additive, backward-compatible API. Every existing call site, return shape
and the `from saidso import …` surface are unchanged; new parameters/fields default
to the prior behavior.

### Reads — own the completion *claim*, not just the facts
- **`render_spoken(..., requires_write=attested("book_appointment"))`** grounds the
  *verb* of a spoken line against the AttestationLog: the named action must have a
  successful attestation this call or the line is refused with `UnattestedAction`,
  even when every `fact(...)` is grounded. Closes the "you *have* an appointment"
  hole where the nouns were real but the write never ran.
- **`reconcile_turn(agent_text, attestations=…, claim_patterns=COMPLETION_CLAIMS)`** —
  a turn-level reconciler that flags spoken completion claims ("you're all set",
  "you're registered", "booked") that no successful action backs. Broader than
  `find_ungrounded_names` (which only catches titled names); makes a bespoke regex
  watchdog deletable. Ships a default claim vocabulary; extend `ClaimPattern`s per app.
- **`attest_action("transfer_to_human", metadata=…)`** records consequential,
  *argument-less* actions (`end_call`, `transfer_to_human`) so the reconciler and
  `requires_write` can cover them and the audit trail is complete.

### Policies — make `SPOKEN` usable on real ASR
- **Per-argument tuning via `Policy.SPOKEN(normalize=…, threshold=…)`** (a new
  `PolicySpec`). `Policy("spoken")` value-lookup is unchanged.
- **Normalizers on the `@grounded`/SPOKEN side**, matching what `@grounded_outputs`
  already had: `normalize="spelled-name"` assembles "R O M U L A" → "Romula" (and
  tolerates ASR drift), `normalize="phonetic"` grounds near-homophones the model
  silently corrected ("mail" → "male") via Soundex, `normalize="spoken-date"` pins
  the spoken-date path.
- **Per-argument thresholds** — loosen `gender` without loosening `date_of_birth`.
- **Read-back correction handling** — when the agent spells a value back wrong and the
  caller corrects it ("no, it's R O M M U L A"), the leading "no" is recognized as a
  rejection of the prior turn, so the *re-asserted* value grounds instead of being
  dropped by the supersession guard. A genuine value-negation ("not X", "old X but
  now Y") still retracts.

### Rollout & ops
- **Shadow mode** — `GroundingConfig(enforce=False)` records every would-block to the
  AttestationLog (`status="shadow_block"`) **without blocking the call**, so policies
  can be calibrated on real traffic before enforcing.
- **Voice-safe SteerBack** — `GroundingConfig(steer_style="spoken")` emits a
  caller-facing re-ask with no tool/id/internal jargon ("Sorry, could you give me your
  date of birth again?").
- **Idempotency / double-write guard** — `GroundingConfig(idempotency_key=lambda a: …)`
  on `@grounded` / `@grounded_outputs` refuses a repeat of an already-committed call
  this session (reason code `DUPLICATE`), de-risking recovery-injection loops.
- **Provenance freshness/TTL** — `ToolLedger.record(tool, rows, ttl_s=…, source=…)`
  plus `is_stale()`; `GroundingConfig(on_stale="warn"|"block"|"ignore")` controls how
  grounding treats candidates from an expired (e.g. cache-seeded) ledger entry.
- **Structured reason codes** — every decision (pass *and* block) now carries a
  machine-readable `ReasonCode` on `GroundingResult.code` / `SteerBack.code` and in
  the attestation record (`NOT_IN_TRANSCRIPT`, `BELOW_THRESHOLD`, `WRONG_TOOL_SOURCE`,
  `NORMALIZE_MISMATCH`, `RETRACTED`, `DUPLICATE`, `STALE_PROVENANCE`, `OK_*`, …) for
  tuning and observability.

### Locale
- **Locale-aware grounding** — `call_context(metadata={"locale": "es-ES"})` selects a
  language's month names, relative dates and yes/no vocabulary. Ships **English**
  (default; byte-for-byte the prior behavior) and **Spanish**; `get_locale()` resolves
  BCP-47 tags and is extensible by registering another `Locale`. Accent-folding makes
  "sí"/"mañana" robust to ASR.

## 0.4.6

CLI: export docs to a folder.

### New
- **`saidso docs --dump [DIR]`** — write all bundled doc pages as `.md` files
  into `DIR` (created if missing; default `saidso-docs/`). The terminal-reader
  forms (`saidso docs`, `saidso docs <topic>`, `saidso docs --list`) are
  unchanged.

## 0.4.5

CLI: uninstall command.

### New
- **`saidso uninstall`** — uninstall the installed `saidso` package via pip
  (`pip uninstall -y saidso`), mirroring `saidso upgrade`. Falls back to a clear
  message if pip is unavailable in the environment.

## 0.4.4

Correction-aware grounding + production hardening.

### New
- **Supersession/retraction guard** in the `SPOKEN` matcher. A value the caller
  explicitly took back is no longer grounded from that retracted mention:
  - retraction cues before a value — "my **old** number was 555-1234", "my name
    is **not** John", "**instead of** X" — drop that value;
  - a correction pivot ("but", "i mean", "no wait", …) followed by a competing
    value of the same kind supersedes the earlier one.
  It only ever *removes* false grounds (fail-closed: when unsure it re-asks), and
  remains deterministic — a heuristic for common self-corrections, not a semantic
  intent model. Dates are split on pivots only, so "January first, nineteen
  ninety" is never fragmented by its comma.

### Hardened
- **Security:** `saidso docs <topic>` now resolves only against the known topic
  list (closes a path-traversal vector). Clean `bandit` and `pip-audit` runs.
- **Quality gates:** added `ruff`, `mypy`, `bandit`, `pip-audit`, and
  `pytest-cov` (≥90% gate) — all configured in `pyproject.toml` and green.
  Source modernized to PEP 585/604 typing; public API type-checks cleanly.
- Promoted to Beta.

## 0.4.3

In-terminal documentation.

### New
- **`saidso docs [TOPIC]`** — read the saidso docs in the terminal (overview,
  quickstart, writes, policies, reads, observability, testing, integrate).
  `saidso docs` shows the overview; `saidso docs --list` lists all topics.
  Lightly styled headings on a TTY. Doc pages are bundled in the wheel, so this
  works from a plain `pip install saidso`.

### Changed
- Removed the `--version` / `-V` flag; use the `saidso version` subcommand.

## 0.4.1

A small command-line interface (stdlib only).

### New
- **`saidso` CLI** via a console-script entry point:
  - `saidso --version` / `saidso version` — print the installed version.
  - `saidso upgrade` — upgrade to the latest release on PyPI (via pip).
  - `saidso quickstart [DIR]` — scaffold a runnable example (`quickstart.py`) and
    a `GETTING_STARTED.md` into a folder (default `saidso-quickstart/`).
  - `saidso --help` lists all commands; `python -m saidso` works as an alias.
- Quickstart templates are bundled in the wheel, so the CLI works from a plain
  `pip install saidso` (no repo checkout needed).

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
  `fuzz`). Added `docs/ARCHITECTURE.md` (layout + vocabulary reference). The
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
