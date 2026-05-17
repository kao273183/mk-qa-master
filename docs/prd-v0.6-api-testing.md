# mk-qa-master v0.6 — Native API Testing

**Status:** Draft v0.1 · **Author:** Jack Kao (kao273183) · **Last updated:** 2026-05-17 · **Target ship:** v0.6.0 (Phase 1) within 2 weeks

---

## 1. Vision

> **A QA reader scans the README and finds API testing as a first-class capability — not a footnote, not a "you can write it in pytest if you want", not a v0.6 roadmap promise. A real runner, a real demo, a real ship.**

Today mk-qa-master drives web (pytest / Jest / Cypress / Go) and mobile (Maestro) test suites natively. API testing is **implicit-only** — your existing pytest-with-`httpx` or Jest-with-`supertest` tests run, but there's no dedicated API runner, no OpenAPI introspection, no contract-test surface. That gap is what the family-site copy is currently hedging around.

v0.6 ships **native API testing** as the third capability of the family's execution layer. Three runners across two phases:

1. **`schemathesis`** (v0.6.0) — OpenAPI-driven test generation. Hand it a schema URL, get auto-generated API tests, run them, return structured results into mk-qa-master's standard history / flake / coach pipeline.
2. **`newman`** (v0.6.1) — Postman collection runner. Drop-in for the 100M+ Postman user base.
3. **`pact_provider`** (v0.7.0, conditional) — Pact provider verification. Reserved for if Phase 1+2 produce traction signal.

Chinese brand: continues as **AI 測試大師**. No rebrand.

---

## 2. Problem Statement

The current state has a credibility gap:

| Position | Truth |
|---|---|
| **"mk-qa-master tests web + mobile + API"** (current copy) | API is **inferred** — there's no dedicated API runner |
| **"Just write pytest with `httpx`"** (current advice) | True but underwhelming — every Python QA team already does this; nothing about mk-qa-master uniquely enables it |
| **"Schema-driven API testing"** (what QA teams actually want) | Not currently provided. **No competitor MCP provides it either** |

QA engineers reading the README come away with one of two impressions:

1. **"Pass-through API support"** — accurate but unexciting. Doesn't drive installs.
2. **"Native API support"** — what the copy implies but the runner doesn't deliver. Drives installs but produces disappointment.

Neither is the position we want. v0.6 collapses the gap by **making the copy true**.

**Hypothesis:** the same MCP that drives pytest / Jest / Cypress / Go / Maestro can drive `schemathesis` / `newman` / Pact CLIs with no architectural changes. The runner abstraction was designed for exactly this case; we just haven't filled the slot.

---

## 3. Why now

- **The family-site already promises API testing.** Phase 1 makes that promise true within 2 weeks — no need to walk back marketing copy that just shipped.
- **The Dev.to article is unwritten.** Shipping a real API runner *before* the launch post means "we support API testing" is a hard fact, not a hedged claim. Better trending odds.
- **QA reader expectations are high.** Postman has 30M users; Schemathesis ships in Cloudflare's QA pipelines; Pact is a standard at every microservice-heavy company. Saying "we test APIs" without supporting the tools they know is a credibility tax.
- **The technical bar is low.** Runner abstraction is already in place (5 runners, identical interface). A new runner is `~150 lines of Python` + `~30 lines of report normalization`. Schemathesis is the easiest to land because it's pytest-compatible under the hood.
- **No competitor MCP does this.** "First / only OSS MCP with native OpenAPI-driven API contract testing" is a real moat. The window is open.

---

## 4. Competitive Positioning

### vs. dedicated API testing tools

| Tool | What they own | Where v0.6 differs |
|---|---|---|
| **Postman / Postman CLI** | Collection editor, biggest user base | They're the editor; we're the runner that integrates into your AI client |
| **Schemathesis (standalone CLI)** | Property-based API fuzzing | We wrap the CLI + add longitudinal flake / broken classification + AI advisor |
| **Pact / PactFlow** | Contract testing, broker UI | We add the runner side (provider verification) into MCP |
| **REST Assured (Java)** | Library for API assertions | Different ecosystem; not in scope |
| **Karate** | DSL-driven API + UI test | Different DSL; not in scope |
| **Tavern** | YAML-driven HTTP test | Pytest plugin — already runs under existing `pytest` runner |

### vs. AI test generators

| Tool | Their API support | v0.6 difference |
|---|---|---|
| **Copilot Tests** | Generates unit + small integration tests from code context | No OpenAPI ingestion; no schema-aware test generation |
| **Cursor's test gen** | Same as Copilot Tests | Same gap |
| **Codeium API tests** | None native | — |

### Defensible position

> The **only MCP** that drives Schemathesis / Newman / Pact natively, with the same MCP tool surface QA engineers already use for pytest / Jest / Cypress / Go / Maestro — and threads API tests through the same history / flake / coach pipeline.

Five differentiators no competitor combines for API testing:

1. **MCP-native** — lives inside Claude / Cursor / Codex, not a separate UI
2. **Schema-aware** — OpenAPI / Postman collection ingestion, no manual test scaffolding
3. **Unified history** — API tests + UI tests + mobile tests in one `tests-history/` archive
4. **Flake / broken classification** — same logic as UI tests; an API that fails 3× with same signature = broken, not flaky
5. **Open source baseline** — no SaaS lock-in, no per-user fees

---

## 5. Target Users

**Primary:** Backend / full-stack QA engineers shipping APIs as part of a web or mobile product. Already have pytest or Jest with some API tests, but no schema-driven coverage or contract verification.

**Secondary:** Microservice teams using Pact or considering it. Currently running Pact via custom CI scripts; want it inside their AI client's tool surface.

**Tertiary:** Solo founders / indie devs who pay for Postman Team but resent the per-seat fee. Want a local-first alternative with the same workflow.

**Anti-personas:**
- Teams that only do manual API testing in Postman GUI — no automation appetite, not in scope
- Pure load testers (k6 / JMeter / Locust) — that's `mk-perf-master` territory (different MCP, deferred)

---

## 6. MVP Scope (v0.6.0 = Phase 1)

> **Decision boundary:** v0.6.0 ships **schemathesis only**. Newman and Pact wait for v0.6.1 / v0.7.0 to keep the first ship's surface tight and the demo story clean.

**In scope (v0.6.0):**
- New runner: `src/mk_qa_master/runners/schemathesis.py`
- New env vars: `QA_OPENAPI_URL` (required for `QA_RUNNER=schemathesis`)
- Config additions: schemathesis CLI invocation, checks selection, output parsing
- Optional dep in `pyproject.toml`: `[project.optional-dependencies] api = ["schemathesis>=3.0"]`
- README + README.zh-TW: replace hedged "API testing too" with concrete "QA_RUNNER=schemathesis" section
- Family-site qa-master deep page: add schemathesis to runners table, drop the v0.6-roadmap hedge
- `tests/test_smoke.py`: add schemathesis runner registration check
- `.github/workflows/ci.yml`: add a Petstore-OpenAPI smoke job (no live API, use the local sample bundled with schemathesis)
- Sample bundled at `examples/sample_api_project/openapi.yaml` — a 3-endpoint fictional API so users can try it locally without network
- `docs/walkthrough-api.md` — end-to-end example: "AI agent gets OpenAPI URL → runs schemathesis → coaches what's broken"

**Explicitly out of scope (deferred):**
- Newman runner → v0.6.1
- Pact provider verification → v0.7.0
- `analyze_api` tool (OpenAPI introspection for generate_test) → v0.7.0
- API-specific assertions in generate_test (status codes, schema match) → v0.7.0
- API mock servers (Prism, mockoon) → out of scope, MCP shouldn't own this
- Contract publishing to Pact broker → out of scope
- Web UI / dashboard → never (MCP-native)

**v0.6.0 timeline target:** ship to PyPI within **2 weeks** of starting (~3-5 working hours of actual code, plus testing, docs, PR cycle).

---

## 7. System Architecture

```
mk-qa-master/
├── src/mk_qa_master/
│   ├── runners/
│   │   ├── __init__.py        # REGISTRY, get_runner(), @register
│   │   ├── pytest.py          # existing (web — Playwright)
│   │   ├── jest.py            # existing (web)
│   │   ├── cypress.py         # existing (web)
│   │   ├── go.py              # existing (web / unit)
│   │   ├── maestro.py         # existing (mobile)
│   │   └── schemathesis.py    # NEW (v0.6.0) — API
│   ├── config.py              # add QA_OPENAPI_URL
│   └── server.py              # unchanged — runner is plug-and-play
├── examples/
│   └── sample_api_project/
│       ├── openapi.yaml       # 3-endpoint fictional API
│       └── README.md          # how to dogfood
├── tests/
│   └── test_smoke.py          # add schemathesis registration check
└── docs/
    ├── prd-v0.6-api-testing.md  # this file
    └── walkthrough-api.md       # NEW
```

**Key env vars (new):**

| Var | Required when | Purpose |
|---|---|---|
| `QA_OPENAPI_URL` | `QA_RUNNER=schemathesis` | Path or URL to OpenAPI 3.x / Swagger 2.0 schema. Supports `http(s)://` and `file://` |
| `QA_SCHEMATHESIS_CHECKS` | optional | Comma-separated subset of checks (default: `all`) |
| `QA_SCHEMATHESIS_AUTH` | optional | Auth header / token for API requests (passed via `-H "Authorization: ..."`) |

---

## 8. Tool Surface

**v0.6.0 introduces no new MCP tools.** Existing tools transparently handle schemathesis via the runner abstraction:

| Existing tool | Behavior with `QA_RUNNER=schemathesis` |
|---|---|
| `get_runner_info` | Returns `current: "schemathesis"`, `available: [...schemathesis...]` |
| `list_tests` | Returns endpoint coverage: `POST /pet`, `GET /pet/{id}`, etc. — one "test" per endpoint × check type |
| `run_tests` | Invokes `schemathesis run --checks <X> <openapi_url>`. Parses JSON output into mk-qa-master report shape |
| `run_failed` | Reruns only the failed endpoint × check combinations from last run |
| `get_test_report` | Same shape as existing; per-endpoint pass/fail with request/response captured as artifact |
| `get_failure_details` | Returns the failing request + response + Schemathesis violation reason |
| `generate_test` | Currently emits Playwright / Maestro. For API tests this is **out of scope** in v0.6.0 — `schemathesis` already generates the tests internally from the schema. v0.7.0 may add `--render-pytest` to emit standalone pytest files |
| `analyze_url` | Currently DOM-only. For API equivalence we'd need a new `analyze_api` tool — **deferred to v0.7.0** |
| `auto_generate_tests` | Not applicable for schemathesis (the schema *is* the source of truth) |
| `codegen` | Out of scope |
| `get_optimization_plan` | Same 3-lens advisor, but API tests now appear in the suite-quality lens. Broken/flaky classification works identically |
| `get_test_history` | Includes API tests in trend output |
| `init_qa_knowledge` / `get_qa_context` | `qa-knowledge.md` gets a new section template: API auth patterns, common API anti-patterns (chatty endpoints, missing pagination, etc.) |

**v0.7.0 will add (deferred):**

- `analyze_api(openapi_url)` — return discovered endpoints + auth flows + candidate test scenarios
- `generate_api_test(endpoint, scenarios)` — emit a runnable pytest + `httpx` test file (for users who want code, not Schemathesis fuzz)

---

## 9. Data Model

**No new persistent state.** Schemathesis runner reuses existing `tests-history/<timestamp>/report.json` schema.

One subtle addition: API test reports include a new `artifacts.request_response` field per failed test:

```json
{
  "nodeid": "POST /pet :: response_conformance",
  "outcome": "failed",
  "duration": 0.18,
  "call": {
    "longrepr": "Response did not conform to schema: status 500, expected 200|400|404"
  },
  "artifacts": {
    "request_response": {
      "method": "POST",
      "url": "https://petstore.example.com/pet",
      "request_body": "{\"name\": \"\\u0000\"}",
      "response_status": 500,
      "response_body": "Internal Server Error",
      "violation": "status_code_conformance"
    }
  }
}
```

This is **additive**. UI tests' `artifacts.screenshot` / `artifacts.video` keep working unchanged.

---

## 10. Runner Design

### `schemathesis.py` — interface contract

```python
@register("schemathesis")
class SchemathesisRunner:
    name = "schemathesis"

    def __init__(self):
        self.openapi_url = config.QA_OPENAPI_URL
        if not self.openapi_url:
            raise ValueError(
                "QA_OPENAPI_URL is required for the schemathesis runner. "
                "Set it to an OpenAPI 3.x URL (http(s)://...) or file path (file://...)"
            )

    def list_tests(self) -> str:
        """schemathesis run --list-operations <url> → endpoint list."""
        ...

    def run_tests(self, filter: str | None = None, **kwargs) -> dict:
        """
        Invoke `schemathesis run --checks all --hypothesis-database=none <url>`.
        Parse JSON output → mk-qa-master standard report shape.
        Capture per-endpoint request/response into artifacts.
        Write JUnit XML compatible with reporter.
        """
        ...

    def run_failed(self) -> dict:
        """Read last failed endpoints from history → rerun only those."""
        ...
```

### Schemathesis CLI invocation

Base command:
```bash
schemathesis run \
  --checks all \
  --hypothesis-database=none \
  --report-json /tmp/sch-report.json \
  --junit-xml /tmp/sch-junit.xml \
  --hypothesis-max-examples=20 \
  <openapi_url>
```

Customization via env:
- `QA_SCHEMATHESIS_CHECKS=response_schema_conformance,status_code_conformance` → restrict checks
- `QA_SCHEMATHESIS_AUTH="Bearer xxx"` → injected as `--header "Authorization: Bearer xxx"`
- `QA_SCHEMATHESIS_MAX_EXAMPLES=50` → bump fuzz examples per endpoint

### Output normalization

Schemathesis emits its own JSON report shape. We map to mk-qa-master's `report.json` (already used by pytest / Jest / etc. via `pytest-json-report`).

Mapping table:

| Schemathesis field | mk-qa-master field |
|---|---|
| `checks[].method + path` | `nodeid` (formatted as `"POST /pet :: response_conformance"`) |
| `checks[].status` ("success" / "failure" / "error") | `outcome` ("passed" / "failed" / "error") |
| `checks[].seed` + `checks[].request` | `artifacts.request_response.request_body` |
| `checks[].response` | `artifacts.request_response.response_body` |
| `checks[].message` | `call.longrepr` |
| `summary.total_time` | `duration` per test (split proportionally) |

---

## 11. Integration with mk-spec-master + mk-plan-master

**No new MCP-to-MCP RPC** (same as the existing family). The AI client orchestrates.

New canonical chain enabled by v0.6.0:

```
1.  user: "Test the API at https://api.example.com/openapi.json"
2.  mk-qa-master.get_runner_info()                  → current: schemathesis
3.  mk-qa-master.run_tests()                        → 24 endpoints × 5 checks = 120 cases
                                                       8 failed (3 endpoints have schema violations)
4.  mk-qa-master.get_optimization_plan()            → broken: POST /pet (3 consec fails, same sig)
                                                       flaky: GET /pet/{id} (PFPFP pattern)
5.  mk-qa-master.get_failure_details("POST /pet :: response_conformance")
                                                    → request body, response body, violation
6.  user fixes the API in their IDE
7.  mk-qa-master.run_failed()                       → now 0 failures
8.  mk-spec-master.link_test_to_spec(...)           → tie API tests back to acceptance criteria
```

For the full pipeline (Idea → API tests):

```
mk-plan-master.generate_spec_draft         → Markdown spec
mk-spec-master.parse_spec → extract_scenarios → API endpoint behaviors
[user writes the API + OpenAPI spec in their IDE]
mk-qa-master (QA_RUNNER=schemathesis) → run_tests → coverage
```

**This is the first chain where the family's "code in your IDE" boundary is on the *API* side, not the UI side.** Marketing angle: "Plan → Spec → API → Test, decomposed."

---

## 12. Self-reinforcement

Schemathesis tests inherit the entire existing optimizer pipeline. No changes needed — the abstraction handles it.

**Suite quality lens** for API tests:

- A `POST /pet` that fails 3 consecutive runs with identical Schemathesis violation → **broken** (real API contract bug)
- A `GET /pet/{id}` with `flake_score >= 0.3` over 5 runs → **flaky** (likely auth token expiry, race, or non-deterministic backend)
- A `DELETE /pet/{id}` that runs 5× and passes every time but takes 4s → no special category, but slow_regression triggers if past avg was 0.8s
- A previously-broken endpoint that's been green for 3 runs → still tracked as "recovering" until 5 stable

**MCP usability lens** picks up API patterns:

- Same `args_hash` for `run_tests` called repeatedly with no filter change → suggests caching
- `analyze_url` → `run_tests` chain is the dominant pair → API analog will be `analyze_api` → `run_tests` (when shipped)

**AI effectiveness lens** for API tests:

- For schemathesis, "generated test adoption" is N/A (Schemathesis generates internally from schema)
- For v0.7.0 `analyze_api` → `generate_api_test` chain, adoption rate measured the same way as UI

---

## 13. Non-functional Requirements

| Concern | Requirement |
|---|---|
| **Privacy** | API request/response bodies are stored under `tests-history/`. **Default: redact common secret patterns** (Bearer tokens, API keys, password fields). `QA_NO_REDACT=1` to disable for debugging. |
| **Performance** | Schemathesis with default `max-examples=20` against a 10-endpoint API completes in ~30s. Wall clock kept under `QA_TIMEOUT_SECONDS` (default 600). |
| **Storage** | Per-run report size grows ~2-5KB per endpoint × checks. For a 50-endpoint API at full check coverage, ~500KB per run. History rotation at 100 runs keeps it under 50MB. |
| **Auth** | Tokens / API keys ride in env vars or `QA_SCHEMATHESIS_AUTH`. Never logged. |
| **Errors** | Adapter raises structured `{error, retryable, hint}` (mirror existing runners). |
| **Compatibility** | Python 3.10+, schemathesis>=3.0, MCP SDK >=1.0.0. Optional dep — installs only when user does `pip install 'mk-qa-master[api]'`. |

---

## 14. Roadmap

| Milestone | Scope | Target |
|---|---|---|
| **v0.6.0 (Phase 1)** | `schemathesis` runner, env vars, README + site updates, sample API project, CI smoke job | ~2 weeks |
| **v0.6.1 (Phase 2)** | `newman` runner (Postman collections). `QA_POSTMAN_COLLECTION` / `QA_POSTMAN_ENVIRONMENT` env vars | +1 week |
| **v0.7.0 (Phase 3, conditional)** | `pact_provider` runner + `analyze_api` tool + `generate_api_test` extension. Gated on v0.6.x receiving real adoption signal | +3 weeks |
| **v1.0** | All three runners + analyze_api + production-ready API testing docs | Q4 2026 |

**Realistic calendar** (assuming the same ~30 hrs/week pace as plan-master):
- v0.6.0 ship: ~2026-05-31
- v0.6.1 ship: ~2026-06-10
- v0.7.0 ship: dependent on signal — could be Q3 2026 or never if Postman / Schemathesis cover the user base

---

## 15. Open Questions / Risks

| # | Question | Mitigation |
|---|---|---|
| Q1 | Schemathesis defaults to `--hypothesis-database=.hypothesis` for shrinking — should we keep or disable? | Default disable (`--hypothesis-database=none`) to keep tests deterministic across runs; expose `QA_SCHEMATHESIS_DB_PATH` for users who want reproducibility |
| Q2 | OpenAPI specs are often huge (200+ endpoints). Should we cap `list_tests` output? | Yes — same 200-line cap as `list_tests` for other runners; full list via JUnit XML |
| Q3 | What if the OpenAPI URL requires auth? | `QA_SCHEMATHESIS_AUTH` env var passes Authorization header. For OAuth flows, document a "use mock server" pattern |
| Q4 | Schemathesis can issue *destructive* requests (POST/DELETE on real endpoints). Safety? | Document explicitly: "**default config will hit your real API**. Use `--dry-run` via `QA_SCHEMATHESIS_DRY_RUN=1` for non-mutating preview, or point at a staging URL." |
| R1 | Schemathesis CLI changes incompatibly between major versions | Pin `schemathesis>=3.0,<4` in optional dep; tracking issue for v4 upgrade |
| R2 | Users without OpenAPI schema can't use this runner | Plan-master can help generate OpenAPI from natural-language spec (future). Out of scope for v0.6 |
| R3 | Adoption could be lower than UI testing because mid-stage QA teams haven't standardized on OpenAPI | The Newman runner (v0.6.1) covers the Postman segment, which is much larger |
| R4 | The "we test web + mobile + API" copy bumps mk-qa-master into more competitive comparison (vs. specialist API testing vendors) | Stay honest about scope — we're a runner orchestrator, not a Postman replacement |

---

## 16. Success Metrics

**Adoption (3 months from v0.6.0):**
- 50+ uses of `QA_RUNNER=schemathesis` per week (tracked via telemetry, anonymously)
- 5 unsolicited Issues / PRs mentioning API testing
- 1 external blog post / tweet mentioning the feature
- mk-qa-master starts being listed on "MCP for API testing" comparison posts

**Quality (any time):**
- 100% backwards-compatible — existing pytest / Jest / Cypress / Go / Maestro users see no regression
- Sample API project (`examples/sample_api_project/`) passes on every CI run
- Glama coherence score doesn't drop (would mean we broke tool listing)

**Family effect (6 months):**
- v0.6.1 (Newman) ships if v0.6.0 has any pull
- Dev.to launch post + Show HN both lead with "API testing in your AI client" hook
- Family-site qa-master deep page becomes the #1 referrer to GitHub stars (proxy for narrative clarity)

---

## 17. Open Source Strategy

- License: MIT (mirror existing mk-qa-master)
- Repo: same repo, branch `feat/api-testing-v0.6` → PR → squash merge to `main`
- CI from day 1 (extend existing `ci.yml` with a `schemathesis` job)
- Optional dep group (`pip install 'mk-qa-master[api]'`) so the base install stays small
- Sample API spec bundled at `examples/sample_api_project/openapi.yaml` (no external dependency)
- Blog post on launch: extend the planned Dev.to article with an "and API too" section, or run a follow-up post specifically on API testing
- Show HN on v0.6.0 ship day: "Show HN: mk-qa-master v0.6 — Schemathesis runner so Claude can drive OpenAPI-driven API tests"

---

## 18. Naming

| Surface | Value |
|---|---|
| Feature name (EN) | Native API testing |
| Feature name (中) | 原生 API 測試 |
| Runner id | `schemathesis` (Phase 1), `newman` (Phase 2), `pact_provider` (Phase 3) |
| Env var prefix | Existing `QA_*` continues. `QA_OPENAPI_URL`, `QA_POSTMAN_COLLECTION`, `QA_PACT_*` |
| Version target | v0.6.0 (Phase 1 only) |
| Tagline in README | "Web · mobile · API — your AI drives all three" |

---

## 19. Walkthrough Example

A solo dev shipping a SaaS with a public REST API.

**Step 1 — In Claude / Cursor:**
> "Run schema-driven tests against my staging API at https://staging.example.com/openapi.json — find anything broken, then give me a prioritized action plan."

**Step 2 — AI orchestrates:**

```
mk-qa-master.get_runner_info()
  → current: schemathesis, available: [pytest, jest, cypress, go, maestro, schemathesis]

mk-qa-master.list_tests()
  → 24 endpoints, 5 checks each = 120 test cases

mk-qa-master.run_tests()
  → 112 passed, 6 failed, 2 errored
  → execution time: 47s
  → failures:
      POST /users :: response_schema_conformance (3 cases)
      DELETE /users/{id} :: status_code_conformance (1 case)
      GET /search :: not_a_server_error (2 cases — actual 500s under fuzzing)

mk-qa-master.get_optimization_plan()
  → 🔴 broken  · POST /users :: response_schema_conformance
    Same Schemathesis signature × 3 → "Returned status 500 expected 201|400"
    Action: response schema doesn't allow 500; either fix the validation
    bug or update the schema's responses block

  → 🔴 broken  · GET /search (status 500 on edge inputs)
    Crashes under `?q=null` and `?limit=-1`
    Action: input validation missing on the search handler

  → 🟡 warn    · DELETE /users/{id} returned 204 when schema says 200
    Likely safe to update schema; verify with PM

  → 🟢 stable  · 18 endpoints, no findings

user: "Walk me through the POST /users failure."

mk-qa-master.get_failure_details("POST /users :: response_schema_conformance")
  → request:
      POST /users
      Content-Type: application/json
      { "email": "x@x", "age": -1 }
    response:
      status: 500
      body: "Internal Server Error"
    violation:
      "Status 500 is not in the OpenAPI response definitions {201, 400}"

user: "Fix the validation in src/users/create.ts."

[user fixes in their IDE]

mk-qa-master.run_failed()
  → 8/8 previously-failed cases now passing
  → 0 regressions
```

**Step 3 — The user has, in one AI session:**
1. Validated 120 test cases against the spec
2. Identified 3 real bugs without writing a single test
3. Got prioritized fix order with evidence
4. Iterated until green

**Total time:** ~6 minutes, vs the usual half-day of "write tests, run, debug" cycle.

**This is the v0.6 demo video.** This is the Show HN screenshot.

---

## 20. Decision Required Before Coding

1. **Confirm scope of v0.6.0** — schemathesis only? Or bundle Newman in the same release?
2. **`QA_OPENAPI_URL` shape** — accept `http(s)://`, `file://`, and plain filesystem path? Pick 1-2 patterns.
3. **Optional dep vs hard dep** — should `schemathesis` install with `pip install mk-qa-master` (hard) or only `pip install 'mk-qa-master[api]'` (optional)? Recommendation: optional, keeps base install slim.
4. **Sample API project** — bundle a 3-endpoint fictional OpenAPI? Use the public Petstore swagger? Build both with a flag?
5. **Public PRD timing** — publish this doc on the repo Day 1 (matches mk-plan-master pattern) or hold until v0.6.0 ships?

---

## 21. Decisions ratified

Locked 2026-05-17 after PRD review:

1. **v0.6.0 scope** — `schemathesis` runner ONLY. Newman moves to v0.6.1 to keep the first ship's demo story clean.
2. **`QA_OPENAPI_URL` shape** — accept `http(s)://` and `file://` only. Plain filesystem paths require `file://` prefix to avoid ambiguity with relative-vs-absolute resolution.
3. **Dependency model** — `schemathesis` is an **optional dep**. Users install via `pip install 'mk-qa-master[api]'`. Base install stays slim; the runner module imports schemathesis lazily and raises a clear `ImportError` with the install hint if missing.
4. **Sample API project** — bundle a **3-endpoint fictional OpenAPI** at `examples/sample_api_project/openapi.yaml`. Self-contained, no external service, CI-friendly.
5. **PRD public timing** — Day 1 (this commit). Mirrors mk-plan-master's build-in-public pattern; no reason to hide.

---

## 22. v0.6.1 ratified

Locked 2026-05-16 alongside the Phase 2 (Newman) build:

1. **Scope** — Newman runner ONLY in v0.6.1. No new MCP tools (the
   existing 16-tool surface drives Newman the same way it drives
   Schemathesis). Pact provider verification stays deferred to v0.7.0.
2. **CLI dependency model** — Newman is an **npm** package, not pip, so
   it cannot ship as an optional dep in `pyproject.toml`. Document as a
   system prerequisite (`npm install -g newman`). The runner detects
   the CLI via `shutil.which("newman")` and raises a clear
   `ImportError` with the install hint if missing — same UX as the
   Schemathesis import-error path.
3. **Sample collection** — bundle a self-contained Postman 2.1.0
   collection at `examples/sample_api_project/postman-collection.json`.
   Three requests targeting the same fictional Library API as the
   existing `openapi.yaml`. Each request wraps `pm.test(...)`
   assertions covering status code + response shape. `{{baseUrl}}`
   collection variable defaults to `http://localhost:4010` to mirror
   the OpenAPI sample's `servers[0].url`.
4. **Report parsing** — use Newman's JSON reporter via
   `--reporter-json-export <path>`. (Different from Schemathesis, which
   parses JUnit XML because Schemathesis 3.x has no JSON-report flag —
   Newman's JSON output is richer than its JUnit, so we use the richer
   source.) Per-execution × per-assertion mapping yields one
   mk-qa-master nodeid per `pm.test(...)` call.
5. **File-path requirement** — `QA_POSTMAN_COLLECTION` accepts a plain
   filesystem path only. **No `file://` scheme** — Newman doesn't need
   scheme disambiguation since collections are always local artifacts.
   Matches what users will paste in directly. (Schemathesis kept the
   `file://` requirement because OpenAPI URLs are also a valid input
   for that runner; Newman has no such dual-mode.)

---

*End of PRD v0.1 for mk-qa-master v0.6. Discussion in mk-qa-master Issues once a draft is opened.*
