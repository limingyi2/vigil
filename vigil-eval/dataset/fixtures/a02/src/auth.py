# src/auth.py
from .session import get_session


def get_user():
    session = get_session()
    return session["user_id"]
