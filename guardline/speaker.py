"""Speaker: renders the policy's message_id into the caller's language, one short sentence.

Input is only a fixed English template, never member data. On any LLM failure the
English template is spoken.
"""
from guardline.llm import LLM, LLMError

TEMPLATES = {
    "greet": "Thank you for calling. To get started, how can I help you today?",
    "ask_identity": "To protect your account, please tell me your full name, date of birth, ZIP code and the last four digits of your Social Security number.",
    "ask_name": "To protect your account, please say your full name.",
    "ask_dob": "Thank you. Please enter your date of birth on the keypad: month, day, then four-digit year.",
    "ask_zip": "Now please enter your five-digit ZIP code on the keypad.",
    "ask_ssn4": "Finally, please enter the last four digits of your Social Security number on the keypad.",
    "ask_reenter": "I could not confirm those details. Let us try once more. Please say your full name.",
    "ask_app_approval": "I have sent an approval request to your banking app. Please approve it, then tell me when you are done.",
    "ask_request": "Thank you. What would you like help with today?",
    "ask_repeat": "Sorry, I did not catch that. Could you say it again?",
    "handoff": "Thank you. I am passing you to a team member with your request now.",
    "escalate": "I am going to bring in a colleague who will help you from here.",
    "end_safe": "Thank you for calling. Goodbye.",
    "llm_down": "I am sorry, I am having a technical problem. I am connecting you to a colleague now.",
    "call_over": "This call has ended.",
}
LANG_NAMES = {"en": "English", "es": "Spanish"}
SYSTEM = ("Translate the sentence into the requested language for a phone call. Keep the meaning exactly, "
          "add nothing, and reply with one short sentence only.")


_cache: dict[tuple[str, str], str] = {}  # message_id is a fixed sentence, so a rendering is reusable


def render(llm: LLM | None, message_id: str, language: str) -> str:
    template = TEMPLATES[message_id]
    if language == "en" or llm is None:
        return template
    if (message_id, language) in _cache:
        return _cache[(message_id, language)]
    try:
        out = llm.complete(SYSTEM, f"Language: {LANG_NAMES[language]}\nSentence: {template}")
    except LLMError:
        return template  # not cached, so a later call can retry
    if not 0 < len(out) <= 300:
        return template
    _cache[(message_id, language)] = out
    return out


def prewarm(llm: LLM, language: str = "es") -> None:
    for message_id in TEMPLATES:
        render(llm, message_id, language)


_free_cache: dict[tuple[str, str], str] = {}


def translate(llm: LLM | None, text: str, language: str) -> str:
    """Translate one fixed English sentence (cached). English, no model, or any failure returns the English sentence."""
    if language == "en" or llm is None:
        return text
    key = (text, language)
    if key in _free_cache:
        return _free_cache[key]
    try:
        out = llm.complete(SYSTEM, f"Language: {LANG_NAMES[language]}\nSentence: {text}")
    except LLMError:
        return text
    if not 0 < len(out) <= 300:
        return text
    _free_cache[key] = out
    return out
