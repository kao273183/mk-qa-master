---
description: Generate maintainable pytest tests from a URL or mobile screen via mk-qa-master's analyzer.
argument-hint: <url-or-mobile-bundle-id>
---

You are operating as the mk-qa-master agent in test-generation mode.
Follow Flow 2 in the parent `SKILL.md`.

Target:

$ARGUMENTS

Steps:

1. **Decide web vs mobile.** If the target starts with `http://` /
   `https://`, it's web → `analyze_url`. If it's a bundle id / package
   name, it's mobile → `analyze_screen`. Otherwise ask the user.

2. **Discover.** Run `analyze_url(url, timeout_ms=15000)` (or
   `analyze_screen(...)` for mobile). Surface the discovered modules
   (`form`, `cta`, `tab_bar`, etc.) and the candidate test cases per
   module to the user BEFORE generating. Let them prune if there are
   too many (>10 modules is usually too many).

3. **Pick mode.**
   - If user said "generate everything" → `auto_generate_tests(url=...,
     tests_per_module=1)`. Default to 1 test per module; only go higher
     (3-5) if the user explicitly wants denser coverage.
   - If user pointed at one specific flow → `generate_test(description=
     "<the flow>", filename="<slug>", url=..., module=<the_module>)`.

4. **Verify the generated tests run.** Run them once with
   `run_tests(filter="<the_new_test_slug>")`. If pytest collection itself
   fails, the generated file has a syntax issue — surface the error and
   fix the file before reporting done.

5. **Report file paths.** The generated test file(s) go under
   `<PROJECT_ROOT>/tests/`. List them by absolute path. Tell the user how
   to re-run: `pytest tests/<filename>`.

Don't generate without showing the candidate modules first. Auto-
generation that surprises the user with 50 tests is worse than no
generation. The `tests_per_module` ceiling is 10 but anything above 3 is
usually garbage from the long tail of `candidate_tcs`.
