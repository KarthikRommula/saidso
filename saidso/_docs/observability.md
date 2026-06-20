# observability — see and audit every decision

Two layers: a live log stream (pass/block) and an audit trail (passes, with proof).

## Live stream

Every decision emits one structured event on the `saidso` logger
(saidso_event = pass|block, saidso_action, saidso_args). Turn on the pretty console:

  from saidso import enable_pretty_logging, EventRecorder, summary

  enable_pretty_logging()              # colored ✓/✗ (auto-off when not a TTY)
  rec = EventRecorder().attach()       # remember events for a summary
  ...
  print(summary(audit, rec))

Output:

  13:38:15 ✓ grounded register_patient  name, dob
  13:38:15 ✗ blocked  book_appointment  slot_start
  ┌─ saidso — 1 grounded, 1 blocked
    ✓ register_patient       name, dob
    ✗ book_appointment       slot_start
  └──────────────────────────────

For production, skip pretty logging and attach your own handler (the events are
plain fields, JSON-friendly), or set the `saidso` logger level as usual:

  import logging
  logging.getLogger("saidso").setLevel(logging.INFO)

`saidso_event` is `pass` | `block` | `shadow_block` (the last is a would-block recorded
under GroundingConfig(enforce=False) — see `saidso docs writes`).

## Reason codes (route + tune on a stable key)

Every decision — pass AND block — carries a machine-readable `code` (a ReasonCode):

  result.code        # on a GroundingResult / attested arg
  steer.code         # on the SteerBack you receive

  OK_EXACT · OK_FUZZY · OK_NORMALIZED · OK_CONFIRMED · OK_CALLER_ID · OK_INFERRED
  NOT_IN_TRANSCRIPT · BELOW_THRESHOLD · WRONG_TOOL_SOURCE · NORMALIZE_MISMATCH
  RETRACTED · NO_CONFIRMATION · AMBIGUOUS · DUPLICATE · STALE_PROVENANCE · NO_VALUE

A score + code on a PASS surfaces near-misses ("passed, family_name at 0.55") for
calibration; a code on a BLOCK lets a watchdog route recovery to the right re-ask
instead of a generic one. Codes are also written into each attestation arg.

## Audit trail (the receipts)

Every PASSING action records an Attestation: which value came from which words,
when, with what confidence.

  from saidso import AttestationLog, call_context

  audit = AttestationLog(path="audit.jsonl")   # omit path to keep in memory only
  with call_context(transcript, ledger=audit):
      ...

  len(audit)            # how many actions passed
  audit.records         # list of Attestation objects
  audit.export()        # list of dicts (JSON-friendly)

Enforced blocks are NOT in the audit log (nothing happened) — they appear in the
logger and as the SteerBack you receive. Shadow-mode would-blocks ARE recorded
(`status="shadow_block"`) so you can calibrate against real traffic; filter them out
when counting real commits.

Next:  saidso docs testing
