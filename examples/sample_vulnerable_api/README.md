# Sample Vulnerable API — `examples/sample_vulnerable_api/`

> ⚠️ This Flask app contains **deliberate vulnerabilities** as ground
> truth for the v0.8.0 API security testing suite. It binds to
> `127.0.0.1:5099` and **must not be deployed to any reachable
> network.** The vulnerabilities are intentional and exist solely so
> the scanner can be validated against known positives and negatives.

Tier 1 of the v0.8.0 dogfood pyramid (see
[`docs/prd-v0.8-api-security.md`](../../docs/prd-v0.8-api-security.md) §5).

## Vuln/safe endpoint pairs

| OWASP # | Category | Vulnerable | Safe |
|---|---|---|---|
| API1 | Broken Object Level Authz (BOLA / IDOR) | `GET /vuln/orders/{id}` | `GET /safe/me/orders` |
| API2 | Broken Authentication | `GET /vuln/profile` | `GET /safe/profile` |
| API3 | Mass Assignment | `POST /vuln/signup` | `POST /safe/signup` |
| API5 | Broken Function Level Authz | `GET /vuln/admin/users` | `GET /safe/admin/users` |
| API8 | Security Misconfiguration | `GET /vuln/data` | `GET /safe/data` |

Each pair is documented in [`openapi.yaml`](openapi.yaml). Vulnerable
operations carry an `x-vulnerable` extension naming the OWASP
category — useful both for scanner authors and as inline
documentation.

## Auth fixture

`POST /login` with one of three pre-seeded credentials returns a
HS256 JWT:

| username | password | role | user_id |
|---|---|---|---|
| `alice` | `alice123` | user | 1 |
| `bob` | `bob123` | user | 2 |
| `admin` | `admin123` | admin | 99 |

JWT secret: `vulnerable-api-test-secret-do-not-deploy` (no, really).

## Run it locally

```bash
cd examples/sample_vulnerable_api
pip install -r requirements.txt
python app.py
# binds to 127.0.0.1:5099
```

Then prove a vulnerability:

```bash
# Alice logs in, reads bob's order (BOLA):
ALICE=$(curl -s -X POST http://127.0.0.1:5099/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"alice123"}' | jq -r .token)

curl -H "Authorization: Bearer $ALICE" http://127.0.0.1:5099/vuln/orders/2
# → {"id":2,"item":"bob's pizza","owner_id":2,"total":18.0}   ← bob's order!
```

## The smoke test (the v0.8 lesson applied)

`tests/test_fixture_vulnerable.py` proves the fixture is **actually
vulnerable** with real HTTP requests and real-response assertions —
not just `rc == 0`. This is the gate that the v0.8 mobile work
didn't have, and it's how PR-1 demonstrates that subsequent
rule-PRs have a sound ground truth to scan against.

```bash
pytest examples/sample_vulnerable_api/tests/
```

Each test in there is named for what it's asserting — e.g.
`test_vuln_bola_returns_other_users_order` is the literal proof
that alice can read bob's order. If those assertions ever go red,
the fixture is broken before any scanner-rule PR can run.

## What this app is NOT

- not a benchmark suite — too small, by design
- not a comprehensive OWASP catalog — covers 5 of the 10 categories
  in scope for v0.8.0 (see PRD §3 for what's deferred)
- not safe to deploy publicly — the vulnerabilities are real
- not a SAST target — these issues are deliberately introduced; a
  scanner that doesn't find them is buggy
