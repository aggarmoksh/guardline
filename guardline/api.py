"""FastAPI backend for the phone page, the member app and the dashboard.

Run: python -m uvicorn guardline.api:app --port 8000
Cases and events persist to SQLite (code-only). Server-sent events push approval requests, approval results and
case updates; the approval resolving advances the call without the caller saying anything.
"""
import base64
import csv
import hmac
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from guardline import db, interpreter, speaker
from guardline.broker import Broker
from guardline.facts import LiveFacts
from guardline.llm import LLM, LLMError
from guardline.members import load_members
from guardline.policy import Policy
from guardline.redact import get_logger, register_sensitive
from guardline.speech import Speech, SpeechError, get_speech

load_dotenv()
log = get_logger("guardline.api")
LATENCY_CSV = Path(__file__).resolve().parents[1] / "logs" / "latency.csv"
LATENCY_FIELDS = ["ts", "kind", "provider", "language", "stt_ms", "llm_ms", "tts_ms", "total_ms"]
APPROVAL_SECONDS = int(os.environ.get("GUARDLINE_APPROVAL_SECONDS", "60"))
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"}

app = FastAPI(title="GuardLine")
broker = Broker()
_lock = threading.Lock()
_llm: LLM | None = None
_speech: Speech | None = None
_leak_values: list[str] | None = None


@dataclass
class Session:
    policy: Policy
    calling_from: str
    approval: str | None = None  # None until the simulated member decides (or the 60 s timer fires)
    lock: threading.Lock = field(default_factory=threading.Lock)
    announced: bool = False
    outcome_logged: bool = False
    requested_at: float = 0.0
    timer: threading.Timer | None = None
    advance_result: dict | None = None


_sessions: dict[str, Session] = {}


def llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm


def speech() -> Speech:
    global _speech
    if _speech is None:
        _speech = get_speech()
    return _speech


@app.on_event("startup")
def _startup() -> None:
    db.engine()
    threading.Thread(target=speaker.prewarm, args=(llm(),), daemon=True).start()


def _log_latency(row: dict) -> None:
    LATENCY_CSV.parent.mkdir(exist_ok=True)
    new = not LATENCY_CSV.exists()
    with LATENCY_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, LATENCY_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def _leaks_member_value(text: str) -> bool:
    global _leak_values
    if _leak_values is None:
        _leak_values = [v.casefold() for m in load_members().values() for v in m.sensitive_values() if len(v) >= 3]
    low = text.casefold()
    return any(v in low for v in _leak_values)


def _speak(sess: Session, message_id: str) -> tuple[str, str | None, float, float]:
    """Returns (agent text, audio b64 or None, llm_ms, tts_ms). A leak guard checks every agent line."""
    lang = sess.policy.case.language
    t0 = time.perf_counter()
    text = speaker.render(llm(), message_id, lang)
    llm_ms = (time.perf_counter() - t0) * 1000
    if _leaks_member_value(text):  # should never happen: message ids are fixed sentences
        db.add_event(sess.policy.case.case_id, "pii_leak", message_id)
        text = speaker.TEMPLATES[message_id]
    t0 = time.perf_counter()
    try:
        audio = base64.b64encode(speech().synthesize(text, lang)).decode()
    except SpeechError as e:
        log.warning("tts failed: %s", e)
        audio = None
    return text, audio, llm_ms, (time.perf_counter() - t0) * 1000


def _public_state(sess: Session) -> dict:
    c = sess.policy.case
    return {"state": c.state, "outcome": c.outcome, "language": c.language,
            "awaiting_approval": c.state == "step_up" and sess.approval is None,
            "expect": sess.policy.expected_field(), "case_id": c.case_id}


def _approval_request(sess: Session) -> dict:
    c = sess.policy.case
    return {"case_id": c.case_id, "language": c.language, "calling_from": sess.calling_from,
            "requested_at": sess.requested_at, "deadline": sess.requested_at + APPROVAL_SECONDS}


def _after_change(sess: Session) -> None:
    """Persist the case file, announce a new approval request, close out an ended call, notify the dashboard."""
    c = sess.policy.case
    rec = db.upsert_case(c.public(), sess.calling_from)
    if c.state == "step_up" and not sess.announced and sess.approval is None:
        sess.announced = True
        sess.requested_at = time.time()
        db.add_event(c.case_id, "step_up_requested")
        broker.publish("public", {"type": "approval_requested", **_approval_request(sess)})
        sess.timer = threading.Timer(APPROVAL_SECONDS, _expire, args=(sess,))
        sess.timer.daemon = True
        sess.timer.start()
    if c.outcome and not sess.outcome_logged:
        sess.outcome_logged = True
        db.add_event(c.case_id, "outcome", c.outcome)
        if sess.timer:
            sess.timer.cancel()
        if sess.approval is None:  # the call ended while the request was still showing
            broker.publish("public", {"type": "approval_cancelled", "case_id": c.case_id})
    broker.publish("cases", {"type": "case_updated", "case": rec, "stats": db.stats()})


def _resolve(sess: Session, decision: str) -> bool:
    """The member app answered (approved | denied) or the 60 s timer fired (timeout). Advance the call."""
    c = sess.policy.case
    with sess.lock:
        if sess.approval is not None or c.state != "step_up":
            return False
        sess.approval = decision
        if sess.timer:
            sess.timer.cancel()
        db.add_event(c.case_id, "approval", decision)
        broker.publish("public", {"type": "approval_resolved", "case_id": c.case_id, "decision": decision})
        t = sess.policy.advance_after_approval()
        if t:
            text, audio, _, _ = _speak(sess, t.message_id)
            sess.advance_result = {"agent_text": text, "agent_audio": audio, "audio_mime": "audio/wav",
                                   "done": t.done, **_public_state(sess)}
        _after_change(sess)
    broker.publish("public", {"type": "turn_ready", "case_id": c.case_id})
    return True


def _expire(sess: Session) -> None:
    _resolve(sess, "timeout")


class StartRequest(BaseModel):
    persona: str = "P01"
    calling_from: str = "member"
    language: str = "en"


@app.post("/api/call/start")
def start_call(req: StartRequest):
    if req.calling_from not in ("member", "other") or req.language not in ("en", "es"):
        raise HTTPException(400, "bad parameters")
    members = load_members()
    if req.persona not in members:
        raise HTTPException(400, "unknown persona")
    member = members[req.persona]
    register_sensitive(member.sensitive_values())
    holder: dict[str, Session] = {}

    def approval(_case_id: str) -> str:
        return holder["s"].approval or "timeout"  # nobody answered the simulated app

    policy = Policy(member, LiveFacts(req.persona, member.name), req.calling_from, approval, req.language,
                    keypad_mode=True, async_approval=True)
    sess = holder["s"] = Session(policy, req.calling_from)
    turn = policy.start()
    text, audio, _, _ = _speak(sess, turn.message_id)
    with _lock:
        _sessions[policy.case.case_id] = sess
    db.add_event(policy.case.case_id, "call_started")
    _after_change(sess)
    return {"agent_text": text, "agent_audio": audio, "audio_mime": "audio/wav", **_public_state(sess)}


@app.post("/api/turn")
def turn(case_id: str = Form(...), language: str = Form("en"), audio: UploadFile = File(...)):
    sess = _sessions.get(case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    policy = sess.policy
    t_total = time.perf_counter()
    wav = audio.file.read()
    with sess.lock:
        if language in ("en", "es"):
            policy.case.language = language
        lang = policy.case.language

        t0 = time.perf_counter()
        try:
            transcript = speech().transcribe(wav, lang)
            stt_failed = False
        except SpeechError as e:
            log.warning("stt failed: %s", e)
            transcript, stt_failed = "", True
        stt_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        if not transcript:
            t = policy.repeat_needed()
        else:
            try:
                it = interpreter.interpret(llm(), transcript, policy.case.state)
                t = policy.repeat_needed() if it is None else policy.handle(it)
            except LLMError:
                t = policy.llm_unavailable()
        llm_ms = (time.perf_counter() - t0) * 1000

        text, audio_b64, speak_llm_ms, tts_ms = _speak(sess, t.message_id)
        _after_change(sess)
        state = _public_state(sess)
    llm_ms += speak_llm_ms
    total_ms = (time.perf_counter() - t_total) * 1000
    _log_latency({"ts": int(time.time()), "kind": "voice", "provider": speech().name, "language": lang,
                  "stt_ms": round(stt_ms), "llm_ms": round(llm_ms), "tts_ms": round(tts_ms), "total_ms": round(total_ms)})
    return {"transcript": transcript, "stt_failed": stt_failed, "agent_text": text, "agent_audio": audio_b64,
            "audio_mime": "audio/wav", "done": t.done, **state,
            "timings_ms": {"stt": round(stt_ms), "llm": round(llm_ms), "tts": round(tts_ms), "total": round(total_ms)}}


class Entry(BaseModel):
    case_id: str
    field: str  # dob (MMDDYYYY) | zip | ssn_last4
    value: str


@app.post("/api/entry")
def keypad_entry(body: Entry):
    """Typed identity detail: straight to the policy. Not transcribed, not sent to the LLM, never logged."""
    sess = _sessions.get(body.case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    t_total = time.perf_counter()
    with sess.lock:
        t = sess.policy.provide_typed(body.field, body.value)
        text, audio_b64, llm_ms, tts_ms = _speak(sess, t.message_id)
        _after_change(sess)
        state = _public_state(sess)
    total_ms = (time.perf_counter() - t_total) * 1000
    _log_latency({"ts": int(time.time()), "kind": "keypad", "provider": speech().name,
                  "language": sess.policy.case.language, "stt_ms": 0, "llm_ms": round(llm_ms),
                  "tts_ms": round(tts_ms), "total_ms": round(total_ms)})
    return {"agent_text": text, "agent_audio": audio_b64, "audio_mime": "audio/wav", "done": t.done, **state}


@app.get("/api/call/{case_id}/result")
def call_result(case_id: str):
    """The agent's reply to an approval that resolved on its own (read once)."""
    sess = _sessions.get(case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    result, sess.advance_result = sess.advance_result, None
    return result or {"ready": False}


class ApprovalDecision(BaseModel):
    case_id: str
    decision: str  # approved | denied


@app.get("/api/approval")
def pending_approval():
    """Simulated member banking app: the newest call that is waiting for an approval."""
    with _lock:
        for sess in reversed(list(_sessions.values())):
            if sess.policy.case.state == "step_up" and sess.approval is None and sess.announced:
                return {"pending": True, **_approval_request(sess), "now": time.time()}
    return {"pending": False, "now": time.time()}


@app.post("/api/approval")
def decide_approval(body: ApprovalDecision):
    sess = _sessions.get(body.case_id)
    if not sess or body.decision not in ("approved", "denied"):
        raise HTTPException(400, "bad request")
    return {"ok": _resolve(sess, body.decision)}


@app.get("/api/events")
def public_events():
    """Server-sent events for the phone page and the member app: approval requested, resolved, cancelled, turn ready."""
    return StreamingResponse(broker.subscribe("public"), media_type="text/event-stream", headers=SSE_HEADERS)


def _require_key(header_key: str | None, query_key: str | None) -> None:
    expected = os.environ.get("DEMO_KEY", "")
    given = header_key or query_key or ""
    if not expected or not hmac.compare_digest(given, expected):
        raise HTTPException(401, "demo key required")


@app.get("/api/cases")
def cases(key: str | None = Query(None), x_demo_key: str | None = Header(None)):
    _require_key(x_demo_key, key)
    return {"cases": db.list_cases(), "stats": db.stats()}


@app.get("/api/cases/stream")
def cases_stream(key: str | None = Query(None), x_demo_key: str | None = Header(None)):
    _require_key(x_demo_key, key)
    return StreamingResponse(broker.subscribe("cases"), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/case/{case_id}")
def case_file(case_id: str, key: str | None = Query(None), x_demo_key: str | None = Header(None)):
    """Case file for the human: codes and enums only, no member data."""
    _require_key(x_demo_key, key)
    sess = _sessions.get(case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    return sess.policy.case.public()


@app.get("/api/health")
def health():
    return {"ok": True}
