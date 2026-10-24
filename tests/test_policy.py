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


def test_row2_subject_not_found_is_high():
    case, _ = call(StubFacts(status="SUBJECT_NOT_FOUND", risk="HIGH"))
    assert case.outcome == "escalated_high" and "CRS_NOT_FOUND_OR_HIGH" in case.reasons


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
    assert case.outcome == "verified_handoff" and "HUMAN_REVIEW" in case.flags and "CRS_FRAUD_ALERT" in case.reasons


def test_row6_calling_number_name_differs_requires_step_up():
    case, turn = call(FixtureFacts("P01"), calling_from="other",
                      first=Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    assert turn.message_id == "ask_app_approval" and "REVERSE_PHONE_NAME_DIFFERS" in case.reasons


def test_row7_calling_number_no_record_requires_step_up_and_flag():
    case, turn = call(StubFacts(record=False, name_ok=False),
                      first=Interpretation(intent="make_request", request_types=("balance_inquiry",)))
    assert turn.message_id == "ask_app_approval"
    assert "REVERSE_PHONE_NO_RECORD" in case.reasons and "NO_PHONE_RECORD" in case.flags


@pytest.mark.parametrize("app", ["denied", "timeout"])
def test_row8_app_denied_or_timeout_with_reverse_phone_mismatch_is_high(app):
    case, _ = call(FixtureFacts("P01"), calling_from="other", app=app,
                   then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "escalated_high" and f"APP_APPROVAL_{app.upper()}" in case.reasons


@pytest.mark.parametrize("app", ["denied", "timeout"])
def test_row8_app_denied_or_timeout_without_reverse_mismatch_is_unverified_medium(app):
    case, _ = call(FixtureFacts("P01"), app=app, then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "unverified_handoff" and case.risk_level == "MEDIUM"
    assert f"APP_APPROVAL_{app.upper()}" in case.reasons


def test_row8_redirect_to_new_number_is_high():
    case, _ = call(FixtureFacts("P01"), then=[Interpretation(redirect_to_new_number=True)])
    assert case.outcome == "escalated_high" and "REDIRECT_TO_NEW_NUMBER" in case.reasons


def test_row9_invalid_new_email_is_high_flag():
    first = Interpretation(intent="make_request", request_types=("change_email",), new_contact_value="x@x")
    case, _ = call(StubFacts(email_ok=False), first=first)
    assert case.outcome == "escalated_high" and "NEW_EMAIL_INVALID" in case.flags


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
