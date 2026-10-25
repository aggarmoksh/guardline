# Eval rerun with natural wording (Phase 4E, post hoc)

One run of the 30 cases with natural agent wording (a model words each fixed decision; a code guard falls back to the fixed sentence). Decisions come from the policy as before. Cases and labels are unchanged; eval/RESULTS.md and eval/RESULTS_v2.md are untouched.

- Date: 2026-10-24
- Model: `claude-haiku-5-5`
- Run commit: `7506495`
- Cases passed: 29 / 30; impostors verified 0; impostors escalated_high 13 / 15; legit escalated_high 1; legit verified 13; PII leaks 0
- LLM calls: 283; approx. cost $0.291 (assumed price)

## Outcome changes versus RESULTS_v2 (all 3 runs agreed there)

None. Every case ended with the same outcome as in all 3 runs of RESULTS_v2.

## Per case

| Case | Expected | Actual | Reasons | Result |
|---|---|---|---|---|
| L1 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L2 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L3 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L4 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L5 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L6 | verified_handoff | verified_handoff | VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L7 | verified_handoff | verified_handoff | VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L8 | verified_handoff | verified_handoff | VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L9 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, NAME_FUZZY_MATCH, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L10 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, NAME_FUZZY_MATCH, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L11 | unverified_handoff | unverified_handoff | CALLER_REQUESTED_HUMAN | PASS |
| L12 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, IDENTITY_REENTRY_REQUESTED, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L13 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| L14 | unverified_handoff | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, ABANDONED_DURING_STEP_UP, APP_APPROVAL_TIMEOUT | FAIL |
| L15 | verified_handoff | verified_handoff | CONTACT_CHANGE_STEP_UP, CRS_FRAUD_ALERT, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | PASS |
| A1 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER | PASS |
| A2 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER | PASS |
| A3 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER | PASS |
| A4 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER | PASS |
| A5 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS | PASS |
| A6 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS | PASS |
| A7 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, ABANDONED_DURING_STEP_UP, APP_APPROVAL_TIMEOUT | PASS |
| A8 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, ABANDONED_DURING_STEP_UP, APP_APPROVAL_TIMEOUT | PASS |
| A9 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, CRS_IDENTITY_MISMATCH_OR_SYNTHETIC, REVERSE_PHONE_NAME_DIFFERS | PASS |
| A10 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, CRS_IDENTITY_MISMATCH_OR_SYNTHETIC, REVERSE_PHONE_NO_RECORD | PASS |
| A11 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, CRS_IDENTITY_MISMATCH_OR_SYNTHETIC, CRS_DECEASED_INDICATOR | PASS |
| A12 | unverified_handoff | unverified_handoff | CONTACT_CHANGE_STEP_UP, IDENTITY_REENTRY_REQUESTED, IDENTITY_NOT_CONFIRMED | PASS |
| A13 | escalated_high | escalated_high | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER | PASS |
| A14 | unverified_handoff | unverified_handoff | CONTACT_CHANGE_STEP_UP, IDENTITY_REENTRY_REQUESTED, IDENTITY_NOT_CONFIRMED | PASS |
| A15 | escalated_high | escalated_high | REDIRECT_TO_NEW_NUMBER | PASS |

## Notes (written after the run)

- Rerun after the call-review fixes (request type "other" only counts when the caller makes a request; wording after a wrong read-back; Line prompts). Natural wording on. Cases and labels unchanged.
- Natural replies rejected by the guard (GUARD_REJECTED): **0**. PII leaks: 0. No outcome changed against RESULTS_v2.

