"""Local credit-union member records (fixtures/raw/members.json, gitignored)."""
import json
from dataclasses import dataclass
from pathlib import Path

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


def load_members() -> dict[str, Member]:
    raw = json.loads((RAW / "members.json").read_text(encoding="utf-8"))
    return {pid: Member(pid, **rec) for pid, rec in raw.items()}
