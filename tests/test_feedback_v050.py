"""Regression tests for the 0.5.0 feedback gaps (A-L).

Each test reproduces the failing case the eigenh-connect-v2 incident report
(`Docs/saidso-feedback.md`) describes, so the guarantees can't silently regress.
"""

from __future__ import annotations

import time

import pytest

from saidso import (
    EN,
    ES,
    AttestationLog,
    GroundingConfig,
    Policy,
    PolicySpec,
    ReasonCode,
    SteerBack,
    ToolLedger,
    Transcript,
    UnattestedAction,
    as_spec,
    attest_action,
    attested,
    call_context,
    fact,
    from_tool,
    get_locale,
    grounded,
    grounded_outputs,
    reconcile_turn,
    render_spoken,
    try_render_spoken,
)

# --------------------------------------------------------------------------- #
# Keystone — PolicySpec (C/D/E)
# --------------------------------------------------------------------------- #


def test_policy_call_builds_spec_and_lookup_still_works():
    spec = Policy.SPOKEN(normalize="phonetic", threshold=0.6)
    assert isinstance(spec, PolicySpec)
    assert spec.policy is Policy.SPOKEN and spec.normalize == "phonetic"
    assert spec.threshold == 0.6
    assert Policy("spoken") is Policy.SPOKEN  # enum value lookup unaffected


def test_as_spec_coerces_all_inputs():
    assert as_spec(Policy.SPOKEN).policy is Policy.SPOKEN
    assert as_spec("confirmed").policy is Policy.CONFIRMED
    s = Policy.SPOKEN(threshold=0.5)
    assert as_spec(s) is s


# --------------------------------------------------------------------------- #
# C/D — spelled-name + phonetic normalizers
# --------------------------------------------------------------------------- #


def test_spelled_name_grounds_letter_by_letter_surname():
    @grounded(family=Policy.SPOKEN(normalize="spelled-name"))
    def reg(family):
        return family

    tr = Transcript()
    tr.add_user("my last name is R O M U L A")
    with call_context(tr):
        assert reg(family="Romula") == "Romula"


def test_spelled_name_tolerates_asr_drift():
    @grounded(family=Policy.SPOKEN(normalize="spelled-name"))
    def reg(family):
        return family

    tr = Transcript()
    tr.add_user("spelling it r o m m u l a")  # ASR doubled a letter
    with call_context(tr):
        assert reg(family="Romula") == "Romula"


def test_phonetic_grounds_near_homophone():
    @grounded(gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6))
    def reg(gender):
        return gender

    tr = Transcript()
    tr.add_user("gender mail")  # real ASR error for "male"
    with call_context(tr):
        assert reg(gender="male") == "male"


def test_phonetic_does_not_ground_unrelated_word():
    @grounded(gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6))
    def reg(gender):
        return gender

    tr = Transcript()
    tr.add_user("I would like an appointment")
    with call_context(tr):
        assert isinstance(reg(gender="female"), SteerBack)


# --------------------------------------------------------------------------- #
# Read-back correction — "no, it's <spelling>" asserts the corrected value
# (the agent spelled it back wrong; the caller rejects + respells).
# --------------------------------------------------------------------------- #


def _reg_family():
    @grounded(family=Policy.SPOKEN(normalize="spelled-name"))
    def reg(family):
        return family

    return reg


@pytest.mark.parametrize(
    "correction",
    [
        "no it's R O M M U L A",
        "no, its R O M M U L A",
        "no that's R O M M U L A",
        "no the correct spelling is R O M M U L A",
    ],
)
def test_spelled_correction_after_wrong_readback_grounds(correction):
    reg = _reg_family()
    tr = Transcript()
    tr.add_user("my name is karthik")
    tr.add_agent("let me confirm, K A R T H I K  R O M U L A")
    tr.add_user(correction)  # caller rejects the wrong spelling and respells
    with call_context(tr):
        assert reg(family="Rommula") == "Rommula"


def test_phonetic_correction_after_wrong_readback_grounds():
    @grounded(gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6))
    def reg(gender):
        return gender

    tr = Transcript()
    tr.add_agent("did you say female?")
    tr.add_user("no it's male")
    with call_context(tr):
        assert reg(gender="male") == "male"


def test_genuine_retraction_still_blocks_after_correction_fix():
    # "not X" and "old X ... but now Y" must remain retractions, not corrections.
    reg = _reg_family()
    tr = Transcript()
    tr.add_user("my old surname was R O M M U L A but now it's Smith")
    with call_context(tr):
        assert isinstance(reg(family="Rommula"), SteerBack)


# --------------------------------------------------------------------------- #
# E — per-argument thresholds
# --------------------------------------------------------------------------- #


def test_per_argument_threshold_is_independent():
    # Loosen gender (phonetic) without touching dob's strictness.
    @grounded(
        gender=Policy.SPOKEN(normalize="phonetic", threshold=0.6),
        dob=Policy.SPOKEN(threshold=0.99),
    )
    def reg(gender, dob):
        return (gender, dob)

    tr = Transcript()
    tr.add_user("gender mail, born 1990-01-01")
    with call_context(tr):
        out = reg(gender="male", dob="1990-01-01")
    assert out == ("male", "1990-01-01")


# --------------------------------------------------------------------------- #
# K — reason codes
# --------------------------------------------------------------------------- #


def test_reason_code_on_pass_and_block():
    @grounded(name=Policy.SPOKEN)
    def reg(name):
        return name

    tr = Transcript()
    tr.add_user("this is Maria Gomez")
    log = AttestationLog()
    with call_context(tr, ledger=log):
        assert reg(name="Maria Gomez") == "Maria Gomez"
    rec = log.records[0].to_dict()
    assert rec["args"][0]["code"] == ReasonCode.OK_EXACT.value

    with call_context(Transcript()):
        steer = reg(name="Ghost Person")
    assert steer.code == ReasonCode.NOT_IN_TRANSCRIPT.value
    assert steer.failed[0].result.code == ReasonCode.NOT_IN_TRANSCRIPT.value


# --------------------------------------------------------------------------- #
# F — shadow / non-enforcing mode
# --------------------------------------------------------------------------- #


def test_shadow_mode_runs_body_and_records_would_block():
    ran = []

    @grounded(GroundingConfig(enforce=False), name=Policy.SPOKEN)
    def reg(name):
        ran.append(name)
        return "ran"

    log = AttestationLog()
    with call_context(Transcript(), ledger=log):  # empty transcript -> would block
        out = reg(name="Ghost")
    assert out == "ran" and ran == ["Ghost"]
    assert log.records[0].status == "shadow_block"


# --------------------------------------------------------------------------- #
# G — voice-safe SteerBack phrasing
# --------------------------------------------------------------------------- #


def test_spoken_steer_has_no_developer_jargon():
    @grounded(GroundingConfig(steer_style="spoken"), dob=Policy.SPOKEN)
    def reg(dob):
        return dob

    with call_context(Transcript()):
        steer = reg(dob="1990-01-01")
    msg = steer.message.lower()
    assert "date of birth" in msg
    for banned in ("tool", "function", "argument", "placeholder", "grounded"):
        assert banned not in msg


# --------------------------------------------------------------------------- #
# H — idempotency / double-write guard
# --------------------------------------------------------------------------- #


def test_idempotency_blocks_second_identical_write():
    calls = []

    @grounded_outputs(
        GroundingConfig(idempotency_key=lambda a: a["slot"]),
        slot=from_tool("get_slots", "slot"),
    )
    def book(slot):
        calls.append(slot)
        return "booked"

    tl = ToolLedger()
    tl.record("get_slots", [{"slot": "9am"}])
    with call_context(Transcript(), tools=tl):
        assert book(slot="9am") == "booked"
        dup = book(slot="9am")
    assert isinstance(dup, SteerBack) and dup.code == ReasonCode.DUPLICATE.value
    assert calls == ["9am"]  # body ran exactly once


# --------------------------------------------------------------------------- #
# J — provenance freshness / TTL
# --------------------------------------------------------------------------- #


def test_ledger_records_ttl_and_source_and_staleness():
    tl = ToolLedger()
    tl.record("get_slots", [{"slot": "9am"}], ttl_s=120, source="cache")
    assert tl.source_of("get_slots") == "cache"
    assert tl.is_stale("get_slots") is False
    assert tl.is_stale("get_slots", now=time.time() + 200) is True


def test_on_stale_block_refuses_expired_provenance():
    @grounded_outputs(
        GroundingConfig(on_stale="block"), slot=from_tool("get_slots", "slot")
    )
    def book(slot):
        return "booked"

    tl = ToolLedger()
    tl.record("get_slots", [{"slot": "9am"}], ttl_s=-1)  # already expired
    with call_context(Transcript(), tools=tl):
        out = book(slot="9am")
    assert isinstance(out, SteerBack) and out.code == ReasonCode.STALE_PROVENANCE.value


# --------------------------------------------------------------------------- #
# A — ground the completion claim (requires_write)
# --------------------------------------------------------------------------- #


def _booking_ledger():
    tl = ToolLedger()
    tl.record("list_doctors", [{"doctor_name": "Dr. Rashmi Indrakanti"}])
    tl.record("get_slots", [{"slot_start": "9:00 AM"}])
    return tl


def test_requires_write_blocks_when_action_unattested():
    tl = _booking_ledger()
    log = AttestationLog()  # book_appointment never ran
    with pytest.raises(UnattestedAction) as exc:
        render_spoken(
            "You have an appointment with {doctor} at {time}.",
            ledger=tl, attestations=log,
            requires_write=attested("book_appointment"),
            doctor=fact("Dr. Rashmi Indrakanti", ("list_doctors", "doctor_name")),
            time=fact("9:00 AM", ("get_slots", "slot_start")),
        )
    assert exc.value.action == "book_appointment"


def test_requires_write_renders_once_attested():
    tl = _booking_ledger()
    log = AttestationLog()
    log.build("book_appointment", [])
    out = render_spoken(
        "You have an appointment with {doctor} at {time}.",
        ledger=tl, attestations=log,
        requires_write=attested("book_appointment"),
        doctor=fact("Dr. Rashmi Indrakanti", ("list_doctors", "doctor_name")),
        time=fact("9:00 AM", ("get_slots", "slot_start")),
    )
    assert out == "You have an appointment with Dr. Rashmi Indrakanti at 9:00 AM."


def test_try_render_spoken_returns_none_on_unattested():
    tl = _booking_ledger()
    out = try_render_spoken(
        "You have an appointment with {doctor} at {time}.",
        ledger=tl, attestations=AttestationLog(),
        requires_write=attested("book_appointment"),
        doctor=fact("Dr. Rashmi Indrakanti", ("list_doctors", "doctor_name")),
        time=fact("9:00 AM", ("get_slots", "slot_start")),
    )
    assert out is None


# --------------------------------------------------------------------------- #
# B — turn-level completion-claim reconciler
# --------------------------------------------------------------------------- #


def test_reconcile_turn_flags_both_incident_claims():
    text = "Okay, you're all set. You have an appointment with Dr. Rashmi today at 9 AM."
    unbacked = reconcile_turn(text, attestations=AttestationLog())
    claims = {c.claim for c in unbacked}
    assert claims == {"registered", "booked"}


def test_reconcile_turn_passes_when_actions_attested():
    log = AttestationLog()
    log.build("register_patient", [])
    log.build("book_appointment", [])
    text = "You're all set and your appointment is booked."
    assert reconcile_turn(text, attestations=log) == []


def test_reconcile_turn_accepts_exported_dicts():
    log = AttestationLog()
    log.build("register_patient", [])
    unbacked = reconcile_turn("you're registered", attestations=log.export())
    assert unbacked == []


# --------------------------------------------------------------------------- #
# L — attest argument-less actions
# --------------------------------------------------------------------------- #


def test_attest_action_records_into_active_ledger():
    log = AttestationLog()
    with call_context(Transcript(), ledger=log, call_id="c1"):
        rec = attest_action("transfer_to_human", metadata={"dest": "front_desk"})
    assert rec is not None
    assert log.records[0].action == "transfer_to_human"
    assert log.records[0].metadata == {"dest": "front_desk"}
    assert log.records[0].call_id == "c1"


def test_attest_action_completes_reconcile_turn():
    log = AttestationLog()
    with call_context(Transcript(), ledger=log):
        attest_action("transfer_to_human")
    assert reconcile_turn("Transferring you now.", attestations=log) == []


def test_attest_action_without_ledger_returns_none():
    assert attest_action("end_call") is None


# --------------------------------------------------------------------------- #
# I — locale-aware grounding
# --------------------------------------------------------------------------- #


def test_get_locale_resolves_bcp47():
    assert get_locale("es-ES") is ES
    assert get_locale("es_MX") is ES
    assert get_locale("en-US") is EN
    assert get_locale("zz") is EN
    assert get_locale(None) is EN


def test_spanish_spoken_date_grounds_to_iso():
    @grounded(dob=Policy.SPOKEN)
    def reg(dob):
        return dob

    tr = Transcript()
    tr.add_user("nací el 18 de septiembre de 2004")
    with call_context(tr, metadata={"locale": "es-ES"}):
        assert reg(dob="2004-09-18") == "2004-09-18"


def test_spanish_confirmation_grounds():
    @grounded(name=Policy.CONFIRMED)
    def reg(name):
        return name

    tr = Transcript()
    tr.add_user("me llamo Carlos")
    tr.add_agent("¿Carlos, correcto?")
    tr.add_user("sí, exacto")
    with call_context(tr, metadata={"locale": "es"}):
        assert reg(name="Carlos") == "Carlos"


def test_english_locale_unchanged_by_default():
    @grounded(dob=Policy.SPOKEN)
    def reg(dob):
        return dob

    tr = Transcript()
    tr.add_user("born september 18th 2004")
    with call_context(tr):  # no locale -> English path
        assert reg(dob="2004-09-18") == "2004-09-18"
