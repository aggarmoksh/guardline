"""FastAPI backend for the phone page. Run: python -m uvicorn guardline.api:app --port 8000"""
import base64
import csv
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from guardline import interpreter, speaker
from guardline.facts import LiveFacts
from guardline.llm import LLM, LLMError
from guardline.members import load_members
from guardline.policy import Policy
from guardline.redact import get_logger, register_sensitive
from guardline.speech import Speech, SpeechError, get_speech

log = get_logger("guardline.api")
LATENCY_CSV = Path(__file__).resolve().parents[1] / "logs" / "latency.csv"
LATENCY_FIELDS = ["ts", "kind", "provider", "language", "stt_ms", "llm_ms", "tts_ms", "total_ms"]

app = FastAPI(title="GuardLine")
_lock = threading.Lock()
_llm: LLM | None = None
_speech: Speech | None = None


@app.on_event("startup")
def _prewarm() -> None:
    threading.Thread(target=speaker.prewarm, args=(llm(),), daemon=True).start()


@dataclass
class Session:
    policy: Policy
    approval: str | None = None  # None until the simulated member decides
    history: list[dict] = field(default_factory=list)


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


def _log_latency(row: dict) -> None:
    LATENCY_CSV.parent.mkdir(exist_ok=True)
    new = not LATENCY_CSV.exists()
    with LATENCY_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, LATENCY_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def _speak(sess: Session, message_id: str) -> tuple[str, str | None, float, float]:
    """Returns (agent text, audio b64 or None, llm_ms, tts_ms)."""
    lang = sess.policy.case.language
    t0 = time.perf_counter()
    text = speaker.render(llm(), message_id, lang)
    llm_ms = (time.perf_counter() - t0) * 1000
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
            "expect": sess.policy.expected_field()}


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
        decided = holder["s"].approval
        return decided or "timeout"  # nobody answered the simulated app

    policy = Policy(member, LiveFacts(req.persona, member.name), req.calling_from, approval, req.language,
                    keypad_mode=True)
    sess = holder["s"] = Session(policy)
    turn = policy.start()
    text, audio, _, _ = _speak(sess, turn.message_id)
    with _lock:
        _sessions[policy.case.case_id] = sess
    return {"case_id": policy.case.case_id, "agent_text": text, "agent_audio": audio,
            "audio_mime": "audio/wav", **_public_state(sess)}


@app.post("/api/turn")
def turn(case_id: str = Form(...), language: str = Form("en"), audio: UploadFile = File(...)):
    sess = _sessions.get(case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    policy = sess.policy
    if language in ("en", "es"):
        policy.case.language = language
    lang = policy.case.language
    t_total = time.perf_counter()
    wav = audio.file.read()

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
    llm_ms += speak_llm_ms
    total_ms = (time.perf_counter() - t_total) * 1000
    _log_latency({"ts": int(time.time()), "kind": "voice", "provider": speech().name, "language": lang,
                  "stt_ms": round(stt_ms), "llm_ms": round(llm_ms), "tts_ms": round(tts_ms), "total_ms": round(total_ms)})
    return {"transcript": transcript, "stt_failed": stt_failed, "agent_text": text, "agent_audio": audio_b64,
            "audio_mime": "audio/wav", "done": t.done, **_public_state(sess),
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
    t = sess.policy.provide_typed(body.field, body.value)
    text, audio_b64, llm_ms, tts_ms = _speak(sess, t.message_id)
    total_ms = (time.perf_counter() - t_total) * 1000
    _log_latency({"ts": int(time.time()), "kind": "keypad", "provider": speech().name, "language": sess.policy.case.language,
                  "stt_ms": 0, "llm_ms": round(llm_ms), "tts_ms": round(tts_ms), "total_ms": round(total_ms)})
    return {"agent_text": text, "agent_audio": audio_b64, "audio_mime": "audio/wav", "done": t.done,
            **_public_state(sess)}


class ApprovalDecision(BaseModel):
    case_id: str
    decision: str  # approved | denied


@app.get("/api/approval")
def pending_approval():
    """Simulated member banking app: the newest call that is waiting for an approval."""
    with _lock:
        for case_id, sess in reversed(list(_sessions.items())):
            if _public_state(sess)["awaiting_approval"]:
                return {"pending": True, "case_id": case_id}
    return {"pending": False}


@app.post("/api/approval")
def decide_approval(body: ApprovalDecision):
    sess = _sessions.get(body.case_id)
    if not sess or body.decision not in ("approved", "denied"):
        raise HTTPException(400, "bad request")
    sess.approval = body.decision
    return {"ok": True}


@app.get("/api/case/{case_id}")
def case_file(case_id: str):
    """Case file for the human: codes and enums only, no member data."""
    sess = _sessions.get(case_id)
    if not sess:
        raise HTTPException(404, "unknown case")
    return sess.policy.case.public()


@app.get("/api/health")
def health():
    return {"ok": True}
