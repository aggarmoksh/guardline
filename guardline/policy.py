"""Deterministic policy engine. Only this code can mark a caller verified.

The model can make a call safer, never less safe: the interpreter supplies enums and
caller-stated slots, this module decides.
"""
import secrets
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
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
FUZZY_NAME_THRESHOLD = 0.88
TYPED_FIELDS = ("dob", "zip", "ssn_last4")  # entered on the keypad, in this order
FIELD_PROMPT = {"name": "ask_name", "dob": "ask_dob", "zip": "ask_zip", "ssn_last4": "ask_ssn4"}


# -- name matching --------------------------------------------------------------
def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in s.casefold()).split())


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(max(la, lb) // 2 - 1, 0)
    ma, mb = [False] * la, [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not mb[j] and b[j] == ca:
                ma[i] = mb[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = transpositions = 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            transpositions += a[i] != b[k]
            k += 1
    jaro = (matches / la + matches / lb + (matches - transpositions / 2) / matches) / 3
    prefix = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


# -- risk -----------------------------------------------------------------------
@dataclass(frozen=True)
class Signals:
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
    unverified: bool = False  # not HIGH, but the caller could not be verified (hand to a human)
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def score_risk(s: Signals) -> Risk:
    """Pure function of verification signals. Takes no language, accent or name."""
    r = Risk()

    def high(code: str) -> None:
        r.level = "HIGH"
        r.reasons.append(code)

    if s.verification_status is not None and s.verification_status != "SUBJECT_FOUND":
        r.unverified = True  # thin-file or no-file members must not be HIGH: hand to a human
        r.reasons.append("CRS_NO_FILE")
    codes = set(s.alert_codes)
    if codes & MISMATCH_OR_SYNTHETIC:
        high("CRS_IDENTITY_MISMATCH_OR_SYNTHETIC")
    if DECEASED in codes:
        high("CRS_DECEASED_INDICATOR")
    if FRAUD_ALERT in codes:
        r.step_up = True
        r.reasons.append("CRS_FRAUD_ALERT")
        r.flags += ["FRAUD_ALERT", "HUMAN_REVIEW"]
    reverse_mismatch = s.calling_record_found is False or (s.calling_record_found and s.calling_name_matches is False)
    if s.calling_record_found is False:
        r.step_up = True
        r.reasons.append("REVERSE_PHONE_NO_RECORD")
        r.flags.append("NO_PHONE_RECORD")
    elif reverse_mismatch:
        r.step_up = True
        r.reasons.append("REVERSE_PHONE_NAME_DIFFERS")
    if s.request_types & CONTACT_CHANGES:
        r.step_up = True
        r.reasons.append("CONTACT_CHANGE_STEP_UP")
    if s.app_result in ("denied", "timeout"):
        r.reasons.append(f"APP_APPROVAL_{s.app_result.upper()}")
        if reverse_mismatch:
            r.level = "HIGH"
        else:
            r.unverified = True
    if s.redirect_to_new_number:
        high("REDIRECT_TO_NEW_NUMBER")
    if s.new_email_valid is False:  # flag it and require the step-up; not HIGH on its own
        r.step_up = True
        r.reasons.append("NEW_EMAIL_INVALID")
        r.flags.append("EMAIL_INVALID")
    if s.request_types & CONTACT_CHANGES and s.request_types & MONEY_OR_CREDENTIALS:
        high("CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS")
    return r


# -- case and turns -------------------------------------------------------------
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
    expect: str | None = None  # which identity field the caller should give next (name, dob, zip, ssn_last4)


class Policy:
    def __init__(self, member: Member, facts: Facts, calling_from: str,
                 request_app_approval: Callable[[str], str], language: str = "en",
                 lock_language: bool = True, keypad_mode: bool = False):
        """keypad_mode: DOB, ZIP and SSN last 4 arrive only through provide_typed(); the name comes by voice."""
        self.member, self.facts, self.calling_from = member, facts, calling_from
        self._approve = request_app_approval
        self.lock_language = lock_language  # the caller-set language chip wins over per-utterance detection
        self.keypad_mode = keypad_mode
        self.case = Case("case-" + "".join(secrets.choice("abcdefghijklmnop") for _ in range(8)), language)
        self._signals = Signals()
        self._unclear = 0
        self._identity_failures = 0
        self._stepped_up = False  # the simulated app approval has been given on this call

    # -- entry points -------------------------------------------------------
    def start(self) -> Turn:
        self.case.state = "collect_identity"
        return Turn("greet")

    def repeat_needed(self) -> Turn:
        """Interpreter output was invalid; ask the caller to repeat."""
        self._unclear += 1
        if self._unclear >= MAX_UNCLEAR:
            return self._finish("ended_safe", "TOO_MANY_UNCLEAR_TURNS", "end_safe")
        return Turn("ask_repeat", expect=self.expected_field())

    def llm_unavailable(self) -> Turn:
        """The interpreter's LLM call failed: end safely and hand to a human. Never 'please repeat'."""
        self.case.flags.append("ESCALATED_TO_HUMAN")
        return self._finish("ended_safe", "LLM_UNAVAILABLE", "llm_down")

    def provide_typed(self, field_name: str, value: str) -> Turn:
        """Keypad entry. Goes straight to the policy: never through STT or the LLM, never logged."""
        c = self.case
        if c.outcome:
            return Turn("call_over", True)
        if c.state != "collect_identity" or field_name not in TYPED_FIELDS or self.expected_field() != field_name:
            return Turn(FIELD_PROMPT.get(self.expected_field() or "", "ask_repeat"), expect=self.expected_field())
        if field_name == "dob":
            iso = self._dob_to_iso(value)
            if iso is None:
                return Turn("ask_dob", expect="dob")
            value = iso
        elif not (value.isdigit() and len(value) == (5 if field_name == "zip" else 4)):
            return Turn(FIELD_PROMPT[field_name], expect=field_name)
        c.stated[field_name] = value
        if self._apply_risk().level == "HIGH":
            return self._finish("escalated_high", None, "escalate")
        return self._collect_identity()

    def handle(self, it: Interpretation) -> Turn:
        c = self.case
        if c.outcome:
            return Turn("call_over", True)
        if it.language in ("en", "es") and not self.lock_language:
            c.language = it.language
        c.request_types.update(it.request_types)
        if it.new_contact_value:
            c.new_contact_value = it.new_contact_value
        spoken = [("name", it.stated_name)]
        if not self.keypad_mode:  # with a keypad, spoken digits are ignored
            spoken += [("dob", it.stated_dob), ("zip", it.stated_zip), ("ssn_last4", it.stated_ssn_last4)]
        for key, val in spoken:
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
    def expected_field(self) -> str | None:
        if self.case.state != "collect_identity":
            return None
        return next((k for k in ("name", *TYPED_FIELDS) if k not in self.case.stated), None)

    def _collect_identity(self) -> Turn:
        c = self.case
        missing = self.expected_field()
        if missing:
            if self.keypad_mode:
                return Turn(FIELD_PROMPT[missing], expect=missing)
            return Turn("ask_identity")
        c.state = "crs_checks"
        ok, fuzzy = self._identity_check()
        if not ok:
            return self._identity_failed()
        if fuzzy:
            c.add_reason("NAME_FUZZY_MATCH")
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
        if risk.unverified:
            c.risk_level = "MEDIUM"
            return self._finish("unverified_handoff", None, "escalate")
        if risk.step_up:
            c.state = "step_up"
            c.add_reason("STEP_UP_REQUIRED")
            return Turn("ask_app_approval")
        return self._verified()

    def _identity_failed(self) -> Turn:
        """Stated details do not match. Ask once to re-enter (never say which field), then hand to a human."""
        c = self.case
        self._identity_failures += 1
        if self._identity_failures >= 2:
            c.risk_level = "MEDIUM"
            return self._finish("unverified_handoff", "IDENTITY_NOT_CONFIRMED", "escalate")
        c.add_reason("IDENTITY_REENTRY_REQUESTED")
        c.stated.clear()
        c.state = "collect_identity"
        return Turn("ask_reenter", expect="name" if self.keypad_mode else None)

    def _step_up_response(self) -> Turn:
        self._update(app_result=self._approve(self.case.case_id))  # simulated banking-app approval
        risk = self._apply_risk()
        if risk.level == "HIGH":
            return self._finish("escalated_high", None, "escalate")
        if risk.unverified:
            self.case.risk_level = "MEDIUM"
            self.case.verified = False  # identity was checked, but the step-up failed
            return self._finish("unverified_handoff", None, "escalate")
        self._stepped_up = True
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
        c = self.case
        if not c.request_types:
            return Turn("ask_request")
        # A request that arrives after identity was checked still needs the step-up it would have needed earlier.
        if self._apply_risk().step_up and not self._stepped_up:
            c.state = "step_up"
            c.add_reason("STEP_UP_REQUIRED")
            return Turn("ask_app_approval")
        return self._finish("verified_handoff", "HANDOFF_WITH_REQUEST", "handoff")

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _dob_to_iso(mmddyyyy: str) -> str | None:
        if not (mmddyyyy.isdigit() and len(mmddyyyy) == 8):
            return None
        try:
            d = date(int(mmddyyyy[4:]), int(mmddyyyy[:2]), int(mmddyyyy[2:4]))
        except ValueError:
            return None
        return d.isoformat()

    def _identity_check(self) -> tuple[bool, bool]:
        """(match, matched only by fuzzy name). Fuzzy applies only when DOB, ZIP and last 4 are all exact."""
        s, m = self.case.stated, self.member
        digits_exact = s["dob"] == m.dob and s["zip"] == m.zip and s["ssn_last4"] == m.ssn_last4
        if not digits_exact:
            return False, False
        stated, on_file = normalize_name(s["name"]), normalize_name(m.name)
        if stated == on_file:
            return True, False
        return jaro_winkler(stated, on_file) >= FUZZY_NAME_THRESHOLD, True

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
