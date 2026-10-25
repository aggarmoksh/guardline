# GuardLine

GuardLine answers a credit union's member phone line, decides how much a human should trust the caller, and hands that human a one-screen case file. It is a solo build for Cal Hacks 13.0.

- **Fictional credit union.** The demo institution is "Fogline Credit Union (fictional)".
- **Sandbox data only.** All identities come from the CRS sandbox test personas. There is no real personal data anywhere in this repo or demo.
- **Simulated member app.** The step-up approval is a simulated banking app page (`/approve`), clearly labelled as simulated. The CRS sandbox does not mock one-time codes or knowledge quizzes, so a real OTP or KBA step-up is not possible here.
- **Runs from a laptop behind a tunnel.** The demo is not deployed. A Cartesia Line phone number reaches the backend through a Cloudflare quick tunnel that `scripts/start_demo.sh` starts.

GuardLine never moves money, changes contact details or resets credentials. It decides how much the human should trust this caller, and why.

## The rule that shapes everything

**The model can always make a call safer, never less safe.**

| Part | Job |
|---|---|
| Interpreter (LLM) | One forced tool call per caller utterance. Returns enums and caller-stated slots (intent, language, stated name, request types, scam tactics). Output is validated in code. |
| Policy engine (Python, deterministic) | A state machine that calls CRS and sets verification status and risk. Only code can mark a caller verified. |
| Speaker | Words each fixed decision naturally. On the web a code guard rejects any reply that contains a member value, claims verification it does not have, or leaves out the required question, and falls back to a fixed sentence. |
| Phone line | A Cartesia Line agent is a relay: it sends each utterance to `POST /api/line/turn` and speaks naturally from `{intent, must_ask, state, say_fallback}`. The backend owns state, verification and wording rules. |

Credit status (bankruptcy, liens, scores) is never a risk input, and GuardLine does not call those CRS endpoints at all. `score_risk()` does not take language, accent or name as arguments.

## What it does on a call

1. **Who is calling.** A caller ID equal to a demo member's phone on file is a *known number* (smoother handling, never proof, since caller ID can be spoofed). Otherwise the member is chosen from the stated name among the demo members, and the details still have to match.
2. **Details.** Name, then date of birth, ZIP and last 4 of the SSN. On the web these are typed on a keypad and never go through speech or the model. On the phone line the Line docs describe no way to receive keypad tones, so each item is spoken one at a time and read back digit by digit for a yes or no. A name may differ slightly (fuzzy match) only when all three digit fields are exact. A mismatch asks once to try again, then hands the call to a person.
3. **CRS checks** (sandbox): TransUnion identity verification alert codes, AccurateAppend reverse phone (does the number belong to the member?), and AtData FraudFinder email status for a requested new email. Each case stores one plain-words evidence line per CRS call, with no bureau values.
4. **Step-up.** Any contact-change request, an unknown calling number, or a fraud-alert code needs the simulated member app approval. The member taps Approve or Deny; the call advances by itself. A Deny is the strongest fraud signal and always escalates.
5. **Outcomes.** `verified_handoff`, `unverified_handoff`, `escalated_high` or `ended_safe`. Every non-verified outcome speaks the same neutral line: a team member will call back at the number on file. Callers are never accused, and urgency is acknowledged.
6. **Scam tactics** heard on the call (urgency, authority claim, instruction injection, secrecy request, redirect request) become flags on the case. They only ever add flags: they never remove a check and never verify anyone.

Pages: `/call` (web push-to-talk fallback), `/approve` (simulated member app), `/cases` (dashboard with counters and CRS evidence), `/live` (big-screen live call view: masked chat, a checklist that lights up as checks run, and a banner derived from reason codes). `/cases` and `/live` need the shared demo key.

The full policy table and the decision log are in [AGENTS.md](AGENTS.md).

## Evaluation

30 cases (15 legitimate callers, 15 impostors, English and Spanish) were written and committed **before** the runner existed, then run once through the real interpreter, the real policy and the live CRS sandbox. Labels were never edited after results. Model: `claude-haiku-5-5` (its API rejects sampling parameters, so temperature 0 was not possible).

**Pre-registered run** ([eval/RESULTS.md](eval/RESULTS.md)): 27 of 30 passed.

| Metric | Result |
|---|---|
| Impostors verified | 0 of 15 |
| Legitimate callers escalated high | 0 of 15 |
| Legitimate callers verified | 13 of 15 |
| PII leaks in agent utterances | 0 of 196 |
| English / Spanish passed | 19 of 22 / 8 of 8 |

The 3 misses were one wording flaw in the cases (the line "I am done." was read as hanging up, so the approval timeout was never exercised) and are reported as failures as registered.

**Post hoc rerun after a policy fix** ([eval/RESULTS_v2.md](eval/RESULTS_v2.md)): 3 runs of the same 30 cases, labelled post hoc, not pre-registered. Every run: 29 of 30 passed, 0 impostors verified, 13 of 15 impostors escalated high, 1 legitimate caller escalated high, 0 PII leaks. The one remaining miss is a case whose label conflicts with another case that has identical inputs (a traveling member on another phone who never approves is indistinguishable from an impostor), kept as registered.

Later reruns after wording and interface changes (`eval/RESULTS_4afix_rerun.md`, `eval/RESULTS_4e_rerun.md`, `eval/RESULTS_4e2_rerun.md`, `eval/RESULTS_5a_rerun.md`) are also post hoc.

## CRS sandbox facts the design rests on

- The identity score is a constant fixture (MEDIUM, verification point 30 for all 20 personas), so it is never used. Only alert codes differ: 2201/2202/2101 identity mismatch or synthetic, 2301 deceased, 2401 fraud alert. Changing any detail flips the result to SUBJECT_NOT_FOUND.
- Reverse phone returns a person even for a made-up number and has no match or confidence fields, so ownership means the returned name equals the member's name, compared in code.
- FraudFinder is used for email status (valid or invalid), not its score.
- OTP and KBA are not mocked, so step-up is simulated and labelled as such.

## Known limits

- On the phone line the Line model speaks directly, so the code guard that protects the web speaker cannot check its exact words. It relies on the prompt and on a fixed fallback sentence. The model's temperature cannot be set.
- No keypad input on the phone line (spoken digits instead).
- English and Spanish only.
- The demo credit union, members, numbers and approvals are fictional or simulated. Nothing here is a production system.

## Run it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, a CRS sandbox account, an Anthropic API key, a Cartesia API key, `cloudflared`, Git Bash on Windows.

1. `cp .env.example .env` and fill in the keys (names only below; never commit `.env`):
   `CRS_USERNAME`, `CRS_PASSWORD`, `ANTHROPIC_API_KEY`, `LLM_MODEL`, `CARTESIA_API_KEY`, `SPEECH_PROVIDER` (`cartesia`), `DEMO_KEY`, `LINE_PROBE_TOKEN`; optional `OPERATOR_PHONE`, `LINE_SPANISH_NUMBER`, `DEEPGRAM_API_KEY`.
2. Create the local fixtures from the CRS sandbox Postman collection (not included in this repo): `python -m uv run python scripts/extract_personas.py` then `python -m uv run python scripts/build_members.py`. They are written to `fixtures/raw/`, which is git-ignored.
3. `cd web && npm ci`.
4. Tests: `python -m uv run pytest`.
5. Start everything (API, web, two tunnels, repoints the Line agents, health check, prints the URLs): `bash scripts/start_demo.sh`.

Docker alternative for the API and web (reads `.env`, mounts `fixtures/raw` read-only, no secrets in the images): `docker compose up --build`. The phone line additionally needs a public HTTPS URL for the Line tool and `scripts/line_setup.py`.

## Layout

- `guardline/`: policy, interpreter, speaker and phrasing guard, CRS client, API, phone-line relay, live view data, database
- `web/`: Next.js pages
- `eval/`: pre-registered cases, runner, leak check and results
- `scripts/`: persona extraction, Line setup, demo start
- `tests/`: policy, persistence, phone line, phrasing and live view tests
- `AGENTS.md`: the builder brief, policy table and decision log. `FRICTION_LOG.md`: what went wrong and what was done. `POSTLAUNCH.md`: deferred ideas.

See [AI_DISCLOSURE.md](AI_DISCLOSURE.md) for how AI was used.
