def test_twenty_personas_found(personas):
    assert len(personas) == 20
    assert list(personas) == [f"P{i:02d}" for i in range(1, 21)]
    for p in personas.values():
        assert {"identity", "reverse_phone", "fraud_finder"} <= set(p)
