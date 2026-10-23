"""Phase 1A probe of reverse phone and FraudFinder. Writes ../../probe/phase1a_table.json (outside the repo)."""
import json
from pathlib import Path

from guardline.crs import CRSClient
from guardline.redact import register_sensitive

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "probe" / "phase1a_table.json"
MADE_UP_PHONE = "4155550123"


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def main() -> None:
    personas = json.loads((ROOT / "fixtures" / "raw" / "personas.json").read_text(encoding="utf-8"))
    for p in personas.values():
        register_sensitive(str(v) for body in p.values() if isinstance(body, dict)
                           for v in body.values() if isinstance(v, str))
    crs = CRSClient()
    rows: list[dict] = []

    def name_of(pid):
        return personas[pid]["identity"]["firstName"], personas[pid]["identity"]["lastName"]

    def reverse(label, pid_for_expected, phone, filt=None):
        fn, ln = filt if filt else (None, None)
        r = crs.reverse_phone(phone, fn, ln)
        row = {"call": label, "endpoint": "reverse_phone", "http_status": r.status}
        if r.summary:
            s = r.summary
            exp = name_of(pid_for_expected) if pid_for_expected else None
            row.update(
                person_returned=s.person_returned,
                record_count=len(s.returned_names),
                name_matches_persona=(any((_norm(a), _norm(b)) == (_norm(exp[0]), _norm(exp[1]))
                                          for a, b in s.returned_names) if exp and s.person_returned else None),
                match_confidence_fields=s.scalar_fields,
                response_success_flag=(r.data or {}).get("success"),
            )
        rows.append(row)

    def fraud(label, pid, email):
        body = dict(personas[pid]["fraud_finder"])
        body["email"] = email
        r = crs.fraud_finder(body)
        row = {"call": label, "endpoint": "fraud_finder", "http_status": r.status}
        if r.summary:
            row.update(risk_score=r.summary.risk_score, email_status=r.summary.email_status,
                       email_status_code=r.summary.email_status_code)
        rows.append(row)

    for pid in ("P01", "P02", "P13", "P16"):
        reverse(f"{pid} own phone", pid, personas[pid]["reverse_phone"]["phoneNumber"])
    reverse("P01 phone + P02 name filter (expect P01)", "P01", personas["P01"]["reverse_phone"]["phoneNumber"], name_of("P02"))
    reverse("made-up phone", None, MADE_UP_PHONE)
    fraud("P01 own email", "P01", personas["P01"]["fraud_finder"]["email"])
    fraud("P01 body, P02 email", "P01", personas["P02"]["fraud_finder"]["email"])
    fraud("P01 body, email x@x", "P01", "x@x")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} rows; statuses: {[r['http_status'] for r in rows]}")


if __name__ == "__main__":
    main()
