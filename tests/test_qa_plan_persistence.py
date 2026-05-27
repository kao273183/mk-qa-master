"""v0.9.3 tests — qa_plan disk persistence.

Covers:
  - Default policy: ON when QA_PROJECT_ROOT set, OFF otherwise
  - QA_PLAN_PERSIST env override (both directions)
  - MK_QA_PLANS_DIR path override
  - Disk write atomicity (no partial files visible)
  - Disk read after in-memory cache cleared
  - Expiry honored even when file still exists
  - Corrupt JSON / missing file degrades silently
  - Round-trip preserves all CP fields
  - persisted_to surfaced in qa_plan response
  - plan_source surfaced in verify_plan response
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mk_qa_master.tools import qa_plan
from mk_qa_master.tools.qa_plan import (
    _CACHE_TTL_SECONDS,
    _persistence_enabled,
    _plans_dir,
    _reset_cache_for_tests,
    qa_plan_tool,
    verify_plan_tool,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Per-test isolation: clear in-memory cache + scrub persistence env."""
    _reset_cache_for_tests()
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("QA_PLAN_PERSIST", raising=False)
    monkeypatch.delenv("MK_QA_PLANS_DIR", raising=False)
    monkeypatch.delenv("MK_QA_REPORT_PATH", raising=False)
    yield
    _reset_cache_for_tests()


def _plan(critical_points: list = None) -> str:
    result = qa_plan_tool({
        "task": "Persist test",
        "critical_points": critical_points or ["thing happens"],
    })
    return result["plan_id"]


# ---- default policy ----------------------------------------------------

def test_persistence_off_by_default_without_project_root():
    """No env signals → no disk writes."""
    assert _persistence_enabled() is False
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    assert result["persisted_to"] is None


def test_persistence_on_by_default_when_project_root_set(monkeypatch, tmp_path):
    """QA_PROJECT_ROOT set → "mk-qa-master is configured" → persist by default."""
    monkeypatch.setenv("QA_PROJECT_ROOT", str(tmp_path))
    assert _persistence_enabled() is True


def test_qa_plan_persist_env_forces_on(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path / "plans"))
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    assert result["persisted_to"] is not None
    assert Path(result["persisted_to"]).is_file()


def test_qa_plan_persist_env_forces_off(monkeypatch, tmp_path):
    """Even with QA_PROJECT_ROOT set, an explicit `false` overrides."""
    monkeypatch.setenv("QA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("QA_PLAN_PERSIST", "false")
    assert _persistence_enabled() is False
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    assert result["persisted_to"] is None


@pytest.mark.parametrize("truthy", ["true", "TRUE", "1", "yes", "on"])
def test_persist_truthy_values(monkeypatch, truthy):
    monkeypatch.setenv("QA_PLAN_PERSIST", truthy)
    assert _persistence_enabled() is True


@pytest.mark.parametrize("falsy", ["false", "FALSE", "0", "no", "off"])
def test_persist_falsy_values_override_project_root(monkeypatch, tmp_path, falsy):
    monkeypatch.setenv("QA_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("QA_PLAN_PERSIST", falsy)
    assert _persistence_enabled() is False


# ---- path resolution ---------------------------------------------------

def test_plans_dir_uses_mk_qa_plans_dir_override(monkeypatch, tmp_path):
    """MK_QA_PLANS_DIR wins over everything."""
    custom = tmp_path / "my-custom-plans"
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(custom))
    monkeypatch.setenv("QA_PROJECT_ROOT", str(tmp_path / "other"))
    assert _plans_dir() == custom.resolve()


def test_plans_dir_falls_back_to_project_root(monkeypatch, tmp_path):
    """Without env override, derived from QA_PROJECT_ROOT."""
    monkeypatch.setenv("QA_PROJECT_ROOT", str(tmp_path))
    assert _plans_dir() == (tmp_path / "test-results" / "plans").resolve()


def test_plans_dir_falls_back_to_cwd(monkeypatch, tmp_path):
    """Without project root or env override, derived from CWD."""
    monkeypatch.chdir(tmp_path)
    expected = (tmp_path / "test-results" / "plans").resolve()
    assert _plans_dir() == expected


# ---- write semantics ---------------------------------------------------

def test_persist_writes_plan_json_with_schema_marker(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    result = qa_plan_tool({
        "task": "Custom task",
        "critical_points": [{"id": "CP-A", "description": "first thing"},
                            {"id": "CP-B", "description": "second thing"}],
        "kind": "scan",
    })
    persisted = Path(result["persisted_to"])
    assert persisted.is_file()
    data = json.loads(persisted.read_text(encoding="utf-8"))
    assert data["_schema"] == "mk-qa-master.plan.v1"
    assert data["task"] == "Custom task"
    assert data["kind"] == "scan"
    assert len(data["critical_points"]) == 2
    assert data["critical_points"][0]["id"] == "CP-A"


def test_persist_filename_matches_plan_id(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    expected_path = tmp_path / f"{result['plan_id']}.json"
    assert Path(result["persisted_to"]).resolve() == expected_path.resolve()


def test_persist_atomic_no_temp_files_visible_after_write(monkeypatch, tmp_path):
    """After write completes, only the .json file should be in plans_dir.
    No `.tmp` sibling — atomic-replace guarantees clean state."""
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    qa_plan_tool({"task": "x", "critical_points": ["y"]})
    files = sorted(p.name for p in tmp_path.iterdir())
    assert all(f.endswith(".json") for f in files), \
        f"unexpected non-.json files in plans dir: {files}"


# ---- read semantics ----------------------------------------------------

def test_verify_after_cache_clear_loads_from_disk(monkeypatch, tmp_path):
    """The persisted plan survives an in-memory cache reset."""
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    plan_id = _plan(["thing happens"])
    _reset_cache_for_tests()  # simulate process restart
    result = verify_plan_tool({
        "plan_id": plan_id,
        "evidence": ["thing happens here"],
    })
    assert result["status"] == "passed"
    assert result["plan_source"] == "disk"


def test_verify_in_memory_marks_source_as_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    plan_id = _plan()
    result = verify_plan_tool({"plan_id": plan_id, "evidence": []})
    assert result["plan_source"] == "memory"


def test_disk_load_repopulates_memory_cache(monkeypatch, tmp_path):
    """After a disk-load, subsequent calls should hit memory (not disk)."""
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    plan_id = _plan()
    _reset_cache_for_tests()

    # First call — disk
    r1 = verify_plan_tool({"plan_id": plan_id, "evidence": []})
    assert r1["plan_source"] == "disk"

    # Second call — should be memory now
    r2 = verify_plan_tool({"plan_id": plan_id, "evidence": []})
    assert r2["plan_source"] == "memory"


def test_disk_read_honors_expiry(monkeypatch, tmp_path):
    """A persisted plan past its TTL must NOT be loaded.

    Pin _now to far in the future after creating the plan.
    """
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    plan_id = _plan()
    _reset_cache_for_tests()

    # File still exists
    persisted = tmp_path / f"{plan_id}.json"
    assert persisted.is_file()

    with patch.object(qa_plan, "_now") as fake_now:
        fake_now.return_value = datetime.now(timezone.utc) + timedelta(
            seconds=_CACHE_TTL_SECONDS + 60
        )
        result = verify_plan_tool({"plan_id": plan_id, "evidence": []})
        assert result["error"] == "plan_not_found"


# ---- corruption / missing-file resilience -----------------------------

def test_corrupt_json_on_disk_treated_as_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    # Write a malformed file under a fake plan_id
    fake_id = "deadbeef0000"
    (tmp_path / f"{fake_id}.json").write_text("{ not json", encoding="utf-8")
    result = verify_plan_tool({"plan_id": fake_id, "evidence": []})
    assert result["error"] == "plan_not_found"


def test_missing_file_treated_as_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    result = verify_plan_tool({"plan_id": "nonexistent1", "evidence": []})
    assert result["error"] == "plan_not_found"


def test_wrong_shape_json_treated_as_not_found(monkeypatch, tmp_path):
    """JSON parses but doesn't have the expected fields → plan_not_found."""
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    fake_id = "deadbeef0001"
    (tmp_path / f"{fake_id}.json").write_text(
        json.dumps({"unrelated": "data"}), encoding="utf-8"
    )
    result = verify_plan_tool({"plan_id": fake_id, "evidence": []})
    assert result["error"] == "plan_not_found"


def test_persist_failure_does_not_raise(monkeypatch, tmp_path):
    """If the plans dir can't be written (read-only filesystem,
    permission error), qa_plan must still return a valid result with
    persisted_to=None — never raise into the caller."""
    # Point plans dir at something un-writable (simulate by patching).
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")

    def fake_persist(*_args, **_kw):
        return None

    monkeypatch.setattr(qa_plan, "_persist_plan", fake_persist)
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    assert "error" not in result
    assert result["persisted_to"] is None


# ---- round-trip preserves data ------------------------------------------

def test_roundtrip_preserves_critical_point_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    plan_id = qa_plan_tool({
        "task": "Round-trip check",
        "critical_points": [{
            "id": "X-1",
            "description": "Detailed description",
            "verification_hint": "specific marker",
        }],
        "kind": "debug",
    })["plan_id"]

    _reset_cache_for_tests()

    result = verify_plan_tool({
        "plan_id": plan_id,
        "evidence": ["evidence containing specific marker"],
    })
    assert result["plan_source"] == "disk"
    cp = result["checklist"][0]
    assert cp["id"] == "X-1"
    assert cp["description"] == "Detailed description"
    assert cp["verification_hint"] == "specific marker"
    assert cp["satisfied"]


def test_roundtrip_preserves_task_and_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_PLAN_PERSIST", "true")
    monkeypatch.setenv("MK_QA_PLANS_DIR", str(tmp_path))
    plan_id = qa_plan_tool({
        "task": "Original task description verbatim",
        "kind": "captcha",
        "critical_points": ["x"],
    })["plan_id"]

    _reset_cache_for_tests()
    result = verify_plan_tool({"plan_id": plan_id, "evidence": []})
    assert result["task"] == "Original task description verbatim"
    assert result["kind"] == "captcha"


# ---- backward compat with v0.9.1/v0.9.2 ---------------------------------

def test_persist_off_v091_v092_behavior_unchanged():
    """When persistence is off, none of the new fields should have
    surprising values."""
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    assert plan["persisted_to"] is None

    result = verify_plan_tool({"plan_id": plan["plan_id"], "evidence": ["y"]})
    # plan_source still surfaced — "memory" — but no disk involvement
    assert result["plan_source"] == "memory"
    assert result["evidence_sources"]["autodiscovered"] is False
