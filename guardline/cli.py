"""Text call simulator: python -m guardline.cli --persona P01 --calling-from member --app approve --lang en [--script f.txt]"""
import argparse
import json
from pathlib import Path

from guardline import interpreter, speaker
from guardline.facts import LiveFacts
from guardline.llm import LLM
from guardline.members import load_members
from guardline.policy import Policy
from guardline.redact import redact, register_sensitive


def _caller_lines(script: str | None, member):
    if script:
        text = Path(script).read_text(encoding="utf-8")
        fills = {"name": member.name, "dob": member.dob, "zip": member.zip, "ssn4": member.ssn_last4}
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                yield line.format(**fills)
    else:
        while True:
            try:
                yield input("Caller: ")
            except EOFError:
                return


def run(args) -> dict:
    member = load_members()[args.persona]
    register_sensitive(member.sensitive_values())  # agent lines and the case file are redacted from here on
    llm = LLM()
    policy = Policy(member, LiveFacts(args.persona, member.name), args.calling_from,
                    lambda _case_id: args.app, args.lang)

    def agent(message_id: str) -> None:
        print("Agent:", redact(speaker.render(llm, message_id, policy.case.language)))

    turn = policy.start()
    agent(turn.message_id)
    for line in _caller_lines(args.script, member):
        if args.script:
            print("Caller:", line)  # the caller's own words
        it = interpreter.interpret(llm, line, policy.case.state)
        turn = policy.repeat_needed() if it is None else policy.handle(it)
        agent(turn.message_id)
        if turn.done:
            break
    case = policy.case.public()
    print("\nFinal case:", redact(json.dumps(case)))
    print(f"LLM usage: {llm.usage.calls} calls, {llm.usage.input_tokens} in / {llm.usage.output_tokens} out tokens")
    return case


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True)
    ap.add_argument("--calling-from", choices=["member", "other"], default="member")
    ap.add_argument("--app", choices=["approve", "deny", "timeout"], default="approve")
    ap.add_argument("--lang", choices=["en", "es"], default="en")
    ap.add_argument("--script")
    args = ap.parse_args()
    args.app = {"approve": "approved", "deny": "denied", "timeout": "timeout"}[args.app]
    run(args)


if __name__ == "__main__":
    main()
