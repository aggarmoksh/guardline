"""Data for the /live big-screen view: masked chat, a checklist of the checks as they run, and a banner.

Everything here is derived from the case file (reason codes, flags, evidence) and a masked in-memory transcript.
Banner words come from reason codes through the tables below, never from a per-demo script. No raw member values are kept.
"""
import re

MASK = "•••"
_DIGIT_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)"


def mask_text(text: str, terms=()) -> str:
    """Mask members.json values and any name the caller stated, digits and runs of spoken digits. Applied before storing."""
    out = text or ""
    for term in sorted({t for t in terms if t and len(t) >= 3}, key=len, reverse=True):
        out = re.sub(re.escape(term), MASK, out, flags=re.I)
    out = re.sub(rf"\b{_DIGIT_WORDS}(?:[\s,.-]+{_DIGIT_WORDS}){{1,}}\b", MASK, out, flags=re.I)
    out = re.sub(r"\d[\d\s,.-]*\d|\d", MASK, out)
    return out.replace("\n", " ")


# -- banner ------------------------------------------------------------------------------------------------
# (reason code, headline rank, headline, detail). The lowest rank present wins the headline; every detail present is listed.
_SIGNALS = [
    ("CRS_IDENTITY_MISMATCH_OR_SYNTHETIC", 1, "SYNTHETIC IDENTITY", "flagged by TransUnion"),
    ("CRS_DECEASED_INDICATOR", 1, "DECEASED INDICATOR", "flagged by TransUnion"),
    ("MEMBER_DENIED_CALL", 2, "IMPOSTOR DETECTED", "member denied"),
    ("REDIRECT_TO_NEW_NUMBER", 3, "IMPOSTOR SIGNAL", "asked to send the approval to a new number"),
    ("CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS", 4, "HIGH RISK REQUEST", "contact change together with money or credentials"),
    ("CRS_NO_FILE", 5, "NO FILE", "TransUnion has no file for this member"),
    ("IDENTITY_NOT_CONFIRMED", 6, "NOT CONFIRMED", "details did not match after a retry"),
    ("ABANDONED_DURING_STEP_UP", 7, "NO APPROVAL", "caller left while the approval was pending"),
    ("APP_APPROVAL_TIMEOUT", 7, "NO APPROVAL", "member did not answer in the app"),
    ("CALLER_REQUESTED_HUMAN", 8, "HANDED TO A PERSON", "caller asked for a person"),
]
_PHONE = {"REVERSE_PHONE_NAME_DIFFERS": "number belongs to someone else", "REVERSE_PHONE_NO_RECORD": "number has no record"}
_EXTRA = {"CRS_FRAUD_ALERT": "fraud alert on file"}


def banner(rec: dict) -> dict:
    """{severity: progress|ok|warn|alert|neutral, headline, detail, note} from the case's reason codes and outcome."""
    outcome, reasons = rec.get("outcome"), set(rec.get("reasons", []))
    if not outcome:
        return {"severity": "progress", "headline": "Call in progress", "detail": "", "note": ""}
    if rec.get("verified") and outcome == "verified_handoff":
        return {"severity": "ok", "headline": "Verified", "detail": "handed to a person", "note": ""}
    hits = [s for s in _SIGNALS if s[0] in reasons]
    phone = [text for code, text in _PHONE.items() if code in reasons]
    extra = [text for code, text in _EXTRA.items() if code in reasons]
    denied_with_phone = "MEMBER_DENIED_CALL" in reasons and phone
    if hits:
        headline = min(hits, key=lambda s: s[1])[2]
        if "MEMBER_DENIED_CALL" in reasons and not phone and headline == "IMPOSTOR DETECTED":
            headline = "MEMBER DENIED"  # the member says it was not them, but nothing else points at an impostor
        details = []
        if headline == "IMPOSTOR DETECTED":
            details += phone
        details += [s[3] for s in hits if s[3] not in details]
        if headline != "IMPOSTOR DETECTED":
            details += [t for t in phone if t not in details]
        details += extra
        if denied_with_phone:
            details = phone + [d for d in details if d not in phone]
    else:
        headline = {"escalated_high": "HIGH RISK", "unverified_handoff": "NOT VERIFIED", "ended_safe": "CALL ENDED"}.get(outcome, outcome.upper())
        details = phone + extra
    severity = {"escalated_high": "alert", "unverified_handoff": "warn"}.get(outcome, "neutral")
    note = "Caller was told: a team member will call back at the number on file" if "CALLBACK_QUEUED" in rec.get("flags", []) else ""
    return {"severity": severity, "headline": headline, "detail": ", ".join(details), "note": note}


# -- checklist ---------------------------------------------------------------------------------------------
_TACTICS = {"URGENCY": "Urgency", "AUTHORITY_CLAIM": "Authority claim", "INSTRUCTION_INJECTION": "Instruction injection",
            "SECRECY_REQUEST": "Secrecy request", "REDIRECT_REQUEST": "Redirect request"}
_BAD_IDENTITY = ("synthetic", "Deceased", "No file")


def _evidence(rec: dict, product: str) -> str | None:
    return next((e["result"] for e in rec.get("evidence", []) if e["product"].startswith(product)), None)


def checklist(rec: dict) -> list[dict]:
    """The checks, each {key, label, status: pending|ok|warn|bad|na, text}. They light up as the case file fills in."""
    reasons, state, ended = set(rec.get("reasons", [])), rec.get("state", ""), bool(rec.get("outcome"))
    ev_tu, ev_rev = _evidence(rec, "TransUnion"), _evidence(rec, "AccurateAppend")

    if "IDENTITY_NOT_CONFIRMED" in reasons:
        details = ("bad", "Details did not match")
    elif ev_tu is not None or ev_rev is not None:
        details = ("ok", "Details match" + (" (name approximately, digits exact)" if "NAME_FUZZY_MATCH" in reasons else ""))
    elif "IDENTITY_REENTRY_REQUESTED" in reasons:
        details = ("warn", "Did not match, asked to try again")
    else:
        details = ("na", "Not checked") if ended else ("pending", "Waiting for the caller's details")

    def crs(text, bad_words=(), warn_words=()):
        if text is None:
            return ("na", "Not run") if ended else ("pending", "Waiting")
        if any(w in text for w in bad_words):
            return ("bad", text)
        if any(w in text for w in warn_words):
            return ("warn", text)
        return ("ok", text)

    identity = crs(ev_tu, bad_words=_BAD_IDENTITY, warn_words=("Fraud alert",))
    reverse = crs(ev_rev, bad_words=("different person", "No record"))

    if "APP_APPROVED_SIMULATED" in reasons:
        approval = ("ok", "Approved in the member app")
    elif "MEMBER_DENIED_CALL" in reasons:
        approval = ("bad", "Denied in the member app")
    elif reasons & {"APP_APPROVAL_TIMEOUT", "ABANDONED_DURING_STEP_UP"}:
        approval = ("warn", "No answer from the member")
    elif state == "step_up":
        approval = ("pending", "Waiting for the member to answer")
    elif "STEP_UP_REQUIRED" in reasons and not ended:
        approval = ("pending", "Requested")
    else:
        approval = ("na", "Not needed") if ended else ("pending", "Not requested yet")

    tactics = [_TACTICS.get(t.replace("TACTIC_", ""), t.replace("TACTIC_", "").replace("_", " ").title()) for t in rec.get("tactics", [])]
    return [
        {"key": "details", "label": "Details match", "status": details[0], "text": details[1]},
        {"key": "identity", "label": "CRS TransUnion identity", "status": identity[0], "text": identity[1]},
        {"key": "reverse", "label": "CRS reverse phone: who owns this number", "status": reverse[0], "text": reverse[1]},
        {"key": "approval", "label": "Member app approval", "status": approval[0], "text": approval[1]},
        {"key": "tactics", "label": "Tactics detected", "status": "warn" if tactics else ("na" if ended else "pending"),
         "text": ", ".join(tactics) if tactics else ("None detected" if ended else "Listening"), "chips": tactics},
    ]


def from_public(pub: dict, calling_from: str, persona: str | None, channel: str, known_number: bool | None) -> dict:
    """The same shape the database returns, built from the in-memory case without writing anything."""
    flags = pub["flags"]
    return {"case_id": pub["case_id"], "outcome": pub["outcome"], "risk": pub["risk"], "verified": pub["verified"],
            "language": pub["language"], "state": pub["final_state"], "calling_from": calling_from, "persona": persona,
            "channel": channel, "known_number": known_number, "reasons": pub["reasons"],
            "flags": [f for f in flags if not f.startswith("TACTIC_")], "tactics": [f for f in flags if f.startswith("TACTIC_")],
            "evidence": pub.get("evidence", [])}
