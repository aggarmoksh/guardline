"""Redacting logger: persona values and PII-shaped strings never reach a log record."""
import logging
import re

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"(?<!\d)\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), "[PHONE]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*"), "[EMAIL]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b"), "[DATE]"),
    (re.compile(r"Bearer\s+[\w.~+/=-]+", re.I), "Bearer [TOKEN]"),
]

_sensitive: set[str] = set()


def register_sensitive(values) -> int:
    """Add literal strings (names, street, etc.) to redact. Returns how many were added."""
    added = {v for v in values if isinstance(v, str) and len(v) >= 3}
    _sensitive.update(added)
    return len(added)


def redact(text: str) -> str:
    # longest first so a full street is replaced before its substrings
    for value in sorted(_sensitive, key=len, reverse=True):
        text = re.sub(re.escape(value), "[REDACTED]", text, flags=re.I)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def get_logger(name: str = "guardline") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
