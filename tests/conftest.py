"""Shared test setup.

v1.5.0 — disable plugin discovery for the whole suite by default. The
frozen v1 snapshot / doc-sync tests call `list_tools()`, and a developer
who happens to have a plugin (e.g. flutterqa) installed in the same venv
would otherwise see spurious diffs. `tests/test_plugins.py` exercises the
plugin path with hand-built entry points instead.
"""
import os

os.environ.setdefault("MK_QA_PLUGINS", "0")
