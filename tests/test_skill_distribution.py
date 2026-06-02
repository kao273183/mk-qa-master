"""Smoke tests for the v0.9.0 cross-host skill distribution layer.

Validates that:
- skills/mk-qa-master/SKILL.md exists and has parseable YAML frontmatter
- The frontmatter declares the fields agentskills.io / Claude Code expect
- Slash commands under skills/mk-qa-master/commands/ have frontmatter too
- .claude-plugin/plugin.json and .codex-plugin/plugin.json are valid JSON
  with the fields each host's plugin marketplace requires
- The plugin manifests' `version` matches pyproject.toml
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "mk-qa-master"
SKILL_MD = SKILL_DIR / "SKILL.md"
COMMANDS_DIR = SKILL_DIR / "commands"
REFERENCE_DIR = SKILL_DIR / "reference"
CLAUDE_PLUGIN = REPO_ROOT / ".claude-plugin" / "plugin.json"
CODEX_PLUGIN = REPO_ROOT / ".codex-plugin" / "plugin.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# ---- helpers --------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<frontmatter>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Tiny YAML-ish frontmatter parser. Handles `key: value` and
    `key: |\n  multiline`. We don't want a heavy YAML dep for a test."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise AssertionError("missing frontmatter delimited by `---` lines")
    fm_raw = match.group("frontmatter")
    body = match.group("body")
    out: dict[str, str] = {}
    current_key: str | None = None
    for raw_line in fm_raw.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith((" ", "\t")) and current_key:
            out[current_key] += " " + raw_line.strip()
            continue
        if ":" not in raw_line:
            continue
        key, _, val = raw_line.partition(":")
        current_key = key.strip()
        out[current_key] = val.strip()
    return out, body


def _pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "couldn't find version in pyproject.toml"
    return match.group(1)


# ---- SKILL.md -------------------------------------------------------------

def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"missing {SKILL_MD.relative_to(REPO_ROOT)}"


def test_skill_md_frontmatter_has_required_fields():
    fm, body = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    # Claude Code / agentskills.io require name, description, allowed-tools
    for required in ("name", "description", "allowed-tools"):
        assert required in fm, f"SKILL.md frontmatter missing `{required}`"
    assert fm["name"] == "mk-qa-master"


def test_skill_md_description_long_enough_for_router():
    """Skill routers use the description to decide auto-activation. Too
    short = router can't tell when to fire."""
    fm, _ = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    desc = fm["description"]
    assert len(desc) > 200, (
        f"description too short ({len(desc)} chars) — router needs "
        f"enough text to distinguish QA-testing prompts from other "
        f"intents"
    )
    # Must mention the headline capabilities for the router to match
    must_mention = ("pytest", "OWASP", "CAPTCHA")
    missing = [m for m in must_mention if m.lower() not in desc.lower()]
    assert not missing, f"description missing keywords: {missing}"


def test_skill_md_body_references_reference_files():
    """Reference files only help if SKILL.md tells the agent to read them."""
    _, body = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    for ref in ("reference/workflow.md", "reference/tool-surface.md"):
        assert ref in body, f"SKILL.md body doesn't reference {ref}"


def test_skill_allowed_tools_subset():
    """Constrain to a known-safe tool surface — don't auto-grant the
    skill carte blanche over the host."""
    fm, _ = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
    tools = {t.strip() for t in fm["allowed-tools"].split(",")}
    # We use Bash + Read + Write + Edit; nothing exotic.
    allowed = {"Bash", "Read", "Write", "Edit"}
    extra = tools - allowed
    assert not extra, (
        f"SKILL.md allowed-tools contains unexpected tools: {extra}. "
        f"Tighten to {allowed} unless there's a documented reason."
    )


# ---- Slash commands -------------------------------------------------------

EXPECTED_COMMANDS = {"run-tests.md", "generate.md", "api-security.md"}


def test_commands_directory_exists():
    assert COMMANDS_DIR.is_dir(), f"missing {COMMANDS_DIR.relative_to(REPO_ROOT)}"


def test_expected_slash_commands_present():
    present = {f.name for f in COMMANDS_DIR.iterdir() if f.suffix == ".md"}
    missing = EXPECTED_COMMANDS - present
    assert not missing, f"missing slash commands: {missing}"


@pytest.mark.parametrize("filename", sorted(EXPECTED_COMMANDS))
def test_slash_command_has_frontmatter(filename):
    path = COMMANDS_DIR / filename
    fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    for required in ("description", "argument-hint"):
        assert required in fm, f"{filename} missing `{required}` frontmatter"
    assert "$ARGUMENTS" in body, (
        f"{filename} doesn't reference $ARGUMENTS — slash command "
        f"won't receive the user's input"
    )


# ---- Reference docs -------------------------------------------------------

def test_reference_directory_has_docs():
    assert REFERENCE_DIR.is_dir()
    docs = [f.name for f in REFERENCE_DIR.iterdir() if f.suffix == ".md"]
    for required in ("workflow.md", "tool-surface.md", "wire-mcp.md"):
        assert required in docs, f"missing reference/{required}"


# ---- Plugin manifests ----------------------------------------------------

def _load_json(path: Path) -> dict:
    assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_claude_plugin_manifest_well_formed():
    manifest = _load_json(CLAUDE_PLUGIN)
    for required in ("name", "version", "description", "skills"):
        assert required in manifest, f"claude plugin.json missing `{required}`"
    assert manifest["name"] == "mk-qa-master"
    assert manifest["skills"] == "./skills"


def test_codex_plugin_manifest_well_formed():
    manifest = _load_json(CODEX_PLUGIN)
    for required in ("name", "version", "description", "skills", "interface"):
        assert required in manifest, f"codex plugin.json missing `{required}`"
    assert manifest["name"] == "mk-qa-master"
    interface = manifest["interface"]
    for required in ("displayName", "shortDescription", "longDescription"):
        assert required in interface, (
            f"codex plugin.json interface missing `{required}`"
        )


def test_plugin_versions_match_pyproject():
    """The plugin manifests' version field must match pyproject's. Drift
    between them is the kind of bug that bites at install time and is
    nearly invisible during development."""
    pyproject_version = _pyproject_version()
    for path in (CLAUDE_PLUGIN, CODEX_PLUGIN):
        manifest = _load_json(path)
        assert manifest["version"] == pyproject_version, (
            f"{path.relative_to(REPO_ROOT)} version {manifest['version']!r} "
            f"doesn't match pyproject.toml version {pyproject_version!r}"
        )


def test_plugin_manifests_point_at_real_skills_dir():
    """If the `skills` path doesn't resolve, /plugin install fails silently.

    Plugin paths are relative to the **plugin root** (the repo root, the
    parent of `.claude-plugin/`), not the manifest file itself. Mirrors
    Webwright's convention: `.claude-plugin/plugin.json` with
    `"skills": "./skills"` referring to the sibling `skills/` dir.
    """
    for path in (CLAUDE_PLUGIN, CODEX_PLUGIN):
        manifest = _load_json(path)
        skills_rel = manifest["skills"]
        resolved = (REPO_ROOT / skills_rel).resolve()
        assert resolved.is_dir(), (
            f"{path.relative_to(REPO_ROOT)} `skills` points at "
            f"{skills_rel} which resolves to {resolved} — not a directory"
        )


def test_claude_plugin_commands_points_at_real_dir():
    manifest = _load_json(CLAUDE_PLUGIN)
    commands_rel = manifest["commands"]
    resolved = (REPO_ROOT / commands_rel).resolve()
    assert resolved.is_dir(), f"claude plugin commands path doesn't resolve: {resolved}"


# ---- pyproject version sanity -------------------------------------------

def test_pyproject_version_is_semver_and_at_or_above_floor():
    """v1.0 PR-2 (postmortem §9 #3) — replaces the previous
    `startswith("0.10.")` hardcoded check with a softer guard:

      - version must be parseable as a major.minor.patch triple
      - version must be ≥ MIN_VERSION_FLOOR

    This stops every bump from having to edit a test, while still
    catching the case where someone accidentally downgrades or writes
    an unparseable version string.

    To raise the floor on a release (e.g., when v1.0 ships, set floor
    to (1, 0, 0)), update MIN_VERSION_FLOOR and the human-facing
    error message together — the floor itself is documented invariant,
    not just a constant."""
    # v1.0 shipped 2026-06-02 — floor is now (1, 0, 0). Any future
    # bump can leave this alone; downgrades and malformed versions are
    # what the test catches. Next floor raise lands with v2.0.
    MIN_VERSION_FLOOR = (1, 0, 0)

    raw = _pyproject_version()
    parts = raw.split(".")
    assert len(parts) == 3, (
        f"version must be major.minor.patch; got {raw!r}"
    )
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        pytest.fail(
            f"version components must be integers; got {raw!r}. "
            "If shipping a pre-release like '1.0.0rc1', update this "
            "test to handle the suffix."
        )
    current = (major, minor, patch)
    assert current >= MIN_VERSION_FLOOR, (
        f"pyproject version {raw!r} is below the documented floor "
        f"{MIN_VERSION_FLOOR}; this looks like an accidental downgrade. "
        "Bump the version and re-run, or raise the floor if you're "
        "intentionally backporting."
    )


# ---- PyPI Summary 512-char limit (regression test for v0.9.4→0.9.5) ----

def _pyproject_description() -> str:
    """Pull the `description = "..."` value from pyproject.toml.

    Stripped down to avoid pulling in `tomllib` or `toml` libs just
    for one field. The pyproject grammar always quotes description
    with double-quotes, and we don't currently use multi-line strings
    for it.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^description\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "couldn't find description in pyproject.toml"
    return match.group(1)


def test_pyproject_summary_under_512_chars():
    """PyPI rejects uploads where the Core Metadata `Summary` field
    exceeds 512 chars. pyproject's `description` is what becomes
    `Summary` in the built distribution, so this is the gate that
    keeps us from re-bricking PyPI publishes.

    v0.9.4 hit this wall: PyPI returned
        400 'summary' field must be 512 characters or less
    after the GitHub release had already been cut. The release
    workflow couldn't auto-publish to PyPI; we had to ship v0.9.5
    with a shorter description.

    Stay strictly under the limit — the boundary itself is fine but
    leave breathing room for one more sentence before the next limit
    hit.
    """
    desc = _pyproject_description()
    assert len(desc) <= 512, (
        f"pyproject `description` is {len(desc)} chars — PyPI rejects "
        f"anything > 512 in the `Summary` Core Metadata field. "
        f"Trim before bumping the version."
    )
