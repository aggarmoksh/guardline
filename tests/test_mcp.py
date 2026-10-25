"""The read-only MCP server: no member values, no write tools, a read-only database. No network, no model."""
import asyncio
import json
import re
import sqlite3
from pathlib import Path

import pytest

mcp_server = pytest.importorskip("guardline.mcp_server")  # needs the optional extra: uv sync --extra mcp

from guardline import db  # noqa: E402
from tests.test_line import MEMBERS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def public(case_id, outcome=None, risk="LOW", verified=False, reasons=(), flags=(), evidence=(), state="outcome"):
    return {"case_id": case_id, "outcome": outcome, "risk": risk, "verified": verified, "language": "en", "final_state": state,
            "requests": [], "reasons": list(reasons), "flags": list(flags), "evidence": list(evidence)}


@pytest.fixture()
def cases_db(tmp_path):
    path = tmp_path / "mcp.db"
    db.engine(path)
    ev = [{"product": "TransUnion identity verification", "ran_at": 1_700_000_000.0, "result": "Subject found, no alerts"},
          {"product": "AccurateAppend reverse phone", "ran_at": 1_700_000_001.0, "result": "Number registered to a different person"}]
    db.upsert_case(public("case-aaaaaaaa", "escalated_high", "HIGH", reasons=["REVERSE_PHONE_NAME_DIFFERS", "MEMBER_DENIED_CALL"],
                          flags=["CALLBACK_QUEUED", "TACTIC_URGENCY"], evidence=ev), "other", "P01", "line", False)
    db.upsert_case(public("case-bbbbbbbb", "verified_handoff", verified=True, reasons=["VERIFIED", "HANDOFF_WITH_REQUEST"]),
                   "member", "P16", "line", True)
    db.upsert_case(public("case-cccccccc", None, state="collect_identity"), "member", "P13")
    db._engine = None
    return path


def all_member_values():
    values = {v for m in MEMBERS.values() for v in m.sensitive_values()}
    real = ROOT / "fixtures" / "raw" / "members.json"
    if real.exists():  # on the author's machine also check the real fixture values (never printed)
        values |= {str(v) for rec in json.loads(real.read_text(encoding="utf-8")).values() for v in rec.values()}
    return {v.lower() for v in values if len(v) >= 3}


def assert_no_member_values(obj):
    blob = json.dumps(obj, default=str).lower()
    assert not [v for v in all_member_values() if v in blob]


# -- what the tools return -------------------------------------------------------------------------------------
def test_list_recent_cases_is_newest_first_clamped_and_code_only(cases_db):
    rows = mcp_server.list_recent_cases_data(2, cases_db)
    assert len(rows) == 2 and {"case_id", "outcome", "risk", "reason_codes", "tactics", "demo_member"} <= set(rows[0])
    assert len(mcp_server.list_recent_cases_data(500, cases_db)) == 3 and len(mcp_server.list_recent_cases_data(0, cases_db)) == 1
    assert_no_member_values(rows)


def test_get_case_returns_the_code_only_file_with_plain_evidence(cases_db):
    c = mcp_server.get_case_data("case-aaaaaaaa", cases_db)
    assert c["outcome"] == "escalated_high" and c["tactics"] == ["TACTIC_URGENCY"] and "CALLBACK_QUEUED" in c["flags"]
    assert c["crs_evidence"][1]["result"] == "Number registered to a different person"
    assert set(c) == {"case_id", "demo_member", "channel", "known_number", "language", "outcome", "risk", "verified", "reasons",
                      "flags", "tactics", "crs_evidence", "started_at", "ended_at"}
    assert mcp_server.get_case_data("aaaaaaaa", cases_db)["case_id"] == "case-aaaaaaaa"  # the dashboard shows the last 8 characters
    assert mcp_server.get_case_data("nope", cases_db) == {"error": "case not found"}
    assert_no_member_values(c)


def test_explain_case_is_plain_english_from_the_codes(cases_db):
    e = mcp_server.explain_case_data("case-aaaaaaaa", cases_db)
    assert e["headline"] == "IMPOSTOR DETECTED: number belongs to someone else, member denied"
    s = e["summary"]
    assert "high risk" in s and "tapped Deny" in s and "registered to someone else" in s and "urgency" in s
    assert "TransUnion identity verification said: Subject found, no alerts." in s
    assert "call back at the number on file" in s
    ok = mcp_server.explain_case_data("case-bbbbbbbb", cases_db)
    assert ok["summary"].startswith("The caller was verified and handed to a person.")
    assert mcp_server.explain_case_data("zzz", cases_db) == {"error": "case not found"}
    assert_no_member_values([e, ok])


def test_an_unknown_reason_code_is_described_generically_not_dropped(cases_db):
    db.engine(cases_db)
    db.upsert_case(public("case-dddddddd", "escalated_high", "HIGH", reasons=["SOME_FUTURE_CODE"]), "member", "P01")
    db._engine = None
    assert "some future code" in mcp_server.explain_case_data("case-dddddddd", cases_db)["summary"]


def test_eval_summary_reads_the_numbers_from_results_v2():
    s = mcp_server.eval_summary_data()
    assert s["source"] == "eval/RESULTS_v2.md" and "post hoc" in s["note"] and s["model"] == "claude-haiku-5-5"
    passed = s["metrics"]["Cases passed (of 30)"]
    assert passed["per_run"] == [29, 29, 29] and passed["min"] == passed["max"] == 29
    assert s["metrics"]["Impostors verified (target 0)"]["max"] == 0 and s["metrics"]["PII leaks (agent utterances, target 0)"]["max"] == 0
    assert_no_member_values(s)


# -- read-only by construction ---------------------------------------------------------------------------------
def test_there_are_only_read_only_tools_and_none_can_write_or_verify():
    tools = asyncio.run(mcp_server.server.list_tools())
    assert {t.name for t in tools} == {"list_recent_cases", "get_case", "explain_case", "eval_summary"}
    forbidden = re.compile(r"verify|approve|update|delete|set_|write|create|mark|escalate|insert|remove|reset|change|grant|send")
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert not forbidden.search(t.name)
        assert not forbidden.search(" ".join(t.input_schema.get("properties", {})))  # no parameter that could carry a write


def test_the_database_is_opened_read_only(cases_db):
    conn = mcp_server._connect(cases_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO caserecord (case_id) VALUES ('x')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM caserecord")
    conn.close()


def test_the_module_contains_no_write_statements_and_imports_no_decision_code():
    src = (ROOT / "guardline" / "mcp_server.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", code)
    assert "upsert" not in code and ".commit(" not in code
    imports = re.findall(r"^(?:from|import) (guardline[\w.]*)", src, flags=re.M)
    assert set(imports) <= {"guardline"}  # only `from guardline import live` (pure display helpers)
    assert "policy" not in " ".join(re.findall(r"^from guardline import (.+)$", src, flags=re.M))


def test_calling_the_tools_through_the_server_returns_no_member_values(cases_db, monkeypatch):
    monkeypatch.setenv("GUARDLINE_DB", str(cases_db))
    results = [asyncio.run(mcp_server.server.call_tool(name, args)) for name, args in
               [("list_recent_cases", {"limit": 3}), ("get_case", {"case_id": "case-aaaaaaaa"}),
                ("explain_case", {"case_id": "case-aaaaaaaa"}), ("eval_summary", {})]]
    texts = [c.text for r in results for c in r.content]
    assert len(texts) >= 4 and all(not r.is_error for r in results)
    assert_no_member_values(texts)
    assert any("case-aaaaaaaa" in t for t in texts)
