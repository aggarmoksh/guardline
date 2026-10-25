"""Natural wording with a code guard: decisions fixed, words free. No network, no model."""
import pytest

from guardline import api, db, phrasing, speaker
from guardline.llm import LLMError
from guardline.phrasing import guard, natural_reply
from tests.test_line import MEMBERS, FakeLLM  # noqa: F401  (fake members and model)


def leaks(text):
    return any(v.lower() in text.lower() for m in MEMBERS.values() for v in m.sensitive_values() if len(v) >= 3)


def check(text, message_id="ask_dob", verified=False, required=None, language="en"):
    return guard(text, message_id=message_id, language=language, verified=verified, leaks=leaks, required=required)


def test_a_natural_reply_that_asks_the_question_passes():
    assert check("Thanks, that helps. What is your date of birth?") is None
    assert check("Gracias. ¿Cuál es su fecha de nacimiento?", language="es") is None


def test_a_reply_containing_a_member_value_is_rejected():
    assert check("Thanks Test Person, what is your date of birth?") == "MEMBER_VALUE"
    assert check("Got it, 90210. What is your date of birth?") == "MEMBER_VALUE"


@pytest.mark.parametrize("claim", [
    "You are verified, what is your date of birth?", "You're now verified. What is your date of birth?",
    "Your identity has been confirmed. What is your date of birth?", "We've approved it. What is your date of birth?",
    "Verification is complete. What is your date of birth?", "You have been successfully verified. Birth date?"])
def test_claims_of_verification_or_approval_are_rejected_when_not_verified(claim):
    assert check(claim) == "CLAIMS_VERIFICATION"


def test_the_same_words_are_allowed_when_the_caller_really_is_verified():
    assert check("You are verified, I am passing you to a team member.", message_id="handoff", verified=True) is None


def test_describing_the_pending_approval_is_not_a_claim():
    assert check("I sent a request to your app. Once you have approved it, I will continue. Please open the app.",
                 message_id="ask_app_approval") is None


def test_omitting_the_required_question_is_rejected():
    assert check("Thanks, that is helpful.") == "OMITS_MUST_ASK"
    assert check("Could you give me your ZIP?", message_id="ask_dob") == "OMITS_MUST_ASK"


def test_a_digit_readback_must_contain_the_digits_exactly():
    assert check("I heard 0 1 0 1, is that right?", message_id="heard", required="0 1 0 1") is None
    assert check("I heard zero one zero one, right?", message_id="heard", required="0 1 0 1") == "OMITS_MUST_ASK"


def test_empty_or_runaway_replies_are_rejected():
    assert check("") == "EMPTY_OR_LONG" and check("x" * 500) == "EMPTY_OR_LONG"


class _Say:
    def __init__(self, text=None, boom=False):
        self.text, self.boom = text, boom

    def complete(self, system, user, max_tokens=120):
        if self.boom:
            raise LLMError("down")
        self.user = user
        return self.text


def test_a_rejected_reply_falls_back_to_the_fixed_sentence_and_is_counted():
    phrasing.GUARD_STATS.clear()
    text, reason = natural_reply(_Say("You are verified!"), "ask_dob", "en", "hello", "FIXED", verified=False, leaks=leaks)
    assert (text, reason) == ("FIXED", "CLAIMS_VERIFICATION") and phrasing.GUARD_STATS["CLAIMS_VERIFICATION"] == 1


def test_a_good_reply_is_used_and_the_model_input_holds_no_member_data():
    llm = _Say("Thanks for that. What is your date of birth?")
    text, reason = natural_reply(llm, "ask_dob", "en", "my name is [REDACTED]", "FIXED", verified=False, leaks=leaks)
    assert (text, reason) == ("Thanks for that. What is your date of birth?", None)
    assert "Intent:" in llm.user and "Required question:" in llm.user and "[REDACTED]" in llm.user
    assert not leaks(llm.user)


def test_a_model_failure_uses_the_fixed_sentence():
    assert natural_reply(_Say(boom=True), "ask_dob", "en", None, "FIXED", verified=False, leaks=leaks) == ("FIXED", "LLM_ERROR")


def test_no_intent_or_must_ask_contains_a_member_value():
    for message_id, (intent, must_ask) in phrasing.INTENTS.items():
        assert not leaks(intent) and not leaks(must_ask or ""), message_id


def test_every_asking_intent_has_keywords_in_both_languages():
    for message_id, (_, must_ask) in phrasing.INTENTS.items():
        if must_ask and message_id in phrasing.KEYWORDS["en"]:
            assert message_id in phrasing.KEYWORDS["es"]


def test_the_web_speaker_uses_natural_wording_and_falls_back_with_an_event(tmp_path, monkeypatch):
    from guardline.facts import FixtureFacts
    from guardline.policy import Policy
    from guardline.redact import register_sensitive
    register_sensitive(MEMBERS["P01"].sensitive_values())
    db.engine(tmp_path / "p.db")
    monkeypatch.setattr(api, "NATURAL_WEB", True)
    monkeypatch.setattr(api, "_leak_values", None)
    monkeypatch.setattr(api, "load_members", lambda: MEMBERS)
    sess = api.Session(Policy(MEMBERS["P01"], FixtureFacts("P01"), "member", lambda _i: "approved", keypad_mode=True), "member", "P01")
    fake = _Say("Sure thing. What is your full name?")
    monkeypatch.setattr(api, "_llm", fake)
    text, _, _, _ = api._speak(sess, "ask_name", audio=False)  # audio=False is the phone line: no natural wording here
    assert text == speaker.TEMPLATES["ask_name"]
    monkeypatch.setattr(api, "speech", lambda: type("S", (), {"synthesize": lambda self, t, l: b"x" * 10})())
    text, _, _, _ = api._speak(sess, "ask_name", utterance="hi there, my name is Test Person")
    assert text == "Sure thing. What is your full name?" and "Test Person" not in fake.user
    fake.text = "You are verified. What is your full name?"
    text, _, _, _ = api._speak(sess, "ask_name")
    assert text == speaker.TEMPLATES["ask_name"]
    events = [e.kind + ":" + str(e.code) for e in db.Session(db.engine()).exec(db.select(db.EventRecord)).all()]
    assert "guard_rejected:CLAIMS_VERIFICATION" in events
    db._engine = None


def test_the_spanish_approval_reply_may_say_aplicacion():
    assert check("Le enviamos una solicitud a su aplicación bancaria. Por favor, ábrala y toque aprobar.",
                 message_id="ask_app_approval", language="es") is None


def test_other_is_noise_unless_the_caller_makes_a_request():
    from guardline import interpreter
    base = {"language": "en", "redirect_to_new_number": False, "tactics": ["none"]}
    assert interpreter.validate({**base, "intent": "provide_info", "request_types": ["other"]}).request_types == ()
    assert interpreter.validate({**base, "intent": "confirm_approval", "request_types": ["other"]}).request_types == ()
    assert interpreter.validate({**base, "intent": "make_request", "request_types": ["other"]}).request_types == ("other",)
    assert interpreter.validate({**base, "intent": "provide_info", "request_types": ["change_phone"]}).request_types == ("change_phone",)
