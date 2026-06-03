"""v1.2.0 PR-1 — Meta-test: CI workflow has the BREAKING_CHANGE_ACK
+ migration-doc-pairing check.

Three postmortems flagged this gap (v0.11 §9 #1, v1.0 §9 #1,
v1.1 §9 #2). This test exists so a future PR that accidentally
removes the workflow step gets caught locally before CI even runs.

The workflow step itself does the real enforcement at PR time; this
test just confirms it's present in the YAML.
"""
from __future__ import annotations

from pathlib import Path

import pytest


CI_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
)


def _ci_text() -> str:
    if not CI_WORKFLOW.is_file():
        pytest.fail(
            f"CI workflow missing: {CI_WORKFLOW}. This is the load-"
            f"bearing surface — every PR runs through it."
        )
    return CI_WORKFLOW.read_text(encoding="utf-8")


def test_ci_workflow_has_ack_check_job():
    """The job exists and runs on pull_request events."""
    text = _ci_text()
    assert "ack-check:" in text, (
        "ci.yml is missing the `ack-check` job. v0.11 / v1.0 / v1.1 "
        "postmortems each flagged this; v1.2 PR-1 is supposed to land "
        "it. Re-add the job before merging."
    )
    # Sanity: the job runs on PRs (not just push).
    assert "github.event_name == 'pull_request'" in text


def test_ack_check_inspects_breaking_change_ack_env():
    """The step reads BREAKING_CHANGE_ACK from CI vars/env."""
    text = _ci_text()
    assert "BREAKING_CHANGE_ACK" in text, (
        "ack-check job doesn't reference BREAKING_CHANGE_ACK; the "
        "enforcement is the whole point."
    )


def test_ack_check_diffs_migration_docs():
    """The step uses `git diff` to look at docs/MIGRATION-*.md."""
    text = _ci_text()
    assert "docs/MIGRATION-*.md" in text, (
        "ack-check job doesn't diff docs/MIGRATION-*.md; the pairing "
        "rule is unenforced."
    )
    assert "origin/main...HEAD" in text, (
        "ack-check job doesn't compare against origin/main; the diff "
        "may compare against the wrong base."
    )


def test_ack_check_fails_loudly_with_remediation_text():
    """When the check fails, the error message tells the contributor
    how to fix it. Three options, one of them ironic ('unset the ack')."""
    text = _ci_text()
    # Look for the remediation hints in the heredoc.
    assert "Unset BREAKING_CHANGE_ACK" in text or "Unset" in text
    assert "MIGRATION-1.x.md" in text


def test_pr_templates_exist():
    """PR description templates land alongside the ack-check (same PR-1
    per the v1.2 PRD §8). Four files: feat-runner, feat-tool,
    feat-bookend, release. GitHub picks the right one when multiple
    templates exist."""
    repo_root = Path(__file__).resolve().parent.parent
    template_dir = repo_root / ".github" / "PULL_REQUEST_TEMPLATE"
    assert template_dir.is_dir(), (
        ".github/PULL_REQUEST_TEMPLATE/ missing — v1.2 PR-1 is "
        "supposed to add four templates."
    )
    expected = {"feat-runner.md", "feat-tool.md", "feat-bookend.md", "release.md"}
    present = {p.name for p in template_dir.glob("*.md")}
    missing = expected - present
    assert not missing, (
        f"Missing PR templates: {sorted(missing)}. v1.2 PRD §11 #6 "
        f"ratified all four."
    )
