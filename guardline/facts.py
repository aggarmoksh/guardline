"""CRS facts as the policy sees them: typed summaries only, never raw bureau JSON.

LiveFacts calls the CRS sandbox; FixtureFacts replays tests/fixtures/crs_summaries.json.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "crs_summaries.json"
RAW = Path(__file__).resolve().parents[1] / "fixtures" / "raw"
MADE_UP_PHONE = "4155550123"


@dataclass(frozen=True)
class IdentityFacts:
    alert_codes: tuple[int, ...]
    verification_status: str | None
    risk_indicator: str | None = None


@dataclass(frozen=True)
class CallingFromFacts:
    record_found: bool
    name_matches_member: bool


class Facts(Protocol):
    def identity(self) -> IdentityFacts: ...
    def calling_from(self, kind: str) -> CallingFromFacts: ...
    def email_valid(self, email: str) -> bool: ...


def _same_name(returned: list[tuple[str, str]], member_name: str) -> bool:
    want = member_name.casefold().split()
    return any((a + " " + b).casefold().split() == want for a, b in returned)


class LiveFacts:
    def __init__(self, persona_id: str, member_name: str):
        from guardline.crs import CRSClient  # imported lazily so policy tests need no network stack

        self._crs = CRSClient()
        self._persona = json.loads((RAW / "personas.json").read_text(encoding="utf-8"))[persona_id]
        self._member_name = member_name

    def identity(self) -> IdentityFacts:
        r = self._crs.identity_verify(self._persona["identity"])
        s = r.summary
        if s is None:
            return IdentityFacts((), None, "HIGH")  # fail closed
        return IdentityFacts(tuple(s.alert_codes), s.verification_status, s.risk_indicator)

    def calling_from(self, kind: str) -> CallingFromFacts:
        phone = self._persona["reverse_phone"]["phoneNumber"] if kind == "member" else MADE_UP_PHONE
        r = self._crs.reverse_phone(phone)
        s = r.summary
        if s is None:
            return CallingFromFacts(False, False)
        return CallingFromFacts(s.person_returned, _same_name(s.returned_names, self._member_name))

    def email_valid(self, email: str) -> bool:
        body = dict(self._persona["fraud_finder"], email=email)
        r = self._crs.fraud_finder(body)
        return bool(r.summary and r.summary.email_status == "valid")


class FixtureFacts:
    def __init__(self, persona_id: str, email_valid: bool = True, path: Path = FIXTURES):
        data = json.loads(path.read_text(encoding="utf-8"))
        self._p = data["personas"][persona_id]
        self._other = data["calling_from_other"]
        self._email_valid = email_valid

    def identity(self) -> IdentityFacts:
        return IdentityFacts(tuple(self._p["alert_codes"]), self._p["verification_status"],
                             self._p.get("risk_indicator"))

    def calling_from(self, kind: str) -> CallingFromFacts:
        src = (self._p["reverse_phone"] if kind == "member" else self._other)
        return CallingFromFacts(src["record_found"], src["name_matches_member"])

    def email_valid(self, email: str) -> bool:
        return self._email_valid
