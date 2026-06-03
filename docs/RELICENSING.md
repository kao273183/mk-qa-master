# Relicense Plan — MIT → Apache 2.0

**Status:** Announced 2026-06-03 in v1.2.1 · **Target:** v2.0.0 · **Author:** Jack Kao (kao273183)

This document is the formal announcement that **mk-qa-master will relicense from MIT to Apache 2.0 in v2.0.0**. It captures the timeline, the user impact, and the rationale.

---

## TL;DR

If you pin `mk-qa-master>=1,<2` or `==1.x.*`, **nothing changes for you** — every v1.x release stays MIT-licensed forever. The relicense lands in v2.0.0 and applies only to v2.0+ releases.

If you upgrade to v2.0.0 when it ships, the code you receive is licensed under Apache 2.0. Apache 2.0 grants **strictly more rights** than MIT (explicit patent grant, trademark protection) while keeping the same commercial-use permission. There is no scenario where v1.x → v2.0 reduces your usage rights.

---

## Timeline

| Version | Status | License | Notes |
|---|---|---|---|
| v0.x → v1.2.0 | shipped to PyPI | MIT | All historical releases stay MIT forever — you can't retroactively change a license |
| **v1.2.1** | this release | MIT | **Announcement only**. Adds this document + README "License Evolution Plan" section. Starts the deprecation clock. |
| v1.3.x → v1.x.y | future v1.x releases | MIT | Hold cycle — at minimum one minor (v1.3.0) before v2.0 lands, possibly longer. v1.x users on a corporate pin can keep going. |
| **v2.0.0** | future | **Apache 2.0** | Actual relicense. Source headers, NOTICE file, LICENSE file replacement, manifest sync all land here. |

The deprecation cycle for a license change follows `docs/DEPRECATION-POLICY.md` §"License changes" (added in this v1.2.1 patch).

---

## Why Apache 2.0

Apache 2.0 is "MIT with explicit patent + trademark protection." Specifically:

| Right | MIT | Apache 2.0 |
|---|---|---|
| Commercial use | ✅ | ✅ (same) |
| Modification + derivative works | ✅ | ✅ (same) |
| Private / closed-source integration | ✅ | ✅ (same) |
| Distribution in proprietary products | ✅ | ✅ (same) |
| Explicit patent grant | ❌ implied at most | ✅ **§3 grants patent license; §3 terminates on patent litigation against the project** |
| Trademark protection | ❌ | ✅ **§6 reserves trademark** |
| Contributor IP clarity | ❌ informal | ✅ **§5 implies CLA — contributions are Apache 2.0 by default** |
| Corporate adoption friendliness | high | **higher** (Google, Apache Foundation, IBM all prefer it) |

**For users**: every right you have under MIT, you still have under Apache 2.0. Plus a few more (patent protection, trademark reservation enforced by license).

**For the project**: long-term sustainability. The implicit CLA in §5 means future external contributions are unambiguously Apache 2.0; the trademark reservation in §6 means the `mk-qa-master` name stays protected from confusing forks.

---

## What v1.x users should do

**Nothing.** Continue using mk-qa-master under MIT. Every release tag you have access to (v0.7.0 through v1.x.y) stays MIT forever.

If your company has a license policy that requires re-review for license changes, you have time:

- v1.2.1 (today): announcement. No license file changes.
- v1.3.0 (planned: Phase 4 + Theme C bundle per `docs/v1.2-planning.md`): still MIT.
- v1.4.x+ (TBD): still MIT.
- v2.0.0 (TBD, no earlier than v1.3.0 ships): Apache 2.0.

A pin like `mk-qa-master>=1,<2` keeps you on MIT indefinitely. Apache 2.0 only applies if you opt in by upgrading to v2.x.

---

## What this means for v1.x maintenance

mk-qa-master commits to bugfix-only patch releases on the v1.x line **for at least 6 months after v2.0.0 ships**, even after the v2.x line begins. v1.x.y patches stay MIT.

Specifically:
- Security fixes: backported to v1.x and shipped as v1.x.y+1 patches under MIT
- Critical bug fixes: same
- Feature additions: v2.x only (no backports)

After 6 months, v1.x enters reduced-support mode (security fixes only). v1.x reaches EOL when v3.0 ships, at the earliest.

---

## What v2.0+ users get

Same product, broader rights:
- Patent peace (Apache 2.0 §3) — if someone sues over a patent the project may infringe, the licensor's patent grant to that party terminates. Disincentivizes patent trolling against users.
- Trademark protection (§6) — the `mk-qa-master` name is reserved. Forks must rename if they want to distribute under their own brand.
- Implicit CLA (§5) — every contribution to v2.x main is auto-licensed Apache 2.0 to the project. Removes IP ambiguity for future external contributors.

Plus whatever v2.0's feature scope ends up being. Likely candidates per `docs/v1.2-planning.md`:
- Phase 4 (resilience + Edge optimizer signals)
- Theme C (YAML config UX)
- Theme E (OWASP API4 rate limit)
- Plus any v1.x → v2.0 surface cleanups that were waiting for a major bump

---

## Mechanical checklist for v2.0.0

When v2.0.0 actually ships, this checklist runs:

1. [ ] `LICENSE` file replaced with Apache 2.0 full text (from https://www.apache.org/licenses/LICENSE-2.0.txt)
2. [ ] `NOTICE` file created (Apache 2.0 §4(d) convention; lists project name + copyright)
3. [ ] `pyproject.toml`: `license = { text = "Apache-2.0" }`; classifier `"License :: OSI Approved :: Apache Software License"`
4. [ ] `.claude-plugin/plugin.json`: `"license": "Apache-2.0"`
5. [ ] `.codex-plugin/plugin.json`: `"license": "Apache-2.0"`
6. [ ] Source file headers (optional but recommended): SPDX-License-Identifier comment on the main source files
7. [ ] `docs/MIGRATION-1.x-to-2.0.md` created — license section as the first entry
8. [ ] `docs/DEPRECATION-POLICY.md` updated for v2.x cycle conventions
9. [ ] README license badge: shields.io Apache 2.0 SVG
10. [ ] `tests/test_license_metadata.py` — invariant: pyproject license string == LICENSE file SPDX-ID == both plugin manifests
11. [ ] Version bump 1.x.y → **2.0.0** across pyproject + 2 manifests
12. [ ] Soft semver floor in `tests/test_skill_distribution.py` bumped (1, 0, 0) → (2, 0, 0)
13. [ ] `v2.0.0` git tag + GitHub release + PyPI publish via OIDC

---

## Why v2.0.0 instead of a v1.x patch / minor

Per `docs/DEPRECATION-POLICY.md`:

> | Change | Breaking? |
> | --- | --- |
> | Loosen an arg's validation | No |

Apache 2.0 is strictly a "loosening" of MIT (more rights, fewer obligations on users — they get extra protection). Under that table, it could arguably be a minor bump.

**But license change is a contract change**, and the project's stability promise is the entire load-bearing thing v1.0 built. Doing license changes at major-version boundaries:

1. Sends a clear signal: "the contract evolved; pin accordingly"
2. Lets `docs/MIGRATION-*.md` documents have a clean v1.x ↔ v2.x split
3. Bundles with whatever other cleanups were waiting for a major bump (avoiding repeated v2.x patch noise)
4. Honors the v1.0 stability lock spirit, not just the letter

The cost is one extra version-number digit. Worth it.

---

## What "long-term sustainability" means here

The motivation for relicensing isn't dissatisfaction with MIT. MIT served mk-qa-master well from v0.7 through v1.2. The motivation is:

1. **Patent peace at scale**: as the user base grows, the probability that someone has a patent reading on some part of the toolset grows too. Apache 2.0's automatic patent grant + termination clause is a meaningful protection that MIT doesn't provide.

2. **Trademark protection**: as the project becomes more recognized, the value of the `mk-qa-master` name grows. Apache 2.0 §6 reserves it; MIT doesn't.

3. **Contributor IP unambiguity**: if external contributors arrive (currently none — but planning ahead), Apache 2.0's §5 means their contributions are unambiguously Apache 2.0 to the project. MIT requires per-contribution clarification.

4. **Corporate adoption**: many enterprise procurement processes specifically list Apache 2.0 as a preferred OSS license. MIT is also preferred, but Apache 2.0 is the modal corporate-friendly choice for new infrastructure tooling.

None of these are urgent — they're long-term hygiene. Hence: announce now, hold a cycle, ship as v2.0.0. No fire drill.

---

*Last updated: 2026-06-03 (v1.2.1 — relicense announcement). Cross-reference: `docs/DEPRECATION-POLICY.md`, `docs/MIGRATION-1.x.md`, the future `docs/MIGRATION-1.x-to-2.0.md`.*
