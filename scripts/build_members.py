"""Build fixtures/raw/members.json (gitignored): one credit-union member record per persona."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "fixtures" / "raw"


def main() -> None:
    personas = json.loads((RAW / "personas.json").read_text(encoding="utf-8"))
    members = {}
    for pid, p in personas.items():
        ident = p["identity"]
        members[pid] = {
            "name": f"{ident['firstName']} {ident['lastName']}",
            "dob": ident["dob"],
            "zip": ident["zip"],
            "ssn_last4": ident["ssn"][-4:],
            "phone_on_file": p["reverse_phone"]["phoneNumber"],
            "email_on_file": p["fraud_finder"]["email"],
        }
    (RAW / "members.json").write_text(json.dumps(members, indent=2), encoding="utf-8")
    print(f"members: {len(members)}")


if __name__ == "__main__":
    main()
