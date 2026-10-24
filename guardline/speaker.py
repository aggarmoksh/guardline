"""Speaker: renders the policy's message_id into the caller's language, one short sentence.

Input is only a fixed English template, never member data. On any LLM failure the
English template is spoken.
"""
from guardline.llm import LLM, LLMError

TEMPLATES = {
    "greet": "Thank you for calling. To get started, how can I help you today?",
    "ask_identity": "To protect your account, please tell me your full name, date of birth, ZIP code and the last four digits of your Social Security number.",
    "ask_app_approval": "I have sent an approval request to your banking app. Please approve it, then tell me when you are done.",
    "ask_request": "Thank you. What would you like help with today?",
    "ask_repeat": "Sorry, I did not catch that. Could you say it again?",
    "handoff": "Thank you. I am passing you to a team member with your request now.",
    "escalate": "I am going to bring in a colleague who will help you from here.",
    "end_safe": "Thank you for calling. Goodbye.",
    "call_over": "This call has ended.",
}
LANG_NAMES = {"en": "English", "es": "Spanish"}
SYSTEM = ("Translate the sentence into the requested language for a phone call. Keep the meaning exactly, "
          "add nothing, and reply with one short sentence only.")


def render(llm: LLM | None, message_id: str, language: str) -> str:
    template = TEMPLATES[message_id]
    if language == "en" or llm is None:
        return template
    try:
        out = llm.complete(SYSTEM, f"Language: {LANG_NAMES[language]}\nSentence: {template}")
    except LLMError:
        return template
    return out if 0 < len(out) <= 300 else template
