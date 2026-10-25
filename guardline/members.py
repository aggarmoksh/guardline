"""Local credit-union member records (fixtures/raw/members.json, gitignored)."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAW = Path(__file__).resolve().parents[1] / "fixtures" / "raw"


@dataclass(frozen=True)
class Member:
    persona_id: str
    name: str
    dob: str
    zip: str
    ssn_last4: str
    phone_on_file: str
    email_on_file: str

    def sensitive_values(self) -> list[str]:
        return [self.name, self.dob, self.zip, self.ssn_last4, self.phone_on_file, self.email_on_file]

    def is_known_number(self, caller_number: str) -> bool:
        """Caller ID equals the phone on file. Smoother handling only: caller ID can be spoofed, so never proof."""
        return _digits(caller_number) == _digits(self.phone_on_file) != ""


def _digits(number: str) -> str:
    d = re.sub(r"\D", "", number or "")
    return d[1:] if len(d) == 11 and d.startswith("1") else d


DEMO_PHONE_MEMBER = "P01"  # OPERATOR_PHONE in .env is this demo member's phone on file


def load_members() -> dict[str, Member]:
    load_dotenv()  # picks up OPERATOR_PHONE when the caller has not loaded .env yet
    raw = json.loads((RAW / "members.json").read_text(encoding="utf-8"))
    operator_phone = os.environ.get("OPERATOR_PHONE", "").strip()
    if operator_phone and DEMO_PHONE_MEMBER in raw:  # local override, never committed, never printed
        raw[DEMO_PHONE_MEMBER] = {**raw[DEMO_PHONE_MEMBER], "phone_on_file": _digits(operator_phone)}
    return {pid: Member(pid, **rec) for pid, rec in raw.items()}
