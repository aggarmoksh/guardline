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


def main() -> None:
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
