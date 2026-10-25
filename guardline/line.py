"""Helpers for the Cartesia Line relay channel: spoken digits, yes/no, candidate member, line wording.

The Line agent only relays. It sends each caller utterance to POST /api/line/turn and speaks the `say` text.
Everything else (state, verification, wording) stays in our backend. DTMF is not available to the agent
(the docs describe send_dtmf only), so DOB, ZIP and last 4 are spoken one item at a time and read back for confirmation.
"""
import re
from dataclasses import dataclass

from guardline.members import Member
from guardline.policy import jaro_winkler, normalize_name

DEMO_MEMBERS = ("P01", "P13", "P16")  # the members a Line caller can be matched to

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9",
    "cero": "0", "uno": "1", "un": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
    "siete": "7", "ocho": "8", "nueve": "9",
}
_YES = {"yes", "yeah", "yep", "yup", "correct", "right", "ok", "okay", "sure", "si", "sí", "correcto", "claro", "exacto"}
_NO = {"no", "nope", "wrong", "incorrect", "nah", "incorrecto", "negativo"}

PHRASES = {
    "en": {
        "ask_dob": "Please say your date of birth: month, day, then four-digit year.",
        "ask_zip": "Now please say your five-digit ZIP code.",
        "ask_ssn4": "Finally, please say the last four digits of your Social Security number.",
        "heard": "I heard {digits}. Is that right?",
        "retry": "Sorry, I did not catch that.",
        "yes_no": "Please say yes or no.",
        "fallback": "I am connecting you to a team member.",
    },
    "es": {
        "ask_dob": "Por favor, diga su fecha de nacimiento: mes, día y luego el año de cuatro dígitos.",
        "ask_zip": "Ahora, por favor, diga su código postal de cinco dígitos.",
        "ask_ssn4": "Por último, por favor diga los últimos cuatro dígitos de su número de Seguro Social.",
        "heard": "Escuché {digits}. ¿Es correcto?",
        "retry": "Lo siento, no le entendí.",
        "yes_no": "Por favor, diga sí o no.",
        "fallback": "Le estoy conectando con un miembro del equipo.",
    },
}


def spoken_digits(text: str) -> str:
    """Digits in an utterance, from numerals and from digit words (English and Spanish), in order."""
    out = []
    for token in re.findall(r"[\w']+", text.lower()):
        if token.isdigit():
            out.append(token)
        elif token in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[token])
    return "".join(out)


def yes_no(text: str) -> bool | None:
    words = set(re.findall(r"[\w']+", text.lower()))
    yes, no = bool(words & _YES), bool(words & _NO)
    if yes == no:
        return None
    return yes


_FILLERS = {"and", "is", "it", "its", "it's", "my", "the", "uh", "um", "hmm", "that", "that's", "thats", "zip", "code", "of",
            "please", "thanks", "thank", "you", "y", "es", "right", "ok"}
_REPEAT_PHRASES = ("repeat", "say that again", "say it again", "say again", "again please", "once more", "come again", "pardon",
                   "what did you say", "i missed", "missed that", "didn't catch", "didn't hear", "did not catch", "did not hear",
                   "could you say", "can you say", "say that one more", "what was that", "sorry?", "repita", "otra vez",
                   "de nuevo", "no escuch", "no entend", "perd")


def is_repeat_request(utterance: str) -> bool:
    """The caller asks to hear the last sentence again (for example after a read-back they missed)."""
    low = utterance.lower().strip()
    return bool(low) and any(p in low for p in _REPEAT_PHRASES)


def pure_digits(utterance: str) -> bool:
    """Only digits, digit words and filler: safe to handle without the language model."""
    tokens = re.findall(r"[\w']+", utterance.lower())
    return bool(tokens) and all(t.isdigit() or t in _DIGIT_WORDS or t in _FILLERS for t in tokens) and bool(spoken_digits(utterance))


def pure_yes_no(utterance: str) -> bool:
    """A bare yes or no (with filler only): safe to handle without the language model."""
    tokens = re.findall(r"[\w']+", utterance.lower())
    return bool(tokens) and all(t in _YES or t in _NO or t in _FILLERS for t in tokens) and yes_no(utterance) is not None


def read_back(digits: str) -> str:
    """Digits spaced out so a voice reads them one by one (and so they never look like an on-file value)."""
    return " ".join(digits)


def dob_digits(utterance: str, stated_dob_iso: str | None) -> str | None:
    """MMDDYYYY from the interpreter's date (month names, ordinals) or from spoken digits."""
    if stated_dob_iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", stated_dob_iso):
        y, m, d = stated_dob_iso.split("-")
        return m + d + y
    digits = spoken_digits(utterance)
    return digits if len(digits) == 8 else None


def item_digits(field: str, utterance: str, stated_dob_iso: str | None = None) -> str | None:
    """The caller's value for the item being collected, as typed-keypad digits, or None if it is not usable."""
    if field == "dob":
        return dob_digits(utterance, stated_dob_iso)
    digits = spoken_digits(utterance)
    return digits if len(digits) == (5 if field == "zip" else 4) else None


def best_candidate(stated_name: str, members: dict[str, Member]) -> Member | None:
    """The demo member whose name is closest to what the caller said (digits still have to match later)."""
    stated = normalize_name(stated_name)
    scored = [(jaro_winkler(stated, normalize_name(m.name)), m) for pid, m in members.items() if pid in DEMO_MEMBERS]
    return max(scored, key=lambda x: x[0])[1] if scored else None


def known_member(caller_number: str | None, members: dict[str, Member]) -> Member | None:
    """Caller ID equals a demo member's phone on file: a known number. Smoother handling only, never proof."""
    if not caller_number:
        return None
    return next((m for pid, m in members.items() if pid in DEMO_MEMBERS and m.is_known_number(caller_number)), None)


@dataclass
class LineState:
    """Per-call state that lives outside the policy: digit collection and the language of the number called."""
    language: str = "en"
    collecting: str | None = None  # dob | zip | ssn_last4 while a spoken item is being collected
    pending: str | None = None  # digits heard, waiting for the caller's yes
    known_number: bool = False
    caller_digits: str = ""  # kept in memory only, for the CRS reverse lookup; never logged or stored
    waits: int = 0
    used_llm: bool = True
    last_say: dict | None = None  # the last reply, so a caller who missed it can ask to hear it again
    facts: object = None  # DeferredFacts of an unknown caller, completed once a member is chosen
