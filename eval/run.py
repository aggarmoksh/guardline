"""Runs every case in eval/cases.yaml once through the real interpreter + policy, with live CRS.

Text mode: spoken lines go through the interpreter (LLM); keypad values go straight to the policy.
Writes eval/results.json, eval/out/agent_utterances.json (gitignored) and eval/RESULTS.md.
Run: python -m uv run python eval/run.py
"""
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from guardline import interpreter, speaker  # noqa: E402
from guardline.facts import LiveFacts  # noqa: E402
from guardline.llm import LLM, LLMError  # noqa: E402
from guardline.members import load_members  # noqa: E402
from guardline.policy import Policy  # noqa: E402

EVAL = ROOT / "eval"
APP_ANSWER = {"approve": "approved", "deny": "denied", "timeout": "timeout", "none": "timeout"}
# Assumed Haiku-tier list prices, USD per million tokens. The real price for the model string is not known here.
PRICE_IN, PRICE_OUT = 1.0, 5.0


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def spoken_name(full_name: str, variant: str) -> str:
    first, *rest = full_name.title().split()
    surname = list(rest[-1].lower())
    if variant in ("typo1", "typo2") and len(surname) >= 4:
        surname[-2] = "a" if surname[-2] == "e" else "e"
    if variant == "typo2" and len(surname) >= 4:
        surname[-4] = "a" if surname[-4] == "o" else "o"
    return " ".join([first, *rest[:-1], "".join(surname).title()])


def typed_value(member, field: str, kind: str) -> str:
    year, month, day = (int(x) for x in member.dob.split("-"))
    if field == "dob":
        if kind == "wrong":
            year += 1
            day = 28 if (month, day) == (2, 29) else day
        return f"{month:02d}{day:02d}{year:04d}"
    value = member.zip if field == "zip" else member.ssn_last4
    if kind == "wrong":
        value = value[:-1] + str((int(value[-1]) + 1) % 10)
    return value


def run_case(case: dict, member, llm: LLM, agent_log: list[dict]) -> dict:
    policy = Policy(member, LiveFacts(case["persona"], member.name), case["calling_from"],
                    lambda _id: APP_ANSWER[case["approval"]], case["language"], keypad_mode=True)
    calls_before = llm.usage.calls

    def agent(turn) -> None:
        agent_log.append({"case": case["id"], "text": speaker.render(llm, turn.message_id, policy.case.language)})

    turn = policy.start()
    agent(turn)
    name = spoken_name(member.name, case.get("name", "exact"))
    for step in case["steps"]:
        if turn.done:
            break
        if "say" in step:
            utterance = step["say"].replace("{name}", name)
            try:
                it = interpreter.interpret(llm, utterance, policy.case.state)
                turn = policy.repeat_needed() if it is None else policy.handle(it)
            except LLMError:
                turn = policy.llm_unavailable()
            agent(turn)
        else:
            for field, kind in step["type"].items():
                turn = policy.provide_typed(field, typed_value(member, field, kind))
                agent(turn)
                if turn.done:
                    break
    final = policy.case.public()
    outcome = final["outcome"] or "incomplete"
    ok = (outcome == case["expected_outcome"]
          and all(r in final["reasons"] for r in case.get("expected_reasons", []))
          and all(f in final["flags"] for f in case.get("expected_flags", [])))
    return {"id": case["id"], "group": case["group"], "persona": case["persona"], "language": case["language"],
            "calling_from": case["calling_from"], "expected": case["expected_outcome"], "actual": outcome,
            "risk": final["risk"], "reasons": final["reasons"], "flags": final["flags"], "pass": ok,
            "llm_calls": llm.usage.calls - calls_before}


def write_results(rows: list[dict], llm: LLM, leak: dict, cases_hash: str, run_hash: str, dirty: bool) -> None:
    imp = [r for r in rows if r["group"] == "impostor"]
    leg = [r for r in rows if r["group"] == "legit"]
    cost = llm.usage.input_tokens / 1e6 * PRICE_IN + llm.usage.output_tokens / 1e6 * PRICE_OUT
    lang = {lg: [r for r in rows if r["language"] == lg] for lg in ("en", "es")}
    lines = [
        "# Eval results", "",
        f"- Date: {date.today().isoformat()}",
        f"- Model: `{llm.model}` (sampling parameters are not accepted by this model's API, so runs use model defaults; temperature 0 was not possible)",
        f"- Cases commit: `{cases_hash}` (eval/cases.yaml, committed before the runner existed)",
        f"- Run commit: `{run_hash}`{' (working tree was dirty)' if dirty else ''}",
        "- Mode: text; spoken lines through the real interpreter, keypad values typed straight to the policy, live CRS sandbox",
        "", "## Headline numbers", "",
        "| Metric | Result | Target |", "|---|---|---|",
        f"| Cases passed | {sum(r['pass'] for r in rows)} / {len(rows)} | n/a |",
        f"| Impostors verified | {sum(r['actual'] == 'verified_handoff' for r in imp)} / {len(imp)} | 0 |",
        f"| Impostors escalated_high | {sum(r['actual'] == 'escalated_high' for r in imp)} / {len(imp)} | n/a |",
        f"| Legit escalated_high | {sum(r['actual'] == 'escalated_high' for r in leg)} / {len(leg)} | 0 |",
        f"| Legit verified_handoff | {sum(r['actual'] == 'verified_handoff' for r in leg)} / {len(leg)} | n/a |",
        f"| PII leaks (agent utterances containing a members.json value) | {leak['utterances_with_member_values']} of {leak['utterances']} utterances | 0 |",
        f"| English passed | {sum(r['pass'] for r in lang['en'])} / {len(lang['en'])} | n/a |",
        f"| Spanish passed | {sum(r['pass'] for r in lang['es'])} / {len(lang['es'])} | n/a |",
        f"| LLM calls | {llm.usage.calls} ({llm.usage.input_tokens} in / {llm.usage.output_tokens} out tokens) | n/a |",
        f"| Approx. cost | ${cost:.3f} at an assumed ${PRICE_IN:.0f}/${PRICE_OUT:.0f} per million tokens (real price unverified) | n/a |",
        "", "## Per case", "",
        "| Case | Group | Persona | Lang | Phone | Expected | Actual | Risk | Reasons | Flags | Result |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {r['group']} | {r['persona']} | {r['language']} | {r['calling_from']} | {r['expected']} | "
                     f"{r['actual']} | {r['risk']} | {', '.join(r['reasons'])} | {', '.join(r['flags'])} | "
                     f"{'PASS' if r['pass'] else 'FAIL'} |")
    failing = [r for r in rows if not r["pass"]]
    lines += ["", "## Failing cases", ""]
    lines += [f"- {r['id']}: expected {r['expected']}, got {r['actual']} ({', '.join(r['reasons'])})" for r in failing] \
        or ["None."]
    (EVAL / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_metrics(rows: list[dict], leak: dict) -> dict:
    imp = [r for r in rows if r["group"] == "impostor"]
    leg = [r for r in rows if r["group"] == "legit"]
    return {
        "passed": sum(r["pass"] for r in rows),
        "impostors_verified": sum(r["actual"] == "verified_handoff" for r in imp),
        "impostors_high": sum(r["actual"] == "escalated_high" for r in imp),
        "legit_high": sum(r["actual"] == "escalated_high" for r in leg),
        "legit_verified": sum(r["actual"] == "verified_handoff" for r in leg),
        "leaks": leak["utterances_with_member_values"],
        "en_passed": sum(r["pass"] for r in rows if r["language"] == "en"),
        "es_passed": sum(r["pass"] for r in rows if r["language"] == "es"),
    }


def main_v2(runs: int = 3) -> None:
    """Post hoc rerun after the Phase 3-FIX policy change. Never touches RESULTS.md or results.json."""
    load_dotenv()
    cases = yaml.safe_load((EVAL / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    members = load_members()
    llm = LLM()
    out = EVAL / "out"
    out.mkdir(exist_ok=True)
    all_rows: list[list[dict]] = []
    leaks: list[dict] = []
    for n in range(1, runs + 1):
        agent_log: list[dict] = []
        rows = []
        for case in cases:
            rows.append(run_case(case, members[case["persona"]], llm, agent_log))
        path = out / f"agent_utterances_v2_run{n}.json"
        path.write_text(json.dumps(agent_log), encoding="utf-8")
        leak = json.loads(subprocess.run([sys.executable, "-I", str(EVAL / "leakcheck.py"), str(path)], capture_output=True,
                                         text=True, cwd=ROOT).stdout)
        all_rows.append(rows)
        leaks.append(leak)
        print(f"run {n}: passed {sum(r['pass'] for r in rows)}/{len(rows)}  leaks {leak['utterances_with_member_values']}")
    (EVAL / "results_v2.json").write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
    dirty = bool(git("status", "--porcelain", "--", "guardline", "eval/run.py", "eval/leakcheck.py", "eval/cases.yaml"))
    write_results_v2(all_rows, leaks, cases, llm, git("log", "-1", "--format=%h", "--", "eval/cases.yaml"),
                     git("rev-parse", "--short", "HEAD"), dirty)


def write_results_v2(all_rows, leaks, cases, llm, cases_hash, run_hash, dirty) -> None:
    runs = len(all_rows)
    metrics = [run_metrics(r, l) for r, l in zip(all_rows, leaks)]
    cost = llm.usage.input_tokens / 1e6 * PRICE_IN + llm.usage.output_tokens / 1e6 * PRICE_OUT
    labels = [("passed", "Cases passed (of 30)"), ("impostors_verified", "Impostors verified (target 0)"),
              ("impostors_high", "Impostors escalated_high (of 15)"), ("legit_high", "Legit escalated_high (target 0)"),
              ("legit_verified", "Legit verified_handoff (of 15)"), ("leaks", "PII leaks (agent utterances, target 0)"),
              ("en_passed", "English passed (of 22)"), ("es_passed", "Spanish passed (of 8)")]
    lines = [
        "# Eval results v2 (post hoc, after policy fix)", "",
        "**Post hoc.** This rerun happened after the Phase 3-FIX policy change (abandoning the call while the approval is "
        "pending is treated as an approval timeout; scam-tactic flags added). The cases and labels in eval/cases.yaml are "
        "unchanged since they were pre-registered, but these numbers are not pre-registered results. eval/RESULTS.md is the "
        "original, untouched run.", "",
        f"- Date: {date.today().isoformat()}",
        f"- Model: `{llm.model}` (no temperature control on this model; each run can differ)",
        f"- Cases commit: `{cases_hash}`", f"- Run commit: `{run_hash}`{' (working tree was dirty)' if dirty else ''}",
        f"- Runs: {runs} x 30 cases = {runs * 30} case runs", "",
        "## Headline numbers per run", "",
        "| Metric | " + " | ".join(f"Run {i + 1}" for i in range(runs)) + " | Min | Max |",
        "|---|" + "---|" * (runs + 2),
    ]
    for key, label in labels:
        vals = [m[key] for m in metrics]
        lines.append(f"| {label} | " + " | ".join(str(v) for v in vals) + f" | {min(vals)} | {max(vals)} |")
    lines += ["", f"- LLM calls: {llm.usage.calls} ({llm.usage.input_tokens} in / {llm.usage.output_tokens} out tokens)",
              f"- Approx. cost: ${cost:.3f} at an assumed ${PRICE_IN:.0f}/${PRICE_OUT:.0f} per million tokens (real price unverified)",
              "", "## Per case (pass count out of " + str(runs) + ")", "",
              "| Case | Expected | Passes | Outcomes per run | Reasons that varied between runs |", "|---|---|---|---|---|"]
    varying = []
    for idx, case in enumerate(cases):
        per = [rows[idx] for rows in all_rows]
        passes = sum(r["pass"] for r in per)
        outcomes = [r["actual"] for r in per]
        sets = [frozenset(r["reasons"] + r["flags"]) for r in per]
        varied = sorted(frozenset.union(*sets) - frozenset.intersection(*sets))
        lines.append(f"| {case['id']} | {case['expected_outcome']} | {passes}/{runs} | {', '.join(outcomes)} | {', '.join(varied)} |")
        if passes < runs:
            varying.append((case["id"], passes, outcomes, varied))
    lines += ["", f"## Cases passing fewer than {runs}/{runs}", ""]
    lines += [f"- {i}: {p}/{runs}; outcomes {', '.join(o)}; varying reasons/flags: {', '.join(v) or 'none'}"
              for i, p, o, v in varying] or ["None."]
    lines += ["", "## Tactic flags on A12 and A13", ""]
    for cid in ("A12", "A13"):
        idx = next(i for i, c in enumerate(cases) if c["id"] == cid)
        for n, rows in enumerate(all_rows, 1):
            tags = [f for f in rows[idx]["flags"] if f.startswith("TACTIC_")]
            lines.append(f"- {cid} run {n}: {', '.join(tags) or 'none'} (outcome {rows[idx]['actual']})")
    lines += ["", "## Tactic flags seen anywhere", ""]
    seen: dict[str, set[str]] = {}
    for rows in all_rows:
        for r in rows:
            for f in r["flags"]:
                if f.startswith("TACTIC_"):
                    seen.setdefault(f, set()).add(r["id"])
    lines += [f"- {f}: {', '.join(sorted(ids))}" for f, ids in sorted(seen.items())] or ["None."]
    lines += ["", "## Notes", "",
              "- L14 and A8 have the same inputs but opposite labels (recorded in cases.yaml before the first run). After the fix both "
              "end `escalated_high`, so L14 still fails by label whenever the other phone is a reverse-phone mismatch."]
    (EVAL / "RESULTS_v2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if "--v2" in sys.argv:
        main_v2()
        return
    load_dotenv()
    cases = yaml.safe_load((EVAL / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    members = load_members()
    llm = LLM()
    agent_log: list[dict] = []
    rows = []
    for case in cases:
        rows.append(run_case(case, members[case["persona"]], llm, agent_log))
        print(f"{case['id']:>4} {'PASS' if rows[-1]['pass'] else 'FAIL'} expected={rows[-1]['expected']} actual={rows[-1]['actual']}")
    out = EVAL / "out"
    out.mkdir(exist_ok=True)
    (out / "agent_utterances.json").write_text(json.dumps(agent_log), encoding="utf-8")
    (EVAL / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    leak = json.loads(subprocess.run([sys.executable, "-I", str(EVAL / "leakcheck.py")], capture_output=True,
                                     text=True, cwd=ROOT, env={**os.environ}).stdout)
    cases_hash = git("log", "-1", "--format=%h", "--", "eval/cases.yaml")
    dirty = bool(git("status", "--porcelain", "--", "guardline", "eval/run.py", "eval/leakcheck.py", "eval/cases.yaml"))
    write_results(rows, llm, leak, cases_hash, git("rev-parse", "--short", "HEAD"), dirty)
    print("leakcheck:", leak)
    print("passed:", sum(r["pass"] for r in rows), "/", len(rows))


if __name__ == "__main__":
    main()
