"""Structure checks for the pre-registered eval cases. These do not touch labels."""
import collections
from pathlib import Path

import yaml

CASES = yaml.safe_load((Path(__file__).resolve().parents[1] / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]


def test_thirty_cases_fifteen_each_with_unique_ids():
    assert len(CASES) == 30 and len({c["id"] for c in CASES}) == 30
    assert collections.Counter(c["group"] for c in CASES) == {"legit": 15, "impostor": 15}


def test_every_case_has_an_allowed_outcome_and_steps():
    outcomes = {"verified_handoff", "unverified_handoff", "escalated_high", "ended_safe"}
    for c in CASES:
        assert c["expected_outcome"] in outcomes and c["steps"] and c["language"] in ("en", "es")
        assert c["calling_from"] in ("member", "other") and c["approval"] in ("approve", "deny", "timeout", "none")
