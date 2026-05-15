# Smithery Listing — Copy / Paste

This is the content to paste into Smithery's web form when claiming
`kao273183/mk-qa-master`. The `smithery.yaml` at repo root handles
runtime + config schema; this doc covers the human-facing copy.

---

## Display name

```
MK QA Master (AI 測試大師)
```

## Tagline (≤80 chars — shows in catalog cards)

```
Analyze → generate → run → coach. One MCP across pytest, Jest, Cypress, Go, Maestro.
```

## Short description (≤300 chars — shows above the install button)

```
A test-execution + analysis MCP that wraps pytest-playwright, Jest, Cypress,
go test, and Maestro behind one tool surface. Probes a URL or mobile screen,
generates runnable Playwright .py / Maestro .yaml tests, executes them,
renders self-contained HTML reports, and coaches the suite via a three-layer
self-improvement plan.
```

## Long description (Markdown — rendered on the listing page)

```markdown
**MK QA Master** turns your AI client (Claude Desktop, Claude Code, Cursor,
Cline, anything that speaks MCP) into a full QA agent. It stays a pure
**test-execution + analysis** layer — no bundled JIRA / Slack / Sentry SDKs.
Real QA workflows are composed by running multiple MCP servers side-by-side
and letting the AI orchestrate the chain.

### What it does

- **Probe** a web page (`analyze_url`) or mobile screen (`analyze_screen`) and
  return modules, selectors, candidate test cases, API endpoints, and layout
  overflow warnings.
- **Generate** a *runnable* Playwright `.py` or Maestro `.yaml` from the probe
  result — not a skeleton, a test that actually runs.
- **Execute** under the active runner with structured reports, screenshots,
  traces, videos, and a `run_failed` re-run for `pytest --lf`-style loops.
- **Report** to a self-contained HTML file you can drop into Slack or attach
  to a JIRA ticket.
- **Coach** the suite via `get_optimization_plan` — a three-layer
  self-improvement output covering suite health, MCP tool usage, and AI
  prompting strategy.

### Supported runners

| Runner | What you set | Use for |
|---|---|---|
| `pytest` | `QA_RUNNER=pytest` | pytest-playwright (web) |
| `jest` | `QA_RUNNER=jest` | Jest (web / JS) |
| `cypress` | `QA_RUNNER=cypress` | Cypress (web) |
| `go` | `QA_RUNNER=go` | `go test` |
| `maestro` | `QA_RUNNER=maestro` | Maestro (Android / iOS / BlueStacks via remote ADB) |

### Tool surface (16 tools)

`get_runner_info` · `list_tests` · `run_tests` · `run_failed` ·
`get_test_report` · `get_failure_details` · `generate_test` ·
`auto_generate_tests` · `codegen` · `generate_html_report` ·
`get_test_history` · `analyze_url` · `analyze_screen` ·
`init_qa_knowledge` / `get_qa_context` · `get_optimization_plan`

### Pairs well with

- **Atlassian MCP** — auto-open JIRA from `get_failure_details`
- **Slack MCP** — post the HTML report to `#qa-bots`
- **GitHub MCP** — feed PR body as `business_context` into `generate_test`
- **Sentry MCP** — prioritize regression tests from top crashes

Full pairing matrix + example chains in the
[README](https://github.com/kao273183/mk-qa-master#integrations).

### Why no bundled integrations?

Each integration domain (issue trackers, chat, error monitors) already has a
mature dedicated MCP server with its own auth handling. Bolting them in here
would dilute the scope and force every user to inherit dependencies they
don't want. The AI client is the conductor — this server stays the
test loop.
```

## Tags / keywords

```
testing, qa, pytest, playwright, jest, cypress, go-test, maestro, mobile,
test-automation, e2e, regression, test-generation, html-report
```

## Categories (pick the closest Smithery offers)

- Developer Tools
- Testing
- Productivity

## Homepage / Repo / Docs links

- Repository: https://github.com/kao273183/mk-qa-master
- Homepage:   https://github.com/kao273183/mk-qa-master
- README:     https://github.com/kao273183/mk-qa-master#readme
- PyPI:       https://pypi.org/project/mk-qa-master/

## License

`MIT` (auto-detected from `LICENSE`)

## Sample prompts (Smithery shows these as "Try it" examples)

```
1. "Show me which test runner is active and list the existing tests."
2. "Analyze https://example.com/login and generate a test for the login form."
3. "Run the full suite, then render an HTML report I can share."
4. "Re-run only the tests that failed last time."
5. "Analyze the current screen on the connected Android emulator and write
    one Maestro flow per tab in the bottom tab bar."
6. "Look at the last 10 runs and tell me which tests are flakiest."
7. "Generate an optimization plan for my suite and explain the top 3 wins."
```

## Installation preview (for reference — Smithery generates this from smithery.yaml)

```json
{
  "mcpServers": {
    "mk-qa-master": {
      "command": "uvx",
      "args": ["mk-qa-master"],
      "env": {
        "QA_RUNNER": "pytest",
        "QA_PROJECT_ROOT": "/absolute/path/to/your/project"
      }
    }
  }
}
```

## Launch announcement template (X / LinkedIn / Reddit r/mcp)

```
🚀 mk-qa-master is now on Smithery — one-click install for Claude Desktop,
Cursor, Cline, and any MCP client.

Tell Claude:
  "Analyze https://my-app.com/login and generate a Playwright test for it."

It probes the DOM, picks selectors, writes a runnable .py, executes it, and
hands you an HTML report. Same loop works for Jest, Cypress, Go test, and
Maestro (mobile).

→ smithery.ai/server/kao273183/mk-qa-master
→ github.com/kao273183/mk-qa-master
```
