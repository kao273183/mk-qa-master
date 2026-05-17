# Sample API Project — `examples/sample_api_project/`

A 3-endpoint fictional **Library API** (`/books` GET + POST, `/books/{id}` GET)
shipped with mk-qa-master so you can dogfood the schemathesis runner
without standing up a backend.

## Files

- `openapi.yaml` — OpenAPI 3.0.3 schema. Valid, self-contained, no
  external `$ref`s. Parses cleanly under Schemathesis 3.x.

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

## Why this project exists

CI uses `openapi.yaml` to smoke-test the runner integration on every PR
(see `.github/workflows/ci.yml` → `api-sample` job). It also doubles as
the example used in [`docs/walkthrough-api.md`](../../docs/walkthrough-api.md).
