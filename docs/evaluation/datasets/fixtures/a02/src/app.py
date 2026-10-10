# src/app.py
from .auth import get_user


def handler():
    user = get_user()
    return {"user": user}
