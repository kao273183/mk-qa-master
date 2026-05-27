---
description: Run a focused subset of the user's test suite via mk-qa-master, surface failures, and offer next steps.
argument-hint: <optional filter keyword>
---

You are operating as the mk-qa-master agent. The user wants to run their
tests. Follow Flow 1 in the parent `SKILL.md`.

Filter / keyword (if provided):

$ARGUMENTS

Steps:

1. **Confirm the runner.** Call `get_runner_info` and report which runner
   is active. If it's not what the user expected (e.g. they meant pytest
   but `QA_RUNNER=jest`), stop and ask.

2. **Enumerate tests.** Call `list_tests` and surface the count + a tree
   of the top 20 test names. If there's an obvious match for the user's
   filter, narrow to that.

3. **Run.** Call `run_tests` with the supplied filter (or no filter if
   none). Default to `headed=False`. Do NOT pass `headed=True` unless the
   user explicitly asked to see the browser.

4. **Report.**
   - If everything green: total count + duration + the slowest 3 tests.
   - If anything red: walk each failure with `get_failure_details`. Show
     the actual exception + the relevant stack frame, not just "assertion
     failed".
   - If the same tests have failed before (call `get_test_history` with
     `limit=5`), flag the pattern.

5. **Offer next step.** Pick one of:
   - "Want me to run `get_optimization_plan` for the suite?"
   - "Should I `run_failed` after you patch the issue?"
   - "Want me to dig deeper into `<failing_test>`?"
   Don't pile all three on the user — pick the most useful one given
   what's red.

Do NOT silently re-run with relaxed filters, skip markers, or
`--continue-on-collection-errors`. If pytest collection itself fails,
surface the collection error verbatim before doing anything else.
