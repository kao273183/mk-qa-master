---
description: Scan an OpenAPI 3.x spec for OWASP API Top 10 issues via mk-qa-master's v0.8.0 security scanner.
argument-hint: <spec-url-or-path>
---

You are operating as the mk-qa-master agent in OWASP API security
scanning mode. Follow Flow 5 in the parent `SKILL.md` and read
`reference/api-security-deep.md` before proceeding.

Spec target:

$ARGUMENTS

## Required gates BEFORE you call `run_api_security_scan`

1. **Consent.** Check `QA_API_SECURITY_CONSENT=true` is set in the host
   env. If not, the tool will return `consent_required` with a legal
   disclaimer — surface that disclaimer verbatim to the user; do NOT
   re-prompt or paraphrase.

2. **Authorization.** If the spec's `servers[0].url` (or the user-
   provided `base_url`) points to anything other than `localhost` /
   `127.0.0.1`, the host MUST be in
   `QA_API_SECURITY_AUTHORIZED_DOMAINS`. If not set, the tool returns
   `unauthorized_domain` — surface that verbatim too.

3. **mass_assignment opt-in.** The default `categories` exclude
   `mass_assignment` because it mutates server state (it POSTs probe
   data). Include it ONLY if:
   - the spec's base_url is localhost (a known fixture, not a real API),
     OR
   - the user explicitly opted in by saying "include mass assignment" /
     "scan for over-posting" / "test API3".

## Steps

1. **Survey the spec.** Use the MCP tool's `severity_threshold="info"`
   first pass to see ALL endpoints + which rules apply. Don't expose
   info-level findings to the user — they're noisy. Just check the
   `ops_scanned` count looks reasonable (5+ endpoints).

2. **Decide categories.** Default: `["headers", "broken_auth", "bola",
   "function_authz"]`. Add `"mass_assignment"` only per the opt-in rule
   above.

3. **Decide auth.** If the user provided a token, pass it as
   `auth={"token": "..."}`. If they provided BOTH user-a + user-b
   tokens (for BOLA), pass both + `bola_test_ids` mapping each user to
   the object ids they own. Without `bola_test_ids`, BOLA emits INFO
   "skipped" findings rather than firing false positives.

4. **Run.** Call `run_api_security_scan(spec_url=..., auth=..., 
   categories=..., severity_threshold="medium", base_url=...,
   timeout_s=30)`.

   **v0.9.4 — bookend pattern (recommended).** Call `qa_plan` first
   with one CP per OWASP rule you expect to fire (e.g.
   `verification_hint="OWASP-API1-BOLA-CrossUserDataExposure"`), then
   pass `plan_id=<...>` to `run_api_security_scan`. The response's
   `plan_verification` block tells you in one shot which expected
   findings did and didn't fire — no need for a separate
   `verify_plan` call. Lower `severity_threshold` to `"low"` if any
   of your CPs target a LOW-severity finding (the CORS-wildcard-
   without-credentials variant is LOW, for example).

5. **Report findings in severity-rank order.** For each finding above
   medium:
   - severity badge
   - rule_id (e.g. `OWASP-API1-BOLA-CrossUserDataExposure`)
   - endpoint
   - evidence dict (the proof — actor, target_id, status_code, body
     preview)
   - remediation_hint verbatim
   - link to OWASP API Top 10 entry if the user wants context

6. **Summarize.** `summary.by_severity` is your headline number:
   "Found 2 critical, 4 high, 1 medium across 23 endpoints scanned."

## What this command does NOT do

- It does NOT auto-fix the findings. Surfacing them is the entire
  deliverable.
- It does NOT scan rate-limiting (API4), business-flow abuse (API6),
  SSRF (API7), inventory mgmt (API9), or upstream-API consumption
  (API10). Those are out of scope per the v0.8 PRD §3.
- It does NOT verify findings by re-probing — that's filed as v0.9.x
  follow-up work.

If a finding looks like a false positive (e.g. the user says "but role:
admin is part of our API contract for that endpoint"), check the spec's
`requestBody.content."application/json".schema.properties` — if the
field is declared there, the rule should have skipped it. If it didn't,
that's a bug worth filing.
