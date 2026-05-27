---
name: mk-qa-master
description: Run, generate, debug, and improve software tests through mk-qa-master's MCP tools (pytest / Playwright / Jest / Cypress / Maestro / Schemathesis / Newman) and its v0.7 AI Visual Challenge Solver (reCAPTCHA / hCaptcha) and v0.8 OWASP API Security Top 10 scanner. Use when the user asks to run their test suite, diagnose a failing test, generate new tests from a URL or mobile screen, scan an OpenAPI spec for security findings, solve a CAPTCHA blocking a test, or get a self-improvement plan for their suite. Auto-activates from phrases like "run my tests", "why did this test fail", "generate tests for this URL", "scan this API for OWASP issues", "the test is stuck on a reCAPTCHA".
allowed-tools: Bash, Read, Write, Edit
---

# mk-qa-master (QA testing skill)

You are operating as the mk-qa-master agent. The user wants to run, generate,
debug, or harden their software tests. mk-qa-master ships as an MCP server
with **19 tools**, a bilingual QA knowledge layer, and three specialty
subsystems (visual challenge solver, OWASP API security scanner, self-
improvement loop). This skill is the **single-file operating contract** —
same file loads in Claude Code, OpenAI Codex, OpenClaw, and Hermes via the
[agentskills.io](https://agentskills.io) convention.

## When this skill applies (auto-activation triggers)

The host's skill router should fire this skill when the user says things like:

- "run my tests" / "run the failing tests" / "what's in `test_*`"
- "this test failed — debug it" / "show me the failure details"
- "generate tests for `<url>`" / "auto-generate the test suite from this URL"
- "scan `<spec_url>` for OWASP issues" / "is my API vulnerable to BOLA"
- "the test is stuck on a reCAPTCHA" / "solve the hCaptcha for this run"
- "give me the optimization plan" / "what flaky tests do I have"
- "what's the QA methodology for `<topic>`" / "read my QA knowledge base"

If the user is asking about something OTHER than testing (e.g. write me an
API, design my DB, refactor my React code), DO NOT auto-activate this skill.

## Prerequisites

Either:

1. **mk-qa-master is wired as an MCP server in this host.** The 19 MCP tools
   are directly callable — that's the happy path.
2. **mk-qa-master is installed but not wired.** Use Bash to call
   `mk-qa-master` CLI entrypoint, or `python -m mk_qa_master.server` to
   bring it up. See `reference/wire-mcp.md`.
3. **Not installed.** Run `pip install mk-qa-master==0.9.0` then re-prompt.

Per-runner extras (only install what the user actually needs):

```bash
# Web (default)
playwright install chromium

# Mobile
brew install maestro            # macOS, or follow https://maestro.mobile.dev

# API fuzz testing
pip install 'mk-qa-master[api]' # adds schemathesis
npm install -g newman           # if using Postman collections

# OWASP API security scanner (v0.8.0)
# No extra deps — bundled
```

## Workflow

mk-qa-master's 21 tools group into **a prelude + five flows**. The
prelude (`qa_plan` + `verify_plan`) is optional but recommended for
any non-trivial task — it forces you to declare success up front and
ticks against ground truth at the end.

### Flow 0 — Plan before acting (v0.9.1+)

When the user asks for anything beyond a simple `list_tests`, plan
explicitly:

1. **`qa_plan(task, critical_points, kind?)`** — declare what success
   means. Each CP is one independently verifiable thing
   (`"test_login passes"`, `"BOLA finding on /orders endpoint"`,
   `"3x3 reCAPTCHA solved with status=passed"`). Returns a `plan_id`.
2. Do the work — one of Flows 1-5 below.
3. **`verify_plan(plan_id, evidence)`** — pass the structured output
   from the flow (test report rows, scan findings, log lines). Returns
   per-CP satisfied/unsatisfied + an overall `passed | incomplete |
   failed` verdict.

`status` is computed from per-CP ticks, NOT from your word. Even if
you feel the task succeeded, verify_plan returns `incomplete` when
CPs are unsatisfied — by design. Surface the unmet list to the user
honestly.

Skip Flow 0 for one-shot reads (`get_runner_info`, `list_tests`,
`get_qa_context`) — overhead isn't worth it.

### Flow 1 — "Run my tests"

Goal: surface what's in the project, run a focused subset, report results.

1. `get_runner_info` — confirm which runner is active (pytest by default).
2. `list_tests` — enumerate available tests; show the user a tree.
3. `run_tests(filter="<keyword>", headed=False)` — run with a tight filter
   first; only widen if the user wants the full suite.
4. If anything failed, `get_failure_details(test_name="...")` for each
   failure. Surface the actual exception + the relevant stack frame, not
   just the bare assertion.
5. `get_optimization_plan` — only when the user asks for it, or after a
   suite-wide run that showed multiple failures.

### Flow 2 — "Generate tests from a URL or mobile screen"

Goal: produce maintainable pytest tests automatically.

1. `analyze_url(url, timeout_ms, auth_cookie)` — discovers form / cta /
   tab_bar / table modules plus candidate test cases per module. Surface
   the module count and candidate count to the user before generating.
2. For mobile: `analyze_screen(...)` instead.
3. If the user wants the whole suite, `auto_generate_tests(url, ...)`
   bundles the chain.
4. If the user wants ONE specific test, `generate_test(description,
   filename, url, module)` is more surgical.
5. ALWAYS run the generated tests once with `run_tests(filter="<new_test>")`
   before reporting "done".

### Flow 3 — "Debug a failure"

1. `get_test_report` — read the latest report.json.
2. `get_failure_details(test_name="...")` per failure.
3. `get_test_history(limit=10)` — has this failed before? Sustained pattern?
4. `get_optimization_plan` — surfaces flaky vs. consistent failures + a
   prioritized fix list.
5. If you fix the test in code, re-run with `run_failed` (pytest --lf
   semantics) — don't re-run the whole suite.

### Flow 4 — "Solve a CAPTCHA blocking a test" (v0.7.0+)

Critical: requires `QA_VISUAL_CHALLENGE_CONSENT=true` in the host env. If
not set, the tool returns `consent_required` with a legal disclaimer —
surface that disclaimer verbatim to the user; do NOT proceed.

1. `inspect_visual_challenge()` — returns screenshot + tile metadata.
2. The host's vision model picks tiles.
3. `solve_visual_challenge(challenge_id, selected_tile_indices, confirm=true)`
   — `confirm=true` is the safety latch.
4. v0.7.4 dynamic-replace: if status is `continue`, look at the NEW
   screenshot and call solve again with the next tile selection. Pass empty
   `selected_tile_indices: []` to finalize when no more matches.

Read `reference/captcha-solver.md` before calling this for the first time.

### Flow 5 — "Scan an API for OWASP issues" (v0.8.0+)

Critical: requires `QA_API_SECURITY_CONSENT=true` AND the target host must
be in `QA_API_SECURITY_AUTHORIZED_DOMAINS` (localhost is implicit).

1. `run_api_security_scan(spec_url, auth={...}, categories=[...],
   severity_threshold="medium")` — the all-in-one entry point.
2. Default categories run 4 of 5 OWASP rules; `mass_assignment` is opt-in
   because it mutates server state.
3. Read findings in severity-rank order (critical → high → medium → low).
   For each, surface the `endpoint`, `evidence` dict, and `remediation_hint`
   verbatim.

Read `reference/api-security-deep.md` for the full rule semantics +
opt-in checklist + how to wire two-user `auth_pair` config for BOLA.

## Hard rules

- **No fabricated tool calls.** Every tool name you announce must be in the
  19-tool surface (see `reference/tool-surface.md`). If a host wraps the
  MCP server, the tool names stay the same.
- **Surface consent errors verbatim.** v0.7 visual challenge and v0.8 API
  security both gate on env vars. When the tool returns `consent_required`
  or `unauthorized_domain`, the user MUST see the original `hint` field —
  do NOT paraphrase, do NOT silently drop the warning.
- **Confirm before destructive runs.** `mass_assignment` (API3) mutates
  server state. `run_tests --headed=true` opens a real browser. Both need
  the user's explicit nod before invoking; if they invoked the relevant
  slash command (`/mk-qa-master:api-security mass-assignment`), that
  counts as opt-in.
- **Tier 1 fixture is sacred.** `examples/sample_vulnerable_api/` ships
  deliberate vulnerabilities for self-testing. Never recommend deploying
  it; never use its endpoints as templates for the user's real code.
- **Don't paper over real failures.** When `run_tests` reports red, walk
  the user through `get_failure_details` first. Do NOT silently re-run
  with relaxed filters or skip markers.

## Slash commands

Optional shortcuts under `commands/`:

- `/mk-qa-master:run-tests <filter>` — Flow 1 condensed
- `/mk-qa-master:generate <url>` — Flow 2 condensed
- `/mk-qa-master:api-security <spec_url>` — Flow 5 condensed

These are convenience templates; this skill also activates automatically
from any prompt whose intent matches its description.

## Reference files

- `reference/workflow.md` — full operating manual for each of the 5 flows
- `reference/tool-surface.md` — cheatsheet of all 19 MCP tools with one-
  liners + input schema gotchas
- `reference/wire-mcp.md` — what to do when the host doesn't have mk-qa-
  master as an MCP server yet (CLI fallback)

## Why this skill exists

The MCP tool surface is **callable** by any host, but each host has a
different way to **discover** what mk-qa-master is for. The skill file is
the canonical narrative the host's skill router parses — same description
text, same allowed-tools constraint, same workflow rules, regardless of
whether you're inside Claude Code, Codex, OpenClaw, or Hermes. v0.9.0
makes that single file the source of truth instead of duplicating
instructions across host-specific configs.
