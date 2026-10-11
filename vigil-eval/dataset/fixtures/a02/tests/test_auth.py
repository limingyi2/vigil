# tests/test_auth.py
from src.auth import get_user


def test_get_user_returns_id():
    assert get_user() == 42
