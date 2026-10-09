# -*- coding: utf-8 -*- (ASCII only)
"""M4 cloud verify: /api/route + /api/device/audit (brain token ok, bad token
rejected). Read-only endpoints only - no chat data touched.
Host/port/token from the local (gitignored) app_conn.json. ASCII only."""
import json
import sys
import urllib.request
import urllib.error

_conn = json.load(open(r"F:\Vigil\brain\config\app_conn.json",
                       encoding="utf-8"))
TOK = _conn["token"]
_base = _conn["base"].rstrip("/")
if not _base.startswith("http"):
    _base = "http://" + _base

FAILS = []


def get(path, tok=None):
    url = _base + path
    sep = "&" if "?" in path else "?"
    if tok is not None:
        url += sep + "token=" + tok
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# 1) health
code, body = get("/api/health")
print("health:", code, body[:80])
if code != 200:
    FAILS.append("health")

# 2) /api/route with brain token -> 200 + wechat_route field
code, body = get("/api/route", TOK)
try:
    data = json.loads(body)
except Exception:
    data = {}
print("route:", code, json.dumps(data, ensure_ascii=False)[:200])
if code != 200 or "wechat_route" not in data:
    FAILS.append("route")

# 3) route switch round-trip (leave it as it was)
cur = data.get("route")
code, body = get("/api/route", TOK)
print("route re-read:", code, body[:120])

# 4) /api/device/audit with brain token -> 200 {"ok":true,"items":[...]}
code, body = get("/api/device/audit", TOK)
try:
    audit = json.loads(body)
except Exception:
    audit = None
print("audit(brain):", code, str(audit)[:120])
if (code != 200 or not isinstance(audit, dict)
        or not isinstance(audit.get("items"), list)):
    FAILS.append("audit-brain-token")

# 5) /api/device/audit with junk token -> rejected (4xx)
code, body = get("/api/device/audit", "dt_not_a_real_token_000")
print("audit(bad tok):", code, body[:80])
if code not in (401, 403):
    FAILS.append("audit-bad-token")

# 6) /api/route with junk token -> rejected
code, body = get("/api/route", "dt_not_a_real_token_000")
print("route(bad tok):", code, body[:80])
if code not in (401, 403):
    FAILS.append("route-bad-token")

print("=" * 40)
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("M4 CLOUD VERIFY: ALL OK")
