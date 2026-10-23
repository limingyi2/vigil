"""Pure helpers for reading values from an HTTP request URL."""


def query_param(path, key):
    """Return the first decoded value for a query key, or an empty string.

    Keep the legacy server helper's behavior: blank values are omitted by
    ``parse_qs``, repeated values use the first item, and malformed input is
    treated as a missing value.
    """
    from urllib.parse import urlparse, parse_qs
    try:
        q = parse_qs(urlparse(path).query)
        return (q.get(key) or [""])[0]
    except Exception:
        return ""
