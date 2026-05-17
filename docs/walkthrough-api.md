# Walkthrough — Native API testing

mk-qa-master ships two native API runners as of v0.6.1:

- **Track 1 — Schemathesis** (since v0.6.0): point at an OpenAPI 3.x
  schema and get property-based fuzz coverage of every operation.
- **Track 2 — Newman** (since v0.6.1): point at a Postman 2.x collection
  and replay every request with its `pm.test(...)` assertions.

Pick whichever matches how your team already documents the API. The two
tracks share the same MCP tool surface (`run_tests`, `get_failure_details`,
`get_optimization_plan`, etc.) and the same `report.json` / history /
optimizer pipeline — only `QA_RUNNER` and the source-of-truth env var
change between them.

This document covers Track 1 first (Schemathesis), then Track 2 (Newman).

---

## Track 1 — Schemathesis (OpenAPI-driven)

This walkthrough shows the end-to-end loop for testing an OpenAPI-defined
API with mk-qa-master v0.6.0 using the bundled 3-endpoint sample. By the
end you'll have run property-based fuzz tests, read a failure with the
exact request + response captured, fixed the bug, and seen `run_failed`
return zero failures — all from a single AI client session.

The chain has no new MCP tools — the existing 16-tool surface drives the
schemathesis runner transparently. The only thing that changes is the
`QA_RUNNER` env var.

---

## Prerequisites

```bash
pip install 'mk-qa-master[api]'
```

The `[api]` extra pulls in `schemathesis>=3.0,<4`. The base install
stays slim — users who never run API tests don't pay for the dependency.

You don't need Playwright, Maestro, or any other runner installed for
this walkthrough. Schemathesis is a Python package with a CLI; it talks
HTTP, not browsers.

## The sample API

`examples/sample_api_project/openapi.yaml` defines a fictional Library
API with three operations:

| Method + path | Purpose |
|---|---|
| `GET /books` | List books (`limit` query param, 1..100) |
| `POST /books` | Add a book (`{title, author, published_year?}`) |
| `GET /books/{id}` | Fetch one book by id |

Two response schemas (`Book`, `Error`) constrain the shape Schemathesis
will check against. No external `$ref`s, no auth — keeps the demo
hermetic.

## Client config

Drop this into your `claude_desktop_config.json` (or the equivalent for
Cursor / Codex / Gemini CLI):

```jsonc
{
  "mcpServers": {
    "mk-qa-master": {
      "command": "uvx",
      "args": ["mk-qa-master"],
      "env": {
        "QA_RUNNER": "schemathesis",
        "QA_OPENAPI_URL": "file:///absolute/path/to/examples/sample_api_project/openapi.yaml",
        "QA_SCHEMATHESIS_DRY_RUN": "1"
      }
    }
  }
}
```

`QA_SCHEMATHESIS_DRY_RUN=1` makes Schemathesis plan operations without
issuing real HTTP — perfect for a dry-walkthrough against a schema-only
artifact. Drop the var (or set it to `0`) and point at a running server
to do a real fuzz pass.

> **Heads up — destructive requests**: by default Schemathesis will
> issue real `POST` / `DELETE` calls against whatever URL is in the
> schema's `servers[0].url`. Either point at a staging URL, use
> `QA_SCHEMATHESIS_DRY_RUN=1`, or stand up a local mock (Prism, mockoon)
> before pointing at a production schema.

## Session transcript

What follows is a transcript of how the AI client orchestrates the
tools. The MCP tool calls are explicit so you can map them onto your own
client; in practice the user just types natural language.

### 1. `get_runner_info` — confirm the runner is wired

> **You**: Which runner is mk-qa-master using right now?

```json
{
  "current": "schemathesis",
  "available": [
    "pytest", "pytest-playwright", "playwright",
    "jest", "cypress", "go", "go-test",
    "maestro", "mobile",
    "schemathesis", "api"
  ]
}
```

### 2. `list_tests` — enumerate operations from the schema

> **You**: List the API operations.

```text
GET /books
POST /books
GET /books/{id}
```

The runner shells out to `schemathesis run --dry-run --no-color <url>`,
parses the operation lines, and caps output at 200 lines for large
schemas. Three operations, one per row.

### 3. `run_tests` — fuzz the API

> **You**: Run the tests.

(With `QA_SCHEMATHESIS_DRY_RUN=1` against the bundled sample, the
runner exercises schema parsing and operation enumeration without
issuing real HTTP. Set it to `0` and point at a running server for a
full fuzz pass.)

Expected output against a real backend with one bug:

```json
{
  "exit_code": 1,
  "raw_exit_code": 1,
  "openapi_url": "file:///.../openapi.yaml",
  "stdout_tail": "... POST /books FAILED response_schema_conformance ... "
}
```

`get_test_report` then summarizes:

```json
{
  "total": 9,
  "passed": 7,
  "failed": 2,
  "skipped": 0,
  "flaky_in_run": 0,
  "duration": 14.3
}
```

(9 = three operations × three default checks. Exact count depends on
which checks pass through `QA_SCHEMATHESIS_CHECKS`.)

### 4. `get_optimization_plan` — prioritize the fix

> **You**: What should I fix next?

```markdown
### 1. 🔴 HIGH — broken
- **Target**: POST /books :: response_schema_conformance
- **Evidence**: 3 consecutive runs, identical Schemathesis signature
  ("Response status 500 not in {201, 400}")
- **Suggestion**: response schema doesn't allow 500; either harden the
  validation path or add 500 to the responses block (probably not what
  you want).

### 2. 🟡 MEDIUM — broken
- **Target**: GET /books/{id} :: status_code_conformance
- **Evidence**: returns 204 No Content; schema says 200 or 404
- **Suggestion**: align response code with schema; verify intent with PM
```

### 5. `get_failure_details` — see the actual request + response

> **You**: Show me the POST /books failure.

```json
{
  "nodeid": "POST /books :: response_schema_conformance",
  "message": "Response did not conform to schema: status 500, expected 201|400",
  "duration": 0.18,
  "artifacts": {
    "request_response": {
      "method": "POST",
      "url": "http://localhost:4010/books",
      "request_body": "{\"title\": \"\\u0000\", \"author\": \"x\"}",
      "response_status": 500,
      "response_body": "Internal Server Error",
      "violation": "response_schema_conformance"
    }
  }
}
```

Schemathesis found that a null byte in `title` crashes the validator.
The OpenAPI schema constrains `title` to `minLength: 1, maxLength: 200`
but allows any string — including `"\u0000"`. The bug is upstream of
the schema check.

> **Secret note**: `Authorization`, `password`, `token`, `api_key`,
> `secret`, `access_token`, and `refresh_token` values are redacted to
> `[REDACTED]` in archived reports by default. Set `QA_NO_REDACT=1`
> only for short debug sessions.

### 6. User fixes the bug in their IDE

A normal fix: strip control characters from input or 400 on invalid
unicode. The user makes the change, restarts the API (or relies on a
mock with the fix applied), and re-runs only the previously-failing
operations.

### 7. `run_failed` — verify the fix without re-running passes

> **You**: Re-run just the failures.

```json
{
  "exit_code": 0,
  "raw_exit_code": 0,
  "ops_rerun": 2,
  "stdout_tail": "... 2 passed in 3.1s"
}
```

The runner reads the previous `report.json`, extracts failed
`(method, path)` pairs, and re-invokes Schemathesis with
`--include-method` and `--include-path` filters — scoping the second
run to exactly the operations that needed verification.

`get_test_report` confirms:

```json
{ "total": 2, "passed": 2, "failed": 0, "duration": 3.1 }
```

Zero failures, two operations re-verified.

---

## What just happened

In a single AI session, the user:

1. Validated the API's behavior against its own OpenAPI contract — 7+
   property-based test cases per operation, autogenerated.
2. Got two real bugs identified with the exact request body that
   triggered each, the actual response, and the schema clause that was
   violated.
3. Got the bugs ranked (broken vs flaky vs warn) so the order of fixes
   is data-driven, not gut-feel.
4. Iterated to green by re-running only the operations that needed it.

No tests were authored by hand. The schema is the source of truth; the
runner fuzzes against it.

## Where this fits in the family pipeline

```
mk-plan-master.generate_spec_draft   → Markdown spec
mk-spec-master.parse_spec            → extracted scenarios + acceptance criteria
[user writes the API + OpenAPI schema in their IDE]
mk-qa-master (QA_RUNNER=schemathesis) → run_tests → coverage
```

This is the first chain where the family's "code in your IDE" boundary
is on the API side, not the UI side. The OpenAPI schema acts as the
contract between the spec layer and the test layer — no manual test
scaffolding, no boilerplate.

## Knobs worth knowing

| Env | Default | When to change |
|---|---|---|
| `QA_SCHEMATHESIS_MAX_EXAMPLES` | `20` | Bump to `100`+ for nightly / pre-release fuzz; keep low for PR-time. |
| `QA_SCHEMATHESIS_CHECKS` | `all` | Restrict to a subset when isolating a single class of bug (e.g. only `response_schema_conformance` to ignore status-code drift while the API is still settling). |
| `QA_SCHEMATHESIS_AUTH` | — | Set to your bearer token / API key when the API requires auth. Format: `"Bearer xxx"` or whatever the API expects after `Authorization: `. |
| `QA_SCHEMATHESIS_DRY_RUN` | `0` | `1` for plan-without-HTTP; useful when pointing at production for a safety preview. |
| `QA_NO_REDACT` | `0` | `1` only when debugging redaction itself — archived reports may be shared. |
| `QA_TIMEOUT_SECONDS` | `600` | Bump for very large schemas (200+ endpoints × deep fuzz). |

## Where to go from here

- **Try it on your own API**: point `QA_OPENAPI_URL` at an OpenAPI URL
  you already have. Most modern frameworks (FastAPI, NestJS, ASP.NET
  Core, Spring Boot, Go-Swagger) ship one out of the box at
  `/openapi.json` or `/swagger.json`.
- **Pair with a mock**: spin up `npx @stoplight/prism-cli mock openapi.yaml`
  for a self-contained dev loop (see
  `examples/sample_api_project/README.md`).
- **Cross-runner workflows**: API tests live in the same `report.json`
  / history archive as UI / mobile tests. The optimizer ranks them
  side-by-side. A single `get_optimization_plan` call surfaces the
  weakest link across all three layers.

---

## Track 2 — Newman (Postman collections)

If your team's source of truth is a hand-curated Postman collection
rather than an OpenAPI schema, the Newman runner replays the collection
end-to-end and runs every embedded `pm.test(...)` assertion. Same MCP
tool surface, same `report.json` shape — just a different runner key
and a different source artifact.

### Prerequisites

Newman ships via **npm**, not pip:

```bash
npm install -g newman
```

There's no `mk-qa-master[postman]` extra to install. The runner shells
out to the `newman` binary on PATH; if it's missing, you'll get a clear
`ImportError` pointing at the install line.

### The bundled Postman sample

`examples/sample_api_project/postman-collection.json` defines the same
fictional Library API as the OpenAPI sample, organized into a single
`Books` folder with three requests:

| Method + path | Assertions (via `pm.test(...)`) |
|---|---|
| `GET /books` | 200 status · response is an array |
| `POST /books` | 201 status · response has `id` (cached for next request) |
| `GET /books/{id}` | 200 status · response `id` matches the one captured above |

A `{{baseUrl}}` collection variable (default `http://localhost:4010`)
lets you point at a Prism mock running the bundled OpenAPI schema, or
at any real backend, without editing the file.

### Client config

```jsonc
{
  "mcpServers": {
    "mk-qa-master": {
      "command": "uvx",
      "args": ["mk-qa-master"],
      "env": {
        "QA_RUNNER": "newman",
        "QA_POSTMAN_COLLECTION": "/absolute/path/to/examples/sample_api_project/postman-collection.json"
      }
    }
  }
}
```

Unlike `QA_OPENAPI_URL`, `QA_POSTMAN_COLLECTION` accepts a **plain
filesystem path** — no `file://` prefix. Postman collections are always
local artifacts, so the scheme-disambiguation argument the OpenAPI case
makes doesn't apply here.

### Session transcript

The MCP tool calls stay the same — only the runner-side semantics change.

**1. `get_runner_info`**

```json
{
  "current": "newman",
  "available": [
    "pytest", "pytest-playwright", "playwright",
    "jest", "cypress", "go", "go-test",
    "maestro", "mobile",
    "schemathesis", "api",
    "newman", "postman"
  ]
}
```

**2. `list_tests`**

The runner parses the collection JSON locally (no subprocess) and emits
one line per request, including the folder breadcrumb:

```text
GET {{baseUrl}}/books?limit=20 :: Books :: List books
POST {{baseUrl}}/books :: Books :: Create book
GET {{baseUrl}}/books/{{bookId}} :: Books :: Get book by id
```

**3. `run_tests`**

Newman replays each request and runs the embedded `pm.test(...)` calls.
The JSON report Newman emits gets translated into mk-qa-master's
`report.json` shape: **one mk-qa-master "test" per pm.test assertion**.
Three requests × 2 assertions each = 6 nodeids.

Against a Prism mock that conforms to the schema, all 6 pass. Against a
real backend with a bug, you might see:

```json
{
  "total": 6,
  "passed": 4,
  "failed": 2,
  "skipped": 0,
  "duration": 1.4
}
```

**4. `get_failure_details`**

```json
{
  "nodeid": "POST Create book :: POST /books response has id",
  "message": "expected undefined to have property 'id'",
  "duration": 0.18,
  "artifacts": {
    "request_response": {
      "method": "POST",
      "url": "http://staging.example.com/books",
      "request_body": "{\"title\": \"The Pragmatic Programmer\", \"author\": \"Andrew Hunt\"}",
      "response_status": 201,
      "response_body": "{\"title\": \"The Pragmatic Programmer\"}",
      "violation": "POST /books response has id",
      "parent_folder": "Books"
    }
  }
}
```

The runner captured the exact request body that triggered the failure,
the actual response (status 201 but missing the `id` field the schema
expects), and the assertion message verbatim — same artifact shape as
the Schemathesis runner, so downstream tools (HTML reporter, optimizer)
treat it identically.

**5. `run_failed`**

The runner reads the previous `report.json`, extracts the
`parent_folder` from each failed nodeid's artifacts, and re-runs Newman
scoped to those folders via `--folder` flags. If the collection has no
folder structure (everything at the root), `run_failed` degrades to a
full re-run — which is still cheap on small collections.

### Knobs worth knowing

| Env | Default | When to change |
|---|---|---|
| `QA_POSTMAN_ENVIRONMENT` | — | Point at a Postman environment file with `baseUrl` / credentials. Lets you keep the collection itself environment-agnostic. |
| `QA_POSTMAN_GLOBALS` | — | Same shape as environment, globally scoped. Rarely needed in solo workflows. |
| `QA_POSTMAN_ITERATIONS` | `1` | Soak / flake detection — replay the collection 10× / 100× and see which assertions flap. The optimizer's flake-score logic picks it up automatically. |
| `QA_POSTMAN_FOLDER` | — | CSV of folder names. Useful for "only run the auth flow folder" on a large collection. |
| `QA_POSTMAN_TIMEOUT_REQUEST_MS` | `30000` | Tighten for fast local mocks (250–1000ms catches hung endpoints quickly). |
| `QA_NO_REDACT` | `0` | Same redaction policy as Schemathesis. Default redacts `Authorization`, `password`, `token`, `api_key`, `secret`, `access_token`, `refresh_token`. |

### When to choose Newman vs Schemathesis

Both runners target the same outcome (verified API behavior), but the
artifact each consumes differs:

| Pick Newman when | Pick Schemathesis when |
|---|---|
| You already maintain a Postman collection | You already maintain an OpenAPI schema |
| You want concrete, hand-authored assertions per request | You want property-based fuzz coverage of every operation |
| Your team flows use `pm.environment.set(...)` chaining between requests | The schema is the source of truth and you trust it |
| You're testing a specific user flow (login → cart → checkout) | You're testing a public REST contract for breakage under fuzz |

Nothing stops you from running both side-by-side — `QA_RUNNER` is just
an env var, and the report archive is shared. A nightly CI run could
fire Schemathesis (broad coverage), and a per-PR run could fire Newman
(targeted flows). Both feed the same optimizer.
