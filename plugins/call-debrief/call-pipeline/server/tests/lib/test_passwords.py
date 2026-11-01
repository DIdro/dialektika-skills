from app.lib.passwords import hash_password, verify_password


def test_roundtrip():
    h = hash_password("s3cret-пароль")
    assert h != "s3cret-пароль"
    assert verify_password("s3cret-пароль", h)


def test_wrong_password():
    h = hash_password("right")
    assert not verify_password("wrong", h)


def test_none_hash_is_false():
    # Юзер без пароля (Telegram-only) не может войти по паролю
    assert not verify_password("anything", None)
