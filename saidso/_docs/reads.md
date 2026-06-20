# reads — guarding what the agent SAYS

A native-audio model speaks directly — you can't gate its mouth (the transcript
arrives after the words). So saidso doesn't gate it: it builds the consequential
line from grounded data and refuses if any fact is fabricated. Your TTS speaks the
verified string. saidso never produces audio (TTS-agnostic).

## render_spoken + fact

  from saidso import render_spoken, fact

  line = render_spoken(
      "You're booked with {doctor} at {time}.",
      ledger=tool_ledger,
      doctor=fact("Dr. Rashmi", ("list_doctors", "doctor_name")),
      time=fact(slot_start, ("get_slots", "slot_start"),
                normalize="datetime-minute", render=to_clock),
  )
  # -> "You're booked with Dr. Rashmi at 5:00 PM."   (every fact verified)
  speak_with_your_tts(line)

- Each `fact(value, *sources, ...)` is reconciled against the ToolLedger, exactly
  like a write. On a pass the CANONICAL tool value is used.
- `render=` is an optional deterministic formatter applied to the canonical value
  (e.g. ISO timestamp -> "5:00 PM"). Default is str().
- Every `{placeholder}` must have a matching fact; static text is yours (trusted).

## When a fact is fabricated

  render_spoken("With {doctor}.", ledger=led,
                doctor=fact("Dr. House", ("list_doctors", "doctor_name")))
  # Dr. House was never returned -> raises UngroundedSpeech. Nothing to say.

Use `try_render_spoken(...)` to get None instead of an exception (fall back to the
model phrasing it, or re-ask).

## Grounding the COMPLETION CLAIM (the verb, not just the nouns)

A line like "you HAVE an appointment" asserts a write SUCCEEDED. Grounding the nouns
(doctor, time) isn't enough — both can be real while book_appointment never ran.
`requires_write=` reconciles the claim against the AttestationLog:

  from saidso import render_spoken, fact, attested

  line = render_spoken(
      "You have an appointment with {doctor} at {time}.",
      ledger=tool_ledger, attestations=attestation_log,
      requires_write=attested("book_appointment", status="ok"),  # NEW
      doctor=fact("Dr. Rashmi", ("list_doctors", "doctor_name")),
      time=fact(slot_start, ("get_slots", "slot_start")),
  )
  # -> UnattestedAction if book_appointment did not succeed this call.

## Catching unbacked claims POST-turn (reconcile_turn)

For claims with no name and no fact — "you're all set", "you're registered",
"booked" — reconcile the agent's whole turn against what actually succeeded:

  from saidso import reconcile_turn, COMPLETION_CLAIMS

  unbacked = reconcile_turn(agent_text, attestations=attestation_log,
                            claim_patterns=COMPLETION_CLAIMS)
  # -> [UnbackedClaim(claim="registered", expected_action=("register_patient",), ...)]
  # inject a spoken correction; COMPLETION_CLAIMS is extensible per app.

Argument-less actions (end_call, transfer_to_human) take no grounded args, so record
them yourself so the two checks above can see them:

  from saidso import attest_action
  attest_action("transfer_to_human", metadata={"dest": "front_desk"})

## Note on single-candidate

Unlike writes, `fact(...)` defaults to allow_single_candidate=False: speaking the
only name on file in place of one that was never returned is the silent error reads
must avoid. A spoken fact must genuinely match.

## Best-effort backup

`find_ungrounded_names(spoken, allowed)` is a POST-turn detector for titled names
("Dr. X") the model spoke that aren't in the ground-truth set. It's a safety net
(reactive, heuristic), not a guarantee — pair it with render_spoken.

Next:  saidso docs writes   ·   saidso docs integrate
