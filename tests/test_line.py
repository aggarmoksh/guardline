"""POST /api/line/turn: the Line agent is a relay; state, verification and wording stay in the backend. No network, no LLM."""
import json
import logging
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from guardline import api, db, line
from guardline.facts import FixtureFacts
from guardline.llm import LLMError
from guardline.members import Member

KNOWN_CALLER = "+1 (415) 555-0100"
OTHER_CALLER = "+1 408 555 0111"
TOKEN = "line-token"

MEMBERS = {
    "P01": Member("P01", "Test Person", "1990-01-02", "90210", "4321", "4155550100", "t@example.test"),
    "P13": Member("P13", "Wholly Different", "1985-05-05", "11111", "1111", "4155550113", "w@example.test"),
    "P16": Member("P16", "Another Name", "1975-07-07", "22222", "2222", "4155550116", "a@example.test"),
}


class FakeLLM:
    def __init__(self):
        self.fail = False
        self.calls = 0

    def forced_tool_call(self, system, user, tool):
        self.calls += 1
        if self.fail:
            raise LLMError("down")
        said = user.split("Caller said: ", 1)[1]
        low = said.lower()
        # like the real model, tag plain answers with request type "other"
        out = {"intent": "provide_info", "language": "en", "request_types": ["other"], "redirect_to_new_number": False,
               "tactics": ["none"]}
        if "mailing address" in low:
            out.update(request_types=["change_mailing_address"], intent="make_request")
        if m := re.search(r"my name is ([\w' ]+)", said, re.I):
            out["stated_name"] = m.group(1).strip()
        if "new number" in low:
            out["redirect_to_new_number"] = True
        if low.strip() in ("yes", "no"):
            out["intent"] = "confirm_approval"
        if "bye" in low:
            out["intent"] = "goodbye"
        return out

    def complete(self, system, user, max_tokens=120):
        return "ES: " + user.split("Sentence: ", 1)[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db.engine(tmp_path / "line.db")
    fake = FakeLLM()
    monkeypatch.setenv("LINE_PROBE_TOKEN", TOKEN)
    monkeypatch.setenv("LINE_SPANISH_NUMBER", "+1 361 555 0199")
    monkeypatch.setattr(api, "_llm", fake)
    monkeypatch.setattr(api, "load_members", lambda: MEMBERS)
    monkeypatch.setattr(api, "make_facts", lambda persona, member, caller: FixtureFacts(persona))
    monkeypatch.setattr(api, "_leak_values", None)
    api._sessions.clear()
    api._line_sessions.clear()
    c = TestClient(api.app)
    c.fake = fake
    yield c
    api._sessions.clear()
    api._line_sessions.clear()
    db._engine = None


def say(client, conv, utterance, caller=KNOWN_CALLER, called="+13613138909"):
    r = client.post("/api/line/turn", json={"line_token": TOKEN, "conversation_id": conv, "caller_id": caller,
                                            "called_number": called, "utterance": utterance})
    assert r.status_code == 200
    return r.json()


def to_step_up(client, conv, caller=KNOWN_CALLER, name="Test Person"):
    say(client, conv, "I need to change my mailing address.", caller)
    r = say(client, conv, f"My name is {name}.", caller)
    assert r["say_fallback"] == line.PHRASES["en"]["ask_dob"]
    for spoken, heard in (("zero one zero two one nine nine zero", "0 1 0 2 1 9 9 0"),
                          ("nine zero two one zero", "9 0 2 1 0"), ("four three two one", "4 3 2 1")):
        r = say(client, conv, spoken, caller)
        assert r["say_fallback"] == f"I heard {heard}. Is that right?"
        r = say(client, conv, "yes", caller)
    return r


def case_rows():
    return db.list_cases()


def test_known_number_is_the_candidate_and_the_call_goes_through_approval_to_handoff(client):
    r = to_step_up(client, "c-known")
    assert r["continue"] is True and not r["end_call"] and "approval request" in r["say_fallback"]
    [row] = case_rows()
    assert row["persona"] == "P01" and row["known_number"] is True and row["channel"] == "line"
    approved = client.post("/api/approval", json={"case_id": row["case_id"], "decision": "approved"})
    assert approved.json() == {"ok": True}
    done = say(client, "c-known", "")  # the continuation call returns the final words
    assert done["end_call"] is True and done["continue"] is False and "team member" in done["say_fallback"]
    [row] = case_rows()
    assert row["outcome"] == "verified_handoff" and row["verified"] is True
    assert {e["product"] for e in row["evidence"]} == {"TransUnion identity verification", "AccurateAppend reverse phone"}


def test_the_held_turn_waits_for_the_approval_and_returns_when_it_arrives(client):
    to_step_up(client, "c-wait")
    [row] = case_rows()
    result = {}
    t = threading.Thread(target=lambda: result.update(say(client, "c-wait", "")))
    t.start()
    time.sleep(0.3)
    assert t.is_alive()  # still holding the turn
    client.post("/api/approval", json={"case_id": row["case_id"], "decision": "denied"})
    t.join(timeout=10)
    assert result["end_call"] is True
    assert case_rows()[0]["outcome"] == "escalated_high" and "MEMBER_DENIED_CALL" in case_rows()[0]["reasons"]


def test_unknown_number_picks_the_candidate_from_the_stated_name_and_gets_step_up(client):
    r = to_step_up(client, "c-unknown", caller=OTHER_CALLER, name="Test Persun")
    assert r["continue"] is True
    [row] = case_rows()
    assert row["persona"] == "P01" and row["known_number"] is False and row["calling_from"] == "other"
    client.post("/api/approval", json={"case_id": row["case_id"], "decision": "approved"})
    say(client, "c-unknown", "", caller=OTHER_CALLER)
    [row] = case_rows()
    assert "NAME_FUZZY_MATCH" in row["reasons"] and "REVERSE_PHONE_NAME_DIFFERS" in row["reasons"]


def test_unknown_number_with_a_name_closest_to_another_demo_member(client):
    say(client, "c-p13", "My name is Wholly Different.", caller=OTHER_CALLER)
    assert case_rows()[0]["persona"] == "P13"


def test_a_wrong_confirmation_asks_for_the_item_again(client):
    say(client, "c-no", "I need to change my mailing address.")
    say(client, "c-no", "My name is Test Person.")
    r = say(client, "c-no", "zero one zero two one nine nine zero")
    assert "Is that right?" in r["say_fallback"]
    r = say(client, "c-no", "no")
    assert r["say_fallback"].endswith(line.PHRASES["en"]["ask_dob"]) and not r["end_call"]
    r = say(client, "c-no", "maybe")  # not usable as a date of birth either
    assert r["say_fallback"] == line.PHRASES["en"]["retry"] + " " + line.PHRASES["en"]["ask_dob"]


def test_redirect_during_digit_collection_escalates_immediately(client):
    say(client, "c-redir", "I need to change my mailing address.", caller=OTHER_CALLER)
    say(client, "c-redir", "My name is Test Person.", caller=OTHER_CALLER)
    r = say(client, "c-redir", "Send it to my new number instead.", caller=OTHER_CALLER)
    assert r["end_call"] is True
    assert case_rows()[0]["outcome"] == "escalated_high" and "REDIRECT_TO_NEW_NUMBER" in case_rows()[0]["reasons"]


def test_llm_failure_ends_safely_with_our_own_words(client):
    client.fake.fail = True
    r = say(client, "c-fail", "Hello, I need help.")
    assert r["end_call"] is True and "technical problem" in r["say_fallback"]
    assert case_rows()[0]["outcome"] == "ended_safe" and "LLM_UNAVAILABLE" in case_rows()[0]["reasons"]


def test_any_other_failure_returns_the_fallback_sentence_and_ends(client, monkeypatch):
    monkeypatch.setattr(api, "_line_turn", lambda body: (_ for _ in ()).throw(RuntimeError("boom")))
    r = say(client, "c-boom", "hi")
    assert r["say_fallback"] == line.PHRASES["en"]["fallback"] and r["end_call"] is True and r["continue"] is False
    assert r["must_ask"] is None and r["state"] == "outcome"
    r = say(client, "c-boom", "hola", called="+1 361 555 0199")
    assert r["say_fallback"] == line.PHRASES["es"]["fallback"]


def test_the_token_is_required(client):
    bad = client.post("/api/line/turn", json={"line_token": "nope", "conversation_id": "x", "utterance": "hi"})
    assert bad.status_code == 401
    assert client.post("/api/line/turn", json={"conversation_id": "x", "utterance": "hi"}).status_code == 401


def test_the_spanish_number_gives_a_spanish_call(client):
    r = say(client, "c-es", "I need to change my mailing address.", called="+1 (361) 555-0199")
    assert r["say_fallback"].startswith("ES: ")
    assert case_rows()[0]["language"] == "es"


def test_the_caller_number_is_never_in_a_response_a_log_or_the_database(client, caplog):
    caplog.set_level(logging.INFO)
    responses = [to_step_up(client, "c-priv")]
    responses.append(say(client, "c-priv", "I need help", caller=OTHER_CALLER))
    blob = json.dumps(responses) + caplog.text + json.dumps(case_rows())
    blob += json.dumps(client.get("/api/approval").json())
    for number in ("4155550100", "4085550111", "+1 (415) 555-0100", "415-555-0100", "(415) 555"):
        assert number not in blob
    assert "5550100" not in blob and "0100" in caplog.text  # only the last 4 digits reach the log
    sess = api._line_sessions["c-priv"]
    assert sess.line.caller_digits == "4155550100"  # held in memory for the CRS lookup only


def test_spoken_digit_helpers():
    assert line.spoken_digits("oh one zero one") == "0101"
    assert line.spoken_digits("my zip is 1 2 3 4 5.") == "12345"
    assert line.spoken_digits("cero uno cero uno") == "0101"
    assert line.item_digits("zip", "one two three four five") == "12345" and line.item_digits("zip", "95") is None
    assert line.item_digits("ssn_last4", "six seven eight nine") == "6789"
    assert line.item_digits("dob", "January 1st 1970", "1970-01-01") == "01011970"
    assert line.item_digits("dob", "zero one zero one one nine seven zero") == "01011970"
    assert line.yes_no("yes that is right") is True and line.yes_no("no") is False and line.yes_no("hmm") is None
    assert line.yes_no("sí") is True and line.read_back("0101") == "0 1 0 1"


def test_asking_to_repeat_the_readback_repeats_it_and_does_not_escalate(client):
    say(client, "c-rep", "I need to change my mailing address.", caller=OTHER_CALLER)
    say(client, "c-rep", "My name is Test Person.", caller=OTHER_CALLER)
    heard = say(client, "c-rep", "zero one zero two one nine nine zero", caller=OTHER_CALLER)
    calls_before = client.fake.calls
    again = say(client, "c-rep", "Sorry, can you repeat that please?", caller=OTHER_CALLER)
    assert again == heard and not again["end_call"]
    assert client.fake.calls == calls_before  # no model call, and certainly no "ask for a human"
    assert case_rows()[0]["outcome"] is None and "CALLER_REQUESTED_HUMAN" not in case_rows()[0]["reasons"]
    assert say(client, "c-rep", "yes", caller=OTHER_CALLER)["say_fallback"] == line.PHRASES["en"]["ask_zip"]


def test_digits_and_yes_no_are_handled_without_the_language_model(client):
    to_step_up(client, "c-fast")
    assert client.fake.calls == 2  # only the request and the name needed the model; 3 digit items and 3 yes answers did not


def test_digits_with_other_words_still_go_to_the_model_so_interrupts_are_caught(client):
    say(client, "c-mix", "I need to change my mailing address.", caller=OTHER_CALLER)
    say(client, "c-mix", "My name is Test Person.", caller=OTHER_CALLER)
    before = client.fake.calls
    r = say(client, "c-mix", "zero one zero two, send it to my new number", caller=OTHER_CALLER)
    assert client.fake.calls == before + 1 and r["end_call"] is True


def test_the_held_turn_reply_tells_the_agent_to_speak_first(client):
    r = to_step_up(client, "c-hold")
    assert r["continue"] is True and "out loud first" in r["next"] and "empty utterance" in r["next"]
    client.post("/api/approval", json={"case_id": case_rows()[0]["case_id"], "decision": "approved"})
    assert say(client, "c-hold", "")["end_call"] is True  # answered, so the held call returns at once


def test_repeat_and_filler_detection_helpers():
    assert line.is_repeat_request("could you repeat that") and line.is_repeat_request("I missed that, again please")
    assert line.is_repeat_request("repita por favor") and not line.is_repeat_request("yes") and not line.is_repeat_request("")
    assert line.pure_digits("uh zero one zero one") and not line.pure_digits("zero three, send it to my new number")
    assert not line.pure_digits("hello there") and line.pure_yes_no("yes that is right") is True
    assert not line.pure_yes_no("yes but send it to my new number") and not line.pure_yes_no("hello")


def test_the_reply_is_intent_must_ask_state_and_fallback_with_no_member_data(client):
    say(client, "c-shape", "I need to change my mailing address.")
    r = say(client, "c-shape", "My name is Test Person.")
    assert set(r) == {"intent", "must_ask", "state", "say_fallback", "end_call", "continue"}
    assert r["state"] == "collect_identity" and "date of birth" in r["must_ask"].lower()
    assert "say" not in r  # the agent words it; the fixed sentence is only the fallback
    blob = json.dumps(r)
    for m in MEMBERS.values():
        for value in m.sensitive_values():
            assert value.lower() not in blob.lower()


def test_the_approval_reply_names_no_outcome_and_asks_the_member_to_use_the_app(client):
    r = to_step_up(client, "c-appr")
    assert r["state"] == "step_up" and r["continue"] is True and "approved" in r["intent"].lower()
    assert "do not say it was approved" in r["intent"].lower() and "app" in r["must_ask"].lower()


def test_a_no_to_a_readback_asks_just_the_question_again_without_i_did_not_catch_that(client):
    say(client, "c-wrong", "I need to change my mailing address.")
    say(client, "c-wrong", "My name is Test Person.")
    say(client, "c-wrong", "zero one zero two one nine nine zero")
    r = say(client, "c-wrong", "no")
    assert r["say_fallback"] == line.PHRASES["en"]["ask_dob"] and "catch" not in r["say_fallback"]
    assert r["must_ask"] == line.PHRASES["en"]["ask_dob"] and "wrong" in r["intent"]


def test_a_call_with_no_stated_request_is_asked_what_it_needs_after_verification(client):
    # the model tags every plain answer with request type "other"; that must not count as a request
    say(client, "c-norq", "Hi.")
    say(client, "c-norq", "My name is Test Person.")
    for spoken in ("zero one zero two one nine nine zero", "nine zero two one zero", "four three two one"):
        say(client, "c-norq", spoken)
        r = say(client, "c-norq", "yes")
    assert r["state"] == "request" and "help" in r["must_ask"].lower() and not r["end_call"]
    assert case_rows()[0]["outcome"] is None


def test_a_long_wait_becomes_short_holds_with_a_reassuring_reply(client, monkeypatch):
    monkeypatch.setattr(api, "HOLD_SECONDS", 0.2)
    to_step_up(client, "c-short")
    r = say(client, "c-short", "")  # no answer yet
    assert r["continue"] is True and r["state"] == "step_up" and "still here" in r["say_fallback"].lower()
    assert "outcome" not in r["intent"].lower() or "no outcome" in r["intent"].lower() or "nothing about" in r["intent"].lower()
    client.post("/api/approval", json={"case_id": case_rows()[0]["case_id"], "decision": "approved"})
    done = say(client, "c-short", "")
    assert done["end_call"] is True and case_rows()[0]["outcome"] == "verified_handoff"


def test_what_the_caller_says_while_waiting_is_handled_at_once_not_after_the_hold(client):
    to_step_up(client, "c-talk", caller=OTHER_CALLER)
    r = say(client, "c-talk", "Actually just send it to my new number.", caller=OTHER_CALLER)
    assert r["end_call"] is True
    assert case_rows()[0]["outcome"] == "escalated_high" and "REDIRECT_TO_NEW_NUMBER" in case_rows()[0]["reasons"]


def test_chatter_while_waiting_gets_the_waiting_reply_and_does_not_resolve_the_approval(client):
    to_step_up(client, "c-chat")
    r = say(client, "c-chat", "Hmm, how would you even know if it is really me?")
    assert r["continue"] is True and r["state"] == "step_up" and not r["end_call"]
    assert case_rows()[0]["outcome"] is None
