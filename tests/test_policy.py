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


def test_row1_stated_mismatch_is_high():
    case, _ = call(StubFacts(), second=identity_utterance(stated_zip="11111"))
    assert case.outcome == "escalated_high" and "STATED_MISMATCH" in case.reasons


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
def test_row8_app_denied_or_timeout_is_high(app):
    case, _ = call(FixtureFacts("P01"), app=app, then=[Interpretation(intent="confirm_approval")])
    assert case.outcome == "escalated_high" and f"APP_APPROVAL_{app.upper()}" in case.reasons


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
