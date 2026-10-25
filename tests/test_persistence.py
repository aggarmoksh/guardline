"""Persistence, dashboard counters, SSE broker, async approval. No network and no LLM."""
import json
import threading

import pytest

from guardline import db
from guardline.broker import Broker
from guardline.facts import FixtureFacts
from guardline.policy import Interpretation, Policy
from tests.test_policy import FAKE, type_all


@pytest.fixture()
def fresh_db(tmp_path):
    db.engine(tmp_path / "t.db")
    yield
    db._engine = None


def public(case_id, outcome=None, risk="LOW", verified=False, reasons=(), flags=(), lang="en", state="outcome"):
    return {"case_id": case_id, "outcome": outcome, "risk": risk, "verified": verified, "language": lang,
            "final_state": state, "requests": [], "reasons": list(reasons), "flags": list(flags)}


def test_case_file_round_trips_and_splits_tactics_from_flags(fresh_db):
    db.upsert_case(public("c1", "escalated_high", "HIGH", reasons=["REDIRECT_TO_NEW_NUMBER"],
                          flags=["FRAUD_ALERT", "TACTIC_URGENCY"], lang="es"), "other")
    [row] = db.list_cases()
    assert row["outcome"] == "escalated_high" and row["risk"] == "HIGH" and row["language"] == "es"
    assert row["reasons"] == ["REDIRECT_TO_NEW_NUMBER"] and row["flags"] == ["FRAUD_ALERT"]
    assert row["tactics"] == ["TACTIC_URGENCY"] and row["calling_from"] == "other"
    assert row["ended_at"] is not None and row["created_at"] <= row["updated_at"]


def test_upsert_updates_the_same_case_and_keeps_created_at(fresh_db):
    first = db.upsert_case(public("c1", state="step_up"), "member")
    second = db.upsert_case(public("c1", "verified_handoff", verified=True), "member")
    assert len(db.list_cases()) == 1 and second["created_at"] == first["created_at"]
    assert first["ended_at"] is None and second["ended_at"] is not None


def test_stored_case_has_only_code_fields(fresh_db):
    db.upsert_case(public("c1", "verified_handoff", verified=True, reasons=["VERIFIED"]), "member")
    [row] = db.list_cases()
    assert set(row) == {"case_id", "outcome", "risk", "verified", "language", "state", "calling_from", "reasons",
                        "flags", "tactics", "created_at", "updated_at", "ended_at"}


def test_counters_come_from_stored_rows(fresh_db):
    assert db.stats() == {"calls": 0, "impostor_attempts": 0, "impostors_verified": 0, "pii_leaks": 0}
    db.upsert_case(public("legit", "verified_handoff", verified=True), "member")
    db.upsert_case(public("redirect", "escalated_high", "HIGH"), "other")
    db.upsert_case(public("pushy", "unverified_handoff", "MEDIUM", flags=["TACTIC_AUTHORITY_CLAIM"]), "member")
    assert db.stats() == {"calls": 3, "impostor_attempts": 2, "impostors_verified": 0, "pii_leaks": 0}


def test_impostors_verified_counter_is_live_not_hardcoded(fresh_db):
    db.upsert_case(public("bad", "verified_handoff", "HIGH", verified=True), "member")  # a (hypothetical) failure
    assert db.stats()["impostors_verified"] == 1
    db.upsert_case(public("pushy-but-verified", "verified_handoff", verified=True, flags=["TACTIC_URGENCY"]), "member")
    assert db.stats()["impostors_verified"] == 2


def test_pii_leak_counter_counts_leak_events(fresh_db):
    db.add_event("c1", "pii_leak", "greet")
    db.add_event("c1", "outcome", "ended_safe")
    assert db.stats()["pii_leaks"] == 1


def test_dashboard_endpoints_need_the_demo_key(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    from guardline import api
    monkeypatch.setenv("DEMO_KEY", "test-key")
    client = TestClient(api.app)
    assert client.get("/api/cases").status_code == 401
    assert client.get("/api/cases?key=wrong").status_code == 401
    assert client.get("/api/cases/stream").status_code == 401
    ok = client.get("/api/cases?key=test-key")
    assert ok.status_code == 200 and set(ok.json()) == {"cases", "stats"}
    assert client.get("/api/cases", headers={"X-Demo-Key": "test-key"}).status_code == 200


def test_dashboard_is_closed_when_no_demo_key_is_configured(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    from guardline import api
    monkeypatch.delenv("DEMO_KEY", raising=False)
    assert TestClient(api.app).get("/api/cases?key=").status_code == 401


def test_broker_delivers_to_each_subscriber_on_its_channel():
    b = Broker()
    got_public, got_cases = [], []
    ready = threading.Barrier(3)

    def listen(channel, sink):
        stream = b.subscribe(channel, keepalive=60)
        next(stream)  # ": connected"
        ready.wait()
        sink.append(next(stream))

    threads = [threading.Thread(target=listen, args=("public", got_public), daemon=True),
               threading.Thread(target=listen, args=("cases", got_cases), daemon=True)]
    [t.start() for t in threads]
    ready.wait()
    b.publish("public", {"type": "approval_requested"})
    threads[0].join(timeout=5)  # the cases listener stays blocked: nothing was published on its channel
    assert got_public and json.loads(got_public[0][len("data: "):])["type"] == "approval_requested"
    assert got_cases == []


def test_async_approval_waits_then_advances_without_a_caller_line():
    decision = {"v": None}
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: decision["v"] or "timeout", keypad_mode=True,
               async_approval=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    p.handle(Interpretation(stated_name="Test Person"))
    assert type_all(p).message_id == "ask_app_approval" and p.case.state == "step_up"
    waiting = p.handle(Interpretation(intent="unclear"))  # a stray line must not resolve the approval
    assert waiting.message_id == "ask_app_approval" and p.case.outcome is None
    decision["v"] = "approved"
    t = p.advance_after_approval()
    assert t.done and p.case.outcome == "verified_handoff" and p.case.verified
    assert p.advance_after_approval() is None  # nothing left to advance


def test_async_approval_denied_with_clean_phone_is_unverified_handoff():
    p = Policy(FAKE, FixtureFacts("P01"), "member", lambda _i: "denied", keypad_mode=True, async_approval=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    p.handle(Interpretation(stated_name="Test Person"))
    type_all(p)
    p.advance_after_approval()
    assert p.case.outcome == "unverified_handoff" and p.case.risk_level == "MEDIUM"


def test_hangup_while_waiting_in_async_mode_is_still_abandonment():
    p = Policy(FAKE, FixtureFacts("P01"), "other", lambda _i: "timeout", keypad_mode=True, async_approval=True)
    p.start()
    p.handle(Interpretation(intent="make_request", request_types=("change_mailing_address",)))
    p.handle(Interpretation(stated_name="Test Person"))
    type_all(p)
    p.handle(Interpretation(intent="goodbye"))
    assert p.case.outcome == "escalated_high" and "ABANDONED_DURING_STEP_UP" in p.case.reasons
