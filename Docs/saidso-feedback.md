# saidso — incident analysis & improvement requests

**Date:** 2026-06-20
**Context:** eigenh-connect-v2 — Gemini 3.1 Live (native-audio realtime) clinic receptionist.
**Source:** console session `session-06-20-191605` (`call_log_id=clog_b606d6305e154755ab5264f66718144d`).

---

## 1. The incident

In one console call the agent **spoke two completed-action confirmations for writes that never happened**:

| Agent said (spoken audio) | Tool that should have run | Actually ran? |
|---|---|---|
| "Let me get you registered real quick. Okay, you're all set." | `register_patient` | **No** |
| "I'm scheduling that for you now. … You have an appointment with Dr. Rashmi Indrakanti today at 9:00 AM." | `book_appointment` | **No** |

Evidence: `session_reporter` logged `tool_calls=5`, and the only `executing tool` lines were
`list_departments`, `list_doctors`, `get_slots`, `get_clinic_info`, `end_call`. No patient was
created; no appointment was booked. The caller was told otherwise.

This is the known Gemini 3.1 Live failure: the model generates the *holding line* and the *result
text* in one turn **without emitting the function call between them**.

---

## 2. Responsibility split — this was primarily OUR integration gap

saidso exposes two enforcement surfaces. One did its job; the other we never wired.

### 2a. Write gate — worked, but structurally cannot help here
`call_context` + the `_ground_*` / `grounded_outputs` guards live **inside the tool body**. The
logs confirm they passed on every tool that ran (`saidso_event: pass` for `_ground_list_doctors`,
`_ground_get_slots`). A write gate can only fire when the model calls the tool. The model never
emitted `register_patient` / `book_appointment`, so there was no call boundary to gate. **saidso is
a gate, not a driver — correct by design. Not a saidso defect.**

### 2b. Read gate — the right tool, never integrated (our fault)
saidso's own `reads.md` / `integrate.md` prescribe exactly the missing defense for native-audio
models:

> "A native-audio model speaks directly — you can't gate its mouth … saidso builds the
> consequential line from grounded data and refuses if any fact is fabricated." (`reads.md`)
>
> "Native-audio realtime (Gemini Live, OpenAI Realtime): add a side TTS and speak the verified
> string via the platform's say() while stopping the model turn." (`integrate.md:34`)

**We never integrated hook #3.** `render_spoken` appears nowhere in our codebase — only in the
vendored `saidso-docs/`. Instead we hand-rolled `agent.py::_ConversationWatchdog`, a regex that
keys on holding-line phrasing. It missed both writes (booking line "I'm scheduling…" isn't in its
vocabulary; the registration recovery was pre-empted by the caller's next turn). That homegrown
net is a weaker reimplementation of saidso's read side.

**Action item (ours):** wire `render_spoken` / `try_render_spoken` for the consequential
confirmation lines via a side-TTS lane, suppressing the model's own turn — per `integrate.md`.

---

## 3. Genuine saidso improvement requests

These are real gaps that would bite **any** team, even one that integrates the read side fully.
Filing them as requested so saidso can improve.

### Request A — ground the *completion claim*, not just the facts inside it
`render_spoken` reconciles each `fact(...)` placeholder against the **read** ledger. In this
incident that is insufficient:

- `Dr. Rashmi Indrakanti` **was** a real `list_doctors` output.
- `9:00 AM` **was** a real `get_slots` output.
- Therefore `render_spoken("You have an appointment with {doctor} at {time}.")` would have
  **PASSED** — every placeholder is grounded — even though `book_appointment` never ran.

`render_spoken` grounds the **nouns** but not the **verb**. The sentence "you *have* an
appointment" asserts that a **write succeeded**, and saidso has no way to express "this line claims
a completed action; require the corresponding write to have succeeded in the AttestationLog this
turn."

**Proposed API** — let a render assert a backing write, reconciled against the AttestationLog the
same way facts are reconciled against the ToolLedger:

```python
render_spoken(
    "You have an appointment with {doctor} at {time}.",
    ledger=tool_ledger,
    attestations=attestation_log,
    requires_write=attested("book_appointment", status="ok"),   # NEW
    doctor=fact("Dr. Rashmi", ("list_doctors", "doctor_name")),
    time=fact(slot_start, ("get_slots", "slot_start")),
)
# -> UnattestedAction if book_appointment did not succeed this turn.
```

This makes saidso own the full claim ("the named, timed thing actually got booked"), not just the
named/timed nouns. saidso already holds the AttestationLog, so it has the data.

### Request B — a turn-level reconciler broader than `find_ungrounded_names`
The shipped post-turn net (`find_ungrounded_names`) only catches titled names ("Dr. X"). It cannot
see action-completion hallucinations with no name and no fact: "you're all set", "you're
registered", "okay, you're booked". There is no primitive at the granularity of *"this agent turn
asserted a completion that no successful tool call backs."*

**Proposed API** — a reusable reconciler that, given the turn transcript + ledger + attestations,
returns the unbacked completion claims:

```python
unbacked = reconcile_turn(
    agent_text,                       # what the model said this turn
    attestations=attestation_log,     # what actually succeeded this turn
    claim_patterns=COMPLETION_CLAIMS, # "you're all set", "you're registered", "booked", ...
)
# -> [("registered", no attested register_patient), ("booked", no attested book_appointment)]
```

This belongs in saidso, not in each app's bespoke watchdog: saidso already owns the transcript and
the attestation log, and every native-audio integrator needs the same reactive backstop. It would
make the regex in `agent.py::_ConversationWatchdog` deletable.

### Note on what saidso CANNOT be blamed for
For a native-audio model, saidso (or anyone) can only **detect post-utterance and drive a
correction** — it cannot pre-empt the spoken words, because the transcript arrives after the audio.
`reads.md` already states this. So even with Requests A & B, the realtime path is
**detect-and-recover**, never **prevent**. That limitation is inherent to native-audio, not a
saidso shortcoming.

---

## 3b. Second incident — `Policy.SPOKEN` false-blocked legitimate registrations

Separately from the spoken-hallucination above, we hit a real wall integrating saidso's **write
side** on `register_patient`. This one is a genuine saidso usability gap, not our mis-wiring.

### What we did, and what broke
saidso's `policies.md` explicitly recommends SPOKEN for exactly these fields:

> "Use SPOKEN for plain facts the caller states (**name, DOB, gender**)." (`policies.md:25`)
> "SPOKEN — the value appears in the caller's speech (**digits/dates/names normalized and
> fuzzy-matched**)." (`policies.md:5`)

So we wired it as the docs prescribe:

```python
@grounded(given_name=Policy.SPOKEN, family_name=Policy.SPOKEN,
          date_of_birth=Policy.SPOKEN, gender=Policy.SPOKEN)
def _ground_registration(...): ...
```

On a native-audio (Gemini 3.1 Live) ASR transcript it **false-blocked legitimate registrations**.
The committed argument values systematically do not string-match the transcript, even with SPOKEN's
fuzzy matching:

| Field | Caller's audio (as transcribed) | Arg the model commits | Why SPOKEN fails |
|---|---|---|---|
| `family_name` | "Romula", then spelled "R O M U L A" | `"Romula"` | Spelled-out letters ≠ the assembled word; ASR also drifts ("Rommula"/"Ramula"). |
| `date_of_birth` | "18 September 2004" | `"2004-09-18"` | A spoken month-name phrase vs an **ISO-normalized** date — a format/semantic transform, not fuzzy string distance. |
| `gender` | **"mail"** (real ASR error this session, log 19:16:54) | `"male"` | Near-homophone the model corrected; SPOKEN matches the literal transcript token "mail", not the intended "male". |

Forced path: `Policy.SPOKEN → Policy.INFERABLE` (memory 6064, logged as a bug fix), and currently
`register_patient` runs `_ground_registration` at **`Policy.INFERABLE`** (the weakest policy) plus a
plain non-grounding format check (`_validate_registration_fields`). Net effect: **the single
highest-risk write in the system — creating a NEW patient PII record — ended up effectively
ungrounded**, which is backwards from its risk profile. We only kept INFERABLE because SPOKEN was
unusable, not because INFERABLE adds real protection here.

### The saidso gaps this exposes

**Gap C — SPOKEN under-delivers on the very fields the docs recommend it for.** The docs promise
"names/dates normalized and fuzzy-matched" and name "name, DOB, gender" as the use case, but on
real ASR it can't bridge (a) spelled-out assembly ("K A R T H I K" → "Karthik"), (b) spoken date
phrase → ISO date, or (c) ASR near-homophones ("mail"→"male"). Either the matcher needs to actually
cover these, or the docs should stop recommending SPOKEN for ASR-sourced registration.

**Gap D — no per-argument normalizers on the `@grounded` (SPOKEN) side.** The provenance side
(`@grounded_outputs` / `from_tool`) has named normalizers (`exact · casefold · e164 ·
datetime-minute · money`). The conversation side (`@grounded` / SPOKEN) has **none** — only a single
global threshold knob. We need the same normalizer hooks for SPOKEN, e.g.:

```python
@grounded(
    family_name=Policy.SPOKEN(normalize="spelled-name"),   # assemble "R O M U L A" -> "Romula"
    date_of_birth=Policy.SPOKEN(normalize="spoken-date"),   # "18 September 2004" -> "2004-09-18"
    gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6),  # tolerate "mail"~"male"
)
def register(...): ...
```

**Gap E — thresholds are global per-policy, not per-argument.**
`GroundingConfig(thresholds={Policy.SPOKEN: 0.9})` sets one threshold for **all** SPOKEN args
(`policies.md:35`). We can't loosen `gender` (homophone-prone) without also loosening `date_of_birth`.
Per-argument thresholds are needed.

**Missing middle.** Practically there is no policy that fits ASR-sourced, model-assembled,
normalized PII: SPOKEN is too strict, INFERABLE is too loose to be a meaningful guarantee. A
CONFIRMED-style policy that grounds on the **read-back + caller "yes"** turn (which the prompt
already requires before registering) rather than on the messy original utterance would fit this
case far better — saidso has CONFIRMED, but its matching still falls back to SPOKEN-style transcript
matching and hit the same wall.

## 3c. Further saidso improvements specific to this agent

These come straight from our deployment shape: a native-audio realtime model, a voice channel that
forbids technical jargon, multi-tenant + multilingual config, Redis-cached provenance, and a
watchdog that *re-prods the model to call tools*. Each would concretely help this agent.

### Gap F — shadow / non-enforcing mode (safe rollout)
The `Policy.SPOKEN` registration breakage (§3b) reached a live call before we knew SPOKEN was too
strict. There is no way to run a policy in **audit-only** mode first.

```python
@grounded(GroundingConfig(enforce=False), given_name=Policy.SPOKEN, ...)   # record, never block
```

Shadow mode would record every would-block decision (+ score, + reason) to the AttestationLog
without affecting the call, so we calibrate thresholds against **real traffic** before enforcing.
This is the single highest-leverage addition for us — it turns policy rollout from "discover in
production" into "measure, then enforce."

### Gap G — voice-safe / caller-facing SteerBack phrasing
On a block, `writes.md` says to feed `steer.message` back to the model. But our prompt
(`prompts.py`) forbids the agent from ever uttering "tool", "function", "id", or naming internal
steps. A developer-flavored SteerBack ("couldn't verify slot_start against get_slots output") is
**unspeakable** on this channel. We want SteerBack to emit a natural spoken re-ask:

```python
GroundingConfig(steer_style="spoken")   # -> "Sorry, which time did you want again?"
```

i.e. a caller-facing register, no internal vocabulary — so we can route it straight into the voice
turn instead of hand-rewriting every block message.

### Gap H — idempotency / double-write guard
Our `_ConversationWatchdog` *injects recovery turns that tell the model to "call the tool NOW"*
(`agent.py:454`). That intentionally risks a **double fire** — book the same slot twice, register
twice. saidso already sits at the write boundary and owns the AttestationLog, so it is the natural
place to dedupe:

```python
@grounded_outputs(..., idempotency_key=lambda a: (a["patient_id"], a["slot_start"]))
# second success-attested call with the same key this session -> blocked as duplicate
```

This directly de-risks the recovery mechanism we need for native-audio.

### Gap I — locale-aware grounding (multilingual tenants)
Language is tenant-driven BCP-47 (`agent.py:102`), not always English — and even this English call
had stray non-English ASR ("¿Quién era el", Korean text). SPOKEN fuzzy-matching, spoken-date → ISO,
and number/name normalization are English-centric today. We need normalizers and matching that take
the **call's locale** (e.g. a Spanish date phrase → `2004-09-18`, locale digit words), driven from
`call_context(..., metadata={"locale": runtime.default_language})`.

### Gap J — provenance freshness / TTL on ledger entries
We pre-seed the ToolLedger from **dispatch metadata** on follow-up calls (`agent.py:748-756`) and
rebuild it from **Redis cache hits** (`reach:v1:doc`, `reach:v1:dept` in the logs). `@grounded_outputs`
then grounds writes against possibly **stale** provenance — a doctor/slot that changed since it was
cached or seeded would still pass. Let ledger records carry a timestamp/validity so grounding can
flag (or refuse) grounding against expired provenance:

```python
ledger.record("get_slots", rows, ttl_s=120)        # stale -> grounding warns/blocks
ledger.record("list_doctors", rows, source="cache")  # provenance origin tracked for audit
```

### Gap K — expose match score + structured reason on every decision (pass *and* block)
Grounding is effectively binary today. For tuning and our existing PostHog observability we want,
on **every** call, the per-argument score and a machine-readable reason
(`NOT_IN_TRANSCRIPT` / `BELOW_THRESHOLD` / `WRONG_TOOL_SOURCE` / `NORMALIZE_MISMATCH`). Scores on a
*pass* surface near-misses ("registration passed, family_name at 0.55") so we calibrate from data;
reason codes on a block let the watchdog route recovery to the *right* re-ask instead of a generic one.

### Gap L — attest argument-less consequential actions (`end_call`, `transfer_to_human`)
`end_call` and `transfer_to_human` are highly consequential but take no grounded arguments, so
saidso never records them. A lightweight `attest_action("transfer_to_human", metadata=...)` would
complete the AttestationLog so the turn-reconciler (Request B) can also catch "I'm transferring you
now" / "goodbye" claims with no matching action — and give a full audit trail of every consequential
act in the call.

## 4. Summary

- **Root cause:** our integration — we never wired saidso's read side (`render_spoken`) for the
  native-audio surface, despite `integrate.md` prescribing it. Our regex watchdog is a weaker stand-in.
- **saidso did its job** on every tool that was actually called.
- **Five legitimate saidso feature requests:**
  - (A) ground completion-claims against successful writes, not just facts against reads;
  - (B) ship a turn-level completion-claim reconciler broader than `find_ungrounded_names`;
  - (C) make `Policy.SPOKEN` actually cover the name/DOB/gender ASR cases the docs recommend it for
    (or stop recommending it for them);
  - (D) add per-argument normalizers to the `@grounded`/SPOKEN side (spelled-name, spoken-date,
    phonetic), matching what `@grounded_outputs` already has;
  - (E) per-argument thresholds, not one global threshold per policy;
  - (F) **shadow / non-enforcing mode** to calibrate policies on real traffic before enforcing —
    highest leverage for us;
  - (G) voice-safe / caller-facing SteerBack phrasing (no tool/id jargon);
  - (H) idempotency / double-write guard (de-risks our recovery-injection loop);
  - (I) locale-aware grounding for multilingual tenants;
  - (J) provenance freshness/TTL on ledger entries (we ground against cached + seeded provenance);
  - (K) match score + structured reason on every pass/block, for tuning and observability;
  - (L) attest argument-less consequential actions (`end_call`, `transfer_to_human`).
- **Our fixes:** integrate `render_spoken` + side-TTS per `integrate.md`; broaden the watchdog to
  detect success-claims (not just holding lines) and reconcile against tool calls; revisit whether
  registration can ground on the read-back+"yes" turn instead of sitting at INFERABLE.
