"""Deterministic policy engine. Only this code can mark a caller verified.

The model can make a call safer, never less safe: the interpreter supplies enums and
caller-stated slots, this module decides.
"""
import secrets
from dataclasses import dataclass, field, replace
from typing import Callable

from guardline.facts import Facts
from guardline.members import Member

STATES = ("greet", "collect_identity", "crs_checks", "step_up", "request", "outcome")
OUTCOMES = ("verified_handoff", "unverified_handoff", "escalated_high", "ended_safe")

CONTACT_CHANGES = frozenset({"change_mailing_address", "change_email", "change_phone"})
MONEY_OR_CREDENTIALS = frozenset({"move_money", "reset_credentials"})
MISMATCH_OR_SYNTHETIC = frozenset({2201, 2202, 2101})
DECEASED = 2301
FRAUD_ALERT = 2401
MAX_UNCLEAR = 3


@dataclass(frozen=True)
class Signals:
    stated_match: bool | None = None
    verification_status: str | None = None
    risk_indicator: str | None = None
    alert_codes: tuple[int, ...] = ()
    calling_record_found: bool | None = None
    calling_name_matches: bool | None = None
    request_types: frozenset[str] = frozenset()
    app_result: str | None = None
    redirect_to_new_number: bool = False
    new_email_valid: bool | None = None


@dataclass
class Risk:
    level: str = "LOW"  # LOW | HIGH
    step_up: bool = False
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def score_risk(s: Signals) -> Risk:
    """Pure function of verification signals. Takes no language, accent or name."""
    r = Risk()

    def high(code: str) -> None:
        r.level = "HIGH"
        r.reasons.append(code)

    if s.stated_match is False:
        high("STATED_MISMATCH")
    if (s.verification_status is not None and s.verification_status != "SUBJECT_FOUND") \
            or s.risk_indicator == "HIGH":
        high("CRS_NOT_FOUND_OR_HIGH")
    codes = set(s.alert_codes)
    if codes & MISMATCH_OR_SYNTHETIC:
        high("CRS_IDENTITY_MISMATCH_OR_SYNTHETIC")
    if DECEASED in codes:
        high("CRS_DECEASED_INDICATOR")
    if FRAUD_ALERT in codes:
        r.step_up = True
        r.reasons.append("CRS_FRAUD_ALERT")
        r.flags.append("HUMAN_REVIEW")
    if s.calling_record_found is False:
        r.step_up = True
        r.reasons.append("REVERSE_PHONE_NO_RECORD")
        r.flags.append("NO_PHONE_RECORD")
    elif s.calling_record_found and s.calling_name_matches is False:
        r.step_up = True
        r.reasons.append("REVERSE_PHONE_NAME_DIFFERS")
    if s.request_types & CONTACT_CHANGES:
        r.step_up = True
        r.reasons.append("CONTACT_CHANGE_STEP_UP")
    if s.app_result in ("denied", "timeout"):
        high(f"APP_APPROVAL_{s.app_result.upper()}")
    if s.redirect_to_new_number:
        high("REDIRECT_TO_NEW_NUMBER")
    if s.new_email_valid is False:
        high("NEW_EMAIL_INVALID")
        r.flags.append("NEW_EMAIL_INVALID")
    if s.request_types & CONTACT_CHANGES and s.request_types & MONEY_OR_CREDENTIALS:
        high("CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS")
    return r


@dataclass
class Interpretation:
    intent: str = "unclear"
    language: str | None = None
    stated_name: str | None = None
    stated_dob: str | None = None
    stated_zip: str | None = None
    stated_ssn_last4: str | None = None
    request_types: tuple[str, ...] = ()
    new_contact_value: str | None = None
    redirect_to_new_number: bool = False


@dataclass
class Case:
    case_id: str
    language: str
    state: str = "greet"
    outcome: str | None = None
    risk_level: str = "LOW"
    verified: bool = False
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    request_types: set[str] = field(default_factory=set)
    stated: dict[str, str] = field(default_factory=dict)
    new_contact_value: str | None = None
    audit: list[tuple[str, str]] = field(default_factory=list)  # (state, reason code)

    def add_reason(self, code: str) -> None:
        if code not in self.reasons:
            self.reasons.append(code)
            self.audit.append((self.state, code))

    def public(self) -> dict:
        """Case file for the human: codes and enums only, no member data."""
        return {
            "case_id": self.case_id, "outcome": self.outcome, "risk": self.risk_level,
            "verified": self.verified, "language": self.language, "final_state": self.state,
            "requests": sorted(self.request_types), "reasons": self.reasons, "flags": self.flags,
        }


@dataclass
class Turn:
    message_id: str
    done: bool = False


def _norm_name(s: str) -> list[str]:
    return s.casefold().replace(",", " ").split()


class Policy:
    def __init__(self, member: Member, facts: Facts, calling_from: str,
                 request_app_approval: Callable[[str], str], language: str = "en", lock_language: bool = True):
        self.member, self.facts, self.calling_from = member, facts, calling_from
        self._approve = request_app_approval
        self.lock_language = lock_language  # the caller-set language chip wins over per-utterance detection
        self.case = Case("case-" + "".join(secrets.choice("abcdefghijklmnop") for _ in range(8)), language)
        self._signals = Signals()
        self._unclear = 0

    # -- entry points -------------------------------------------------------
    def start(self) -> Turn:
        self.case.state = "collect_identity"
        return Turn("greet")

    def repeat_needed(self) -> Turn:
        """Interpreter output was invalid; ask the caller to repeat."""
        self._unclear += 1
        if self._unclear >= MAX_UNCLEAR:
            return self._finish("ended_safe", "TOO_MANY_UNCLEAR_TURNS", "end_safe")
        return Turn("ask_repeat")

    def llm_unavailable(self) -> Turn:
        """The interpreter's LLM call failed: end safely and hand to a human. Never 'please repeat'."""
        self.case.flags.append("ESCALATED_TO_HUMAN")
        return self._finish("ended_safe", "LLM_UNAVAILABLE", "llm_down")

    def handle(self, it: Interpretation) -> Turn:
        c = self.case
        if c.outcome:
            return Turn("call_over", True)
        if it.language in ("en", "es") and not self.lock_language:
            c.language = it.language
        c.request_types.update(it.request_types)
        if it.new_contact_value:
            c.new_contact_value = it.new_contact_value
        for key, val in (("name", it.stated_name), ("dob", it.stated_dob),
                         ("zip", it.stated_zip), ("ssn_last4", it.stated_ssn_last4)):
            if val:
                c.stated[key] = val

        # available in every state
        if it.intent == "ask_human":
            return self._escalate_or_handoff("CALLER_REQUESTED_HUMAN")
        if it.intent == "goodbye":
            return self._finish("ended_safe", "CALLER_ENDED_CALL", "end_safe")

        # HIGH regardless of verification: redirect request, contact change with money or credentials
        self._update(redirect_to_new_number=self._signals.redirect_to_new_number or it.redirect_to_new_number)
        if self._apply_risk().level == "HIGH":
            return self._finish("escalated_high", None, "escalate")

        if c.state == "collect_identity":
            return self._collect_identity()
        if c.state == "step_up":
            return self._step_up_response()
        if c.state == "request":
            return self._request_state()
        return Turn("ask_repeat")

    # -- states -------------------------------------------------------------
    def _collect_identity(self) -> Turn:
        c = self.case
        if not all(k in c.stated for k in ("name", "dob", "zip", "ssn_last4")):
            return Turn("ask_identity")
        c.state = "crs_checks"
        self._update(stated_match=self._stated_matches())
        if self._apply_risk().level == "HIGH":  # local mismatch: no CRS call needed
            return self._finish("escalated_high", None, "escalate")
        ident = self.facts.identity()
        calling = self.facts.calling_from(self.calling_from)
        self._update(verification_status=ident.verification_status, risk_indicator=ident.risk_indicator,
                     alert_codes=ident.alert_codes, calling_record_found=calling.record_found,
                     calling_name_matches=calling.name_matches_member)
        if "change_email" in c.request_types and c.new_contact_value:
            self._update(new_email_valid=self.facts.email_valid(c.new_contact_value))
        risk = self._apply_risk()
        if risk.level == "HIGH":
            return self._finish("escalated_high", None, "escalate")
        if risk.step_up:
            c.state = "step_up"
            c.add_reason("STEP_UP_REQUIRED")
            return Turn("ask_app_approval")
        return self._verified()

    def _step_up_response(self) -> Turn:
        self._update(app_result=self._approve(self.case.case_id))  # simulated banking-app approval
        if self._apply_risk().level == "HIGH":
            return self._finish("escalated_high", None, "escalate")
        self.case.add_reason("APP_APPROVED_SIMULATED")
        return self._verified()

    def _verified(self) -> Turn:
        c = self.case
        c.verified = True
        c.add_reason("VERIFIED")
        if c.request_types:
            return self._finish("verified_handoff", "HANDOFF_WITH_REQUEST", "handoff")
        c.state = "request"
        return Turn("ask_request")

    def _request_state(self) -> Turn:
        if not self.case.request_types:
            return Turn("ask_request")
        return self._finish("verified_handoff", "HANDOFF_WITH_REQUEST", "handoff")

    # -- helpers ------------------------------------------------------------
    def _stated_matches(self) -> bool:
        s, m = self.case.stated, self.member
        return (_norm_name(s["name"]) == _norm_name(m.name) and s["dob"] == m.dob
                and s["zip"] == m.zip and s["ssn_last4"] == m.ssn_last4)

    def _update(self, **changes) -> None:
        self._signals = replace(self._signals, **changes)

    def _apply_risk(self) -> Risk:
        c = self.case
        risk = score_risk(replace(self._signals, request_types=frozenset(c.request_types)))
        for code in risk.reasons:
            c.add_reason(code)
        for f in risk.flags:
            if f not in c.flags:
                c.flags.append(f)
        if risk.level == "HIGH":
            c.risk_level = "HIGH"
        return risk

    def _escalate_or_handoff(self, code: str) -> Turn:
        if self.case.verified:
            return self._finish("verified_handoff", code, "handoff")
        self.case.risk_level = "MEDIUM"  # unverified, but nothing says the caller is hostile
        return self._finish("unverified_handoff", code, "escalate")

    def _finish(self, outcome: str, reason: str | None, message_id: str) -> Turn:
        c = self.case
        if reason:
            c.add_reason(reason)
        c.outcome, c.state = outcome, "outcome"
        return Turn(message_id, True)
