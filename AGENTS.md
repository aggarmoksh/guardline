# AGENTS.md: GuardLine (builder brief)

Read this whole file at the start of every session. It is the single source of truth for any coding agent (Claude Code, Codex/GPT, opencode). `CLAUDE.md` only points here. If something you are asked to do contradicts this file, stop and ask.

## Roles

- **Advisor (a separate chat):** makes the decisions and writes one phase prompt at a time.
- **Builder (you):** writes code and runs commands, only inside the phase you were given. You never make product or design decisions.
- **Operator (Moksh):** approves, tests on a real phone, and performs anything public or irreversible (pushing, deploying, making the repo public, submitting).

## What this is

GuardLine answers a credit union's member phone line (simulated as a web page on a phone). It verifies the caller with CRS identity and fraud APIs, refuses high-risk requests, and hands a human a one-screen case file. Solo build at Cal Hacks 13.0. **Sandbox data only.**

GuardLine never moves money, changes contact details or resets credentials. It decides how much the human should trust this caller, and why.

## The rule that shapes everything

**The model can always make a call safer, never less safe.**

- **Interpreter (LLM):** turns each caller utterance into one forced tool call. Its fields are enums or caller-stated slots (intent, language, stated name, stated DOB, stated ZIP, stated SSN last 4, stated new email or phone, request type).
- **Policy engine (Python, deterministic):** a state machine that calls CRS and sets verification status and risk. Only code can mark a caller verified.
- **Per-state allowlist:** each state exposes only the tools allowed in that state. `escalate_to_human` and `end_call_safely` are available in every state.
- **Speaker (LLM):** renders the policy's chosen `message_id` into the caller's language. Its input never contains member data on file.
- **Fairness by signature:** `score_risk()` does not take language, accent or name as arguments.
- **Credit status is never a risk input.** Bankruptcy, low score, liens, criminal and eviction data are not identity signals, and GuardLine does not call those CRS endpoints at all.

## Stack (locked; changes need advisor approval)

- **Backend:** Python 3.12, uv, FastAPI, SQLModel + SQLite
- **LLM:** behind an adapter `guardline/llm.py`. The default is the Anthropic API, Haiku tier; the model string comes from `.env`.
- **Speech:** behind an adapter `guardline/speech.py`. Cartesia STT + TTS is the candidate primary; Deepgram nova-3 STT + Aura TTS is the fallback. Phase 2 picks one on measured latency and Spanish accuracy.
- **Frontend:** Next.js + Tailwind with two routes: `/call` (phone, push-to-talk) and `/cases` (one-screen dashboard)
- **MCP:** official Python MCP SDK, stdio, as a thin wrapper over the same tool functions
- **Environment:** Windows 11, Git Bash, VS Code

## CRS sandbox facts (measured Oct 22; evidence lives outside the repo in `../probe/`)

- **Base URL:** `https://api-sandbox.stitchcredit.com:443/api`. Auth is `POST /users/login`, which returns a Bearer token. Keep the token in memory only and re-login once on a 401.
- **Identity:** `POST /transunion/identity-manager-verification/basic` with fields `firstName, lastName, ssn, dob, street1, city, state, zip`.
  - All 20 personas return MEDIUM, verificationPoint 30, SUBJECT_FOUND, address NO_MATCH and DOB NOT_FOUND. Results were identical across 2 runs each.
  - **The score is a constant fixture.** Never use riskIndicator or verificationPoint to tell unmodified personas apart.
  - Changing any detail flips the result to SUBJECT_NOT_FOUND and HIGH (DOB +5 years: 150; changed last name: 100).
  - Alert codes live at `addOnProducts[*].idManagerVerification.messages[*].code`:
    - P12 identity mismatch: 2201, 2202, 2101
    - P13 synthetic: 2201, 2202, 2101
    - P14 deceased: 2301, 2101
    - P16 fraud alert: 2401
    - Everyone else: none
- **Phone ownership:** `POST /accurate-append/reverse` with `{phoneNumber}`. It exists for every persona. Its behavior is untested (Phase 1A probes it).
- **Fraud score:** `POST /fraud-finder/fraud-finder` (AtData FraudFinder) with fields `firstName, lastName, phoneNumber, email, ipAddress, address`. The sample returned score 3 with a valid email; an altered email returned score 8 with an invalid email. The scale and threshold are unknown.
- **OTP and KBA (TUC3004) are not mocked.** No send-code or quiz request exists in the collection. Any step-up is **simulated and labeled "simulated" in the UI and README.**
- **CRS booth, Oct 23:** a CRS engineer said "you can check phone number and verify address and identify the person," and that they want to be surprised by uses they had not thought of.

## Policy (v1; the advisor finalizes it after Phase 1A)

| Signal | Source | Effect |
|---|---|---|
| Stated name, DOB, ZIP and last 4 do not match the member record | local member fixture | HIGH, escalate. Never say which field failed |
| SUBJECT_NOT_FOUND or HIGH | CRS identity | HIGH, escalate |
| Code 2201, 2202 or 2101 | CRS identity | HIGH: identity mismatch or synthetic |
| Code 2301 | CRS identity | HIGH: deceased indicator |
| Code 2401 | CRS identity | Step-up required + human review: fraud alert |
| Calling-from number not tied to the member | CRS reverse phone (pending probe) | Step-up required |
| Simulated app approval denied, timed out, or "send it to a new number" | simulated | HIGH, escalate |
| Requested new email scores invalid or high | CRS FraudFinder | Flag on the case file |
| Contact change on the same call as money movement or a credential reset | policy | HIGH regardless of verification |

The agent never reads back, confirms or denies any on-file value.

## Tools (typed; no raw query; none returns raw bureau JSON to the model)

| Tool | Returns |
|---|---|
| `open_case(language, request_type)` | `case_id` |
| `verify_identity(case_id, stated)` | `match` / `no_match` (never which field) |
| `check_fraud_signals(case_id)` | enum flags only |
| `request_app_approval(case_id)` | `approved` / `denied` / `timeout` (simulated) |
| `escalate_to_human(case_id, reason_code)` | `ok` |
| `end_call_safely(case_id, outcome)` | `message_id` |

## Phase gates

Do only the phase you were given. At its end, print exactly:
`Phase <id> complete, consult advisor with: <artifacts>`
Then stop. Never start the next phase on your own.

| Phase | Scope | Gate |
|---|---|---|
| 1A | Repo skeleton, memory files, CRS client, probe of reverse phone + FraudFinder | CRS client calls succeed; probe table written |
| 1B | Member fixtures, policy state machine, interpreter, text CLI | A text call ends correctly for P01 legitimate, P01 impostor and P13 |
| 2 | `/call` push-to-talk on iPhone, speech adapter, English + Spanish | One full Spanish call on the phone, with per-turn latency measured |
| 3 | Eval runner + 30 pre-registered cases + scoreboard | `eval/RESULTS.md` written |
| 4 | `/cases` dashboard, handoff, redaction audit, deploy (dry run, then execute) | The public URL works from the phone over cellular |
| 5 | MCP wrapper, Phase 3 fixes only, README, AI_DISCLOSURE.md | **FEATURE FREEZE** |

## Anti-features (do not build; write the idea in POSTLAUNCH.md instead)

- No telephony: no Twilio, no real phone numbers.
- No account actions of any kind.
- No raw query tool, and no tool that returns raw bureau JSON to the model.
- No credit, criminal, eviction or bankruptcy endpoints.
- No streaming, VAD or barge-in. Push-to-talk only.
- No dashboard user accounts; it uses one shared demo key.
- No real PII.
- No fine-tuning, no multi-agent setup, no hardware.
- No new features after the freeze.

## Privacy and secrets

- Keys go in `.env` only. `.env`, `*.db`, `logs/`, `fixtures/raw/` and anything under `../probe/` never enter git.
- Never print or log SSN, DOB, full address or phone numbers. Count sensitive strings, never print them.
- The logger redacts every persona field before writing.

## Project memory (update every phase)

- `AGENTS.md`: this file. Append decisions under "Decision log."
- `FRICTION_LOG.md`: entries in the form `date [area] tried / expected vs actual / impact / workaround`, written as things happen.
- `POSTLAUNCH.md`: deferred ideas.

## Reporting format (every phase)

Report:
- what changed (files, with line counts)
- the commit hash
- the numbers from your acceptance checks
- what you did **not** do or could not verify

Flag anything inconsistent outside your scope and stop. Never claim a check passed unless you ran it.

## Known pitfalls

- **iOS Safari:** the mic needs HTTPS and a user gesture. Unlock audio playback on the first tap.
- **Language detection:** short utterances mis-detect. Show a language chip the caller can tap to correct.
- **Wi-Fi:** venue Wi-Fi is unreliable. The demo phone uses cellular to reach the cloud backend.
- **Windows:** use `python -m`, forward slashes in Git Bash, and no symlinks.

## Decision log

- Oct 22: Option A. The step-up is a simulated banking-app approval, labeled. Reason: OTP and KBA are not mocked in the sandbox.
- Oct 22: "Pass" in the sandbox means SUBJECT_FOUND with no alert codes, plus a local member-record match. Reason: the score is a constant fixture.
- Oct 23: Speech goes behind an adapter, and Cartesia STT is a candidate. Reason: Cartesia has STT, TTS and voice agents in 40+ languages, which strengthens the Cartesia prize.
