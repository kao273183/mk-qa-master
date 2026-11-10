"""Plugin discovery via Python entry points (v1.5.0).

Lets third-party packages extend mk-qa-master without forking it. A package
declares entry points in its own ``pyproject.toml``; installing it into the
same environment is enough — mk-qa-master picks it up at server start.

Four groups:

``mk_qa_master.runners``
    ``name = "pkg.module:RunnerClass"`` — a ``TestRunner`` subclass. Selected
    with ``QA_RUNNER=<name>``. Built-in runner names always win; a plugin
    trying to shadow one is ignored and reported in ``plugin_errors``.

``mk_qa_master.tools``
    ``name = "pkg.module:register"`` — a zero-arg callable returning a list of
    :class:`PluginTool`. Plugin tools are appended *after* the frozen v1 core
    surface, so core tool schemas never change. A tool may restrict itself to
    specific runners (``runners=("flutterqa",)``) and is hidden otherwise.

``mk_qa_master.knowledge``
    ``name = "pkg.module:sections"`` — a callable ``(lang: str) -> str``
    returning Markdown with H2 (``##``) sections. Appended to the built-in
    methodology served by ``get_qa_context`` / ``init_qa_knowledge``.

``mk_qa_master.result_hooks``
    ``name = "pkg.module:on_result"`` — a callable ``(event: dict) -> dict |
    None`` invoked after ``run_tests`` / ``run_failed``. Typical use: push
    results to a private test-case-management system. The event carries
    ``tool``, ``runner``, ``result``, ``report_path``, ``project_root``.

Isolation rules
---------------
* A plugin that fails to import or register never breaks the server: the
  error is recorded and surfaced via ``get_runner_info`` → ``plugins.errors``.
* ``MK_QA_PLUGINS=0`` disables discovery entirely (the test suite sets this
  so the frozen v1 snapshot is not affected by whatever is installed locally).
* Plugin tool names must not collide with core tools or each other.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable, Iterable

GROUP_RUNNERS = "mk_qa_master.runners"
GROUP_TOOLS = "mk_qa_master.tools"
GROUP_KNOWLEDGE = "mk_qa_master.knowledge"
GROUP_RESULT_HOOKS = "mk_qa_master.result_hooks"
ALL_GROUPS = (GROUP_RUNNERS, GROUP_TOOLS, GROUP_KNOWLEDGE, GROUP_RESULT_HOOKS)


@dataclass
class PluginTool:
    """A tool contributed by a plugin.

    ``handler(args) -> dict | str`` runs in a worker thread. A dict result is
    JSON-encoded; if it contains ``image_base64`` (PNG), that key is popped
    and returned as native MCP ImageContent alongside the JSON.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]
    runners: tuple[str, ...] | None = None  # None = available for every runner

    def active_for(self, runner_name: str) -> bool:
        return self.runners is None or runner_name in self.runners


@dataclass
class PluginState:
    runners: dict[str, type] = field(default_factory=dict)
    tools: list[PluginTool] = field(default_factory=list)
    knowledge: list[tuple[str, Callable[[str], str]]] = field(default_factory=list)
    result_hooks: list[tuple[str, Callable[[dict], Any]]] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "enabled": plugins_enabled(),
            "loaded": self.sources,
            "errors": self.errors,
        }


_STATE: PluginState | None = None


def plugins_enabled() -> bool:
    return os.getenv("MK_QA_PLUGINS", "1").strip().lower() not in ("0", "false", "no", "off")


def _default_entry_points(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


def _dist_name(ep: EntryPoint) -> str | None:
    dist = getattr(ep, "dist", None)
    return getattr(dist, "name", None) if dist is not None else None


def discover(
    eps_for: Callable[[str], Iterable[EntryPoint]] | None = None,
    reserved_runner_names: Iterable[str] = (),
    reserved_tool_names: Iterable[str] = (),
) -> PluginState:
    """Load every plugin entry point. Never raises."""
    state = PluginState()
    if not plugins_enabled():
        return state
    eps_for = eps_for or _default_entry_points
    reserved_runners = set(reserved_runner_names)
    reserved_tools = set(reserved_tool_names)

    def record_error(group: str, ep: EntryPoint, message: str) -> None:
        state.errors.append({"group": group, "name": ep.name, "value": ep.value, "error": message})

    for group in ALL_GROUPS:
        try:
            eps = list(eps_for(group))
        except Exception as e:  # noqa: BLE001 — metadata scan must never crash boot
            state.errors.append({"group": group, "name": "*", "value": "", "error": f"{type(e).__name__}: {e}"})
            continue
        for ep in eps:
            try:
                obj = ep.load()
            except Exception as e:  # noqa: BLE001
                record_error(group, ep, f"import failed: {type(e).__name__}: {e}")
                continue

            if group == GROUP_RUNNERS:
                from .runners.base import TestRunner

                if ep.name in reserved_runners or ep.name in state.runners:
                    record_error(group, ep, f"runner name {ep.name!r} already registered; ignored")
                    continue
                if not (isinstance(obj, type) and issubclass(obj, TestRunner)):
                    record_error(group, ep, "entry point must be a TestRunner subclass")
                    continue
                state.runners[ep.name] = obj

            elif group == GROUP_TOOLS:
                try:
                    tools = list(obj())
                except Exception as e:  # noqa: BLE001
                    record_error(group, ep, f"register() failed: {type(e).__name__}: {e}")
                    continue
                for tool in tools:
                    if not isinstance(tool, PluginTool):
                        record_error(group, ep, f"register() returned {type(tool).__name__}, expected PluginTool")
                        continue
                    taken = reserved_tools | {t.name for t in state.tools}
                    if tool.name in taken:
                        record_error(group, ep, f"tool name {tool.name!r} already registered; ignored")
                        continue
                    state.tools.append(tool)

            elif group == GROUP_KNOWLEDGE:
                if not callable(obj):
                    record_error(group, ep, "entry point must be callable(lang) -> str")
                    continue
                state.knowledge.append((ep.name, obj))

            elif group == GROUP_RESULT_HOOKS:
                if not callable(obj):
                    record_error(group, ep, "entry point must be callable(event) -> dict | None")
                    continue
                state.result_hooks.append((ep.name, obj))

            state.sources.append({"group": group, "name": ep.name, "value": ep.value, "dist": _dist_name(ep)})
    return state


def get_state() -> PluginState:
    """Lazily discover plugins once per process."""
    global _STATE
    if _STATE is None:
        from .runners import BUILTIN_REGISTRY

        # Collisions with core *tool* names are checked by the server when it
        # merges plugin tools into list_tools() (it owns the core list).
        _STATE = discover(reserved_runner_names=BUILTIN_REGISTRY.keys())
    return _STATE


def reset() -> None:
    """Forget discovered plugins (tests / hot reload)."""
    global _STATE
    _STATE = None


def set_state(state: PluginState) -> None:
    """Install a hand-built state (tests)."""
    global _STATE
    _STATE = state


def knowledge_markdown(lang: str) -> str:
    """Concatenated plugin knowledge sections for the given language."""
    chunks = []
    for name, fn in get_state().knowledge:
        try:
            text = fn(lang)
        except Exception as e:  # noqa: BLE001
            get_state().errors.append({"group": GROUP_KNOWLEDGE, "name": name, "value": "", "error": f"{type(e).__name__}: {e}"})
            continue
        if text and text.strip():
            chunks.append(text.strip() + "\n")
    return ("\n" + "\n".join(chunks)) if chunks else ""


def run_result_hooks(event: dict) -> list[dict]:
    """Invoke every result hook; returns one status row per hook. Never raises."""
    rows = []
    for name, fn in get_state().result_hooks:
        try:
            out = fn(dict(event))
            rows.append({"hook": name, "ok": True, "output": out})
        except Exception as e:  # noqa: BLE001
            rows.append({"hook": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return rows
