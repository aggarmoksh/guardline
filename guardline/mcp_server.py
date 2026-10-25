"""Read-only MCP server (stdio) for a fraud analyst: look at GuardLine cases without any way to change one.

Tools: list_recent_cases, get_case, explain_case, eval_summary. Every tool is read-only by construction:
- the database is opened read-only (SQLite mode=ro), so even a bug could not write;
- there is no tool that verifies a caller, approves anything or changes a case;
- output is the code-only case file (outcome, risk, reason codes, flags, tactics, CRS evidence in plain words).
  No raw bureau JSON, no member values and no caller numbers exist in what these tools read.

Run: python -m guardline.mcp_server   (needs the optional extra: uv sync --extra mcp)
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from guardline import live

ROOT = Path(__file__).resolve().parents[1]
RESULTS_V2 = ROOT / "eval" / "RESULTS_v2.md"
MAX_LIMIT = 50

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

OUTCOME_TEXT = {
    "verified_handoff": "The caller was verified and handed to a person.",
    "unverified_handoff": "The caller could not be verified. A team member will call back at the number on file.",
    "escalated_high": "The call was escalated as high risk. A team member will call back at the number on file.",
    "ended_safe": "The call ended safely without verifying anyone.",
}
REASON_TEXT = {
    "VERIFIED": "Identity was verified.",
    "HANDOFF_WITH_REQUEST": "The call was handed to a person together with the caller's request.",
    "CONTACT_CHANGE_STEP_UP": "The caller asked for a contact change, which needs the member's approval in the app.",
    "STEP_UP_REQUIRED": "A confirmation request was sent to the member's banking app.",
    "APP_APPROVED_SIMULATED": "The member approved the request in the (simulated) banking app.",
    "MEMBER_DENIED_CALL": "The member tapped Deny: they say it was not them calling.",
    "APP_APPROVAL_TIMEOUT": "The member did not answer the approval request in time.",
    "ABANDONED_DURING_STEP_UP": "The caller left while the approval was pending.",
    "REVERSE_PHONE_NAME_DIFFERS": "The calling number is registered to someone else.",
    "REVERSE_PHONE_NO_RECORD": "The calling number has no record in the reverse phone lookup.",
    "REDIRECT_TO_NEW_NUMBER": "The caller asked to send the approval to a new number.",
    "CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS": "A contact change was requested together with money movement or a credential reset.",
    "CRS_IDENTITY_MISMATCH_OR_SYNTHETIC": "TransUnion returned identity-mismatch or synthetic-identity alert codes.",
    "CRS_DECEASED_INDICATOR": "TransUnion returned a deceased indicator.",
    "CRS_FRAUD_ALERT": "TransUnion shows a fraud alert on file.",
    "CRS_NO_FILE": "TransUnion has no file for this member.",
    "NAME_FUZZY_MATCH": "The stated name matched approximately, with date of birth, ZIP and last 4 exact.",
    "IDENTITY_REENTRY_REQUESTED": "The details did not match, so the caller was asked to try again.",
    "IDENTITY_NOT_CONFIRMED": "The details still did not match after a retry.",
    "NEW_EMAIL_INVALID": "The requested new email address was invalid.",
    "CALLER_REQUESTED_HUMAN": "The caller asked for a person.",
    "CALLER_ENDED_CALL": "The caller ended the call.",
    "CALL_ABANDONED": "The call was abandoned (no activity for 3 minutes).",
    "TOO_MANY_UNCLEAR_TURNS": "Too many unclear turns.",
    "LLM_UNAVAILABLE": "The language model was unavailable, so the call ended safely.",
}
FLAG_TEXT = {"FRAUD_ALERT": "fraud alert", "HUMAN_REVIEW": "needs human review", "NO_PHONE_RECORD": "no phone record",
             "EMAIL_INVALID": "invalid new email", "ESCALATED_TO_HUMAN": "escalated to a human",
             "CALLBACK_QUEUED": "callback queued to the number on file"}


def _db_path() -> Path:
    return Path(os.environ.get("GUARDLINE_DB", ROOT / "guardline.db"))


def _connect(path: Path | None = None) -> sqlite3.Connection:
    """Read-only connection: any write attempt raises."""
    path = path or _db_path()
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _title(code: str) -> str:
    return code.replace("TACTIC_", "").replace("_", " ").lower()


def _case(row: sqlite3.Row) -> dict:
    """The code-only case file, from whitelisted columns only."""
    reasons, flags = json.loads(row["reasons"] or "[]"), json.loads(row["flags"] or "[]")
    tactics, evidence = json.loads(row["tactics"] or "[]"), json.loads(row["evidence"] or "[]")
    known = row["known_number"]
    return {
        "case_id": row["case_id"], "demo_member": row["persona"], "channel": row["channel"] or "web",
        "known_number": None if known is None else bool(known), "language": row["language"],
        "outcome": row["outcome"], "risk": row["risk"], "verified": bool(row["verified"]),
        "reasons": reasons, "flags": flags, "tactics": tactics,
        "crs_evidence": [{"product": e["product"], "ran_at": _iso(e["ran_at"]), "result": e["result"]} for e in evidence],
        "started_at": _iso(row["created_at"]), "ended_at": _iso(row["ended_at"]),
    }


def _find(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row | None:
    case_id = case_id.strip()
    row = conn.execute("SELECT * FROM caserecord WHERE case_id = ?", (case_id,)).fetchone()
    if row is None and case_id:  # the dashboard shows only the last 8 characters
        rows = conn.execute("SELECT * FROM caserecord WHERE case_id LIKE ?", ("%" + re.sub(r"[%_]", "", case_id),)).fetchall()
        row = rows[0] if len(rows) == 1 else None
    return row


def list_recent_cases_data(limit: int = 10, path: Path | None = None) -> list[dict]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    with _connect(path) as conn:
        rows = conn.execute("SELECT * FROM caserecord ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        c = _case(row)
        out.append({k: c[k] for k in ("case_id", "demo_member", "channel", "known_number", "language", "outcome", "risk",
                                      "verified", "started_at")} | {"reason_codes": c["reasons"], "tactics": c["tactics"]})
    return out


def get_case_data(case_id: str, path: Path | None = None) -> dict:
    with _connect(path) as conn:
        row = _find(conn, case_id)
    return _case(row) if row else {"error": "case not found"}


def explain_case_data(case_id: str, path: Path | None = None) -> dict:
    with _connect(path) as conn:
        row = _find(conn, case_id)
    if row is None:
        return {"error": "case not found"}
    c = _case(row)
    rec = {"outcome": c["outcome"], "reasons": c["reasons"], "flags": c["flags"], "verified": c["verified"]}
    banner = live.banner(rec)
    lines = [OUTCOME_TEXT.get(c["outcome"], "The call is still in progress.")]
    lines += [REASON_TEXT.get(code, "Signal recorded: " + code.lower().replace("_", " ") + ".") for code in c["reasons"]
              if code not in ("VERIFIED", "HANDOFF_WITH_REQUEST")]
    if c["tactics"]:
        lines.append("Scam tactics heard: " + ", ".join(_title(t) for t in c["tactics"]) + ". These only add flags; they never verify anyone.")
    if c["flags"]:
        lines.append("Flags: " + ", ".join(FLAG_TEXT.get(f, f.lower().replace("_", " ")) for f in c["flags"]) + ".")
    for e in c["crs_evidence"]:
        lines.append(f"{e['product']} said: {e['result']}.")
    return {"case_id": c["case_id"], "headline": banner["headline"] + (": " + banner["detail"] if banner["detail"] else ""),
            "summary": " ".join(lines), "outcome": c["outcome"], "risk": c["risk"]}


def eval_summary_data(path: Path | None = None) -> dict:
    """Headline numbers from eval/RESULTS_v2.md (a post hoc rerun: 3 runs of the same 30 cases)."""
    text = (path or RESULTS_V2).read_text(encoding="utf-8")
    meta = {k: v for k, v in re.findall(r"^- (Model|Cases commit|Run commit|Runs): (.+)$", text, flags=re.M)}
    section = text.split("## Headline numbers per run", 1)[1].split("\n## ", 1)[0]
    metrics = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"\d+", cells[1]):
            *runs, low, high = [int(x) for x in cells[1:]]
            metrics[cells[0]] = {"per_run": runs, "min": low, "max": high}
    return {"source": "eval/RESULTS_v2.md", "note": "post hoc rerun after a policy fix; not the pre-registered run",
            "model": meta.get("Model", "").split(" ")[0].strip("`"), "runs": meta.get("Runs"), "metrics": metrics}


# -- the MCP server: read-only tools only ----------------------------------------------------------------------
server = MCPServer(
    "guardline-analyst",
    instructions="Read-only view of GuardLine fraud-triage cases (sandbox data). You can list cases, read a code-only case file, "
                 "get a plain-English explanation, and read the evaluation summary. Nothing here can verify a caller or change a case.")


@server.tool(annotations=READ_ONLY)
def list_recent_cases(limit: int = 10) -> list[dict]:
    """List the most recent cases (newest first): outcome, risk, reason codes, tactics. Limit 1 to 50."""
    return list_recent_cases_data(limit)


@server.tool(annotations=READ_ONLY)
def get_case(case_id: str) -> dict:
    """Get one code-only case file by case id (the full id or the last 8 characters): outcome, risk, reasons, flags, tactics, CRS evidence in plain words."""
    return get_case_data(case_id)


@server.tool(annotations=READ_ONLY)
def explain_case(case_id: str) -> dict:
    """Explain a case in plain English, built only from its reason codes, flags, tactics and CRS evidence."""
    return explain_case_data(case_id)


@server.tool(annotations=READ_ONLY)
def eval_summary() -> dict:
    """Headline numbers from the post hoc evaluation (eval/RESULTS_v2.md): per run, min and max."""
    return eval_summary_data()


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
