"""Sample deliberately-vulnerable API for the v0.8.0 API security testing suite.

This Flask app exposes pairs of endpoints — one VULNERABLE to a given
OWASP API Top 10 category, one SAFE — so v0.8.0's `api_security` rules
can be exercised against known ground truth. Each rule's PR (PR-2
through PR-5 per `docs/prd-v0.8-api-security.md` §6) must:

  - flag the corresponding vulnerable endpoint
  - NOT flag the corresponding safe endpoint

Mapping:

  OWASP API1 (BOLA)            : GET  /vuln/orders/<id>   (no ownership check)
                                 GET  /safe/me/orders     (uses JWT sub claim)

  OWASP API2 (Broken Auth)     : GET  /vuln/profile       (accepts alg:none + ignores exp)
                                 GET  /safe/profile       (validates signature + exp + alg)

  OWASP API3 (Mass Assignment) : POST /vuln/signup        (persists every field in body)
                                 POST /safe/signup        (whitelists allowed fields)

  OWASP API5 (Function Auth)   : GET  /vuln/admin/users   (auth'd but no role check)
                                 GET  /safe/admin/users   (checks role claim)

  OWASP API8 (Misconfig)       : GET  /vuln/data          (loose CORS, missing headers, stack trace on err)
                                 GET  /safe/data          (strict CORS, headers set, generic error)

Auth fixture: three pre-seeded users — alice (id=1, role=user), bob
(id=2, role=user), admin (id=99, role=admin). `POST /login` returns
a HS256 JWT signed with `_JWT_SECRET`.

DO NOT deploy this app anywhere reachable from the internet. Bound to
127.0.0.1 by default. The vulnerabilities are intentional and exist
solely to give the v0.8.0 rule scanner positive ground truth.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt  # PyJWT
from flask import Flask, jsonify, make_response, request


_JWT_SECRET = "vulnerable-api-test-secret-do-not-deploy"
_JWT_ALG = "HS256"

# Pre-seeded users. password is plaintext on purpose — this is a test
# fixture, not production code. Each user has a separate set of orders.
_USERS: dict[str, dict[str, Any]] = {
    "alice": {"id": 1, "role": "user", "password": "alice123"},
    "bob":   {"id": 2, "role": "user", "password": "bob123"},
    "admin": {"id": 99, "role": "admin", "password": "admin123"},
}

# Orders keyed by id. Pre-seeded so BOLA can be demonstrated cross-user.
_ORDERS: dict[int, dict[str, Any]] = {
    1: {"id": 1, "owner_id": 1, "item": "alice's coffee", "total": 4.50},
    2: {"id": 2, "owner_id": 2, "item": "bob's pizza",    "total": 18.00},
    3: {"id": 3, "owner_id": 1, "item": "alice's book",   "total": 12.00},
}

# Users created via /vuln/signup or /safe/signup land here. Used by the
# smoke test to verify whether the vuln endpoint actually persisted a
# tampered field.
_SIGNUPS: list[dict[str, Any]] = []


app = Flask(__name__)


# ---- Auth helpers ----------------------------------------------------------

def _issue_jwt(user: dict[str, Any], *, exp_offset: int = 3600,
               alg: str = _JWT_ALG, secret: str = _JWT_SECRET) -> str:
    payload = {
        # RFC 7519 requires `sub` to be a StringOrURI; PyJWT 2.x's strict
        # decoder rejects integer subs. Encode as string here and parse
        # back to int in the handlers that compare it to owner ids.
        "sub": str(user["id"]),
        "username": next(k for k, v in _USERS.items() if v["id"] == user["id"]),
        "role": user["role"],
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    return jwt.encode(payload, secret, algorithm=alg)


def _decode_strict(token: str) -> dict[str, Any] | None:
    """Validates signature, algorithm, expiry. Returns None on failure."""
    try:
        return jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=[_JWT_ALG],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None


def _decode_loose(token: str) -> dict[str, Any] | None:
    """VULNERABLE decoder — accepts alg:none and ignores exp.

    Mirrors a class of real-world bugs where teams disable verification
    "temporarily" and forget to re-enable. Used by /vuln/profile.
    """
    try:
        # alg:none — happily accepts unsigned tokens
        return jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["none", _JWT_ALG],
        )
    except jwt.PyJWTError:
        return None


def _bearer() -> str | None:
    h = request.headers.get("Authorization", "")
    if h.lower().startswith("bearer "):
        return h.split(" ", 1)[1].strip()
    return None


# ---- /login (helper, not a vuln target) -----------------------------------

@app.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    user = _USERS.get(body.get("username", ""))
    if not user or user["password"] != body.get("password", ""):
        return jsonify({"error": "invalid_credentials"}), 401
    return jsonify({"token": _issue_jwt(user)})


# ---- /health (for smoke-test boot-readiness check) ------------------------

@app.get("/health")
def health():
    return jsonify({"ok": True})


# ---- OWASP API1 (BOLA) ----------------------------------------------------

@app.get("/vuln/orders/<int:order_id>")
def vuln_orders(order_id: int):
    """VULNERABLE: returns any order to any authenticated user.

    Real-world equivalent: an /orders/{id} endpoint that checks "is the
    user logged in?" but not "is this order theirs?" — the textbook
    OWASP API1 (BOLA / IDOR) shape.
    """
    token = _bearer()
    if not token or _decode_strict(token) is None:
        return jsonify({"error": "unauthorized"}), 401
    order = _ORDERS.get(order_id)
    if not order:
        return jsonify({"error": "not_found"}), 404
    return jsonify(order)  # ← no ownership check; here be the bug


@app.get("/safe/me/orders")
def safe_my_orders():
    """SAFE: returns ONLY orders owned by the caller."""
    token = _bearer()
    claims = _decode_strict(token) if token else None
    if not claims:
        return jsonify({"error": "unauthorized"}), 401
    mine = [o for o in _ORDERS.values() if o["owner_id"] == int(claims["sub"])]
    return jsonify({"orders": mine})


# ---- OWASP API2 (Broken Authentication) -----------------------------------

@app.get("/vuln/profile")
def vuln_profile():
    """VULNERABLE: accepts alg:none JWTs, ignores expiry.

    A scanner that submits a `{alg: none, sub: 99, role: admin}` token
    will get a 200 with admin-looking claims back.
    """
    token = _bearer()
    if not token:
        return jsonify({"error": "no_token"}), 401
    claims = _decode_loose(token)
    if not claims:
        return jsonify({"error": "bad_token"}), 401
    return jsonify({"username": claims.get("username"),
                    "role": claims.get("role"), "sub": claims.get("sub")})


@app.get("/safe/profile")
def safe_profile():
    """SAFE: full signature + alg + exp validation."""
    token = _bearer()
    if not token:
        return jsonify({"error": "no_token"}), 401
    claims = _decode_strict(token)
    if not claims:
        return jsonify({"error": "bad_token"}), 401
    return jsonify({"username": claims.get("username"),
                    "role": claims.get("role"), "sub": claims.get("sub")})


# ---- OWASP API3 (Mass Assignment) ----------------------------------------

@app.post("/vuln/signup")
def vuln_signup():
    """VULNERABLE: blindly persists the entire request body.

    Send `{"username": "x", "password": "y", "role": "admin",
    "is_verified": true}` and all four fields land in the stored record.
    Mass-assignment textbook case.
    """
    body = request.get_json(silent=True) or {}
    if not body.get("username") or not body.get("password"):
        return jsonify({"error": "missing_fields"}), 400
    record = dict(body)  # ← every field, including dangerous ones
    record["id"] = 1000 + len(_SIGNUPS)
    _SIGNUPS.append(record)
    return jsonify(record), 201


@app.post("/safe/signup")
def safe_signup():
    """SAFE: only `username` + `password` accepted; extras silently dropped."""
    body = request.get_json(silent=True) or {}
    if not body.get("username") or not body.get("password"):
        return jsonify({"error": "missing_fields"}), 400
    record = {
        "id": 2000 + len(_SIGNUPS),
        "username": body["username"],
        "password": body["password"],  # don't store plaintext in real apps
        "role": "user",
    }
    _SIGNUPS.append(record)
    return jsonify(record), 201


@app.get("/_inspect/signups")
def inspect_signups():
    """TEST-ONLY: lets the smoke test verify whether the vuln signup
    actually persisted the tampered field. Not in the OpenAPI spec.
    """
    return jsonify({"signups": _SIGNUPS})


# ---- OWASP API5 (Broken Function Level Authz) ----------------------------

@app.get("/vuln/admin/users")
def vuln_admin_users():
    """VULNERABLE: requires *any* valid token, but no role check.

    Alice (role=user) can list all users via her own token. Classic
    function-level authz bug.
    """
    token = _bearer()
    if not token or _decode_strict(token) is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"users": [{"id": v["id"], "role": v["role"]}
                              for v in _USERS.values()]})


@app.get("/safe/admin/users")
def safe_admin_users():
    """SAFE: requires role=admin."""
    token = _bearer()
    claims = _decode_strict(token) if token else None
    if not claims:
        return jsonify({"error": "unauthorized"}), 401
    if claims.get("role") != "admin":
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"users": [{"id": v["id"], "role": v["role"]}
                              for v in _USERS.values()]})


# ---- OWASP API8 (Security Misconfiguration) ------------------------------

@app.get("/vuln/data")
def vuln_data():
    """VULNERABLE: loose CORS + missing security headers + stack trace
    on the optional `?crash=1` query.
    """
    if request.args.get("crash") == "1":
        # Intentionally leak a traceback-ish error body, like a
        # production server with DEBUG=True.
        return ("Traceback (most recent call last):\n"
                "  File \"/app/handlers.py\", line 42, in load_data\n"
                "    payload = db.fetch_secret_query()\n"
                "DatabaseError: connection string='postgres://admin:hunter2@db:5432'\n",
                500, {"Content-Type": "text/plain"})
    resp = make_response(jsonify({"data": [1, 2, 3]}))
    # The vulnerable combo — wildcard origin paired with credentials.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    # No HSTS, no CSP, no X-Content-Type-Options, no X-Frame-Options.
    return resp


@app.get("/safe/data")
def safe_data():
    """SAFE: strict CORS + full security header set + generic error."""
    if request.args.get("crash") == "1":
        return jsonify({"error": "internal_server_error"}), 500
    resp = make_response(jsonify({"data": [1, 2, 3]}))
    resp.headers["Access-Control-Allow-Origin"] = "https://app.example.com"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Content-Security-Policy"] = "default-src 'self'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ---- Reset hook for the test suite ---------------------------------------

@app.post("/_reset")
def reset():
    """Test-only — clears mutable state. Called between smoke-test cases."""
    _SIGNUPS.clear()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Bind to loopback only. The vulnerabilities are intentional but
    # must never be reachable beyond the host.
    app.run(host="127.0.0.1", port=5099, debug=False)
