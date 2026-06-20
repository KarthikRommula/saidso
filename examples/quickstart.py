from saidso import (
    grounded, grounded_outputs, Policy, from_tool,
    Transcript, ToolLedger, AttestationLog, call_context,
    render_spoken, fact,
    enable_pretty_logging, EventRecorder, summary,
)

# Pretty, colored ✓/✗ live stream + remember every decision for a final summary.
enable_pretty_logging()
recorder = EventRecorder().attach()


# --- fake backends so this demo runs on its own ---
class _DB:
    def insert(self, given_name, date_of_birth):
        print(f"[db] inserted {given_name} ({date_of_birth})")
        return {"patient_id": "p1"}


class _API:
    def book(self, slot_start):
        print(f"[api] booked {slot_start}")
        return {"ok": True}


db = _DB()
api = _API()


# --- your tools, guarded ---
@grounded(given_name=Policy.SPOKEN, date_of_birth=Policy.SPOKEN)
def register_patient(given_name, date_of_birth):
    return db.insert(given_name, date_of_birth)


@grounded_outputs(slot_start=from_tool("get_slots", "slot_start",
                                       normalize="datetime-minute"))
def book_appointment(slot_start):
    return api.book(slot_start)


# --- one phone call ---
tr = Transcript()
tr.add_user("Hi, I'm Maria, born June 5th 1990.")

ledger = ToolLedger()
# Two real slots: with >1 candidate there's no single-candidate fallback, so a fabricated
# time matches none and is blocked (with only one slot, anything would fall back to it).
ledger.record("get_slots", [
    {"slot_start": "2026-05-22T17:00:00+05:30"},
    {"slot_start": "2026-05-22T09:30:00+05:30"},
])

audit = AttestationLog()

with call_context(tr, ledger=audit, tools=ledger):

    print(register_patient(given_name="Maria", date_of_birth="1990-06-05"))  # ✅ she said both
    print(register_patient(given_name="Bob", date_of_birth="1990-06-05"))    # ❌ "Bob" never said -> SteerBack

    print(book_appointment(slot_start="2026-05-22T17:00:00-05:00"))  # ✅ wrong tz -> rewritten to +05:30
    print(book_appointment(slot_start="2026-05-22T03:00:00+05:30"))  # ❌ never offered -> SteerBack

    # what the agent is allowed to SAY:
    say = render_spoken("You're set for {time}.", ledger=ledger,
                        time=fact("2026-05-22T17:00:00+05:30",
                                  ("get_slots", "slot_start")))
    print(say)  # -> "You're set for 2026-05-22T17:00:00+05:30."  (real; your TTS speaks it)

# End-of-run observability: counts + one row per decision.
print(summary(audit, recorder))
