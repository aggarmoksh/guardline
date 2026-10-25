# AI disclosure

GuardLine was built with heavy AI assistance, and it uses AI models at run time. This file says where.

## How it was built

- **Roles** (see [AGENTS.md](AGENTS.md)): an *advisor* (a separate Claude chat) made the product and design decisions and wrote one phase prompt at a time; a *builder* (Claude Code, Claude Sonnet 5.5, per the commit attribution lines) wrote the code and ran the commands inside each phase; the *operator* (the human author) approved decisions, tested on real phones, and was the only one who performed anything public or irreversible.
- Almost all of the code, tests, eval runner and documentation were written by the builder. The human author set the goals, made the rulings recorded in the decision log, ran every live phone test, and reviewed the results.
- Quality was checked by automated tests, a pre-registered 30-case evaluation (labels committed before the runner existed and never edited afterwards), post hoc reruns labelled as such, and real calls reviewed from call transcripts.
- Mistakes the AI made, and what changed because of them, are in [FRICTION_LOG.md](FRICTION_LOG.md) (for example: a hang-up that closed a case with no human review, a model that tagged plain answers as requests, and a few times the builder printed a value it should not have).

## What AI does at run time

- **Interpreter and speaker:** Claude (`claude-haiku-5-5` through the Anthropic API). The interpreter turns each utterance into enums and caller-stated slots; the speaker words a decision the policy has already made. Neither can verify a caller.
- **Phone agent:** a Cartesia Line managed agent (model `claude-haiku-5.5` on Cartesia) that relays utterances to the backend and speaks from the backend's reply.
- **Speech:** Cartesia speech-to-text (`ink-whisper`) and text-to-speech (`sonic-3`).
- **Decisions are not AI decisions.** Verification, risk and every outcome come from deterministic Python code. The model can only make a call safer, never less safe.

## Data

- Only CRS sandbox test personas are used. No real personal data was used, stored or sent. The credit union ("Fogline Credit Union (fictional)"), its members, the phone numbers on file and the member app approval are fictional or simulated.
- The member records and persona fixtures are generated locally from the sandbox collection and are git-ignored.

## Limits of this disclosure

- Model and tool versions are the ones recorded in this repo's documents at the time of writing.
- The phone agent's exact spoken words are produced by a third-party model and cannot be checked by our code while the call is live.
