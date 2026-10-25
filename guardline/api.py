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

from guardline import db, interpreter, line, live, phrasing, speaker
from guardline.broker import Broker
from guardline.facts import DeferredFacts, LiveFacts
from guardline.llm import LLM, LLMError
from guardline.members import Member, load_members
from guardline.policy import CONTACT_CHANGES, MONEY_OR_CREDENTIALS, Policy
from guardline.redact import get_logger, redact, register_sensitive
from guardline.speech import Speech, SpeechError, get_speech

load_dotenv()
log = get_logger("guardline.api")
LATENCY_CSV = Path(__file__).resolve().parents[1] / "logs" / "latency.csv"
LATENCY_FIELDS = ["ts", "kind", "provider", "language", "stt_ms", "llm_ms", "tts_ms", "total_ms"]
IDLE_SECONDS = int(os.environ.get("GUARDLINE_IDLE_SECONDS", "180"))
HOLD_SECONDS = float(os.environ.get("GUARDLINE_HOLD_SECONDS", "12"))  # one held tool call; the agent calls again
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
    persona: str | None = None
    last_active: float = field(default_factory=time.time)
    approval: str | None = None  # None until the simulated member decides (or the 60 s timer fires)
    lock: threading.Lock = field(default_factory=threading.Lock)
    announced: bool = False
    outcome_logged: bool = False
    requested_at: float = 0.0
    timer: threading.Timer | None = None
    advance_result: dict | None = None
    channel: str = "web"  # web | line
    known_number: bool | None = None
    line: "line.LineState | None" = None
    resolved: threading.Event = field(default_factory=threading.Event)  # set when the approval is answered or expires
    started_at: float = field(default_factory=time.time)
    transcript: list = field(default_factory=list)  # masked at insertion, in memory only, for the /live view


_line_sessions: dict[str, Session] = {}
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


def _sweep_once(idle_seconds: float = IDLE_SECONDS, now: float | None = None) -> list[str]:
    """Close calls with no activity for `idle_seconds`. A pending approval follows the abandon rule."""
    now = time.time() if now is None else now
    closed = []
    for sess in list(_sessions.values()):
        if sess.policy.case.outcome or now - sess.last_active < idle_seconds:
            continue
        with sess.lock:
            if sess.timer:
                sess.timer.cancel()
            if sess.policy.idle_timeout():
                sess.approval = sess.approval or "timeout"
                _after_change(sess)
                closed.append(sess.policy.case.case_id)
    return closed


def _sweeper() -> None:
    while True:
        time.sleep(10)
        try:
            _sweep_once()
        except Exception as e:  # never let the sweeper die
            log.warning("sweep failed: %s", type(e).__name__)


@app.on_event("startup")
def _startup() -> None:
    db.engine()
    db.close_stale(IDLE_SECONDS)  # open cases left over from a previous run
    threading.Thread(target=speaker.prewarm, args=(llm(),), daemon=True).start()
    threading.Thread(target=_sweeper, daemon=True).start()


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


NATURAL_WEB = os.environ.get("NATURAL_REPLIES", "1") != "0"


def _callback_variant(sess: "Session", message_id: str) -> str:
    """Urgency or an emergency is acknowledged, and the callback is called the fastest path. Same decision, other words."""
    if message_id == "escalate" and "TACTIC_URGENCY" in sess.policy.case.flags:
        return "escalate_urgent"
    return message_id


def _mask_terms(sess: Session) -> list[str]:
    """Everything that must never reach the big screen: all members.json values and the name this caller stated."""
    _leaks_member_value("")  # makes sure the value list is loaded
    terms = list(_leak_values or [])
    stated = sess.policy.case.stated
    return terms + [stated[k] for k in ("name", "dob", "zip", "ssn_last4") if stated.get(k)]


def _note(sess: Session, who: str, text: str | None) -> None:
    """Add a bubble to the live view. Masked here, before it is stored; never persisted."""
    if not text:
        return
    sess.transcript.append({"who": who, "text": live.mask_text(text, _mask_terms(sess)), "ts": time.time()})
    del sess.transcript[:-60]


def _speak(sess: Session, message_id: str, audio: bool = True, utterance: str | None = None) -> tuple[str, str | None, float, float]:
    """Returns (agent text, audio b64 or None, llm_ms, tts_ms). A leak guard checks every agent line.

    On the web the wording is natural (a model words the policy's intent and must_ask); a code guard falls back to the
    fixed sentence and counts GUARD_REJECTED. The decision itself never comes from the model.
    """
    lang = sess.policy.case.language
    message_id = _callback_variant(sess, message_id)
    t0 = time.perf_counter()
    text = fixed = speaker.render(llm(), message_id, lang)
    if NATURAL_WEB and audio and message_id in phrasing.INTENTS:
        text, reason = phrasing.natural_reply(llm(), message_id, lang, redact(utterance) if utterance else None, fixed,
                                              verified=sess.policy.case.verified, leaks=_leaks_member_value)
        if reason:
            db.add_event(sess.policy.case.case_id, "guard_rejected", reason)
    llm_ms = (time.perf_counter() - t0) * 1000
    if _leaks_member_value(text):  # should never happen: message ids are fixed sentences
        db.add_event(sess.policy.case.case_id, "pii_leak", message_id)
        text = speaker.TEMPLATES[message_id]
    if not audio:  # the phone line speaks the text itself
        return text, None, llm_ms, 0.0
    t0 = time.perf_counter()
    try:
        audio_b64 = base64.b64encode(speech().synthesize(text, lang)).decode()
    except SpeechError as e:
        log.warning("tts failed: %s", e)
        audio_b64 = None
    return text, audio_b64, llm_ms, (time.perf_counter() - t0) * 1000


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
    rec = db.upsert_case(c.public(), sess.calling_from, sess.persona, sess.channel, sess.known_number)
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
    sess.last_active = time.time()
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
            text, audio, _, _ = _speak(sess, t.message_id, audio=sess.channel == "web")
            sess.advance_result = {"agent_text": text, "agent_audio": audio, "audio_mime": "audio/wav",
                                   "done": t.done, **_public_state(sess)}
            _note(sess, "agent", text)
        _after_change(sess)
    sess.resolved.set()
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
    sess = holder["s"] = Session(policy, req.calling_from, req.persona)
    turn = policy.start()
    text, audio, _, _ = _speak(sess, turn.message_id)
    _note(sess, "agent", text)
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
    sess.last_active = time.time()
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

        text, audio_b64, speak_llm_ms, tts_ms = _speak(sess, t.message_id, utterance=transcript)
        _note(sess, "caller", transcript)
        _note(sess, "agent", text)
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
    sess.last_active = time.time()
    t_total = time.perf_counter()
    with sess.lock:
        t = sess.policy.provide_typed(body.field, body.value)
        text, audio_b64, llm_ms, tts_ms = _speak(sess, t.message_id)
        _note(sess, "caller", "typed on the keypad: " + live.MASK)
        _note(sess, "agent", text)
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


@app.get("/api/live")
def live_view(key: str | None = Query(None), x_demo_key: str | None = Header(None)):
    """The most recent call for the big screen: masked chat, the checks as they run, a banner from the reason codes."""
    _require_key(x_demo_key, key)
    with _lock:
        sessions = list(_sessions.values())
    sess = max(sessions, key=lambda s: (s.policy.case.outcome is None, s.started_at), default=None)
    stats = db.stats()
    if sess is None:
        return {"call": None, "stats": stats}
    rec = live.from_public(sess.policy.case.public(), sess.calling_from, sess.persona, sess.channel, sess.known_number)
    return {"call": {"case_id": rec["case_id"][-8:], "persona": rec["persona"], "channel": rec["channel"],
                     "known_number": rec["known_number"], "language": rec["language"], "outcome": rec["outcome"],
                     "state": rec["state"], "started_at": sess.started_at, "transcript": sess.transcript[-40:],
                     "checks": live.checklist(rec), "banner": live.banner(rec)}, "stats": stats}


@app.get("/api/notice")
def callback_notice():
    """Simulated member app: a case just ended non-verified, so the credit union will call back. Code-only."""
    return db.latest_callback_notice(within_seconds=120)


@app.get("/api/case/{case_id}")
def case_file(case_id: str, key: str | None = Query(None), x_demo_key: str | None = Header(None)):
    """Case file for the human: codes and enums only, no member data."""
    _require_key(x_demo_key, key)
    sess = _sessions.get(case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    return sess.policy.case.public()


class LineProbe(BaseModel):
    probe_token: str = ""
    call_id: str = ""
    caller_number: str | None = None


LINE_PROBE_CSV = Path(__file__).resolve().parents[1] / "logs" / "line_probe.csv"


@app.post("/api/line/probe")
def line_probe(body: LineProbe):
    """Phase 4C probe: a Cartesia Line agent reports the caller number and call id. Only the last 4 digits are kept."""
    expected = os.environ.get("LINE_PROBE_TOKEN", "")
    if not expected or not hmac.compare_digest(body.probe_token, expected):
        raise HTTPException(401, "bad probe token")
    digits = "".join(ch for ch in (body.caller_number or "") if ch.isdigit())
    last4 = digits[-4:] if digits else ""
    log.info("line probe call_id=%s caller_present=%s caller_last4=%s", body.call_id[:12], bool(digits), last4)
    LINE_PROBE_CSV.parent.mkdir(exist_ok=True)
    new = not LINE_PROBE_CSV.exists()
    with LINE_PROBE_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "call_id_prefix", "caller_present", "caller_digits", "caller_last4"])
        w.writerow([int(time.time()), body.call_id[:12], bool(digits), len(digits), last4])
    return {"ok": True, "caller_received": bool(digits)}


# -- Cartesia Line phone channel: the Line agent is a relay, our backend owns state, verification and wording ----------
class LineTurn(BaseModel):
    line_token: str = ""
    conversation_id: str = ""
    caller_id: str | None = None  # filled from system__caller_id; never visible to the Line model
    called_number: str | None = None  # filled from system__called_number: tells us which language number was dialled
    utterance: str = ""


def _digits_only(number: str | None) -> str:
    d = "".join(ch for ch in (number or "") if ch.isdigit())
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def _line_language(called_number: str | None) -> str:
    spanish = _digits_only(os.environ.get("LINE_SPANISH_NUMBER"))
    return "es" if spanish and _digits_only(called_number) == spanish else "en"


def make_facts(persona: str, member: Member, caller_number: str | None):
    """Live CRS facts for a member; tests replace this with fixtures."""
    return LiveFacts(persona, member.name, caller_number)


def _create_line_session(body: LineTurn) -> Session:
    members = load_members()
    for m in members.values():
        register_sensitive(m.sensitive_values())
    language = _line_language(body.called_number)
    caller = _digits_only(body.caller_id)
    known = line.known_member(caller, members)
    holder: dict[str, Session] = {}

    def approval(_case_id: str) -> str:
        return holder["s"].approval or "timeout"

    facts = DeferredFacts()
    member = known or Member("UNKNOWN", "", "", "", "", "", "")  # replaced once the caller says a name
    if known:
        facts.inner = make_facts(known.persona_id, known, None)
    policy = Policy(member, facts, "member" if known else "other", approval, language, keypad_mode=True,
                    async_approval=True)
    sess = holder["s"] = Session(policy, "member" if known else "other", known.persona_id if known else None,
                                 channel="line", known_number=bool(known),
                                 line=line.LineState(language=language, known_number=bool(known), caller_digits=caller))
    sess.line.facts = facts  # type: ignore[attr-defined]
    policy.start()
    with _lock:
        _line_sessions[body.conversation_id] = sess
        _sessions[policy.case.case_id] = sess
    log.info("line call started case=%s language=%s known_number=%s caller_last4=%s", policy.case.case_id[-8:],
             language, bool(known), caller[-4:])
    db.add_event(policy.case.case_id, "call_started")
    _after_change(sess)
    return sess


def _line_reply(sess: Session, message_id: str, fixed: str, *, must_ask: str | None = None, done: bool = False,
                cont: bool = False) -> dict:
    """What the Line agent needs to speak naturally: intent (what to convey), must_ask (the exact question), state, fallback.

    No member data in any field. The policy decided everything in it; the agent only chooses the words."""
    intent, default_ask = phrasing.intent_for(message_id)
    lang = sess.line.language
    ask = must_ask if must_ask is not None else (speaker.translate(llm(), default_ask, lang) if default_ask else None)
    if ask and _leaks_member_value(ask):
        db.add_event(sess.policy.case.case_id, "pii_leak", "line_must_ask")
        ask = fixed = line.PHRASES[lang]["retry"]
    reply = {"intent": intent, "must_ask": ask, "state": sess.policy.case.state,
             "say_fallback": fixed, "end_call": done, "continue": cont}
    if cont:
        reply["next"] = HOLD_INSTRUCTION
    return reply


def _line_say(sess: Session, message_id: str, *, done: bool = False, cont: bool = False) -> dict:
    message_id = _callback_variant(sess, message_id)
    text, _, _, _ = _speak(sess, message_id, audio=False)  # the fixed sentence is the fallback
    return _line_reply(sess, message_id, text, done=done, cont=cont)


HOLD_INSTRUCTION = ("Speak your reply to the caller out loud first. Only after you have finished speaking it, call guardline_turn "
                    "again, with the caller's words if they said something meanwhile, or an empty utterance if they said nothing.")


def _line_text(sess: Session, text: str, message_id: str = "retry") -> dict:
    """A line-specific sentence (digit prompts and read-backs). Spoken digits are spaced, so no on-file value matches."""
    if _leaks_member_value(text):
        db.add_event(sess.policy.case.case_id, "pii_leak", "line_text")
        text = line.PHRASES[sess.line.language]["retry"]
        message_id = "retry"
    return _line_reply(sess, message_id, text, must_ask=text)


def _line_turn_result(sess: Session, t, speak=True) -> dict:
    """Turn the policy's Turn into what the Line agent should say. Digit items are asked by voice, not on a keypad."""
    st, ph = sess.line, line.PHRASES[sess.line.language]
    field_name = sess.policy.expected_field()
    if not t.done and field_name in ("dob", "zip", "ssn_last4"):
        st.collecting, st.pending = field_name, None
        key = {"dob": "ask_dob", "zip": "ask_zip", "ssn_last4": "ask_ssn4"}[field_name]
        return _line_text(sess, ph[key], key)
    st.collecting = st.pending = None
    waiting = sess.policy.case.state == "step_up" and sess.approval is None
    return _line_say(sess, t.message_id, done=t.done, cont=waiting)


def _line_hear(sess: Session, value: str) -> dict:
    """Read the caller's own digits back, spaced out, and wait for a yes."""
    st, ph = sess.line, line.PHRASES[sess.line.language]
    st.pending = value
    return _line_text(sess, ph["heard"].format(digits=line.read_back(value)), "heard")


def _line_confirm(sess: Session, answer: bool) -> dict:
    st, ph = sess.line, line.PHRASES[sess.line.language]
    if not answer:
        st.pending = None
        ask = ph[{"dob": "ask_dob", "zip": "ask_zip", "ssn_last4": "ask_ssn4"}[st.collecting]]
        return _line_text(sess, ask, "digit_wrong")  # just the question again, not "I did not catch that"
    t = sess.policy.provide_typed(st.collecting, st.pending)
    _after_change(sess)
    return _line_turn_result(sess, t)


def _line_wait_for_approval(sess: Session) -> dict:
    """Hold the turn for a short while (HOLD_SECONDS) until the member app answers; never holds sess.lock while waiting.

    A long silent hold left callers talking to nobody, and anything they said (even "send it to a new number") was not
    heard until it ended. So the hold is short: if there is no answer yet, reassure briefly and let the agent call again,
    this time with whatever the caller said. The 60 s approval timer still decides the outcome."""
    sess.resolved.wait(timeout=HOLD_SECONDS)
    with sess.lock:
        result, sess.advance_result = sess.advance_result, None
        if result:
            mid = "handoff" if sess.policy.case.verified else _callback_variant(sess, "escalate")
            return _line_reply(sess, mid, result["agent_text"], done=bool(result["done"]))
        if sess.resolved.is_set() or sess.policy.case.outcome:  # answered and already collected
            return _line_say(sess, "call_over", done=True)
        sess.line.waits += 1
        if sess.line.waits > int((APPROVAL_SECONDS + 15) / max(HOLD_SECONDS, 1)) + 2:  # something is wrong: stop waiting
            t = sess.policy.idle_timeout()
            _after_change(sess)
            return _line_say(sess, t.message_id if t else "escalate", done=True)
        return _line_say(sess, "still_waiting", cont=True)


def _line_turn(body: LineTurn) -> dict:
    if not body.conversation_id:
        raise HTTPException(400, "conversation_id required")
    sess = _line_sessions.get(body.conversation_id) or _create_line_session(body)
    sess.last_active = time.time()
    utterance = (body.utterance or "").strip()
    st, policy = sess.line, sess.policy
    if not utterance and (sess.advance_result or (policy.case.state == "step_up" and sess.approval is None)):
        return _line_wait_for_approval(sess)  # the held turn: the answer to the approval, or wait for it
    if policy.case.outcome:
        return _line_say(sess, "call_over", done=True)
    ph = line.PHRASES[st.language]
    with sess.lock:
        if st.last_say and line.is_repeat_request(utterance):  # "can you repeat that?" is not a request for a human
            return st.last_say
        # digits and yes/no are handled in code, without the language model (saves about a second a turn)
        if st.collecting and st.pending is not None and line.pure_yes_no(utterance):
            sess.line.used_llm = False
            return _line_confirm(sess, bool(line.yes_no(utterance)))
        if st.collecting and st.pending is None and line.pure_digits(utterance):
            sess.line.used_llm = False
            value = line.item_digits(st.collecting, utterance)
            if value:
                return _line_hear(sess, value)
            return _line_text(sess, ph["retry"] + " " + ph[{"dob": "ask_dob", "zip": "ask_zip", "ssn_last4": "ask_ssn4"}[st.collecting]])
        sess.line.used_llm = True
        try:
            it = interpreter.interpret(llm(), utterance, policy.case.state) if utterance else None
        except LLMError:
            t = policy.llm_unavailable()
            _after_change(sess)
            return _line_say(sess, t.message_id, done=True)
        if it is None:  # empty or invalid model output
            if not utterance:
                return _line_text(sess, ph["retry"])
            t = policy.repeat_needed()
            _after_change(sess)
            return _line_turn_result(sess, t)
        # an unknown number: the member is chosen from the stated name among the demo members
        if sess.persona is None and it.stated_name:
            member = line.best_candidate(it.stated_name, load_members())
            if member:
                sess.persona = member.persona_id
                policy.member = member
                sess.line.facts.inner = make_facts(member.persona_id, member, st.caller_digits or None)
                register_sensitive(member.sensitive_values())

        # digits asked one item at a time and confirmed by reading back only what the caller just said
        real_request = bool(set(it.request_types) & (CONTACT_CHANGES | MONEY_OR_CREDENTIALS))  # "other" is model noise on a plain "yes"
        interrupt = it.intent in ("goodbye", "ask_human") or it.redirect_to_new_number or real_request
        if st.collecting and not interrupt:
            if st.pending is not None:
                answer = line.yes_no(utterance)
                if answer is None:
                    return _line_text(sess, ph["yes_no"], "yes_no")
                return _line_confirm(sess, answer)
            value = line.item_digits(st.collecting, utterance, it.stated_dob)
            if value is None:
                return _line_text(sess, ph["retry"] + " " + ph[{"dob": "ask_dob", "zip": "ask_zip", "ssn_last4": "ask_ssn4"}[st.collecting]])
            return _line_hear(sess, value)
        t = policy.handle(it)
        _after_change(sess)
        return _line_turn_result(sess, t)


LINE_LATENCY_CSV = Path(__file__).resolve().parents[1] / "logs" / "line_latency.csv"


def _log_line_latency(ms: int, used_llm: bool) -> None:
    """Backend time per phone-line turn (no text). Line's own turn detection and speech are not in this number."""
    LINE_LATENCY_CSV.parent.mkdir(exist_ok=True)
    new = not LINE_LATENCY_CSV.exists()
    with LINE_LATENCY_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "backend_ms", "used_llm"])
        w.writerow([int(time.time()), ms, used_llm])


@app.post("/api/line/turn")
def line_turn(body: LineTurn):
    expected = os.environ.get("LINE_PROBE_TOKEN", "")
    if not expected or not hmac.compare_digest(body.line_token, expected):
        raise HTTPException(401, "bad token")
    t0 = time.perf_counter()
    try:
        result = _line_turn(body)
        sess = _line_sessions.get(body.conversation_id)
        if sess:
            sess.line.last_say = result
            _note(sess, "caller", body.utterance)
            _note(sess, "agent", result.get("say_fallback"))  # the Line model words it; we show our fixed sentence
            _log_line_latency(round((time.perf_counter() - t0) * 1000), sess.line.used_llm)
        return result
    except HTTPException:
        raise
    except Exception as e:  # never leave the caller in silence: our own words, then a human
        log.warning("line turn failed: %s", type(e).__name__)
        fixed = line.PHRASES[_line_language(body.called_number)]["fallback"]
        return {"intent": phrasing.intent_for("fallback")[0], "must_ask": None, "state": "outcome",
                "say_fallback": fixed, "end_call": True, "continue": False}


@app.get("/api/health")
def health():
    return {"ok": True}
