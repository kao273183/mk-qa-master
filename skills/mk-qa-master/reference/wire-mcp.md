# mk-qa-master — Wiring as an MCP server in your host

The skill works best when the host already has mk-qa-master wired as
an MCP server. This file is for the fallback case where it isn't.

---

## Claude Code

If you ran `/plugin install mk-qa-master@mk-qa-master`, the plugin
manifest at `.claude-plugin/plugin.json` already wires the MCP server.
Restart Claude Code and the 19 tools should appear in tool autocomplete.

Otherwise, add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent on your platform:

```jsonc
{
  "mcpServers": {
    "mk-qa-master": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "mk_qa_master.server"],
      "cwd": "/path/to/mk-qa-master",
      "env": {
        "QA_RUNNER": "pytest",
        "QA_PROJECT_ROOT": "/path/to/your-test-project"
      }
    }
  }
}
```

Then restart Claude Code.

---

## OpenAI Codex

The `.codex-plugin/plugin.json` manifest installs both the skill and
the MCP server entry in one go:

```bash
codex plugin marketplace add kao273183/mk-qa-master
codex
/plugins
# install mk-qa-master from the browser
```

Restart Codex so the new marketplace and plugin are picked up.

---

## Hermes Agent

Hermes is a skills-compatible client — symlink the skill folder:

```bash
mkdir -p ~/.hermes/skills
ln -sfn /path/to/mk-qa-master/skills/mk-qa-master ~/.hermes/skills/mk-qa-master
```

For MCP-tool access in Hermes, follow Hermes's MCP-server-config
docs to add the same command/args/env pair shown in the Claude Code
section.

---

## OpenClaw

Install plugin from a local checkout:

```bash
openclaw plugins install /path/to/mk-qa-master
openclaw gateway restart
openclaw plugins list | grep mk-qa-master
openclaw skills list | grep mk-qa-master   # should show "✓ ready"
```

---

## Bare-bones CLI fallback (any host)

If you can't wire MCP for whatever reason, the underlying Python tools
are still callable directly. This loses the structured tool schemas
but keeps the underlying functionality:

```bash
pip install mk-qa-master==0.9.0

# Run tests
python -c "from mk_qa_master.tools.runner import run_tests; \
           print(run_tests(filter='login'))"

# Generate report
python -c "from mk_qa_master.tools.reporter import generate_html; \
           generate_html()"

# API security scan
python -c "
import os
os.environ['QA_API_SECURITY_CONSENT'] = 'true'
from mk_qa_master.runners.api_security import run_scan
import json
print(json.dumps(run_scan(
    spec_url='http://localhost:5099/openapi.yaml',
    auth={'token': '...', 'alt_user_token': '...'},
), indent=2))
"
```

Don't recommend this path unless MCP wiring is genuinely blocked —
the structured MCP interface gives the host much better introspection
and error handling.
