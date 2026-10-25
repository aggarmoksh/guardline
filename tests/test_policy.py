import json
"""Policy tests: no network, no LLM. CRS facts come from tests/fixtures/crs_summaries.json."""
import inspect

import pytest

from guardline.facts import CallingFromFacts, FixtureFacts, IdentityFacts
from guardline.members import Member
from guardline.policy import Interpretation, Policy, Signals, score_risk

FAKE = Member("T01", "Test Person", "1990-01-02", "90210", "4321", "0000000000", "t@example.test")


class StubFacts:
    def __init__(self, codes=(), status="SUBJECT_FOUND", risk="MEDIUM", record=True, name_ok=True, email_ok=True):
        self._i = IdentityFacts(tuple(codes), status, risk)
        self._c = CallingFromFacts(record, name_ok)
        self._e = email_ok

    def identity(self):
        return self._i

    def calling_from(self, kind):
        return self._c

    def email_valid(self, email):
        return self._e


def identity_utterance(**over):
    base = dict(intent="provide_info", language="en", stated_name="test person", stated_dob="1990-01-02",
                stated_zip="90210", stated_ssn_last4="4321")
    base.update(over)
    return Interpretation(**base)


def call(facts, *, calling_from="member", app="approved", first=None, second=None, then=()):
    p = Policy(FAKE, facts, calling_from, lambda _id: app)
    p.start()
    turns = [first or Interpretation(intent="make_request", request_types=("change_mailing_address",)),
             second or identity_utterance(), *then]
    turn = None
    for it in turns:
        turn = p.handle(it)
        if turn.done:
            break
    return p.case, turn


def test_row1_stated_mismatch_asks_once_then_unverified_handoff_medium():
    bad = identity_utterance(stated_zip="11111")
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "approved")
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    turn = p.handle(bad)
    assert turn.message_id == "ask_reenter" and not turn.done and p.case.outcome is None
    assert p.case.stated == {}  # re-entry from scratch, and the reply never says which field failed
    turn = p.handle(bad)
    assert turn.done and p.case.outcome == "unverified_handoff" and p.case.risk_level == "MEDIUM"
    assert "IDENTITY_NOT_CONFIRMED" in p.case.reasons and not p.case.verified


def test_row1_second_attempt_can_succeed():
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "approved")
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    p.handle(identity_utterance(stated_dob="1991-01-01"))
    turn = p.handle(identity_utterance())
    assert p.case.outcome == "verified_handoff" and turn.done


def test_row2_subject_not_found_is_unverified_handoff_medium_crs_no_file():
    case, _ = call(StubFacts(status="SUBJECT_NOT_FOUND", risk="HIGH"))
    assert case.outcome == "unverified_handoff" and case.risk_level == "MEDIUM"
    assert "CRS_NO_FILE" in case.reasons and not case.verified


@pytest.mark.parametrize("persona", ["P12", "P13"])
def test_row3_mismatch_or_synthetic_codes_fixture(persona):
    case, _ = call(FixtureFacts(persona))
    assert case.outcome == "escalated_high" and "CRS_IDENTITY_MISMATCH_OR_SYNTHETIC" in case.reasons


@pytest.mark.parametrize("code", [2201, 2202, 2101])
def test_row3_each_code_is_high(code):
    case, _ = call(StubFacts(codes=[code]))
    assert case.outcome == "escalated_high"


def test_row4_deceased_is_high():
    case, _ = call(StubFacts(codes=[2301]))
    assert case.outcome == "escalated_high" and "CRS_DECEASED_INDICATOR" in case.reasons


def test_row4_deceased_fixture_p14():
    case, _ = call(FixtureFacts("P14"))
    assert case.outcome == "escalated_high" and "CRS_DECEASED_INDICATOR" in case.reasons


def test_row5_fraud_alert_requires_step_up_and_review():
    case, turn = call(FixtureFacts("P16"))
    assert case.state == "step_up" and turn.message_id == "ask_app_approval"
    case, _ = call(FixtureFacts("P16"), then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "verified_handoff" and {"FRAUD_ALERT", "HUMAN_REVIEW"} <= set(case.flags) and "CRS_FRAUD_ALERT" in case.reasons


def test_row6_calling_number_name_differs_requires_step_up():
    case, turn = call(FixtureFacts("P01"), calling_from="other",
                      first=Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    assert turn.message_id == "ask_app_approval" and "REVERSE_PHONE_NAME_DIFFERS" in case.reasons


def test_row7_calling_number_no_record_requires_step_up_and_flag():
    case, turn = call(StubFacts(record=False, name_ok=False),
                      first=Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    assert turn.message_id == "ask_app_approval"
    assert "REVERSE_PHONE_NO_RECORD" in case.reasons and "NO_PHONE_RECORD" in case.flags


@pytest.mark.parametrize("calling_from", ["member", "other"])
def test_row8_member_deny_is_escalated_high_whatever_the_caller_id(calling_from):
    case, _ = call(FixtureFacts("P01"), calling_from=calling_from, app="denied",
                   then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "escalated_high" and case.risk_level == "HIGH" and "MEMBER_DENIED_CALL" in case.reasons
    assert not case.verified


def test_row8_timeout_with_reverse_phone_mismatch_is_high():
    case, _ = call(FixtureFacts("P01"), calling_from="other", app="timeout",
                   then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "escalated_high" and "APP_APPROVAL_TIMEOUT" in case.reasons


def test_row8_timeout_without_reverse_mismatch_is_unverified_medium():
    case, _ = call(FixtureFacts("P01"), app="timeout", then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "unverified_handoff" and case.risk_level == "MEDIUM"
    assert "APP_APPROVAL_TIMEOUT" in case.reasons and "MEMBER_DENIED_CALL" not in case.reasons


def test_row8_redirect_to_new_number_is_high():
    case, _ = call(FixtureFacts("P01"), then=[Interpretation(redirect_to_new_number=True)])
    assert case.outcome == "escalated_high" and "REDIRECT_TO_NEW_NUMBER" in case.reasons


def test_row9_invalid_new_email_flags_and_requires_step_up_but_is_not_high():
    first = Interpretation(intent="make_request", request_types=("change_email",), new_contact_value="x@x")
    case, turn = call(StubFacts(email_ok=False), first=first)
    assert turn.message_id == "ask_app_approval" and case.outcome is None and case.risk_level != "HIGH"
    assert "EMAIL_INVALID" in case.flags
    case, _ = call(StubFacts(email_ok=False), first=first, then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "verified_handoff" and "EMAIL_INVALID" in case.flags and case.risk_level == "LOW"
    case, _ = call(StubFacts(email_ok=False), first=first, app="denied", then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "escalated_high" and "MEMBER_DENIED_CALL" in case.reasons


def test_row10_contact_change_with_money_is_high_even_when_clean():
    first = Interpretation(intent="make_request", request_types=("change_phone", "move_money"))
    case, _ = call(FixtureFacts("P01"), first=first)
    assert case.outcome == "escalated_high" and "CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS" in case.reasons
    assert not case.verified


def test_clean_member_with_contact_change_steps_up_then_verified():
    case, _ = call(FixtureFacts("P01"), then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "verified_handoff" and case.verified and case.risk_level == "LOW"


def test_every_decision_leaves_a_reason_code():
    case, _ = call(FixtureFacts("P13"))
    assert case.audit and {code for _, code in case.audit} == set(case.reasons)


def test_score_risk_takes_no_language_or_name():
    params = set(inspect.signature(score_risk).parameters)
    assert params == {"s"} and not {"language", "name", "accent"} & {f for f in Signals.__dataclass_fields__}


def test_goodbye_ends_safe_and_unclear_x3_ends_safe():
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "approved")
    p.start()
    assert p.handle(Interpretation(intent="goodbye")).done and p.case.outcome == "ended_safe"
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "approved")
    p.start()
    turns = [p.repeat_needed() for _ in range(3)]
    assert turns[-1].done and p.case.outcome == "ended_safe"


def test_human_request_before_verification_is_unverified_handoff_medium():
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "approved")
    p.start()
    turn = p.handle(Interpretation(intent="ask_human"))
    assert turn.done and p.case.outcome == "unverified_handoff"
    assert p.case.risk_level == "MEDIUM" and "CALLER_REQUESTED_HUMAN" in p.case.reasons


def test_llm_failure_ends_safe_with_escalation_not_repeat():
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "approved")
    p.start()
    turn = p.llm_unavailable()
    assert turn.done and turn.message_id == "llm_down" and p.case.outcome == "ended_safe"
    assert "LLM_UNAVAILABLE" in p.case.reasons and "ESCALATED_TO_HUMAN" in p.case.flags


class _BrokenLLM:
    def forced_tool_call(self, *a, **k):
        from guardline.llm import LLMError
        raise LLMError("down")


class _GarbageLLM:
    def forced_tool_call(self, *a, **k):
        return {"intent": "not_an_intent", "language": "en", "request_types": []}


def test_interpreter_distinguishes_llm_down_from_invalid_output():
    from guardline import interpreter
    from guardline.llm import LLMError
    with pytest.raises(LLMError):
        interpreter.interpret(_BrokenLLM(), "hello", "collect_identity")
    assert interpreter.interpret(_GarbageLLM(), "hello", "collect_identity") is None


# -- keypad and name matching -------------------------------------------------------
def keypad_policy(facts=None):
    p = Policy(FAKE, facts or FixtureFacts("P01"), "member", lambda _i: "approved", keypad_mode=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    return p


def type_all(p, dob="01021990", zip_="90210", ssn="4321"):
    p.provide_typed("dob", dob)
    p.provide_typed("zip", zip_)
    return p.provide_typed("ssn_last4", ssn)


def test_keypad_requests_one_field_at_a_time_in_order():
    p = keypad_policy()
    assert p.handle(Interpretation(intent="provide_info")).expect == "name"
    t = p.handle(Interpretation(stated_name="Test Person"))
    assert (t.message_id, t.expect) == ("ask_dob", "dob")
    t = p.provide_typed("dob", "01021990")
    assert (t.message_id, t.expect) == ("ask_zip", "zip")
    t = p.provide_typed("zip", "90210")
    assert (t.message_id, t.expect) == ("ask_ssn4", "ssn_last4")
    assert p.provide_typed("ssn_last4", "4321").done and p.case.outcome == "verified_handoff"


def test_keypad_mode_ignores_digits_the_interpreter_heard():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Person", stated_dob="1990-01-02", stated_zip="90210",
                            stated_ssn_last4="4321"))
    assert set(p.case.stated) == {"name"}
    assert p.expected_field() == "dob"


def test_keypad_rejects_malformed_entries_without_counting_a_failure():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Person"))
    assert p.provide_typed("dob", "13452020").expect == "dob"  # month 13
    assert p.provide_typed("dob", "0102").expect == "dob"  # too short
    assert p.provide_typed("zip", "90210").expect == "dob"  # not the field being asked for
    assert p._identity_failures == 0


def test_keypad_wrong_dob_twice_ends_unverified_handoff_medium():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Person"))
    t = type_all(p, dob="01021991")
    assert t.message_id == "ask_reenter" and t.expect == "name"
    p.handle(Interpretation(stated_name="Test Person"))
    t = type_all(p, dob="01021991")
    assert t.done and p.case.outcome == "unverified_handoff" and p.case.risk_level == "MEDIUM"
    assert p.case.reasons.count("IDENTITY_NOT_CONFIRMED") == 1


@pytest.mark.parametrize("spoken", ["TEST PERSON", "  test   person ", "Tést Pérson"])
def test_name_exact_after_normalizing_case_accents_spaces(spoken):
    p = keypad_policy()
    p.handle(Interpretation(stated_name=spoken))
    type_all(p)
    assert p.case.outcome == "verified_handoff" and "NAME_FUZZY_MATCH" not in p.case.reasons


def test_misheard_surname_with_exact_digits_passes_with_fuzzy_reason():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Persun"))
    type_all(p)
    assert p.case.outcome == "verified_handoff" and "NAME_FUZZY_MATCH" in p.case.reasons


def test_fuzzy_name_never_helps_when_a_digit_is_wrong():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Persun"))
    t = type_all(p, ssn="4322")
    assert t.message_id == "ask_reenter" and not p.case.verified and "NAME_FUZZY_MATCH" not in p.case.reasons


def test_clearly_different_name_with_exact_digits_is_not_a_match():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Wholly Different"))
    t = type_all(p)
    assert t.message_id == "ask_reenter" and not p.case.verified


def test_jaro_winkler_threshold_behaviour():
    from guardline.policy import FUZZY_NAME_THRESHOLD, jaro_winkler, normalize_name
    assert FUZZY_NAME_THRESHOLD == 0.88
    assert jaro_winkler("abc", "abc") == 1.0
    assert jaro_winkler(normalize_name("Test Persun"), normalize_name("Test Person")) >= 0.88
    assert jaro_winkler("test person", "wholly different") < 0.88


def test_p13_still_escalated_high_through_keypad():
    p = keypad_policy(FixtureFacts("P13"))
    p.handle(Interpretation(stated_name="Test Person"))
    type_all(p)
    assert p.case.outcome == "escalated_high" and "CRS_IDENTITY_MISMATCH_OR_SYNTHETIC" in p.case.reasons


def test_typed_values_never_reach_the_public_case_file():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Person"))
    type_all(p)
    blob = str(p.case.public())
    assert not any(v in blob for v in ("1990-01-02", "90210", "4321"))


# -- a request that arrives after identity was verified still needs its step-up ------------
def verified_without_request(app="approved"):
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: app, keypad_mode=True)
    p.start()
    p.handle(Interpretation(intent="unclear"))
    p.handle(Interpretation(stated_name="Test Person"))
    t = type_all(p)
    assert t.message_id == "ask_request" and p.case.verified and p.case.state == "request"
    return p


def test_late_contact_change_request_requires_step_up_then_hands_off():
    p = verified_without_request()
    t = p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    assert t.message_id == "ask_app_approval" and not t.done and p.case.state == "step_up"
    t = p.handle(Interpretation(intent="confirm_approval"))
    assert t.done and p.case.outcome == "verified_handoff" and "APP_APPROVED_SIMULATED" in p.case.reasons


def test_late_contact_change_denied_is_escalated_high_and_not_verified():
    p = verified_without_request(app="denied")
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    p.handle(Interpretation(intent="confirm_approval"))
    assert p.case.outcome == "escalated_high" and not p.case.verified and "MEMBER_DENIED_CALL" in p.case.reasons


def test_late_contact_change_timeout_is_unverified_medium_and_not_verified():
    p = verified_without_request(app="timeout")
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    p.handle(Interpretation(intent="confirm_approval"))
    assert p.case.outcome == "unverified_handoff" and p.case.risk_level == "MEDIUM" and not p.case.verified


# -- idle expiry ----------------------------------------------------------------------
def test_idle_timeout_outside_step_up_ends_safe_call_abandoned():
    p = keypad_policy()
    t = p.idle_timeout()
    assert t.done and p.case.outcome == "ended_safe" and "CALL_ABANDONED" in p.case.reasons
    assert p.idle_timeout() is None  # a closed call is left alone


def test_idle_timeout_while_approval_pending_follows_the_abandon_rule():
    p = at_step_up(FixtureFacts("P01"), calling_from="other")
    p.idle_timeout()
    assert p.case.outcome == "escalated_high" and "ABANDONED_DURING_STEP_UP" in p.case.reasons
    p = at_step_up(FixtureFacts("P01"))
    p.idle_timeout()
    assert p.case.outcome == "unverified_handoff" and p.case.risk_level == "MEDIUM"


def test_late_non_contact_request_needs_no_step_up():
    p = verified_without_request()
    t = p.handle(Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    assert t.done and p.case.outcome == "verified_handoff"


def test_risk_indicator_high_alone_is_not_an_identity_signal():
    case, _ = call(StubFacts(status="SUBJECT_FOUND", risk="HIGH"),
                   first=Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    assert case.outcome == "verified_handoff"


# -- hang-up while the approval is pending, and scam-tactic flags --------------------------
def at_step_up(facts, calling_from="member"):
    p = Policy(FAKE, facts, calling_from, lambda _i: "approved", keypad_mode=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    p.handle(Interpretation(stated_name="Test Person"))
    t = type_all(p)
    assert t.message_id == "ask_app_approval" and p.case.state == "step_up"
    return p


def test_hangup_during_step_up_with_reverse_phone_mismatch_is_escalated_high():
    p = at_step_up(FixtureFacts("P01"), calling_from="other")
    t = p.handle(Interpretation(intent="goodbye"))
    assert t.done and p.case.outcome == "escalated_high"
    assert "ABANDONED_DURING_STEP_UP" in p.case.reasons and "CALLER_ENDED_CALL" not in p.case.reasons
    assert not p.case.verified


def test_hangup_during_step_up_with_clean_reverse_phone_is_unverified_handoff_medium():
    p = at_step_up(FixtureFacts("P01"))
    t = p.handle(Interpretation(intent="goodbye"))
    assert t.done and p.case.outcome == "unverified_handoff" and p.case.risk_level == "MEDIUM"
    assert "ABANDONED_DURING_STEP_UP" in p.case.reasons and not p.case.verified


def test_silence_during_step_up_is_the_same_as_hanging_up():
    p = at_step_up(FixtureFacts("P01"), calling_from="other")
    turns = [p.repeat_needed() for _ in range(3)]
    assert turns[-1].done and p.case.outcome == "escalated_high" and "ABANDONED_DURING_STEP_UP" in p.case.reasons


def test_goodbye_outside_step_up_is_still_a_clean_end():
    p = keypad_policy()
    p.handle(Interpretation(intent="goodbye"))
    assert p.case.outcome == "ended_safe" and "ABANDONED_DURING_STEP_UP" not in p.case.reasons


def test_instruction_injection_adds_flag_and_leaves_the_outcome_unchanged():
    def run(tactics):
        p = keypad_policy()
        p.handle(Interpretation(stated_name="Test Person", tactics=tactics))
        type_all(p, dob="01021991")  # wrong DOB twice -> unverified_handoff either way
        p.handle(Interpretation(stated_name="Test Person", tactics=tactics))
        type_all(p, dob="01021991")
        return p.case

    plain, injected = run(()), run(("instruction_injection", "authority_claim"))
    assert injected.outcome == plain.outcome == "unverified_handoff" and not injected.verified
    assert injected.reasons == plain.reasons
    assert {"TACTIC_INSTRUCTION_INJECTION", "TACTIC_AUTHORITY_CLAIM"} <= set(injected.flags)
    assert not any(f.startswith("TACTIC_") for f in plain.flags)


def test_tactics_never_verify_or_skip_the_step_up():
    p = Policy(FAKE, FixtureFacts("P01"), "other", lambda _i: "approved", keypad_mode=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",),
                            tactics=("authority_claim", "instruction_injection", "urgency")))
    p.handle(Interpretation(stated_name="Test Person"))
    t = type_all(p)
    assert t.message_id == "ask_app_approval" and not p.case.verified and p.case.outcome is None


def test_tactic_none_adds_no_flag_and_validation_rejects_unknown_tactics():
    from guardline import interpreter
    base = {"intent": "unclear", "language": "en", "request_types": [], "redirect_to_new_number": False}
    assert interpreter.validate({**base, "tactics": ["none"]}).tactics == ()
    assert interpreter.validate({**base, "tactics": ["urgency"]}).tactics == ("urgency",)
    with pytest.raises(ValueError):
        interpreter.validate({**base, "tactics": ["made_up"]})


# -- CRS evidence: one plain-words entry per CRS call, no bureau values -------------------------------
def evidence_of(facts, calling_from="member", request=("change_mailing_address",), new_value=None, approve="approved"):
    p = Policy(FAKE, facts, calling_from, lambda _i: approve, keypad_mode=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=request, new_contact_value=new_value))
    p.handle(Interpretation(stated_name="Test Person"))
    type_all(p)
    return {e["product"]: e for e in p.case.evidence}, p


def test_evidence_names_the_product_the_time_and_the_result_in_plain_words():
    import time
    before = time.time()
    ev, _ = evidence_of(FixtureFacts("P01"))
    assert set(ev) == {"TransUnion identity verification", "AccurateAppend reverse phone"}
    assert ev["TransUnion identity verification"]["result"] == "Subject found, no alerts"
    assert ev["AccurateAppend reverse phone"]["result"] == "Number registered to this member"
    assert all(before <= e["ran_at"] <= time.time() for e in ev.values())


def test_evidence_fraud_alert_and_synthetic_and_different_person_in_plain_words():
    ev, _ = evidence_of(FixtureFacts("P16"))
    assert ev["TransUnion identity verification"]["result"] == "Fraud alert on file (code 2401)"
    ev, _ = evidence_of(FixtureFacts("P13"))
    assert "synthetic-identity indicators (codes 2101, 2201, 2202)" in ev["TransUnion identity verification"]["result"]
    assert ev["AccurateAppend reverse phone"]["result"] == "No record for the number the call came from"
    ev, _ = evidence_of(FixtureFacts("P01"), calling_from="other")
    assert ev["AccurateAppend reverse phone"]["result"] == "Number registered to a different person"
    ev, _ = evidence_of(FixtureFacts("P14"))
    assert "Deceased indicator (code 2301)" in ev["TransUnion identity verification"]["result"]


def test_evidence_no_file_and_new_email_checks():
    ev, _ = evidence_of(StubFacts(status="SUBJECT_NOT_FOUND"))
    assert ev["TransUnion identity verification"]["result"] == "No file found for this member"
    ev, _ = evidence_of(StubFacts(email_ok=False), request=("change_email",), new_value="x@x")
    assert ev["AtData FraudFinder"]["result"] == "New email is invalid"
    ev, _ = evidence_of(StubFacts(email_ok=True), request=("change_email",), new_value="a@b.co")
    assert ev["AtData FraudFinder"]["result"] == "New email is valid"


def test_evidence_in_the_public_case_file_holds_no_member_data_or_raw_values():
    import json
    _, p = evidence_of(FixtureFacts("P16"))
    blob = json.dumps(p.case.public()["evidence"])
    for value in ("1990-01-02", "90210", "4321", "Test Person", "t@example.test", "0000000000"):
        assert value not in blob
    assert set(p.case.public()["evidence"][0]) == {"product", "ran_at", "result"}


def test_no_crs_call_means_no_evidence_when_identity_fails_locally():
    p = keypad_policy()
    p.handle(Interpretation(stated_name="Test Person"))
    type_all(p, dob="01021991")
    assert p.case.evidence == []  # the local mismatch stops before any CRS call


# -- phone on file (OPERATOR_PHONE override) ---------------------------------------------------
def test_operator_phone_overrides_p01_phone_on_file_and_is_not_committed(tmp_path, monkeypatch):
    from guardline import members
    raw = tmp_path / "members.json"
    raw.write_text(json.dumps({
        "P01": {"name": "A B", "dob": "1990-01-02", "zip": "90210", "ssn_last4": "4321", "phone_on_file": "0000000001",
                "email_on_file": "a@b.test"},
        "P02": {"name": "C D", "dob": "1990-01-02", "zip": "90210", "ssn_last4": "4321", "phone_on_file": "0000000002",
                "email_on_file": "c@d.test"}}), encoding="utf-8")
    monkeypatch.setattr(members, "RAW", tmp_path)
    monkeypatch.setenv("OPERATOR_PHONE", "+1 (415) 555-0199")  # a fake number
    m = members.load_members()
    assert m["P01"].phone_on_file == "4155550199" and m["P02"].phone_on_file == "0000000002"
    assert m["P01"].is_known_number("+14155550199") and m["P01"].is_known_number("415-555-0199")
    assert not m["P01"].is_known_number("4155550100") and not m["P02"].is_known_number("")
    monkeypatch.delenv("OPERATOR_PHONE")
    monkeypatch.setattr(members, "load_dotenv", lambda *a, **k: None)
    assert members.load_members()["P01"].phone_on_file == "0000000001"
