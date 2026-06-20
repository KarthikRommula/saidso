# saidso

**A grounding firewall for action-taking AI agents.**

`saidso` sits between an AI agent and its consequences and enforces one rule:

> **Nothing is committed (a tool argument) or spoken (a fact) unless it traces
> back to something real — what the user said, or what a tool returned.**
> Anything ungrounded is blocked, before it can cause harm.

The name is the whole idea: an action only goes through if the user *said so*.

```bash
pip install saidso          # zero required dependencies
pip install saidso[fast]    # optional: rapidfuzz for faster matching
```

---

## The problem

LLM agents (especially voice/phone agents) don't just talk — they *do things* and
*state facts*. Sometimes the model **makes things up**: an argument the caller
never said, an appointment ID that doesn't exist, a doctor's name nobody offered.
Today's frameworks execute the call and speak the words anyway — and a fabricated
value lands in a real database, or a wrong fact reaches a real patient.

Prompting ("never make up a DOB") is best-effort: it's the suspect judging itself,
it leaves no proof, and it degrades as you add tools. `saidso` runs in **code, not
the prompt** — it assumes the model *will* hallucinate and refuses to let it matter.

---

## Two guarantees

### 1. Writes — what the agent *does*

Guard a tool's arguments. They must trace to the caller's words **or** to real
tool output. Fail-closed: ungrounded → the body never runs.

```python
from saidso import grounded, grounded_outputs, Policy, from_tool

# Ground against the CONVERSATION
@grounded(name=Policy.SPOKEN, dob=Policy.SPOKEN, phone=Policy.CALLER_ID)
def register_patient(name, dob, phone): ...

# Ground against an earlier TOOL'S OUTPUT (provenance)
@grounded_outputs(
    slot_start=from_tool("get_slots", "slot_start", normalize="datetime-minute")
)
def book_appointment(slot_start): ...
```

- A **fabricated** id/slot/name → blocked, the agent is steered to re-ask.
- A value the model **rebuilt slightly wrong** (right time, wrong timezone) → it's
  rewritten to the **canonical** value the tool actually returned, then committed.

### 2. Reads — what the agent *says*

A native-audio model can't have its mouth gated. So `saidso` doesn't gate it — it
builds the consequential line from grounded data and refuses if any fact is made
up. Your TTS speaks the verified string (saidso is **TTS-agnostic** — it never
produces audio).

```python
from saidso import render_spoken, fact

line = render_spoken(
    "You're booked with {doctor} at {time}.",
    ledger=tool_ledger,
    doctor=fact("Dr. Rashmi", ("list_doctors", "doctor_name")),
    time=fact(slot_start, ("get_slots", "slot_start"),
              normalize="datetime-minute", render=to_clock),
)
# -> "You're booked with Dr. Rashmi at 5:00 PM."   (every fact verified)
# a fabricated value -> raises UngroundedSpeech, produces nothing
```

---

## The policies (`@grounded`)

| Policy | A value is grounded if… |
|---|---|
| `Policy.SPOKEN` | it appears in the caller's speech (digits/dates/names normalized, fuzzy-matched) |
| `Policy.CONFIRMED` | the agent read it back **and** the caller affirmed it |
| `Policy.CALLER_ID` | it matches trusted call metadata, not what was spoken |
| `Policy.INFERABLE` | it's derivable from context ("tomorrow" + clock) or was spoken |

---

## Block → steer back → attest

On every guarded call, `saidso`:

1. **Blocks** — an ungrounded argument means the body never runs.
2. **Steers back** — returns a `SteerBack` whose `.message` makes the agent
   *re-ask* the caller in-conversation (or set `raise_on_block=True` to raise).
3. **Attests** — every value that passes writes a receipt: *this value came from
   these words, at this time, with this confidence.*

```python
from saidso import grounded, Policy, Transcript, call_context, AttestationLog

@grounded(name=Policy.SPOKEN, dob=Policy.SPOKEN)
def register_patient(name, dob): ...   # your real DB write

tr = Transcript()
tr.add_user("It's Maria Gomez, born January first nineteen ninety.")
audit = AttestationLog(path="audit.jsonl")          # optional audit trail

with call_context(tr, ledger=audit):
    out = register_patient(name="Maria Gomez", dob="1990-01-01")  # ✅ commits

if getattr(out, "blocked", False):
    say_to_caller(out.message)          # ❌ nothing was committed; agent re-asks
```

---

## Observability

Every decision emits a structured event on the `saidso` logger. A zero-dependency
console makes it readable:

```python
from saidso import enable_pretty_logging, EventRecorder, summary

enable_pretty_logging()             # colored ✓/✗ live stream (auto-off when not a TTY)
rec = EventRecorder().attach()
# ... run your agent ...
print(summary(audit, rec))
```

```
13:38:15 ✓ grounded register_patient  name, dob
13:38:15 ✗ blocked  book_appointment  slot_start
┌─ saidso — 1 grounded, 1 blocked
  ✓ register_patient       name, dob
  ✗ book_appointment       slot_start
└──────────────────────────────────────
```

---

## Regression harness (CI gate)

Turn "we hope it doesn't fabricate" into a test:

```python
from saidso.testing import GroundingCase

def test_invented_dob_is_blocked():
    (GroundingCase(register_patient)
        .user("Hi, I'd like an appointment")
        .call(name="John Doe", dob="1990-01-01")
        .assert_blocked("name", "dob"))
```

---

## Why it's production-grade

- **Fail-closed** — if a check ever raises, the value is treated as ungrounded; a
  crash never opens the gate.
- **Validated at import time** — a policy naming a non-existent parameter raises
  immediately, so a typo can't silently leave a real argument unguarded.
- **Deterministic & fast** — pure Python, in-process; a write check is ~12µs
  (~1/2000th of a single backend call). No perceptible latency in a voice agent.
- **Zero required dependencies** — `rapidfuzz` used if present, stdlib `difflib`
  otherwise. Ships `py.typed`.
- **Model- & platform-agnostic** — no model SDK is imported anywhere. Works with
  Gemini Live, OpenAI Realtime, cascaded STT→LLM→TTS pipelines, or text agents.

---

## Examples & docs

- [`examples/quickstart.py`](examples/quickstart.py) — writes, reads, and observability in one file.
- [`examples/openai_tooluse.py`](examples/openai_tooluse.py) — raw OpenAI tool-use adapter.
- [`examples/livekit_adapter.py`](examples/livekit_adapter.py) — realtime voice adapter.
- [`Docs/ARCHITECTURE.md`](Docs/ARCHITECTURE.md) — package layout + full vocabulary reference.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [`LICENSE`](LICENSE).
