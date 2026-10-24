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
- **Phone ownership:** `POST /accurate-append/reverse` with `{phoneNumber}`. Measured in Phase 1A: a persona's own phone returns that persona (P01, P02, P16); P13's own phone returns no record; a made-up number still returns a person. The response has no match or confidence fields and the name filter is ignored. Ownership therefore means **the returned name equals the member record's name**, compared in code.
- **Fraud score:** `POST /fraud-finder/fraud-finder` (AtData FraudFinder) with fields `firstName, lastName, phoneNumber, email, ipAddress, address`. Measured: P01's own email gives a valid status (code 50) and score 11; invalid emails return NO score, with status codes 325 or 115. The score scale is unknown. Use the email validation status, never the score alone.
- **OTP and KBA (TUC3004) are not mocked.** No send-code or quiz request exists in the collection. Any step-up is **simulated and labeled "simulated" in the UI and README.**
- **CRS booth, Oct 23:** a CRS engineer said "you can check phone number and verify address and identify the person," and that they want to be surprised by uses they had not thought of.

## Policy (v1; the advisor finalizes it after Phase 1A)

| Signal | Source | Effect |
|---|---|---|
| Stated name, DOB, ZIP and last 4 do not match the member record (first time) | local member fixture | Ask once to re-enter. Never say which field failed |
| Same mismatch a second time | local member fixture | `unverified_handoff`, risk MEDIUM, IDENTITY_NOT_CONFIRMED |
| Name differs from the record but DOB, ZIP and last 4 are all exact | local member fixture | Pass if Jaro-Winkler >= 0.88 on the full name; reason NAME_FUZZY_MATCH. Never fuzzy when any digit field is wrong |
| SUBJECT_NOT_FOUND or HIGH | CRS identity | HIGH, escalate |
| Code 2201, 2202 or 2101 | CRS identity | HIGH: identity mismatch or synthetic |
| Code 2301 | CRS identity | HIGH: deceased indicator |
| Code 2401 | CRS identity | Step-up required + human review: fraud alert |
| Reverse lookup of the calling-from number returns a different name | CRS reverse phone | Step-up required |
| Reverse lookup of the calling-from number returns no record | CRS reverse phone | Step-up required + flag (P13 pattern) |
| Any contact-change request | policy | Step-up required (simulated app approval) |
| Simulated app approval denied or timed out, AND reverse phone returned a different name or no record | simulated + CRS | HIGH, escalate |
| Simulated app approval denied or timed out, reverse phone clean | simulated | `unverified_handoff`, risk MEDIUM |
| Caller asks to "send it to a new number" | interpreter enum | HIGH, escalate |
| Requested new email has invalid status | CRS FraudFinder | HIGH, escalate |
| Contact change on the same call as money movement or a credential reset | policy | HIGH regardless of verification |

DOB, ZIP and SSN last 4 are entered on the keypad on `/call` (asked one at a time, in that order). Typed values go straight to the policy, never through STT or the LLM, and are never logged. The CLI still takes them from script lines through the interpreter.

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

## Judging criteria heard at booths

- **Microsoft (from a TPM/FDE, Oct 23):** real-world usage, customer focus, impact, usability, reproducible in production, uniqueness and innovation, end to end (which he called the hardest), understanding rather than vibe coding, ethics, and a human in the loop.
- **CRS:** surprise us with a use we had not thought of.

**Understanding rule:** every phase report ends with a short plain-English "How it works" section, so the operator can explain any file to a judge.

## Decision log

- Oct 22: Option A. The step-up is a simulated banking-app approval, labeled. Reason: OTP and KBA are not mocked in the sandbox.
- Oct 22: "Pass" in the sandbox means SUBJECT_FOUND with no alert codes, plus a local member-record match. Reason: the score is a constant fixture.
- Oct 23: Speech goes behind an adapter, and Cartesia STT is a candidate. Reason: Cartesia has STT, TTS and voice agents in 40+ languages, which strengthens the Cartesia prize.
- Oct 24: Phase 1A verified (commit 9b8d397). Reverse phone gives an ownership check by comparing names in code. FraudFinder uses email status, not score. Solo submission confirmed by organizers.
- Oct 24 (Phase 1B, builder proposal, needs advisor confirmation): any contact-change request (mailing address, email, phone) requires the simulated app step-up even when every CRS signal is clean. Reason: otherwise the legitimate P01 call never exercises the step-up. Also: an invalid new email escalates (HIGH), and asking for a human before verification ends as escalated_high.
- Oct 24 (advisor, Phase 2): contact-change step-up accepted. Invalid new email is HIGH (accepted). A human request before verification ends as the new outcome `unverified_handoff` with risk MEDIUM, not HIGH.
- Oct 24 (advisor, Phase 2): bug fixed. An LLM failure used to look like 3 unclear turns. It now ends the call as `ended_safe` with reason LLM_UNAVAILABLE and an ESCALATED_TO_HUMAN flag, never "please repeat". Invalid model output still asks the caller to repeat.
- Oct 24 (Phase 2, builder): the caller's language chip locks the language; the interpreter's per-utterance detection no longer overrides it. Fixed Spanish renderings are cached and prewarmed at API startup. Anthropic calls run with thinking disabled (with it on, a 120-token cap returned an empty reply).
- Oct 24 (advisor, Phase 2-FIX): keypad for DOB, ZIP and last 4. Name match is exact after normalizing case, accents and spaces, or fuzzy (Jaro-Winkler >= 0.88 on the full name) only when DOB, ZIP and last 4 are all exact (reason NAME_FUZZY_MATCH). A stated-detail mismatch asks once to re-enter, then ends `unverified_handoff` MEDIUM (IDENTITY_NOT_CONFIRMED). HIGH comes only from CRS alert codes, REDIRECT_TO_NEW_NUMBER, CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS, and a denied or timed-out approval together with a reverse-phone mismatch.
- Oct 24 (Phase 2-FIX, builder interpretation, needs advisor confirmation): CRS SUBJECT_NOT_FOUND/HIGH and an invalid new email stay HIGH, because "HIGH only from" does not mention them and removing them would weaken safety. A denied or timed-out approval with a clean reverse phone ends `unverified_handoff` MEDIUM (APP_APPROVAL_DENIED or APP_APPROVAL_TIMEOUT).
