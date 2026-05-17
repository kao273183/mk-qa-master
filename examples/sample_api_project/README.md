# Sample API Project — `examples/sample_api_project/`

A 3-endpoint fictional **Library API** (`/books` GET + POST, `/books/{id}` GET)
shipped with mk-qa-master so you can dogfood the native API runners
without standing up a backend.

Two equivalent surfaces are bundled — one per runner:

- **OpenAPI 3.0.3** schema → exercised by the **Schemathesis** runner
  (`QA_RUNNER=schemathesis`, v0.6.0).
- **Postman 2.1.0** collection → exercised by the **Newman** runner
  (`QA_RUNNER=newman`, v0.6.1).

Both target the same fictional API, so you can compare runner output
side-by-side. The Newman path requires `npm install -g newman` (Newman
is shipped via npm, not pip — see *Option 3* below).

## Files

- `openapi.yaml` — OpenAPI 3.0.3 schema. Valid, self-contained, no
  external `$ref`s. Parses cleanly under Schemathesis 3.x.
- `postman-collection.json` — Postman v2.1.0 collection with 3 requests
  (each wrapped in `pm.test(...)` assertions). Parses cleanly under
  Newman 6.x.

## Option 1 — Dry-run smoke (no HTTP, no mock server)

The fastest way to verify the runner integration end-to-end. Schemathesis
loads the schema, plans operations, and returns without issuing requests:

```bash
pip install 'mk-qa-master[api]'

export QA_RUNNER=schemathesis
export QA_OPENAPI_URL="file://$(pwd)/openapi.yaml"
export QA_SCHEMATHESIS_DRY_RUN=1

# From inside an MCP client:
# - get_runner_info → current: schemathesis
# - list_tests       → POST /books, GET /books, GET /books/{id}
# - run_tests        → all operations pass (dry-run; no real HTTP)
```

## Option 2 — Full fuzz against a local Prism mock

For an end-to-end loop (real HTTP, real fuzzed payloads, real
property-based assertions), spin up Prism as a mock server first:

```bash
# In one terminal — start Prism (Node 18+ required)
npx -y @stoplight/prism-cli mock examples/sample_api_project/openapi.yaml
# Prism prints something like: > Prism is listening on http://127.0.0.1:4010

# In another terminal — point the runner at the local mock URL.
# We still use file:// for the *schema*; the schema's `servers[0].url`
# tells Schemathesis where to hit.
export QA_RUNNER=schemathesis
export QA_OPENAPI_URL="file://$(pwd)/examples/sample_api_project/openapi.yaml"
unset QA_SCHEMATHESIS_DRY_RUN   # turn live HTTP back on

# Then in your MCP client: `run_tests`
```

Prism by default returns example responses that conform to the schema, so
you should see all checks pass. Useful for verifying the report.json
shape + history archiving.

## What you can expect to see

- `list_tests` returns:
  ```
  GET /books
  POST /books
  GET /books/{id}
  ```
- `run_tests` writes `report.json` with three+ entries (one per operation
  × check name), archives a snapshot under `test-results/history/`, and
  refreshes `optimization-plan.md`.
- Failures (when you point at a real backend with bugs) show up in
  `get_failure_details` with a `request_response` artifact containing
  the method, URL, request body, response status, response body, and
  the Schemathesis violation name. Secrets are redacted by default
  (`Authorization: Bearer […REDACTED]`); set `QA_NO_REDACT=1` to disable.

## Option 3 — Newman runner against the bundled Postman collection

For the Postman / Newman path (v0.6.1):

```bash
# Newman is an npm package, not pip — install once, globally.
npm install -g newman

# Point mk-qa-master at the bundled collection. Plain filesystem path,
# no `file://` prefix (Newman doesn't need it since collections are always
# local artifacts).
export QA_RUNNER=newman
export QA_POSTMAN_COLLECTION="$(pwd)/examples/sample_api_project/postman-collection.json"

# From inside an MCP client:
# - get_runner_info → current: newman
# - list_tests       → GET /books, POST /books, GET /books/{id}
# - run_tests        → runs each request + its pm.test(...) assertions
```

The collection ships with a `{{baseUrl}}` variable pointing at
`http://localhost:4010` (the same default as Prism mock from Option 2).
To target a different server, override via a Postman environment file:

```bash
# environment.json (Postman v2.1 environment shape)
# {"values": [{"key": "baseUrl", "value": "https://staging.example.com"}]}
export QA_POSTMAN_ENVIRONMENT="$(pwd)/environment.json"
```

Other knobs (all optional):

- `QA_POSTMAN_GLOBALS` — globals file, same shape as environment
- `QA_POSTMAN_ITERATIONS` — replay the collection N times (default 1)
- `QA_POSTMAN_FOLDER` — CSV of folder names to scope the run
  (e.g. `Books` for the bundled collection)
- `QA_POSTMAN_TIMEOUT_REQUEST_MS` — per-request timeout (default 30000)

The runner generates one mk-qa-master "test" per `pm.test(...)`
assertion. Three requests × 2 assertions each = 6 nodeids in
`report.json` for a clean run.

> See the Schemathesis sample (Option 1 + 2 above) for the equivalent
> OpenAPI-driven path. Pick whichever matches how your team already
> documents the API: OpenAPI specs go to Schemathesis, Postman exports
> go to Newman. The same `report.json` / history / optimizer pipeline
> consumes both.

## Why this project exists

CI uses both `openapi.yaml` and `postman-collection.json` to smoke-test
the runner integrations on every PR (see `.github/workflows/ci.yml` →
`api-sample` and `api-postman` jobs). The collection also doubles as
the example used in [`docs/walkthrough-api.md`](../../docs/walkthrough-api.md)
(Track 2 — Postman collections).
