# Post-launch ideas

Deferred ideas go here.

## Deferred (Phase 5A rulings and earlier)

- **AWS deploy** (cut for the hackathon): run the docker compose stack on a small instance behind HTTPS, with a stable domain so the Cartesia tool URL stops changing; move SQLite to a managed database if more than one API instance is needed.
- **Hindi and Punjabi:** needs a speech model per language (Line language is fixed per agent), translated wording and voices, and a number per language (the free plan holds 2 numbers). Punjabi voice support is unchecked.
- **DTMF keypad input on the phone line:** the Line docs describe sending tones only. If Cartesia adds received digits, replace the spoken digit read-back with keypad entry.
- **Outbound callback calls:** the case file records CALLBACK_QUEUED and the member app shows a simulated notice. A real callback would be an outbound Line call to the number on file (US numbers only), triggered by the human who picks up the case.
- **Names on the phone line:** guard the Line model's words in code (today only the web speaker has the guard); needs a Line feature that lets the backend speak the final text, or a post-call audit of the transcript.
- **Personalised greeting for a known number:** needs a ruling, because echoing the name confirms an on-file value.

## From the hve-core review of guardline/policy.py (docs/hve-core-review.md)

- The review reported no security vulnerabilities, so there was nothing to fix and no code changed.
- Its summary did not include a correctness assessment, so correctness is not independently cleared by that review. Follow-up: a separate correctness review of `guardline/policy.py` (state transitions, the late-request step-up path, the abandon and idle rules), ideally with property-style tests over the policy table.
