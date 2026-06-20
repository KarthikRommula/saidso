# changelog — what's new, fixed, and improved

The in-package summary of saidso updates. Read it with `saidso docs changelog`;
it's exported alongside every other page by `saidso docs --dump`.

## 0.5.1 — quieter self-upgrade

### Fixed

- `saidso upgrade` / `saidso uninstall` no longer surface pip's benign Windows
  warning "Failed to remove contents in a temporary directory '…pip-uninstall-…'.
  You can safely remove it manually." It came from the running saidso launcher
  locking its own old files (the install still succeeded). The CLI now filters only
  that specific line and sweeps stale pip-uninstall-* backup dirs from TEMP after
  each run.

## 0.5.0 — twelve gaps from a native-audio deployment

A Gemini 3.1 Live clinic-receptionist filed a detailed incident report; this release
closes all of it. Every change is additive and backward-compatible — existing code,
return shapes and `from saidso import …` are unchanged; new parameters default to the
prior behavior.

### New — reads own the completion CLAIM, not just the facts

- render_spoken(..., requires_write=attested("book_appointment")) grounds the *verb*
  of a spoken line against the AttestationLog: the named action must have succeeded
  this call, or the line is refused with UnattestedAction — even when every fact() is
  grounded. Closes the "you HAVE an appointment" hole where the nouns were real but the
  write never ran.
- reconcile_turn(agent_text, attestations=…) flags spoken completion claims with no
  backing action ("you're all set", "you're registered", "booked"). Broader than
  find_ungrounded_names; ships a default COMPLETION_CLAIMS vocabulary you can extend.
- attest_action("transfer_to_human", metadata=…) records argument-less consequential
  actions (end_call / transfer_to_human) so the two checks above can cover them.

### New — make Policy.SPOKEN usable on real ASR

- Per-argument tuning: Policy.SPOKEN(normalize=…, threshold=…).
- Normalizers on the @grounded side (matching @grounded_outputs):
    spelled-name   assemble "R O M U L A" -> "Romula" (tolerates ASR drift)
    phonetic       ground near-homophones the model corrected, "mail" -> "male"
    spoken-date    pin the spoken-date -> ISO path explicitly
- Per-argument thresholds — loosen `gender` without loosening `date_of_birth`.

### New — rollout & ops

- Shadow mode: GroundingConfig(enforce=False) records every would-block
  (status="shadow_block") WITHOUT blocking the call, so you calibrate on real traffic
  before enforcing.
- Voice-safe SteerBack: GroundingConfig(steer_style="spoken") — a caller-facing re-ask
  with no tool/id/internal jargon.
- Idempotency guard: GroundingConfig(idempotency_key=lambda a: …) refuses a repeat of an
  already-committed call this session (reason code DUPLICATE).
- Provenance freshness: ToolLedger.record(tool, rows, ttl_s=…, source=…) + is_stale();
  GroundingConfig(on_stale="warn"|"block"|"ignore").
- Reason codes: every decision (pass AND block) carries a machine-readable code
  (GroundingResult.code / SteerBack.code, and in the attestation) for tuning and
  observability.

### New — locale

- Locale-aware grounding: call_context(metadata={"locale": "es-ES"}) selects a
  language's month names, relative dates and yes/no vocabulary. English (default,
  unchanged) and Spanish ship; get_locale() resolves BCP-47 tags and is extensible.

### Fixed — read-back correction handling

- When the agent spells a value back WRONG and the caller corrects it — "no, it's
  R O M M U L A" — the leading "no" is now recognized as a rejection of the prior turn,
  so the re-asserted value grounds instead of being dropped by the supersession guard.
  A genuine value-negation ("not X", "old X but now Y") still retracts.

## Earlier releases

The full, detailed history (every point release) lives in the repository changelog,
Docs/CHANGELOG.md. Recent highlights:

- 0.4.x  CLI (`saidso docs`/`--dump`, `quickstart`, `upgrade`, `uninstall`),
         correction-aware grounding + the supersession/retraction guard, production
         hardening (ruff/mypy/bandit/90% coverage gates), promoted to Beta.
- 0.4.0  Deterministic grounded speech (render_spoken / fact) — the reads side.
- 0.3.x  Multi-source from_tool; best-effort spoken-name monitor.
- 0.2.x  Tool-output provenance grounding (@grounded_outputs, ToolLedger, reconcile).
- 0.1.0  First release: @grounded firewall, policies, transcript, attestation log.

Next:  saidso docs overview   ·   saidso docs writes   ·   saidso docs reads
