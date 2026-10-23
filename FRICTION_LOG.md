# Friction log

Format: `date [area] tried / expected vs actual / impact / workaround`

2026-10-23 [env] tried `uv` / expected installed vs actual not on PATH (Python 3.10 default, 3.12 via py launcher) / impact none / workaround `pip install uv`, run as `python -m uv`; project pinned to 3.12 via .python-version
2026-10-23 [crs reverse] tried reverse_phone with P01 phone + P02 name filter / expected filter to matter vs actual P01 still returned (no match/confidence fields in response) / impact name filter is not a usable check / workaround compare returned name locally
2026-10-23 [crs reverse] tried made-up number 4155550123 / expected no person vs actual a record was returned / impact "person returned" alone is not proof of ownership / workaround compare returned name against the member record
2026-10-23 [crs fraudfinder] tried P01 own email / expected score 3 (per AGENTS.md) vs actual 11; invalid emails return no risk score at all / impact threshold unknown, score missing when email invalid / workaround treat email_status as the primary signal
