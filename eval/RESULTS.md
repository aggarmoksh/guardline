# Eval results

- Date: 2026-10-24
- Model: `claude-haiku-5-5` (sampling parameters are not accepted by this model's API, so runs use model defaults; temperature 0 was not possible)
- Cases commit: `221de77` (eval/cases.yaml, committed before the runner existed)
- Run commit: `312c299`
- Mode: text; spoken lines through the real interpreter, keypad values typed straight to the policy, live CRS sandbox

## Headline numbers

| Metric | Result | Target |
|---|---|---|
| Cases passed | 27 / 30 | n/a |
| Impostors verified | 0 / 15 | 0 |
| Impostors escalated_high | 11 / 15 | n/a |
| Legit escalated_high | 0 / 15 | 0 |
| Legit verified_handoff | 13 / 15 | n/a |
| PII leaks (agent utterances containing a members.json value) | 0 of 196 utterances | 0 |
| English passed | 19 / 22 | n/a |
| Spanish passed | 8 / 8 | n/a |
| LLM calls | 87 (84672 in / 15249 out tokens) | n/a |
| Approx. cost | $0.161 at an assumed $1/$5 per million tokens (real price unverified) | n/a |

## Per case

| Case | Group | Persona | Lang | Phone | Expected | Actual | Risk | Reasons | Flags | Result |
|---|---|---|---|---|---|---|---|---|---|---|
| L1 | legit | P01 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L2 | legit | P02 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L3 | legit | P03 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L4 | legit | P10 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L5 | legit | P19 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L6 | legit | P01 | es | member | verified_handoff | verified_handoff | LOW | VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L7 | legit | P02 | es | member | verified_handoff | verified_handoff | LOW | VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L8 | legit | P19 | es | member | verified_handoff | verified_handoff | LOW | VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L9 | legit | P01 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, NAME_FUZZY_MATCH, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L10 | legit | P10 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, NAME_FUZZY_MATCH, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L11 | legit | P03 | es | member | unverified_handoff | unverified_handoff | MEDIUM | CALLER_REQUESTED_HUMAN |  | PASS |
| L12 | legit | P02 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, IDENTITY_REENTRY_REQUESTED, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L13 | legit | P19 | en | other | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST |  | PASS |
| L14 | legit | P10 | en | other | unverified_handoff | ended_safe | LOW | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, CALLER_ENDED_CALL |  | FAIL |
| L15 | legit | P16 | en | member | verified_handoff | verified_handoff | LOW | CONTACT_CHANGE_STEP_UP, CRS_FRAUD_ALERT, STEP_UP_REQUIRED, APP_APPROVED_SIMULATED, VERIFIED, HANDOFF_WITH_REQUEST | FRAUD_ALERT, HUMAN_REVIEW | PASS |
| A1 | impostor | P01 | en | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER |  | PASS |
| A2 | impostor | P02 | en | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER |  | PASS |
| A3 | impostor | P10 | es | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER |  | PASS |
| A4 | impostor | P19 | es | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER |  | PASS |
| A5 | impostor | P01 | en | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS |  | PASS |
| A6 | impostor | P19 | en | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS |  | PASS |
| A7 | impostor | P02 | en | other | escalated_high | ended_safe | LOW | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, CALLER_ENDED_CALL |  | FAIL |
| A8 | impostor | P10 | en | other | escalated_high | ended_safe | LOW | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, CALLER_ENDED_CALL |  | FAIL |
| A9 | impostor | P12 | en | member | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, CRS_IDENTITY_MISMATCH_OR_SYNTHETIC, REVERSE_PHONE_NAME_DIFFERS |  | PASS |
| A10 | impostor | P13 | en | member | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, CRS_IDENTITY_MISMATCH_OR_SYNTHETIC, REVERSE_PHONE_NO_RECORD | NO_PHONE_RECORD | PASS |
| A11 | impostor | P14 | en | member | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, CRS_IDENTITY_MISMATCH_OR_SYNTHETIC, CRS_DECEASED_INDICATOR |  | PASS |
| A12 | impostor | P01 | en | member | unverified_handoff | unverified_handoff | MEDIUM | CONTACT_CHANGE_STEP_UP, IDENTITY_REENTRY_REQUESTED, IDENTITY_NOT_CONFIRMED |  | PASS |
| A13 | impostor | P02 | es | other | escalated_high | escalated_high | HIGH | CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, REDIRECT_TO_NEW_NUMBER, CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS |  | PASS |
| A14 | impostor | P10 | en | member | unverified_handoff | unverified_handoff | MEDIUM | CONTACT_CHANGE_STEP_UP, IDENTITY_REENTRY_REQUESTED, IDENTITY_NOT_CONFIRMED |  | PASS |
| A15 | impostor | P01 | es | other | escalated_high | escalated_high | HIGH | REDIRECT_TO_NEW_NUMBER |  | PASS |

## Failing cases

- L14: expected unverified_handoff, got ended_safe (CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, CALLER_ENDED_CALL)
- A7: expected escalated_high, got ended_safe (CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, CALLER_ENDED_CALL)
- A8: expected escalated_high, got ended_safe (CONTACT_CHANGE_STEP_UP, REVERSE_PHONE_NAME_DIFFERS, STEP_UP_REQUIRED, CALLER_ENDED_CALL)

## Notes (written after the run; cases and labels untouched)

- L14, A7 and A8 all ended `ended_safe` with CALLER_ENDED_CALL. Their final spoken line, "I am done.", was read by the interpreter as the caller hanging up (intent `goodbye`), so the simulated approval was never asked for. The approval-timeout rule was therefore not exercised by these three cases. This is a flaw in the case wording, not a measurement of the policy rule, and it is reported as a failure as registered.
- The L14 versus A8 label conflict (same inputs, different expected outcomes; recorded in cases.yaml before the run) is still untested for the same reason.
- No impostor was verified, no legitimate caller was escalated HIGH, and no agent utterance contained a member value.
- Temperature 0 was not possible: the API for this model rejects sampling parameters, so a rerun may differ in the interpreter's output.
