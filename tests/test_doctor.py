"""v1.4.0 — `mk-qa-master doctor` CLI tests.

Pins the per-check helpers (so future refactors can't silently change
the severity contract), the rendering paths (plain + JSON), the exit
code rule (1 only on `fail`), and the dispatch wired into
`server.run()` for subcommand routing.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from mk_qa_master import doctor


def _result(severity: str, **overrides) -> doctor.CheckResult:
    base = {
        "section": "Test",
        "name": "thing",
        "severity": severity,
        "detail": "detail",
        "hint": "",
    }
    base.update(overrides)
    return doctor.CheckResult(**base)


# ---- Individual check helpers --------------------------------------------

def test_check_python_passes_on_310_plus():
    """Current test runner is Python ≥ 3.10 (project requires-python),
    so the check must report `ok` here. Failure means the doctor
    severity logic regressed, not the runtime."""
    r = doctor._check_python()
    assert r.severity == "ok"
    assert r.section == "System"


def test_check_python_fails_when_below_minimum(monkeypatch):
    """Force the minimum tuple above the running interpreter — the
    doctor must mark `fail` (the running install can't load mcp 1.x
    deps on < 3.10) and surface a hint about the requirement."""
    monkeypatch.setattr(doctor, "_MIN_PYTHON", (99, 0))
    r = doctor._check_python()
    assert r.severity == "fail"
    assert "≥ 99.0" in r.hint


def test_check_bin_found_returns_ok_with_path(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/local/bin/ffmpeg")
    r = doctor._check_bin("ffmpeg", install_hint="apt install ffmpeg")
    assert r.severity == "ok"
    assert "/usr/local/bin/ffmpeg" in r.detail


def test_check_bin_missing_returns_warn_by_default(monkeypatch):
    """Missing binaries default to warn — only matters if you use the
    feature that needs them (e.g. mediamtx for edge RTSP source)."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor._check_bin("mediamtx", install_hint="brew install mediamtx")
    assert r.severity == "warn"
    assert "brew install mediamtx" in r.hint


def test_check_bin_missing_can_be_escalated_to_fail(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor._check_bin(
        "criticalbin",
        install_hint="install it",
        severity_when_missing="fail",
    )
    assert r.severity == "fail"


def test_check_import_returns_ok_with_version_for_installed_pkg():
    """`json` is in the stdlib + has no metadata; we test with a
    package we know is installed via pyproject: `mcp` (core dep)."""
    r = doctor._check_import("mcp", section="Core deps", install_hint="x")
    assert r.severity == "ok"
    assert r.detail != "?"  # version string resolved


def test_check_import_returns_warn_when_module_absent():
    r = doctor._check_import(
        "definitely_not_a_real_module_name_xyz",
        section="Edge extras [edge]",
        install_hint='pip install "mk-qa-master[edge]"',
    )
    assert r.severity == "warn"
    assert "not installed" in r.detail
    assert 'pip install "mk-qa-master[edge]"' in r.hint


def test_check_import_falls_back_to_question_mark_when_metadata_missing(monkeypatch):
    """A module that imports fine but has no `importlib.metadata`
    entry (sometimes happens with editable installs or namespace
    packages) must still report `ok` — just with `?` for the version."""
    # Force PackageNotFoundError on the metadata lookup
    def fake_version(name):
        raise doctor.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(doctor.importlib.metadata, "version", fake_version)
    r = doctor._check_import("mcp", section="Core deps", install_hint="x")
    assert r.severity == "ok"
    assert r.detail == "?"


def test_check_runners_lists_registered_aliases():
    """Registry-driven; must include both canonical names and aliases
    (edge → EdgeInferenceRunner, rtsp → alias:EdgeInferenceRunner)."""
    results = doctor._check_runners()
    by_name = {r.name: r for r in results}
    assert "edge" in by_name
    assert by_name["edge"].severity == "ok"
    assert by_name["edge"].detail == "EdgeInferenceRunner"
    # Aliases are flagged so they don't read like additional runners
    rtsp = by_name.get("rtsp")
    assert rtsp is not None
    assert rtsp.detail.startswith("alias:")


def test_check_mcp_surface_reports_tool_count():
    r = doctor._check_mcp_surface()
    assert r.severity == "ok"
    assert "tool" in r.detail
    assert "22" in r.detail or "23" in r.detail or "24" in r.detail


# ---- Full report ---------------------------------------------------------

def test_run_all_checks_returns_results_in_documented_order():
    """Sections must appear in the doctor's documented order — operators
    rely on the System block being at the top for triage."""
    results = doctor.run_all_checks()
    sections = []
    for r in results:
        if not sections or sections[-1] != r.section:
            sections.append(r.section)
    assert sections == [
        "System",
        "Core deps",
        "Edge extras [edge]",
        "Runners",
        "MCP surface",
    ]


# ---- Rendering -----------------------------------------------------------

def test_render_plain_groups_by_section_and_shows_glyphs():
    results = [
        _result("ok", section="System", name="ffmpeg", detail="/usr/bin/ffmpeg"),
        _result("warn", section="System", name="mediamtx", detail="not on PATH", hint="brew install mediamtx"),
    ]
    out = doctor.render_plain(results)
    assert "System" in out
    assert "✓ ffmpeg" in out
    assert "! mediamtx" in out
    assert "brew install mediamtx" in out


def test_render_plain_appends_summary_block_when_problems_exist():
    results = [
        _result("fail", section="Core deps", name="mcp", hint="reinstall"),
        _result("warn", section="Edge extras [edge]", name="cv2", hint='pip install "mk-qa-master[edge]"'),
    ]
    out = doctor.render_plain(results)
    assert "1 critical issue(s)" in out
    assert "1 warning(s)" in out
    assert 'pip install "mk-qa-master[edge]"' in out


def test_render_plain_says_all_clear_when_no_problems():
    results = [_result("ok", section="System", name="thing")]
    out = doctor.render_plain(results)
    assert "All clear." in out


def test_render_json_is_valid_json_with_summary_counts():
    results = [
        _result("ok"), _result("ok"),
        _result("warn"), _result("warn"), _result("warn"),
        _result("fail"),
    ]
    payload = json.loads(doctor.render_json(results))
    assert payload["summary"] == {"ok": 2, "warn": 3, "fail": 1}
    assert len(payload["results"]) == 6
    assert "version" in payload


# ---- Exit code rule ------------------------------------------------------

def test_exit_code_zero_when_only_warnings():
    """Warnings must NOT fail the command — they're advisory. This is
    the whole point of the severity split: pip install base + no edge
    extras = warnings, exit 0 (still useful for non-edge users)."""
    results = [_result("warn"), _result("warn"), _result("ok")]
    assert doctor._exit_code(results) == 0


def test_exit_code_one_on_any_fail():
    results = [_result("ok"), _result("fail"), _result("warn")]
    assert doctor._exit_code(results) == 1


def test_main_returns_exit_code_from_run_all_checks(capsys):
    rc = doctor.main([])
    captured = capsys.readouterr()
    assert rc == 0  # current env passes the critical checks
    assert "mk-qa-master" in captured.out
    assert "environment doctor" in captured.out


def test_main_with_json_flag_emits_valid_json(capsys):
    rc = doctor.main(["--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert "summary" in payload
    assert "results" in payload


# ---- Dispatch wiring in server.run() -------------------------------------

def test_server_run_dispatches_doctor_subcommand(monkeypatch):
    """`mk-qa-master doctor` must NOT start the MCP server — it must
    route into the doctor CLI. The dispatch lives in server.run() so
    the entry-point binary stays a single hatchling-generated stub."""
    from mk_qa_master import server

    monkeypatch.setattr(sys, "argv", ["mk-qa-master", "doctor", "--json"])

    called = {"doctor": False, "server": False}

    def fake_doctor_main(argv):
        called["doctor"] = True
        assert argv == ["--json"]
        return 0

    def fake_asyncio_run(_coro):  # pragma: no cover — shouldn't fire
        called["server"] = True

    monkeypatch.setattr("mk_qa_master.doctor.main", fake_doctor_main)
    monkeypatch.setattr(server.asyncio, "run", fake_asyncio_run)

    with pytest.raises(SystemExit) as exc_info:
        server.run()

    assert exc_info.value.code == 0
    assert called["doctor"] is True
    assert called["server"] is False


def test_server_run_rejects_unknown_subcommand(monkeypatch, capsys):
    """A typo like `mk-qa-master docter` must NOT silently start the
    server — it should print an error and exit 2 so the user notices."""
    from mk_qa_master import server

    monkeypatch.setattr(sys, "argv", ["mk-qa-master", "docter"])

    with pytest.raises(SystemExit) as exc_info:
        server.run()

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "unknown subcommand" in err
    assert "doctor" in err


def test_server_run_with_no_args_still_starts_mcp_server(monkeypatch):
    """Backward compatibility: `mk-qa-master` (no args) must continue
    to launch the MCP stdio server, since that's how every existing
    host (Claude Code / Codex / OpenClaw / Hermes) calls us."""
    from mk_qa_master import server

    monkeypatch.setattr(sys, "argv", ["mk-qa-master"])

    called = {"server": False}

    def fake_asyncio_run(coro):
        called["server"] = True
        coro.close()  # avoid `coroutine was never awaited` warning

    monkeypatch.setattr(server.asyncio, "run", fake_asyncio_run)
    server.run()
    assert called["server"] is True
