# src/session.py

def get_session():
    # Builds the per-request session dict.
    # NOTE: the caller in auth.py expects key "user_id", but we currently
    # publish the field as "uid".
    return {
        "uid": 42,
        "ts": 1700000000,
        "role": "user",
    }
