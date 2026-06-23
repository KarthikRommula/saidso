# saidso — getting started

A grounding firewall: **nothing is committed (a tool argument) or spoken (a fact)
unless it traces back to what the user said or what a tool returned.**

## Run the demo

```bash
python quickstart.py
```

You'll see a colored ✓/✗ stream and a summary — passes commit, fabricated values
are blocked, and only grounded facts are allowed to be spoken.

## The two guarantees

**Writes — what the agent does.** Guard a tool's arguments:

```python
from saidso import grounded, grounded_outputs, Policy, from_tool

@grounded(name=Policy.SPOKEN, dob=Policy.SPOKEN)        # vs the conversation
def register_patient(name, dob): ...

@grounded_outputs(slot=from_tool("get_slots", "slot_start"))   # vs tool output
def book(slot): ...
```

**Reads — what the agent says.** Build a line from grounded facts; refuse if any
is fabricated. saidso returns the verified string — your TTS speaks it:

```python
from saidso import render_spoken, fact

line = render_spoken("Booked with {doctor}.", ledger=ledger,
                     doctor=fact("Dr. Rashmi", ("list_doctors", "doctor_name")))
```

## Policies

| Policy | Grounded if… |
|---|---|
| `SPOKEN` | the value appears in the caller's speech |
| `CONFIRMED` | the agent read it back and the caller affirmed |
| `CALLER_ID` | it matches trusted call metadata |
| `INFERABLE` | derivable from context ("tomorrow" + clock) or spoken |

## Next steps

- Full reference: `docs/ARCHITECTURE.md` in the sdist, or the PyPI page.
- Add a CI gate with `saidso.testing.GroundingCase`.
- Turn on the audit trail: `AttestationLog(path="audit.jsonl")`.
