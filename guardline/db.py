"""Persistence: SQLite via SQLModel. Code-only case files and events; no transcripts, no member data."""
import json
import os
import time
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine, select

DB_PATH = Path(os.environ.get("GUARDLINE_DB", Path(__file__).resolve().parents[1] / "guardline.db"))

_engine = None


class CaseRecord(SQLModel, table=True):
    case_id: str = Field(primary_key=True)
    outcome: str | None = None
    risk: str = "LOW"
    verified: bool = False
    language: str = "en"
    state: str = "greet"
    calling_from: str = "member"  # simulated caller ID
    channel: str = "web"  # web | line
    known_number: bool | None = None  # phone line only: caller ID equals the phone on file (smoother, never proof)
    persona: str | None = None  # sandbox demo-member id such as P13; never a name or value
    reasons: str = "[]"  # JSON list of codes
    flags: str = "[]"  # JSON list of non-tactic flags
    tactics: str = "[]"  # JSON list of TACTIC_ flags
    evidence: str = "[]"  # JSON list of {product, ran_at, result}: plain words, no bureau values
    created_at: float = 0.0
    updated_at: float = 0.0
    ended_at: float | None = None


class EventRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = ""
    ts: float = 0.0
    kind: str = ""  # call_started | step_up_requested | approval | outcome | pii_leak
    code: str | None = None


def engine(path: Path | str | None = None):
    global _engine
    if path is not None:  # tests point this at a temp file
        _engine = create_engine(f"sqlite:///{path}")
        SQLModel.metadata.create_all(_engine)
    if _engine is None:
        _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
    _migrate(_engine)
    return _engine


def _migrate(eng) -> None:
    """create_all does not alter existing tables: add columns introduced after the first release."""
    with eng.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(caserecord)")}
        if "persona" not in cols:
            conn.exec_driver_sql("ALTER TABLE caserecord ADD COLUMN persona TEXT")
        if "channel" not in cols:
            conn.exec_driver_sql("ALTER TABLE caserecord ADD COLUMN channel TEXT DEFAULT 'web'")
        if "known_number" not in cols:
            conn.exec_driver_sql("ALTER TABLE caserecord ADD COLUMN known_number INTEGER")
        if "evidence" not in cols:
            conn.exec_driver_sql("ALTER TABLE caserecord ADD COLUMN evidence TEXT DEFAULT '[]'")


def upsert_case(public: dict, calling_from: str, persona: str | None = None, channel: str = "web",
                known_number: bool | None = None) -> dict:
    """Store the code-only case file. `public` is Case.public(). Returns the stored record as a dict."""
    now = time.time()
    flags = public["flags"]
    with Session(engine()) as s:
        rec = s.get(CaseRecord, public["case_id"]) or CaseRecord(case_id=public["case_id"], created_at=now)
        rec.outcome, rec.risk, rec.verified = public["outcome"], public["risk"], public["verified"]
        rec.language, rec.state, rec.calling_from = public["language"], public["final_state"], calling_from
        rec.persona = persona or rec.persona
        rec.channel, rec.known_number = channel, known_number
        rec.reasons = json.dumps(public["reasons"])
        rec.flags = json.dumps([f for f in flags if not f.startswith("TACTIC_")])
        rec.tactics = json.dumps([f for f in flags if f.startswith("TACTIC_")])
        rec.evidence = json.dumps(public.get("evidence", []))
        rec.updated_at = now
        if public["outcome"] and rec.ended_at is None:
            rec.ended_at = now
        s.add(rec)
        s.commit()
        s.refresh(rec)
        return case_dict(rec)


def add_event(case_id: str, kind: str, code: str | None = None) -> None:
    with Session(engine()) as s:
        s.add(EventRecord(case_id=case_id, ts=time.time(), kind=kind, code=code))
        s.commit()


def case_dict(rec: CaseRecord) -> dict:
    return {"case_id": rec.case_id, "outcome": rec.outcome, "risk": rec.risk, "verified": rec.verified,
            "language": rec.language, "state": rec.state, "calling_from": rec.calling_from, "persona": rec.persona, "channel": rec.channel or "web", "known_number": rec.known_number,
            "reasons": json.loads(rec.reasons), "flags": json.loads(rec.flags), "tactics": json.loads(rec.tactics), "evidence": json.loads(rec.evidence or "[]"),
            "created_at": rec.created_at, "updated_at": rec.updated_at, "ended_at": rec.ended_at}


def list_cases(limit: int = 100) -> list[dict]:
    with Session(engine()) as s:
        rows = s.exec(select(CaseRecord).order_by(CaseRecord.created_at.desc()).limit(limit)).all()
        return [case_dict(r) for r in rows]


def stats() -> dict:
    """Header counters, computed from the stored rows every time. Nothing here is hardcoded."""
    with Session(engine()) as s:
        cases = s.exec(select(CaseRecord)).all()
        leaks = s.exec(select(EventRecord).where(EventRecord.kind == "pii_leak")).all()
    attempts = [c for c in cases if c.risk == "HIGH" or json.loads(c.tactics)]
    return {"calls": len(cases), "impostor_attempts": len(attempts),
            "impostors_verified": sum(1 for c in attempts if c.verified), "pii_leaks": len(leaks)}


def close_stale(idle_seconds: float, now: float | None = None) -> list[str]:
    """Close open cases with no activity for `idle_seconds` that no live session owns (found after a restart).

    Same rule as the live sweeper: a call that was waiting on the member app follows the abandon rule
    (HIGH when the reverse phone had mismatched, else unverified MEDIUM); any other open call is ended safely.
    """
    now = time.time() if now is None else now
    closed = []
    with Session(engine()) as s:
        for rec in s.exec(select(CaseRecord).where(CaseRecord.outcome == None)).all():  # noqa: E711
            if now - rec.updated_at < idle_seconds:
                continue
            reasons = json.loads(rec.reasons)
            if rec.state == "step_up":
                reasons.append("ABANDONED_DURING_STEP_UP")
                mismatch = any(r in reasons for r in ("REVERSE_PHONE_NAME_DIFFERS", "REVERSE_PHONE_NO_RECORD"))
                rec.outcome = "escalated_high" if mismatch else "unverified_handoff"
                rec.risk = "HIGH" if mismatch else "MEDIUM"
            else:
                reasons.append("CALL_ABANDONED")
                rec.outcome = "ended_safe"
            rec.verified, rec.state, rec.reasons = False, "outcome", json.dumps(reasons)
            rec.updated_at = rec.ended_at = now
            s.add(rec)
            s.add(EventRecord(case_id=rec.case_id, ts=now, kind="outcome", code=rec.outcome))
            closed.append(rec.case_id)
        s.commit()
    return closed
