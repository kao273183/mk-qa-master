# Plugins (v1.5.0+)

mk-qa-master can be extended **without forking** through standard Python entry points. Install a plugin package into the same environment as mk-qa-master and it is picked up when the server starts.

| Entry-point group | Points at | Effect |
|---|---|---|
| `mk_qa_master.runners` | a `TestRunner` subclass | Selectable with `QA_RUNNER=<entry-point name>` |
| `mk_qa_master.tools` | `register() -> list[PluginTool]` | Extra MCP tools, appended **after** the frozen v1 core tools |
| `mk_qa_master.knowledge` | `sections(lang: str) -> str` | Extra H2 sections in `get_qa_context` / `init_qa_knowledge` |
| `mk_qa_master.result_hooks` | `on_result(event: dict) -> dict \| None` | Called after `run_tests` / `run_failed` (e.g. push results to a private TCMS) |

## Guarantees

- **The frozen v1 tool surface never changes.** Plugin tools are appended after the 22 core tools. A plugin tool whose name collides with a core tool is ignored.
- **Built-in runner names always win.** A plugin cannot shadow `pytest`, `maestro`, etc.
- **A broken plugin never breaks the server.** Import or registration errors are collected and shown by `get_runner_info` under `plugins.errors`.
- **Plugin-free installs are byte-identical.** `get_runner_info` gains a `plugins` key and `run_tests` / `run_failed` gain a `result_hooks` key only when a plugin is present.
- **Kill switch:** `MK_QA_PLUGINS=0` disables discovery entirely. The test suite sets this by default (`tests/conftest.py`).

## Writing a plugin

```toml
# your-plugin/pyproject.toml
[project.entry-points."mk_qa_master.runners"]
flutterqa = "flutterqa.mk_plugin:FlutterQARunner"

[project.entry-points."mk_qa_master.tools"]
flutterqa = "flutterqa.mk_plugin:register_tools"

[project.entry-points."mk_qa_master.knowledge"]
flutterqa = "flutterqa.mk_plugin:knowledge_sections"

[project.entry-points."mk_qa_master.result_hooks"]
company_tcms = "company_tcms.mk_hook:on_result"
```

### Runner

Subclass `mk_qa_master.runners.base.TestRunner`, or an existing runner such as `PytestPlaywrightRunner` to reuse its `report.json` parsing, history archiving and optimizer hook.

### Tools

```python
from mk_qa_master.plugins import PluginTool

def register_tools() -> list[PluginTool]:
    return [
        PluginTool(
            name="app_tap",
            description="Tap a widget by key.",
            input_schema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
            handler=lambda args: {"ok": True},   # runs in a worker thread
            runners=("flutterqa",),               # hidden unless QA_RUNNER=flutterqa; None = always
        ),
    ]
```

A handler returns a `dict` (JSON-encoded) or a `str`. If the dict contains `image_base64` (PNG), it is returned as native MCP `ImageContent`.

Naming: prefix tool names with your domain (`app_…`, `tcms_…`) to avoid collisions with future core tools.

### Knowledge

```python
def knowledge_sections(lang: str) -> str:
    if lang == "zh-tw":
        return "## Flutter UI 測試 Checklist\n- ..."
    return "## Flutter UI Test Checklist\n- ..."
```

Sections are inserted between the built-in methodology and the `Your …` domain TODO slots, and are included when `init_qa_knowledge` scaffolds `qa-knowledge.md`.

### Result hooks

```python
def on_result(event: dict) -> dict | None:
    # event = {"tool": "run_tests" | "run_failed", "runner": "...",
    #          "result": {...}, "report_path": ".../report.json", "project_root": "..."}
    ...
    return {"run_id": "R-123"}   # shown to the AI client under result_hooks[].output
```

Exceptions are caught and reported per hook (`{"hook": name, "ok": false, "error": ...}`); they never fail the test run. Keep credentials in environment variables, never in the event or the return value.

## Inspecting what loaded

Call `get_runner_info`:

```jsonc
{
  "current": "flutterqa",
  "available": ["cypress", "edge", "flutterqa", "go", ...],
  "plugins": {
    "enabled": true,
    "loaded": [{"group": "mk_qa_master.runners", "name": "flutterqa", "value": "flutterqa.mk_plugin:FlutterQARunner", "dist": "flutterqa"}],
    "errors": []
  }
}
```
