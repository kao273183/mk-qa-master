<!--
Template for: release PR (last PR in a v1.x series — bumps version,
syncs manifests, adds README/SKILL announcements, tags, ships).

For feature PRs, use feat-runner.md / feat-tool.md / feat-bookend.md.
-->

## Release: v1.X.Y

<!-- Patch / minor / major per DEPRECATION-POLICY.md §"Patch/Minor/Major" -->

- **Bump type**: patch / minor / major
- **Closes**: PR-N..M of v1.X.Y series
- **Theme**: <name> (link to docs/prd-v1.X.md)

## What changes

| File | Change |
|---|---|
| `pyproject.toml` | A.B.C → A.B.D (description trimmed to NNN/512 if rewritten) |
| `.claude-plugin/plugin.json` | version + description sync |
| `.codex-plugin/plugin.json` | version + description sync |
| `docs/MIGRATION-1.x.md` | NEW entry for vA.B.C → vA.B.D |
| `README.md` | NEW section announcing the change (if minor) |
| `skills/mk-qa-master/SKILL.md` | tool count refresh (if surface changed) |

## Version sync

| Surface | Old | New |
|---|---|---|
| pyproject.toml | A.B.C | A.B.D |
| .claude-plugin/plugin.json | A.B.C | A.B.D |
| .codex-plugin/plugin.json | A.B.C | A.B.D |
| Soft semver floor (`MIN_VERSION_FLOOR`) | (1, 0, 0) | (unchanged for v1.x) |

## What's NOT in this release

<!-- Items deferred from the PRD. Be honest. -->

## Test plan

- [ ] 389+/389+ tests pass locally
- [ ] PyPI Summary 512-char limit honored
- [ ] Snapshot unchanged (no surface change) OR snapshot ack triggered + MIGRATION entry added
- [ ] CI green (smoke + sample + edge-sample + ack-check)
- [ ] Doc-sync test green (tool counts match in README + SKILL.md + reference/*.md)

## Release procedure (post-merge)

```bash
git tag vA.B.D main
git push origin vA.B.D
gh release create vA.B.D --title "vA.B.D — ..." --notes "..."
# publish.yml auto-fires on release:published → PyPI publish via OIDC
```

## Roadmap context

<!-- What's next: v1.X+1 candidates / planning doc reference -->

## Cross-references

- `docs/prd-v1.X.md` — locked PRD for this release
- `docs/v1.X-planning.md` — strategic context
- `docs/MIGRATION-1.x.md` — v1.x additive change log
- `docs/DEPRECATION-POLICY.md` — formal cycle (unchanged)
