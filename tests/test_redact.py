import logging

from guardline import redact
from guardline.crs import log as crs_log


def _persona_values(personas):
    for p in personas.values():
        for body in p.values():
            if isinstance(body, dict):
                for v in body.values():
                    if isinstance(v, dict):
                        yield from (str(x) for x in v.values())
                    else:
                        yield str(v)


def test_logger_output_contains_no_persona_values(personas, caplog):
    values = set(_persona_values(personas))
    redact.register_sensitive(values)
    crs_log.propagate = True
    with caplog.at_level(logging.DEBUG, logger="guardline.crs"):
        for v in values:
            crs_log.info("value %s and again %r", v, v)
        crs_log.info("Authorization: Bearer abc.def.ghi")
    out = "\n".join(r.getMessage() for r in caplog.records)
    assert "abc.def.ghi" not in out
    leaked = [v for v in values if len(v) >= 3 and v.lower() in out.lower()]
    assert leaked == [] or len(leaked) == 0, f"{len(leaked)} persona values leaked"


def test_pattern_redaction_without_registration():
    out = redact.redact("ssn 123-45-6789 phone 4155550123 mail a@b.com dob 1980-01-02")
    for raw in ("123-45-6789", "4155550123", "a@b.com", "1980-01-02"):
        assert raw not in out
