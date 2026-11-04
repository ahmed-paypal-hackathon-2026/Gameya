from app.leash.ids import new_id


def test_prefixes_and_length() -> None:
    a = new_id("lpol")
    b = new_id("lsd")
    assert a.startswith("lpol_")
    assert b.startswith("lsd_")
    assert len(a) <= 32 and len(b) <= 32


def test_ids_unique() -> None:
    assert len({new_id("lsd") for _ in range(100)}) == 100
