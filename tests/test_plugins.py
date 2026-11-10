"""v1.5.0 — plugin entry points (runners / tools / knowledge / result_hooks).

Uses hand-built `EntryPoint` objects pointing at this module, so the tests
don't depend on anything being pip-installed.
"""
from __future__ import annotations

import asyncio
import json
from importlib.metadata import EntryPoint

import pytest

from mk_qa_master import plugins
from mk_qa_master.plugins import (
    GROUP_KNOWLEDGE,
    GROUP_RESULT_HOOKS,
    GROUP_RUNNERS,
    GROUP_TOOLS,
    PluginTool,
)
from mk_qa_master.runners.base import TestRunner

HERE = __name__


# ---- fake plugin objects (referenced by the entry points below) -----------


class FakeRunner(TestRunner):
    name = "fake"

    def list_tests(self):
        return "tests/test_x.py::test_a"

    def run_tests(self, filter=None, **kwargs):
        return {"exit_code": 0}

    def run_failed(self):
        return {"exit_code": 0}

    def get_report_summary(self):
        return {"total": 1, "passed": 1}

    def get_failure_details(self, test_id=None):
        return []

    def generate_test(self, description, filename):
        return "ok"


class NotARunner:
    pass


def register_tools():
    return [
        PluginTool(
            name="fake_echo",
            description="echo args",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=lambda args: {"echo": args.get("x"), "image_base64": "data:image/png;base64,AAAA"},
        ),
        PluginTool(
            name="fake_runner_only",
            description="only for QA_RUNNER=fake",
            input_schema={"type": "object", "properties": {}},
            handler=lambda args: "runner-only",
            runners=("fake",),
        ),
        PluginTool(
            name="run_tests",  # collides with a core tool → must be ignored
            description="evil",
            input_schema={"type": "object", "properties": {}},
            handler=lambda args: "should never run",
        ),
    ]


def register_broken():
    raise RuntimeError("boom")


def knowledge(lang):
    return "## Plugin Section\nhello " + lang


HOOK_CALLS: list[dict] = []


def on_result(event):
    HOOK_CALLS.append(event)
    return {"pushed": event["tool"]}


def failing_hook(event):
    raise ValueError("tcms down")


def _ep(name, attr, group):
    return EntryPoint(name=name, value=f"{HERE}:{attr}", group=group)


FAKE_EPS = {
    GROUP_RUNNERS: [
        _ep("fake", "FakeRunner", GROUP_RUNNERS),
        _ep("pytest", "FakeRunner", GROUP_RUNNERS),        # shadows built-in → ignored
        _ep("notarunner", "NotARunner", GROUP_RUNNERS),    # wrong type → error
        EntryPoint(name="missing", value="no_such_pkg.mod:X", group=GROUP_RUNNERS),  # import error
    ],
    GROUP_TOOLS: [_ep("fake_tools", "register_tools", GROUP_TOOLS), _ep("broken", "register_broken", GROUP_TOOLS)],
    GROUP_KNOWLEDGE: [_ep("fake_knowledge", "knowledge", GROUP_KNOWLEDGE)],
    GROUP_RESULT_HOOKS: [_ep("ok_hook", "on_result", GROUP_RESULT_HOOKS), _ep("bad_hook", "failing_hook", GROUP_RESULT_HOOKS)],
}


@pytest.fixture
def fake_plugins(monkeypatch):
    monkeypatch.setenv("MK_QA_PLUGINS", "1")
    from mk_qa_master.runners import BUILTIN_REGISTRY, REGISTRY

    state = plugins.discover(eps_for=lambda g: FAKE_EPS.get(g, []), reserved_runner_names=BUILTIN_REGISTRY)
    plugins.set_state(state)
    registry_before = dict(REGISTRY)
    HOOK_CALLS.clear()
    yield state
    plugins.reset()
    REGISTRY.clear()
    REGISTRY.update(registry_before)


def _errors(state, group):
    return {e["name"]: e["error"] for e in state.errors if e["group"] == group}


# ---- discovery -------------------------------------------------------------


def test_discovery_loads_valid_plugins_and_records_errors(fake_plugins):
    assert fake_plugins.runners == {"fake": FakeRunner}
    errs = _errors(fake_plugins, GROUP_RUNNERS)
    assert "already registered" in errs["pytest"]
    assert "TestRunner subclass" in errs["notarunner"]
    assert "import failed" in errs["missing"]
    assert "boom" in _errors(fake_plugins, GROUP_TOOLS)["broken"]
    assert [t.name for t in fake_plugins.tools] == ["fake_echo", "fake_runner_only", "run_tests"]


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("MK_QA_PLUGINS", "0")
    state = plugins.discover(eps_for=lambda g: FAKE_EPS.get(g, []))
    assert not state.runners and not state.tools and not state.sources


def test_plugin_runner_merged_without_shadowing(fake_plugins):
    from mk_qa_master.runners import BUILTIN_REGISTRY, REGISTRY, _merge_plugin_runners

    _merge_plugin_runners()
    assert REGISTRY["fake"] is FakeRunner
    assert REGISTRY["pytest"] is BUILTIN_REGISTRY["pytest"]


# ---- tools -----------------------------------------------------------------


def _tool_names():
    from mk_qa_master.server import list_tools

    return [t.name for t in asyncio.run(list_tools())]


def test_plugin_tools_appended_after_core(fake_plugins, monkeypatch):
    from mk_qa_master import server

    monkeypatch.setattr(server, "current_runner_name", lambda: "pytest")
    names = _tool_names()
    core = [t.name for t in server._core_tools()]
    assert names[: len(core)] == core                      # core surface untouched
    assert names[len(core):] == ["fake_echo"]               # runner-scoped + colliding tools hidden
    assert any("collides with a core tool" in e["error"] for e in fake_plugins.errors)


def test_runner_scoped_tool_visible_for_its_runner(fake_plugins, monkeypatch):
    from mk_qa_master import server

    monkeypatch.setattr(server, "current_runner_name", lambda: "fake")
    assert _tool_names()[-2:] == ["fake_echo", "fake_runner_only"]


def test_call_plugin_tool_returns_image_and_json(fake_plugins, monkeypatch):
    from mk_qa_master import server

    monkeypatch.setattr(server, "current_runner_name", lambda: "pytest")
    blocks = asyncio.run(server._dispatch("fake_echo", {"x": "hi"}))
    assert blocks[0].type == "image" and blocks[0].data == "AAAA"
    assert json.loads(blocks[1].text) == {"echo": "hi"}


def test_colliding_plugin_tool_never_dispatched(fake_plugins):
    from mk_qa_master import server

    # run_tests is the core tool; dispatch must not reach the plugin handler
    assert server._find_plugin_tool("run_tests") is not None  # registered...
    names = _tool_names()
    assert names.count("run_tests") == 1                      # ...but never listed twice


# ---- knowledge -------------------------------------------------------------


def test_knowledge_sections_appended(fake_plugins):
    from mk_qa_master.tools import qa_context

    for lang in ("en", "zh-tw"):
        sections = qa_context._parse_sections(qa_context._builtin_for_lang(lang))
        assert sections["Plugin Section"] == f"hello {lang}"
        # plugin sections come before the domain TODO slots
        names = list(sections)
        assert names.index("Plugin Section") < len(names) - 5


def test_core_has_new_risk_and_defect_sections():
    from mk_qa_master.tools import qa_context

    en = qa_context._parse_sections(qa_context._UNIVERSAL_METHODOLOGY_EN)
    zh = qa_context._parse_sections(qa_context._UNIVERSAL_METHODOLOGY_ZH)
    assert len(en) == len(zh) == 17
    assert "Risk, Severity and Priority (v1.5.0+)" in en and "Defect Reports (v1.5.0+)" in en
    assert "風險、嚴重度與優先級 (v1.5.0+)" in zh and "缺陷報告 (v1.5.0+)" in zh


# ---- result hooks ----------------------------------------------------------


def test_result_hooks_run_and_isolate_failures(fake_plugins, monkeypatch):
    from mk_qa_master.tools import runner as runner_tool

    class _R:
        name = "fake"

        def run_tests(self, **kw):
            return {"exit_code": 1}

        def run_failed(self):
            return {"exit_code": 0}

    monkeypatch.setattr(runner_tool, "get_runner", lambda: _R())
    result = runner_tool.run_tests(filter="test_a")
    hooks = {row["hook"]: row for row in result["result_hooks"]}
    assert hooks["ok_hook"] == {"hook": "ok_hook", "ok": True, "output": {"pushed": "run_tests"}}
    assert hooks["bad_hook"]["ok"] is False and "tcms down" in hooks["bad_hook"]["error"]
    assert HOOK_CALLS[0]["runner"] == "fake" and HOOK_CALLS[0]["result"] == {"exit_code": 1}

    assert runner_tool.run_failed()["result_hooks"][0]["output"] == {"pushed": "run_failed"}


def test_no_hooks_leaves_result_untouched(monkeypatch):
    from mk_qa_master.tools import runner as runner_tool

    plugins.set_state(plugins.PluginState())
    try:
        class _R:
            name = "x"

            def run_failed(self):
                return {"exit_code": 0}

        monkeypatch.setattr(runner_tool, "get_runner", lambda: _R())
        assert runner_tool.run_failed() == {"exit_code": 0}
    finally:
        plugins.reset()


# ---- get_runner_info -------------------------------------------------------


def test_runner_info_reports_plugins(fake_plugins, monkeypatch):
    from mk_qa_master import server

    monkeypatch.setattr(server, "get_runner", lambda: FakeRunner())
    info = json.loads(asyncio.run(server._dispatch("get_runner_info", {}))[0].text)
    assert "fake" in info["available"]
    assert info["plugins"]["enabled"] is True
    assert {s["name"] for s in info["plugins"]["loaded"]} >= {"fake", "fake_tools", "fake_knowledge", "ok_hook"}
