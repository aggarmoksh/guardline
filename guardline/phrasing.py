"""Natural wording on top of fixed decisions: "decisions fixed, words free".

The policy decides every state, check and outcome. This module only describes what must be conveyed (intent) and what must
be asked next (must_ask), lets a model word it naturally, and guards the result in code. Nothing here contains member data.
"""
import logging
import re
from collections import Counter

from guardline.llm import LLM, LLMError

log = logging.getLogger("guardline.phrasing")
GUARD_STATS: Counter = Counter()  # rejected replies by reason, for the logs and the eval report

# message_id -> (intent: what must be conveyed, no member data; must_ask: the exact question needed next, or None)
INTENTS: dict[str, tuple[str, str | None]] = {
    "greet": ("Welcome the caller warmly to the credit union phone line.", "How can I help you today?"),
    "ask_name": ("Say you need to confirm who they are before you can help with an account request. These are the caller's own details.", "What is your full name?"),
    "ask_dob": ("Thank them. This is part of confirming who they are, using the caller's own details.", "What is your date of birth: month, day and four-digit year?"),
    "ask_zip": ("Thank them. This is part of confirming who they are.", "What is your five-digit ZIP code?"),
    "ask_ssn4": ("Thank them. This is part of confirming who they are.", "What are the last four digits of your Social Security number?"),
    "ask_reenter": ("The details did not match. Do not say which part. Offer one more try.", "What is your full name?"),
    "ask_identity": ("Say you need to confirm who they are.", "What are your full name, date of birth, ZIP code and the last four digits of your Social Security number?"),
    "ask_app_approval": ("A confirmation request was just sent to the member's banking app. You will wait while they answer. Do not say it was approved.",
                         "Please open your banking app and tap approve or deny."),
    "ask_request": ("Thank them.", "What would you like help with today?"),
    "ask_repeat": ("You did not catch that.", "Could you say that again?"),
    "handoff": ("The call is being passed to a team member with the caller's request. Say a warm goodbye.", None),
    "escalate": ("A colleague will take over from here. Give no reason and say nothing about checks or results.", None),
    "end_safe": ("The call is ending. Say a warm goodbye.", None),
    "llm_down": ("There is a technical problem and a colleague will take over. Apologise briefly.", None),
    "still_waiting": ("You are still waiting for the member's answer in their banking app. Reassure the caller briefly. Say nothing about any outcome.",
                      "Let me know if you need more time or help finding the app."),
    "call_over": ("The call has ended.", None),
    # phone line only: the digit read-back and the small prompts around it (the must_ask is the exact sentence)
    "heard": ("Read back exactly the digits the caller just said so they can confirm them. Do not change or add any digit.", None),
    "digit_wrong": ("The caller said the digits you read back were wrong. Ask for that item again, briefly and kindly.", None),
    "yes_no": ("You need a yes or a no.", None),
    "retry": ("You did not catch that.", None),
    "fallback": ("A colleague will take over from here. Apologise briefly and give no reason.", None),
}

# At least one of these (lowercase) must appear in a natural reply that has to ask the question.
KEYWORDS: dict[str, dict[str, list[str]]] = {
    "en": {"greet": ["help"], "ask_name": ["name"], "ask_dob": ["birth"], "ask_zip": ["zip"], "ask_ssn4": ["last four", "four digits"],
           "ask_reenter": ["name"], "ask_identity": ["name"], "ask_app_approval": ["app"], "ask_request": ["help"],
           "ask_repeat": ["again", "repeat"]},
    "es": {"greet": ["ayud"], "ask_name": ["nombre"], "ask_dob": ["nacimiento"], "ask_zip": ["postal"], "ask_ssn4": ["cuatro"],
           "ask_reenter": ["nombre"], "ask_identity": ["nombre"], "ask_app_approval": ["app", "aplicaci"], "ask_request": ["ayud"],
           "ask_repeat": ["otra vez", "repit", "de nuevo"]},
}

_CLAIM = re.compile(
    r"\b(?:you(?:'re| are| have been| were|'ve been)|we(?:'ve| have)|i(?:'ve| have))\s+(?:now\s+|successfully\s+|been\s+)?"
    r"(?:verified|approved|confirmed|authenticated)\b"
    r"|\b(?:identity|account|you|caller|request)\s+(?:is|has been|was|are)\s+(?:now\s+)?(?:verified|confirmed|approved|authenticated)\b"
    r"|\bverification\s+(?:is\s+|was\s+)?(?:complete|completed|successful|passed)\b"
    r"|\b(?:ya\s+est[aá]\s+verificad|su identidad (?:ha sido|est[aá]) (?:verificada|confirmada)|ha sido (?:verificad|aprobad))",
    re.I)

SYSTEM = (
    "You are a warm, helpful phone representative at a credit union. Write ONE short spoken reply of at most two sentences "
    "(under 40 words). Briefly acknowledge what the caller just said if there is anything to acknowledge, convey the intent, "
    "and ask the required question in your own natural words. Never say or imply that the caller is verified, approved or "
    "confirmed, and never say a request was completed, unless the intent says so. Never invent account information. Do not "
    "use any names, numbers or personal details. For small talk answer in one short sentence, then ask the required question. "
    "Reply only with the words to speak, in {language}.")
LANG_NAMES = {"en": "English", "es": "Spanish"}


def intent_for(message_id: str) -> tuple[str, str | None]:
    return INTENTS.get(message_id, ("Convey the fixed sentence.", None))


def guard(text: str, *, message_id: str, language: str, verified: bool, leaks, required: str | None = None) -> str | None:
    """Return the rejection reason, or None if the reply may be spoken. Pure code: no model."""
    if not text or len(text) > 400:
        return "EMPTY_OR_LONG"
    if leaks(text):
        return "MEMBER_VALUE"
    if not verified and _CLAIM.search(text):
        return "CLAIMS_VERIFICATION"
    if required is not None:  # digits read back must appear exactly
        if required not in text:
            return "OMITS_MUST_ASK"
    elif INTENTS.get(message_id, (None, None))[1] is not None:
        words = KEYWORDS.get(language, {}).get(message_id)
        if words and not any(w in text.lower() for w in words):
            return "OMITS_MUST_ASK"
    return None


def natural_reply(llm: LLM, message_id: str, language: str, utterance: str | None, fixed: str, *, verified: bool,
                  leaks, required: str | None = None) -> tuple[str, str | None]:
    """One natural reply for a fixed decision, or the fixed sentence (with the reason) if the guard rejects it.

    `utterance` must already be free of member values (the caller's own words after redaction).
    """
    intent, must_ask = intent_for(message_id)
    user = f"Intent: {intent}\nRequired question: {must_ask or 'none'}\nCaller's last words: {utterance or '(none)'}"
    if required:
        user += f"\nSay these digits exactly as written, in order: {required}"
    try:
        text = llm.complete(SYSTEM.format(language=LANG_NAMES[language]), user, max_tokens=120).strip().strip('"')
    except LLMError:
        return fixed, "LLM_ERROR"
    reason = guard(text, message_id=message_id, language=language, verified=verified, leaks=leaks, required=required)
    if reason:
        GUARD_STATS[reason] += 1
        log.warning("GUARD_REJECTED reason=%s message=%s", reason, message_id)
        return fixed, reason
    return text, None
