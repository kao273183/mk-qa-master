# mk-qa-master — Workflow Reference

Detailed expansion of the five flows in `SKILL.md`. Use this when you
need a fuller mental model than the slash commands cover.

---

## Flow 1 — Run my tests

The user's most common ask. Goal: turn vague "run my tests" into a
focused, fast, debuggable run.

```
get_runner_info             # which runner is active
   ↓
list_tests                  # enumerate (don't dump all 200 — show a tree)
   ↓
run_tests(filter=...)       # narrow first, widen only if user wants
   ↓ (red?)
get_failure_details(...)    # per failure
   ↓
get_test_history(limit=5)   # is this a flake or a real bug?
   ↓ (optional)
get_optimization_plan       # only when user asks
```

### Common pitfalls

- **Don't widen filters silently.** If `run_tests(filter="login")`
  returns 0 tests, ASK the user before widening to the full suite.
- **Don't `--headed=True` by default.** Headed browser is slow and
  opens a window — only use it when the user explicitly asks.
- **Don't paper over collection errors.** If pytest itself fails to
  import tests, surface the import error before trying to run.

---

## Flow 2 — Generate tests from a URL or mobile screen

The user wants automation. Goal: produce tests that **actually run**,
not just "look plausible."

```
analyze_url(url, timeout_ms=15000)   # discovers modules + candidate TCs
   ↓
  (show user the modules, let them prune)
   ↓
auto_generate_tests(url=..., tests_per_module=1)   # whole suite
  OR
generate_test(description=..., filename=..., url=..., module=<module>) # one
   ↓
run_tests(filter="<new_test_slug>")  # verify the generated test runs
```

### Picking `tests_per_module`

- **1** (default): cleanest, lowest noise.
- **2-3**: more coverage; tail starts getting weaker.
- **4-10**: usually garbage from the long tail of `candidate_tcs`. Avoid.

### When `analyze_url` returns weird output

- 0 modules detected → site might be SPA-rendered. Try
  `timeout_ms=30000` to give it more time, or pass `auth_cookie` if
  the content lives behind login.
- Modules look wrong (e.g. login form not detected as "form") → the
  module classifier missed it. Surface the raw output and let the
  user choose which module to feed to `generate_test`.

---

## Flow 3 — Debug a failure

The user came in with red tests. Goal: distinguish flake from real bug,
guide them to a fix without re-running the world.

```
get_test_report                       # latest report.json
   ↓
get_failure_details(test_name=...)    # exception + stack frame
   ↓
get_test_history(limit=10)            # has this failed before?
   ↓
  (If sustained pattern)
get_optimization_plan                 # flaky vs. consistent diagnosis
   ↓
  (user patches the code)
run_failed                            # pytest --lf only
```

### Flaky vs. consistent

- **Consistent failure** (red ≥3 of last 5 runs): real bug. Fix the
  test or the code.
- **Flake** (red 1-2 of last 5 runs, green otherwise): infrastructure
  issue. `get_optimization_plan` surfaces these with a "mitigate
  flakiness" section.
- **First-time failure on this test**: history won't help; rely on the
  failure details + stack frame.

### When to NOT re-run

- If the user hasn't actually patched anything since the last run.
- If the failure is "external_dependency" (database unreachable,
  third-party API down). Re-running won't help.

---

## Flow 4 — Solve a CAPTCHA blocking a test (v0.7.0+)

Gates: `QA_VISUAL_CHALLENGE_CONSENT=true` + optionally
`QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS`.

```
inspect_visual_challenge()
   ↓
  (host's vision picks tiles based on challenge_text + screenshot)
   ↓
solve_visual_challenge(challenge_id, selected_tile_indices=[...], confirm=true)
   ↓ (status == "continue" — dynamic replace mode)
  (look at the NEW screenshot, pick again)
   ↓
solve_visual_challenge(... selected_tile_indices=[], confirm=true)  # finalize
   ↓
status: "passed" → token returned
```

### Hard-stop domains

The tool refuses to operate on third-party identity providers
(`accounts.google.com`, `login.microsoftonline.com`, `id.apple.com`,
etc.) regardless of consent. No legitimate QA scenario justifies
solving CAPTCHAs on someone else's login page.

### When to NOT escalate to v0.7

The built-in QA knowledge (`get_qa_context section="CAPTCHA"`) codifies
three tiers:

1. **Bypass**: reCAPTCHA test keys, feature flags, IP allowlist —
   covers ~90% of QA scenarios.
2. **Degrade**: mark as `external_dependency`, skip downstream
   assertions.
3. **AI visual judgment**: this tool. Only when 1+2 don't fit.

If the user is hitting CAPTCHAs in dev, suggest tier 1 first.

---

## Flow 5 — OWASP API security scan (v0.8.0+)

Gates: `QA_API_SECURITY_CONSENT=true` + non-localhost hosts must be in
`QA_API_SECURITY_AUTHORIZED_DOMAINS`. See `api-security-deep.md` for
the full per-rule semantics.

```
  (decide categories — mass_assignment opt-in only)
   ↓
  (decide auth — one token vs auth pair vs none)
   ↓
run_api_security_scan(spec_url, auth, categories, severity_threshold, base_url, timeout_s)
   ↓
  (report findings in severity-rank order)
   ↓
  (summarize by_severity counts)
```

### When to include `mass_assignment`

Default: NO. It POSTs probe data. Include only when:

- the target is a fixture you own (localhost), OR
- the user explicitly opted in.

### When to omit `bola` / `function_authz`

If the user didn't provide BOTH `auth.token` AND `auth.alt_user_token`,
these rules can't do their two-user diff. They'll emit INFO "skipped"
findings rather than firing false positives. That's by design — don't
suppress those INFO entries when the user asks "did BOLA run?"

---

## Cross-flow rules

1. **Never silently drop tool errors.** Every `error` field in a tool
   response goes to the user verbatim. Consent errors, authorization
   errors, schema errors — all of them.

2. **Always cite the report path.** When you reference findings or
   failures, give the path: `test-results/report.json`,
   `test-results/history/<timestamp>.json`, etc.

3. **Don't recommend deploying the fixtures.** `examples/sample_*`
   directories are for self-testing only.

4. **Stay in flow.** Don't mix flow 1 (run tests) with flow 5 (API
   scan) in the same response unless the user explicitly asked for
   both.
