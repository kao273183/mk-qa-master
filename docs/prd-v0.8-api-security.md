# mk-qa-master v0.8.0 — API Security Testing (mini-PRD)

**Status:** Draft v0.1 — scope locked, rules-detail subject to revision · **Author:** Jack Kao (kao273183) · **Last updated:** 2026-05-26 · **Target ship:** v0.8.0 within ~7 days of go-decision

This is a **mini-PRD**, not the full 21-section format. v0.8.0 takes the existing v0.6.x API testing surface (Schemathesis fuzz + Newman replay) and adds **OWASP API Security Top 10 (2023) rule-based scanning**. Same `report.json` shape, same HTML reporter, same history/optimizer pipeline — new runner + new tool surface.

Refer to [`docs/prd-v0.6-api-testing.md`](prd-v0.6-api-testing.md) if it exists, or the existing `schemathesis.py` / `newman.py` runners, for the API-testing foundation this builds on.

---

## §1 — Why this, why now

Schemathesis catches **correctness** drift (response shape mismatch, status-code regressions, content-type errors). It does NOT catch **security** drift — an endpoint that returns the right shape but lets user A read user B's data is invisible to it. Real-world QA teams hit this exact gap once OpenAPI fuzz coverage is in place: "every endpoint passes schema conformance but our auth flow has IDOR everywhere."

OWASP API Security Top 10 (2023) is the de-facto checklist. Five of the ten categories are **purely HTTP-observable** — they don't need internal callback infra, prod recon, or business-domain knowledge — and that's where v0.8.0 lives. The other five are deferred (see §3).

---

## §2 — In scope (v0.8.0)

| OWASP # | Category | Implementation sketch |
|---|---|---|
| **API1** | Broken Object Level Authorization (BOLA / IDOR) | Two-user diff: scan finds object-id endpoints (`/users/{id}`, `/orders/{id}`), calls each with user-A's token, then user-B's token, flags any cross-user data exposure. |
| **API2** | Broken Authentication | For each endpoint declared as requiring auth in the spec: probe with (a) no token, (b) malformed JWT, (c) expired token if available, (d) wrong-algorithm JWT (`alg: none`, `HS256→RS256` confusion). Flag any non-401/403 responses. |
| **API3** | Broken Object Property Authz (mass assignment) | For each POST/PUT operation with `additionalProperties: false` or explicit allowed-fields: send a request body carrying an extra dangerous field (e.g. `"role": "admin"`, `"is_verified": true`) and observe whether the server (a) rejects the request, (b) silently drops the field (acceptable), or (c) persists it (vulnerable — verified via follow-up GET). |
| **API5** | Broken Function Level Authz | If the spec declares scopes or a "low-priv" sample user is provided, probe endpoints documented as requiring elevated scope using the low-priv token. Flag 2xx responses. |
| **API8** | Security Misconfiguration | Static checks on response headers and error bodies — missing HSTS, CSP, X-Content-Type-Options, overly-permissive CORS (`Access-Control-Allow-Origin: *` with credentials), stack-trace leakage in 5xx responses. Pure HTTP — no auth state needed. |

---

## §3 — Out of scope (deferred)

| OWASP # | Category | Why deferred |
|---|---|---|
| API4 | Unrestricted Resource Consumption | Rate-limit probing easily DoS's the target. Needs a careful "responsible probing" design with explicit consent + low-amplitude burst patterns. v0.8.1+. |
| API6 | Unrestricted Business Flows | Requires per-business workflow modeling (e.g. "ticket purchase loop"). Not generic enough for a v0.8.x rule. |
| API7 | Server-Side Request Forgery | Needs a callback / OOB infrastructure to confirm exfiltration. Outside MCP-tool scope. |
| API9 | Improper Inventory Management | Requires prod surface recon (subdomain enumeration, version-string crawling). Different threat model. |
| API10 | Unsafe Consumption of APIs | Requires upstream-API simulation; doesn't fit the "scan one OpenAPI spec" shape. |

If you want **any** of these, file separately. Don't sneak them into v0.8.0 PRs.

---

## §4 — Architecture

```
src/mk_qa_master/runners/api_security.py     ← new runner, sibling to schemathesis.py / newman.py
src/mk_qa_master/security_rules/
  ├── __init__.py
  ├── base.py            # SecurityRule Protocol + Severity enum + Finding dataclass
  ├── bola.py            # API1 + API5 (function-level auth shares the diff machinery)
  ├── broken_auth.py     # API2
  ├── mass_assignment.py # API3
  └── headers_misconfig.py # API8
```

### `SecurityRule` Protocol

```python
class SecurityRule(Protocol):
    id: str                                    # e.g. "OWASP-API1-BOLA"
    severity: Severity                         # critical / high / medium / low / info
    requires_auth_pair: bool                   # True for BOLA, False for headers

    def applies_to(self, op: OpenAPIOperation) -> bool: ...
    def execute(self, client: APIClient, op: OpenAPIOperation) -> list[Finding]: ...
```

Runner walks `_RULES` registry, filters by `applies_to`, calls `execute`. Each rule returns 0+ `Finding`s. All findings flow into the existing `report.json` schema with a new `security` block alongside `tests`.

### MCP tool surface

```python
run_api_security_scan(
    spec_url: str,                 # OpenAPI 3.x URL or file:// path
    auth: dict | None = None,      # {"type": "bearer", "token": "...", "alt_user_token": "..."}
    categories: list[str] | None = None,  # default = all 5 enabled
    severity_threshold: str = "medium",   # min severity to report
    base_url: str | None = None,   # override servers[0].url from spec
    timeout_s: int = 30,           # per-request timeout
) -> dict
```

Return shape (subject to revision in PR-2):

```json
{
  "scan_id": "...",
  "spec_url": "...",
  "findings": [
    {"rule_id": "OWASP-API1-BOLA", "severity": "high", "endpoint": "GET /orders/{id}",
     "evidence": {...}, "remediation_hint": "..."}
  ],
  "summary": {"total": 12, "by_severity": {"high": 3, "medium": 5, ...}},
  "skipped": [{"rule_id": "...", "reason": "no_auth_pair_provided"}]
}
```

---

## §5 — Dogfood pyramid

| Tier | Target | Lives in | Runs when |
|---|---|---|---|
| 1 | `examples/sample_vulnerable_api/` — a **deliberately vulnerable** Flask app shipping with mk-qa-master. Each enabled OWASP category has at least one positive trigger (a known-vulnerable endpoint) AND one negative trigger (a known-safe endpoint that the rule must NOT flag). | bundled in repo | Every PR, every unit-test run |
| 2 | A public sandbox like `https://restful-booker.herokuapp.com` (already used by Postman community for API training) | not bundled — CI-only | Nightly CI |
| 3 | User's own OpenAPI spec + auth, opt-in via MCP tool call | runtime | When user invokes the tool |

**Tier 1 is non-negotiable** — every rule must demonstrably flag the vulnerable endpoint and NOT flag the safe one before the PR merges. (See §10 below — this is the explicit anti-regression gate against the v0.8 Maestro miss.)

---

## §6 — PR breakdown (~6 PRs over 7 days)

| PR | Scope | Locks in | Gate |
|---|---|---|---|
| PR-1 | This PRD + `examples/sample_vulnerable_api/` Flask fixture with all 5 vuln+safe pairs wired and a smoke test running it | scope, fixture shape | Flask app boots, exposes every spec'd endpoint, vuln endpoints actually leak / accept tampering, safe endpoints don't |
| PR-2 | `SecurityRule` Protocol + Severity enum + Finding dataclass + `headers_misconfig` rule (simplest, no auth state) | rule abstraction shape | `headers_misconfig` flags the vuln endpoint's missing-HSTS-no-CSP and ignores the safe one |
| PR-3 | `broken_auth` rule (API2) — token tampering matrix | JWT-handling subdep choice (`pyjwt` vs `python-jose`) | Vuln endpoint with `alg: none` JWT is flagged; safe endpoint with proper Bearer is not |
| PR-4 | `bola` rule (API1 + API5) — two-user diff machinery + function-level reuse | auth-pair config shape | Vuln `GET /orders/{id}` returns user-B's data to user-A → flagged; safe `GET /me` only returns user-A → not flagged |
| PR-5 | `mass_assignment` rule (API3) — over-posting heuristic + persistence check | mass-assignment dangerous-field catalog (`role`, `is_admin`, `verified`, etc.) | Vuln `POST /users` accepts `role: admin` and persists → flagged; safe endpoint that 400s extra fields → not flagged |
| PR-6 | `run_api_security_scan` MCP tool surface + Tier 2 nightly CI + README updates + bump pyproject.toml + GitHub release | tool signature, final | All 5 rules green against Tier 1 fixture; Tier 2 nightly executes against restful-booker without crashing |

PR-1 and PR-6 are non-overlapping book-ends; PRs 2-5 can be reordered if a particular rule's design proves messy.

---

## §7 — Risks and open questions

These are **explicitly not locked**. Each PR may revise them.

1. **JWT tampering library choice**: `pyjwt` is more common but doesn't expose low-level signing flexibility; `python-jose` is more flexible but heavier. PR-3 picks.
2. **Mass-assignment persistence check**: requires a follow-up GET on the created/updated resource. Some APIs return the persisted object inline (cheap); others require a separate GET (extra HTTP call, possibly extra auth). PR-5 picks heuristic.
3. **BOLA needs writable test data**: the second user's "owned object" id must be discoverable. PR-4 design must define how: (a) opt-in `seed_endpoints` config, (b) auto-create via known POST endpoints, or (c) require explicit `bola_pairs` config.
4. **Severity defaults**: which findings are "high" vs "medium"? PR-2 sets a reasonable default; users can override via `severity_threshold` or future per-rule config.
5. **False-positive rate**: any rule that fires on 100% of endpoints is useless. PR-2 onwards must measure FP rate on Tier 1 safe-endpoint set, ship if < 10%.

---

## §8 — Consent and authorization

This tool **runs adversarial test cases against the configured base_url**. Three things are non-negotiable:

1. The MCP tool requires `QA_API_SECURITY_CONSENT=true` in the environment (mirrors the v0.7 visual-challenge consent model).
2. The default `categories` list excludes `bola` and `mass_assignment` (the two that POST/mutate). To enable them the user must explicitly opt in per scan.
3. The tool MUST refuse to scan domains it doesn't own unless `QA_API_SECURITY_AUTHORIZED_DOMAINS` lists the target. This matches the v0.7 `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` pattern.

Tier 1 fixture domain is `localhost:5000` so opt-in is automatic; Tier 2 `restful-booker.herokuapp.com` will be on the default allowlist for CI; everything else requires the user to whitelist.

---

## §9 — Success criteria (measurable)

- **Coverage:** All 5 in-scope OWASP categories ship as rules with both positive and negative tests against the Tier 1 fixture.
- **Tier 1 FP rate:** Less than 10% false-positive rate measured on the safe-endpoint set per rule.
- **Tier 2 stability:** Nightly CI run completes against restful-booker without crashing for 7 consecutive nights post-release. Findings are recorded but not gate the build.
- **Performance:** Full Tier 1 scan completes in under 30 seconds on a stock GitHub Actions ubuntu-latest runner.
- **MCP surface:** Tool callable from any MCP client, returns valid JSON per §4 schema.
- **Docs:** README has a v0.8.0 API security section with a 10-line quickstart.

---

## §10 — Lessons applied from v0.8 mobile postmortem

The v0.8 Maestro mobile work failed because [`v0.8-mobile-postmortem.md`](v0.8-mobile-postmortem.md) §"Why the spike didn't catch this" — capability claims were validated by `rc == 0` rather than by asserting the produced value. Concrete mitigations baked into this PRD:

1. **PR-1's smoke test must assert the vulnerable endpoints actually behave vulnerably.** Booting the Flask app is not enough. The smoke test does an actual `requests.get("http://localhost:5000/orders/1", headers={"Authorization": "Bearer user_a_token"})` and asserts the response body contains user-B's data when it shouldn't. If this assertion fails, the fixture itself is broken and the rest of the PRD is moot.
2. **Every rule's PR includes both positive AND negative real-HTTP tests** against the Tier 1 fixture, not just mocked-`requests` unit tests. Mock-based tests stay for catching orchestration regressions; the dogfood path catches "the rule's actually broken."
3. **No `assert rc == 0` patterns.** Every test asserts the substantive return value (Finding objects, response bodies, persisted side-effects).
4. **No "spike says it works" merges without re-running on the canonical fixture in CI.**

---

## §11 — What's NOT committed (revisable per PR)

- Exact `SecurityRule` Protocol field names — PR-2 can rename / restructure
- Exact `Finding` dataclass shape — PR-2 fixes after building the first rule
- Exact `report.json` `security` block schema — PR-6 finalizes
- Severity defaults per rule
- Whether the Flask fixture lives in `examples/` or `tests/fixtures/` (currently planned: `examples/sample_vulnerable_api/` so users can play with it locally)
- Whether `categories: list[str]` accepts rule IDs (`"OWASP-API1-BOLA"`) or shorter names (`"bola"`) — currently leaning toward shorter
- Whether to ship a "report-mode-only" no-network preview that just lists what WOULD be probed

---

## §12 — Out of scope for v0.8.x even as follow-ups

These are not "v0.8.1 candidates" — they're a different product:

- GUI / dashboard for findings (CLI + report.json is enough for v0.8)
- IDE extensions
- Integration with external SIEM / SAST tools
- AI-driven test case generation (Schemathesis already does stateless fuzz; LLM-driven would be a v1.0+ initiative)

---

*Mini-PRD ends. See §6 for what to build first.*
