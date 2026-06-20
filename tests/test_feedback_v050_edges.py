"""Edge-case coverage for the 0.5.0 features (idempotency on @grounded, shadow +
idempotency interplay, reconcile_turn dict filters, exception payloads, async)."""

from __future__ import annotations

import asyncio

from saidso import (
    AttestationLog,
    GroundingBlocked,
    GroundingConfig,
    Policy,
    ReasonCode,
    SteerBack,
    ToolLedger,
    Transcript,
    UnattestedAction,
    attested,
    call_context,
    fact,
    from_tool,
    grounded,
    grounded_outputs,
    reconcile_turn,
    render_spoken,
)


def test_grounded_idempotency_blocks_repeat():
    calls = []

    @grounded(
        GroundingConfig(idempotency_key=lambda a: a["patient_id"]),
        name=Policy.SPOKEN,
    )
    def register(name, patient_id):
        calls.append(patient_id)
        return "ok"

    tr = Transcript()
    tr.add_user("this is Maria Gomez")
    with call_context(tr):
        assert register(name="Maria Gomez", patient_id="p1") == "ok"
        dup = register(name="Maria Gomez", patient_id="p1")
    assert isinstance(dup, SteerBack) and dup.code == ReasonCode.DUPLICATE.value
    assert calls == ["p1"]


def test_idempotency_key_exception_does_not_crash_call():
    @grounded(
        GroundingConfig(idempotency_key=lambda a: 1 / 0),  # raises
        name=Policy.SPOKEN,
    )
    def register(name):
        return "ok"

    tr = Transcript()
    tr.add_user("this is Maria Gomez")
    with call_context(tr):
        assert register(name="Maria Gomez") == "ok"  # dedupe skipped, not fatal


def test_shadow_with_spoken_steer_style_still_runs():
    @grounded(GroundingConfig(enforce=False, steer_style="spoken"), name=Policy.SPOKEN)
    def register(name):
        return "ok"

    log = AttestationLog()
    with call_context(Transcript(), ledger=log):
        assert register(name="Ghost") == "ok"
    assert log.records[0].status == "shadow_block"


def test_raise_on_block_uses_configured_style():
    @grounded(GroundingConfig(raise_on_block=True, steer_style="spoken"), dob=Policy.SPOKEN)
    def register(dob):
        return dob

    with call_context(Transcript()):
        try:
            register(dob="1990-01-01")
        except GroundingBlocked as exc:
            assert exc.steer.style == "spoken"
        else:  # pragma: no cover
            raise AssertionError("expected GroundingBlocked")


def test_async_grounded_outputs_idempotency():
    calls = []

    @grounded_outputs(
        GroundingConfig(idempotency_key=lambda a: a["slot"]),
        slot=from_tool("get_slots", "slot"),
    )
    async def book(slot):
        calls.append(slot)
        return "booked"

    tl = ToolLedger()
    tl.record("get_slots", [{"slot": "9am"}])

    async def run():
        with call_context(Transcript(), tools=tl):
            first = await book(slot="9am")
            dup = await book(slot="9am")
        return first, dup

    first, dup = asyncio.run(run())
    assert first == "booked"
    assert isinstance(dup, SteerBack) and dup.code == ReasonCode.DUPLICATE.value
    assert calls == ["9am"]


def test_reconcile_turn_dict_filters_by_call_id_and_ts():
    log = AttestationLog()
    log.build("register_patient", [], call_id="other")  # different call
    exported = log.export()
    # call_id mismatch -> still unbacked
    unbacked = reconcile_turn(
        "you're registered", attestations=exported, call_id="mine"
    )
    assert [c.claim for c in unbacked] == ["registered"]
    # matching call_id -> backed
    assert reconcile_turn(
        "you're registered", attestations=log.export(), call_id="other"
    ) == []


def test_reconcile_turn_empty_text_returns_empty():
    assert reconcile_turn("", attestations=AttestationLog()) == []


def test_unattested_action_payload():
    tl = ToolLedger()
    tl.record("get_slots", [{"slot_start": "9:00 AM"}])
    try:
        render_spoken(
            "Booked at {time}.",
            ledger=tl, attestations=AttestationLog(),
            requires_write=attested("book_appointment", status="ok"),
            time=fact("9:00 AM", ("get_slots", "slot_start")),
        )
    except UnattestedAction as exc:
        assert exc.action == "book_appointment"
        assert exc.blocked and exc.blocked[0].value == "book_appointment"
        assert "book_appointment" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected UnattestedAction")


def test_requires_write_falls_back_to_context_ledger():
    tl = ToolLedger()
    tl.record("get_slots", [{"slot_start": "9:00 AM"}])
    log = AttestationLog()
    log.build("book_appointment", [])
    # attestations not passed -> pulled from call_context ledger
    with call_context(Transcript(), tools=tl, ledger=log):
        out = render_spoken(
            "Booked at {time}.",
            requires_write=attested("book_appointment"),
            time=fact("9:00 AM", ("get_slots", "slot_start")),
        )
    assert out == "Booked at 9:00 AM."
