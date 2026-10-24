# Friction log

Format: `date [area] tried / expected vs actual / impact / workaround`

2026-10-23 [env] tried `uv` / expected installed vs actual not on PATH (Python 3.10 default, 3.12 via py launcher) / impact none / workaround `pip install uv`, run as `python -m uv`; project pinned to 3.12 via .python-version
2026-10-23 [crs reverse] tried reverse_phone with P01 phone + P02 name filter / expected filter to matter vs actual P01 still returned (no match/confidence fields in response) / impact name filter is not a usable check / workaround compare returned name locally
2026-10-23 [crs reverse] tried made-up number 4155550123 / expected no person vs actual a record was returned / impact "person returned" alone is not proof of ownership / workaround compare returned name against the member record
2026-10-23 [crs fraudfinder] tried P01 own email / expected score 3 (per AGENTS.md) vs actual 11; invalid emails return no risk score at all / impact threshold unknown, score missing when email invalid / workaround treat email_status as the primary signal
2026-10-24 [crs reverse] tried reverse_phone(4155550123), compared returned name with P01's name / expected possible collision vs actual name equals P01's = false (1 record, different person) / impact none, ownership check by name compare is safe for this case / workaround n/a
2026-10-24 [crs reverse] P12's own phone returns a different name than the member record (name_matches_member=false) while P12 also carries codes 2201/2202/2101 / impact none (already HIGH on codes) / workaround none
2026-10-24 [policy] Legit P01 call from the member phone has no step-up trigger in the table, so --app approve would never be used / impact script "approves app" meaningless / workaround added a row: any contact-change request requires step-up (CONTACT_CHANGE_STEP_UP). Advisor to confirm
2026-10-24 [llm] tried speaker translate with max_tokens=120 / expected Spanish sentence vs actual empty text (model spent the budget on a thinking block), silently fell back to English / impact Spanish calls spoke English / workaround thinking disabled on all adapter calls
2026-10-24 [cartesia docs] tried docs for batch STT and voice list / expected HTTP reference vs actual only SDK examples and a WebSocket page (OpenAPI file blocked by a bot check) / impact none / workaround read the installed cartesia SDK; models ink-whisper (STT) and sonic-3 (TTS) confirmed by live calls
2026-10-24 [deepgram] DEEPGRAM_API_KEY is empty / impact Deepgram fallback code (nova-3 listen, aura-2 speak) written from docs but never called / workaround none until a key is added
2026-10-24 [cli] --lang es with an English script: the interpreter detected English and flipped the case back to en / impact the Spanish check was not Spanish / workaround language chip now locks language
