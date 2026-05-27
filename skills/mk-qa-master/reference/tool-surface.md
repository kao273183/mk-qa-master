# mk-qa-master — Tool Surface Cheatsheet (v0.9.3)

The 21 MCP tools currently exposed by mk-qa-master, grouped by flow.
One-liner + the input-schema gotchas you actually need to remember.

---

## v0.9.1+ — Plan & Verify (prelude to every other flow)

| Tool | Purpose | Gotchas |
|---|---|---|
| `qa_plan` | Store a critical-points checklist BEFORE acting | `critical_points` accepts list[str] OR list[dict{id?, description, verification_hint?}]; 30-min TTL; LRU-bounded at 50. **v0.9.3**: also writes to `<QA_PROJECT_ROOT>/test-results/plans/<plan_id>.json` when persistence is on (default ON when QA_PROJECT_ROOT is set; force with `QA_PLAN_PERSIST=true|false`). Response includes `persisted_to` field. |
| `verify_plan` | Walk plan CPs against evidence; return per-CP pass/fail + overall status | Matching is case-insensitive substring on `verification_hint`; status='passed' only when ALL CPs satisfied — partial = 'incomplete', zero = 'failed'. **v0.9.2**: pass `auto_discover: true` to pull evidence from `<QA_PROJECT_ROOT>/report.json` automatically; combine with explicit `evidence` to merge sources. Response includes `evidence_sources` audit trail. **v0.9.3**: transparently loads plans from disk when memory cache misses; response includes `plan_source: "memory" \| "disk"`. |

Use them as bookends around Flows 1-5. Skip for one-shot reads.

---

## Core (always available)

| Tool | Purpose | Gotchas |
|---|---|---|
| `get_runner_info` | Which runner is active + all available ones | None |
| `list_tests` | Enumerate tests in the project | Output can be large for >200-test suites; consider filtering |
| `run_tests` | Run tests | `filter` is a keyword; `headed=True` only for debugging; `browser` ∈ pytest-playwright targets |
| `run_failed` | Re-run last failures (pytest `--lf`) | Only meaningful after a prior failing run |
| `get_test_report` | Read latest `report.json` | Path: `test-results/report.json` |
| `get_failure_details` | Per-test failure breakdown | Pass exact `test_name` from `list_tests` output |
| `get_test_history` | Historical test runs from `test-results/history/` | `limit` parameter caps how many runs to look back |

## Generation

| Tool | Purpose | Gotchas |
|---|---|---|
| `analyze_url` | Discover modules + candidate TCs from a web URL | SPA-heavy sites need `timeout_ms=30000+`; behind-login needs `auth_cookie` |
| `analyze_screen` | Same but for mobile (Maestro hierarchy) | Requires Maestro CLI + a booted device |
| `generate_test` | Generate ONE pytest test from a description + module | `filename` should be a slug, no `.py` |
| `auto_generate_tests` | Chain `analyze_url` → `generate_test` × N | `tests_per_module` defaults to 1 — anything above 3 produces noise |
| `codegen` | Playwright codegen-style scaffolding | Only meaningful on pytest-playwright |

## Reporting

| Tool | Purpose | Gotchas |
|---|---|---|
| `generate_html_report` | Self-contained dark-mode HTML report | Writes to `test-results/report.html` |
| `get_optimization_plan` | Suite + MCP + AI strategy improvement plan | Reads `test-results/optimization-plan.md`; only meaningful after several runs |

## QA Knowledge Layer

| Tool | Purpose | Gotchas |
|---|---|---|
| `init_qa_knowledge` | Scaffold project's QA knowledge directory | One-shot setup |
| `get_qa_context` | Read methodology + domain knowledge | `section` filter narrows; bilingual (`QA_LANG=en` or `zh-tw`) |

## v0.7 — AI Visual Challenge Solver

| Tool | Purpose | Gotchas |
|---|---|---|
| `inspect_visual_challenge` | Detect CAPTCHA + screenshot + tile grid | Needs `QA_VISUAL_CHALLENGE_CONSENT=true`; multimodal MCP returns ImageContent |
| `solve_visual_challenge` | Apply tile selection + click verify | Requires `confirm=true` safety latch; `status: 'continue'` for dynamic-replace mode means look at the NEW screenshot |

## v0.8 — OWASP API Security Scanner

| Tool | Purpose | Gotchas |
|---|---|---|
| `run_api_security_scan` | Scan an OpenAPI 3.x spec for 5 OWASP API Top 10 issues | Needs `QA_API_SECURITY_CONSENT=true` + `AUTHORIZED_DOMAINS`; `mass_assignment` opt-in; default 4 of 5 categories |

---

## Environment variables you might surface to the user

| Variable | Required for | Default |
|---|---|---|
| `QA_RUNNER` | Picking a non-pytest backend | `pytest` |
| `QA_PROJECT_ROOT` | Where tests live | CWD |
| `QA_LANG` | QA knowledge bilingual selection | `en` |
| `QA_VISUAL_CHALLENGE_CONSENT` | v0.7 tools | unset (refuses to run) |
| `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` | v0.7 production scope | unset (warn-only) |
| `QA_VISUAL_CHALLENGE_TIMEOUT` | v0.7 wall-clock | `120` seconds |
| `QA_API_SECURITY_CONSENT` | v0.8 scanner | unset (refuses to run) |
| `QA_API_SECURITY_AUTHORIZED_DOMAINS` | v0.8 external hosts | unset (only localhost allowed) |

---

## Tool naming convention

- `*_tool` suffix internally in the Python module names (e.g.
  `inspect_visual_challenge_tool`) but the MCP-exposed tool name drops
  the suffix (`inspect_visual_challenge`).
- The 19 names above are the **canonical** ones the host's MCP client
  sees. Never invent variants.

---

## When the host doesn't surface a tool

If your host's MCP wiring drops a tool (e.g. truncated tool list in
some clients), you can fall back to the CLI:

```bash
# Direct module-level invocation
python -m mk_qa_master.tools.visual_challenge --help
python -m mk_qa_master.tools.runner list_tests
```

But MCP-first is always preferred when the host supports it — the
schemas + error envelopes are richer.
