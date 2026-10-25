"""The /live big-screen data: masking, checklist, banners from reason codes, auth. No network, no model."""
import json
import time

import pytest

from guardline import api, db, live
from tests.test_line import KNOWN_CALLER, MEMBERS, OTHER_CALLER, case_rows, client, say, to_step_up  # noqa: F401


def rec(outcome=None, reasons=(), flags=(), tactics=(), evidence=(), state="outcome", verified=False):
    return {"outcome": outcome, "reasons": list(reasons), "flags": list(flags), "tactics": list(tactics),
            "evidence": list(evidence), "state": state, "verified": verified}


TU = "TransUnion identity verification"
REV = "AccurateAppend reverse phone"


def ev(product, result):
    return {"product": product, "ran_at": 1.0, "result": result}


# -- masking -----------------------------------------------------------------------------------------------
def test_mask_text_hides_member_values_stated_names_and_digits():
    out = live.mask_text("My name is Margareto Kafur, born 01/01/1970, zip 12345, ssn six seven eight nine",
                         terms=["Margareto Kafur", "12345"])
    assert "Margareto" not in out and "1984" not in out and "12345" not in out and "six seven eight nine" not in out
    assert out.count(live.MASK) >= 3
    assert live.mask_text("I need to change my mailing address.") == "I need to change my mailing address."


def test_mask_text_short_terms_are_ignored_so_ordinary_words_survive():
    assert live.mask_text("ok yes", terms=["ok", ""]) == "ok yes"


# -- banner: built from reason codes ------------------------------------------------------------------------
def test_banner_impostor_number_belongs_to_someone_else_and_member_denied():
    b = live.banner(rec("escalated_high", ["REVERSE_PHONE_NAME_DIFFERS", "STEP_UP_REQUIRED", "MEMBER_DENIED_CALL"], ["CALLBACK_QUEUED"]))
    assert (b["severity"], b["headline"]) == ("alert", "IMPOSTOR DETECTED")
    assert b["detail"] == "number belongs to someone else, member denied"
    assert "call back" in b["note"]


def test_banner_synthetic_identity_flagged_by_transunion():
    b = live.banner(rec("escalated_high", ["CRS_IDENTITY_MISMATCH_OR_SYNTHETIC", "REVERSE_PHONE_NO_RECORD"]))
    assert b["headline"] == "SYNTHETIC IDENTITY" and b["detail"].startswith("flagged by TransUnion")
    assert "number has no record" in b["detail"]


def test_banner_verified_and_in_progress():
    assert live.banner(rec("verified_handoff", ["VERIFIED"], verified=True))["headline"] == "Verified"
    assert live.banner(rec(None))["severity"] == "progress"


def test_banner_other_outcomes_come_from_their_codes():
    assert live.banner(rec("unverified_handoff", ["IDENTITY_NOT_CONFIRMED"]))["headline"] == "NOT CONFIRMED"
    assert live.banner(rec("unverified_handoff", ["APP_APPROVAL_TIMEOUT"]))["headline"] == "NO APPROVAL"
    assert live.banner(rec("escalated_high", ["REDIRECT_TO_NEW_NUMBER"]))["headline"] == "IMPOSTOR SIGNAL"
    assert live.banner(rec("escalated_high", ["MEMBER_DENIED_CALL"]))["headline"] == "MEMBER DENIED"  # nothing else points at an impostor
    assert live.banner(rec("escalated_high", ["CRS_DECEASED_INDICATOR"]))["headline"] == "DECEASED INDICATOR"


def test_banner_text_depends_on_the_codes_not_on_the_demo():
    a = live.banner(rec("escalated_high", ["MEMBER_DENIED_CALL", "REVERSE_PHONE_NO_RECORD"]))
    b = live.banner(rec("escalated_high", ["MEMBER_DENIED_CALL", "REVERSE_PHONE_NAME_DIFFERS"]))
    assert a["detail"] != b["detail"] and "no record" in a["detail"] and "someone else" in b["detail"]
    unknown = live.banner(rec("escalated_high", ["SOME_FUTURE_CODE"]))
    assert unknown["headline"] == "HIGH RISK" and unknown["severity"] == "alert"  # falls back to the outcome


# -- checklist ------------------------------------------------------------------------------------------------
def status(checks):
    return {c["key"]: c["status"] for c in checks}


def test_checklist_lights_up_as_the_case_fills_in():
    start = live.checklist(rec(None, state="collect_identity"))
    assert set(status(start).values()) == {"pending"}
    ran = live.checklist(rec(None, ["CONTACT_CHANGE_STEP_UP", "STEP_UP_REQUIRED"], evidence=[ev(TU, "Subject found, no alerts"),
                             ev(REV, "Number registered to a different person")], state="step_up"))
    s = status(ran)
    assert s["details"] == "ok" and s["identity"] == "ok" and s["reverse"] == "bad" and s["approval"] == "pending"
    done = live.checklist(rec("escalated_high", ["MEMBER_DENIED_CALL"], tactics=["TACTIC_URGENCY"],
                              evidence=[ev(TU, "Subject found, no alerts"), ev(REV, "Number registered to a different person")]))
    assert status(done)["approval"] == "bad" and status(done)["tactics"] == "warn"
    assert next(c for c in done if c["key"] == "tactics")["chips"] == ["Urgency"]


def test_checklist_plain_results_for_synthetic_fraud_alert_and_timeouts():
    synth = live.checklist(rec("escalated_high", evidence=[ev(TU, "Identity mismatch or synthetic-identity indicators (codes 2101, 2201, 2202)"),
                                                           ev(REV, "No record for the number the call came from")]))
    assert status(synth)["identity"] == "bad" and status(synth)["reverse"] == "bad"
    fraud = live.checklist(rec("verified_handoff", ["APP_APPROVED_SIMULATED", "NAME_FUZZY_MATCH"], verified=True,
                               evidence=[ev(TU, "Fraud alert on file (code 2401)"), ev(REV, "Number registered to this member")]))
    assert status(fraud)["identity"] == "warn" and status(fraud)["approval"] == "ok"
    assert "approximately" in next(c for c in fraud if c["key"] == "details")["text"]
    assert status(live.checklist(rec("unverified_handoff", ["APP_APPROVAL_TIMEOUT"])))["approval"] == "warn"
    failed = live.checklist(rec("unverified_handoff", ["IDENTITY_REENTRY_REQUESTED", "IDENTITY_NOT_CONFIRMED"]))
    assert status(failed)["details"] == "bad" and status(failed)["identity"] == "na"


# -- the endpoint -----------------------------------------------------------------------------------------------
def test_live_needs_the_demo_key(client, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "demo-key")
    assert client.get("/api/live").status_code == 401
    assert client.get("/api/live?key=nope").status_code == 401
    ok = client.get("/api/live?key=demo-key")
    assert ok.status_code == 200 and ok.json()["call"] is None and "stats" in ok.json()


def test_live_shows_a_masked_chat_the_checks_and_a_banner_for_the_latest_call(client, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "demo-key")
    r = to_step_up(client, "c-live", caller=OTHER_CALLER, name="Test Person")
    assert r["continue"] is True
    data = client.get("/api/live?key=demo-key").json()
    call = data["call"]
    assert call["persona"] == "P01" and call["channel"] == "line" and call["known_number"] is False
    assert call["banner"]["headline"] == "Call in progress"
    assert {c["key"]: c["status"] for c in call["checks"]}["approval"] == "pending"
    blob = " ".join(b["text"] for b in call["transcript"])  # the words only (timestamps are digits too)
    assert call["transcript"] and {t["who"] for t in call["transcript"]} == {"caller", "agent"}
    for m in MEMBERS.values():
        for value in m.sensitive_values():
            assert value.lower() not in blob.lower()
    assert live.MASK in blob  # the stated name and the digits were masked
    assert "Test Person" not in blob and "0 1 0 2" not in blob
    client.post("/api/approval", json={"case_id": case_rows()[0]["case_id"], "decision": "denied"})
    say(client, "c-live", "", caller=OTHER_CALLER)
    done = client.get("/api/live?key=demo-key").json()["call"]
    assert done["banner"]["severity"] == "alert" and done["banner"]["headline"] == "IMPOSTOR DETECTED"
    assert done["banner"]["detail"] == "number belongs to someone else, member denied"


def test_live_prefers_an_active_call_over_a_finished_one(client, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "demo-key")
    say(client, "c-old", "Goodbye, bye", caller=OTHER_CALLER)  # ends
    time.sleep(0.01)
    say(client, "c-new", "Hi there", caller=OTHER_CALLER)  # still open
    call = client.get("/api/live?key=demo-key").json()["call"]
    assert call["outcome"] is None


# -- member app notice ---------------------------------------------------------------------------------------
def test_notice_appears_only_for_a_recently_ended_non_verified_case(tmp_path):
    db.engine(tmp_path / "n.db")
    try:
        pub = {"case_id": "c1", "outcome": "escalated_high", "risk": "HIGH", "verified": False, "language": "en",
               "final_state": "outcome", "requests": [], "reasons": [], "flags": ["CALLBACK_QUEUED"]}
        assert db.latest_callback_notice() == {"notice": False}
        rec1 = db.upsert_case(pub, "member", "P01")
        n = db.latest_callback_notice()
        assert n["notice"] is True and n["case_id"] == "c1"
        assert db.latest_callback_notice(now=rec1["ended_at"] + 500)["notice"] is False  # too old
        db.upsert_case({**pub, "case_id": "c2", "outcome": "verified_handoff", "verified": True}, "member", "P01")
        assert db.latest_callback_notice()["case_id"] == "c1"  # a verified case never triggers the callback notice
    finally:
        db._engine = None
