"""Record typed CRS summaries (no names, numbers or other persona values) to tests/fixtures/crs_summaries.json."""
import json
from pathlib import Path

from guardline.facts import LiveFacts
from guardline.members import load_members

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "crs_summaries.json"
PERSONAS = ["P01", "P02", "P12", "P13", "P14", "P16"]


def main() -> None:
    members = load_members()
    out = {"personas": {}}
    for pid in PERSONAS:
        facts = LiveFacts(pid, members[pid].name)
        ident, own = facts.identity(), facts.calling_from("member")
        out["personas"][pid] = {
            "alert_codes": list(ident.alert_codes),
            "verification_status": ident.verification_status,
            "risk_indicator": ident.risk_indicator,
            "reverse_phone": {"record_found": own.record_found, "name_matches_member": own.name_matches_member},
        }
        other = facts.calling_from("other")
    out["calling_from_other"] = {"record_found": other.record_found, "name_matches_member": other.name_matches_member}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(OUT.read_text())


if __name__ == "__main__":
    main()
