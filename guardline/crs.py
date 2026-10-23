"""CRS sandbox client: login, identity verify, reverse phone, FraudFinder."""
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

from guardline.redact import get_logger

BASE_URL = "https://api-sandbox.stitchcredit.com:443/api"
log = get_logger("guardline.crs")


class CRSError(Exception):
    pass


@dataclass
class CRSResult:
    status: int
    data: Any
    summary: Any = None


@dataclass
class IdentitySummary:
    file_hit: str | None
    risk_indicator: str | None
    verification_point: int | None
    alert_codes: list[int] = field(default_factory=list)


@dataclass
class ReversePhoneSummary:
    person_returned: bool
    returned_names: list[tuple[str, str]] = field(default_factory=list)
    scalar_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class FraudSummary:
    risk_score: int | None
    email_status: str | None
    email_status_code: int | None


def _find_all(obj: Any, key: str):
    """Yield every value stored under `key` anywhere in a nested JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _find_all(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_all(v, key)


def summarize_identity(data: Any) -> IdentitySummary:
    summary = next(_find_all(data, "verificationSummary"), None) or {}
    codes = []
    for block in _find_all(data, "idManagerVerification"):
        for msg in (block or {}).get("messages", []) or []:
            if isinstance(msg, dict) and msg.get("code") is not None:
                codes.append(int(msg["code"]))
    return IdentitySummary(
        file_hit=next(_find_all(data, "fileHitIndicator"), None),
        risk_indicator=summary.get("riskIndicator"),
        verification_point=summary.get("verificationPoint"),
        alert_codes=sorted(set(codes)),
    )


def summarize_fraud(data: Any) -> FraudSummary:
    risk = (data or {}).get("risk") or {}
    email = (data or {}).get("email_validation") or {}
    return FraudSummary(risk.get("score"), email.get("status"), email.get("status_code"))


class CRSClient:
    def __init__(self, username: str | None = None, password: str | None = None,
                 base_url: str = BASE_URL, timeout: float = 30.0):
        load_dotenv()
        self._username = username or os.environ.get("CRS_USERNAME", "")
        self._password = password or os.environ.get("CRS_PASSWORD", "")
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._token: str | None = None  # memory only

    def login(self) -> None:
        r = self._http.post("/users/login", json={"username": self._username, "password": self._password})
        log.info("login status=%s", r.status_code)
        if r.status_code != 200 or "token" not in r.json():
            raise CRSError(f"login failed: HTTP {r.status_code}")
        self._token = r.json()["token"]

    def _post(self, path: str, body: dict) -> tuple[int, Any]:
        for attempt in (0, 1):
            if self._token is None:
                self.login()
            r = self._http.post(path, json=body, headers={"Authorization": f"Bearer {self._token}"})
            if r.status_code == 401 and attempt == 0:
                log.info("401 on %s, re-login once", path)
                self._token = None
                continue
            break
        try:
            data = r.json()
        except ValueError:
            data = None
        log.info("POST %s status=%s", path, r.status_code)
        return r.status_code, data

    def identity_verify(self, body: dict) -> CRSResult:
        status, data = self._post("/transunion/identity-manager-verification/basic", body)
        return CRSResult(status, data, summarize_identity(data) if status == 200 else None)

    def reverse_phone(self, phone: str, first_name: str | None = None, last_name: str | None = None) -> CRSResult:
        body: dict[str, Any] = {"phoneNumber": phone}
        if first_name or last_name:
            body.update(firstName=first_name, lastName=last_name)
        status, data = self._post("/accurate-append/reverse", body)
        return CRSResult(status, data, summarize_reverse(data) if status == 200 else None)

    def fraud_finder(self, body: dict) -> CRSResult:
        status, data = self._post("/fraud-finder/fraud-finder", body)
        return CRSResult(status, data, summarize_fraud(data) if status == 200 else None)


def _scalar_fields(obj: Any, pattern: re.Pattern, out: dict[str, Any]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if pattern.search(k) and isinstance(v, (str, int, float, bool)) or (pattern.search(k) and v is None):
                out[k] = v
            _scalar_fields(v, pattern, out)
    elif isinstance(obj, list):
        for v in obj:
            _scalar_fields(v, pattern, out)


_MATCH_KEYS = re.compile(r"match|confiden|score|rank|relevan", re.I)


def summarize_reverse(data: Any) -> ReversePhoneSummary:
    records = ((data or {}).get("data") or {}).get("records") or []
    names = [(r.get("firstName") or "", r.get("lastName") or "") for r in records if isinstance(r, dict)]
    fields: dict[str, Any] = {}
    _scalar_fields(data, _MATCH_KEYS, fields)
    return ReversePhoneSummary(person_returned=bool(records), returned_names=names, scalar_fields=fields)
