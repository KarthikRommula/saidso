# writes — guarding what the agent DOES

A "write" is a tool call. Guard its arguments so a fabricated value can never reach
your backend. Two decorators, by where the value should come from.

## 1. @grounded — ground against the CONVERSATION

The value must appear in what the caller said (or trusted metadata).

  from saidso import grounded, Policy

  @grounded(name=Policy.SPOKEN, dob=Policy.SPOKEN, phone=Policy.CALLER_ID)
  def register_patient(name, dob, phone):
      ...

See `saidso docs policies` for SPOKEN / CONFIRMED / CALLER_ID / INFERABLE.

## 2. @grounded_outputs — ground against TOOL OUTPUT (provenance)

Some values come from an earlier tool, not the caller's mouth — an opaque id, a
slot timestamp. They must match what that tool actually returned this call.

  from saidso import grounded_outputs, from_tool

  @grounded_outputs(
      slot_start=from_tool("get_slots", "slot_start", normalize="datetime-minute"),
      appointment_id=from_tool("list_appointments", "appointment_id"),
  )
  def book_appointment(slot_start, appointment_id):
      ...

You record what read tools return into a ToolLedger:

  from saidso import ToolLedger, call_context
  ledger = ToolLedger()
  ledger.record("get_slots", rows)          # rows = list of dicts the tool returned
  with call_context(transcript, tools=ledger):
      book_appointment(slot_start=..., appointment_id=...)

Behaviour:
- Fabricated id/slot  -> blocked (the body never runs).
- Model rebuilt it slightly wrong (right minute, wrong timezone string) -> it is
  REWRITTEN to the canonical value the tool returned, then committed.
- A value may come from several tools: from_tool(("list_doctors","doctor_id"),
  ("list_appointments","doctor_id")).

Normalizers (the `normalize=` arg): exact · casefold · e164 · datetime-minute ·
money. They let "the same value, written differently" match while still blocking
genuine fakes.

## Freshness / TTL on provenance

If you pre-seed or cache-rebuild the ledger, mark entries so stale provenance can be
flagged or refused:

  ledger.record("get_slots", rows, ttl_s=120, source="cache")
  # GroundingConfig(on_stale="warn"|"block"|"ignore") — default "warn"

## Idempotency (double-write guard)

Recovery loops can re-fire the same write. Dedupe at the boundary:

  from saidso import GroundingConfig
  @grounded_outputs(
      GroundingConfig(idempotency_key=lambda a: (a["patient_id"], a["slot_start"])),
      slot_start=from_tool("get_slots", "slot_start"),
  )
  def book(patient_id, slot_start): ...
  # a second success with the same key this session -> blocked (reason DUPLICATE)

## Shadow mode (safe rollout)

Calibrate a new policy on real traffic before it can block a live call:

  @grounded(GroundingConfig(enforce=False), name=Policy.SPOKEN)
  def register(name): ...
  # every would-block is recorded (status="shadow_block"); the body still runs

## On a block

Both decorators return a `SteerBack` (the body never ran). Feed `steer.message`
back to the model so it re-asks the caller. To raise instead of return:

  from saidso import GroundingConfig
  @grounded(GroundingConfig(raise_on_block=True), name=Policy.SPOKEN)
  def f(name): ...

## On a pass

An attestation (receipt) is recorded — see `saidso docs observability`.

Next:  saidso docs reads   ·   saidso docs testing
