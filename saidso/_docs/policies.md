# policies — the rule for an argument (@grounded)

A Policy says HOW an argument must be proven.

  SPOKEN      the value appears in the caller's speech (digits/dates/names
              normalized and fuzzy-matched)
  CONFIRMED   the agent read the value back AND the caller affirmed it
  CALLER_ID   the value matches trusted call metadata, not what was spoken
  INFERABLE   the value is derivable from context ("tomorrow" + clock) or spoken

## Examples

  from saidso import grounded, Policy

  @grounded(
      name=Policy.SPOKEN,          # caller said it
      phone=Policy.CALLER_ID,      # from the phone line, not the model
      email=Policy.CONFIRMED,      # agent read it back, caller said "yes"
      visit_date=Policy.INFERABLE, # "next Tuesday" -> resolved from the clock
  )
  def register(name, phone, email, visit_date): ...

## Choosing one

- Use SPOKEN for plain facts the caller states (name, DOB, gender).
- Use CONFIRMED for high-stakes or error-prone values (a spelled-out email, an
  amount) — it requires an explicit read-back + "yes".
- Use CALLER_ID for the caller's own phone number (carrier metadata is more
  trustworthy than transcription). Provide it via
  call_context(..., metadata={"caller_id": "+1..."}).
- Use INFERABLE for relative dates/times.

## Per-argument tuning (normalizers + thresholds)

Call a policy member to tune ONE argument without touching the global config:

  @grounded(
      family_name=Policy.SPOKEN(normalize="spelled-name"),  # "R O M U L A" -> Romula
      date_of_birth=Policy.SPOKEN(normalize="spoken-date"), # "18 September 2004" -> ISO
      gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6),  # "mail" ~ "male"
  )
  def register(family_name, date_of_birth, gender): ...

SPOKEN normalizers (the @grounded side, mirroring @grounded_outputs):
  spelled-name  assemble letters the caller spelled out (tolerates ASR drift)
  phonetic      ground near-homophones via Soundex (loosen threshold to taste)
  spoken-date   pin the spoken-date -> ISO path explicitly

`Policy.SPOKEN(threshold=…)` overrides the floor for that argument only — loosen a
homophone-prone field without loosening a strict one.

## Global thresholds

Thresholds are also adjustable per-policy:

  from saidso import GroundingConfig, Policy
  cfg = GroundingConfig(thresholds={Policy.SPOKEN: 0.9})
  @grounded(cfg, name=Policy.SPOKEN)
  def f(name): ...

A per-argument `Policy.SPOKEN(threshold=…)` wins over the global per-policy value.

## Rollout, voice phrasing, idempotency (GroundingConfig)

  enforce=False        shadow mode: record would-blocks (status="shadow_block"),
                       run the body anyway — calibrate on real traffic first
  steer_style="spoken" caller-facing re-ask, no tool/id jargon (voice channels)
  idempotency_key=fn   refuse a repeat of an already-committed call this session
  on_stale="block"     refuse provenance past its ledger TTL (see `saidso docs writes`)

## Locale

Multilingual tenants pass the call's locale; month names, relative dates and yes/no
words follow it (English + Spanish ship; extensible via get_locale):

  with call_context(tr, metadata={"locale": runtime.default_language}):
      ...

## Reason codes

Every decision (pass and block) carries a machine-readable `code`
(ReasonCode: NOT_IN_TRANSCRIPT, BELOW_THRESHOLD, NORMALIZE_MISMATCH, DUPLICATE,
STALE_PROVENANCE, OK_EXACT, …) on the GroundingResult / SteerBack and in the
attestation — route recovery or feed observability off it. See `saidso docs observability`.

Note: for grounding against tool OUTPUT (ids, slots) you don't use a Policy — you
use @grounded_outputs + from_tool. See `saidso docs writes`.
