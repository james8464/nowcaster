# Final Fix Report: Experimental Opportunity Feed

## Fixes

- Experimental opportunity emission no longer returns before the qualified active-setup abstention lifecycle runs. A qualified long setup whose `probability_lower_bound` drops below the threshold now emits the independently safe paper-only opportunity and also emits the normal `evidence_gate_failed` close transition and close notification.
- macOS experimental opportunities retain the newest 100 records. A refreshed existing record moves to the newest position, and the implementation avoids a full collection sort.

## Red evidence

- `pytest tests/unit/test_live_monitor_engine.py -q -k experimental_opportunity_does_not_bypass_active_setup_closure` failed before the engine change: the expected close notification count was `1`, received `0`.
- `swift test --filter LiveMonitorModelsTests` failed before the history implementation because `LiveMonitorExperimentalOpportunityHistory` was not in scope.

## Green evidence

- `pytest tests/unit/test_live_monitor_engine.py tests/unit/test_live_monitor_levels.py -q` — `36 passed`.
- `swift test --filter LiveMonitor` — `16 tests passed`.
- `git diff --check` — clean.

## Commit

Implementation commit: `cba6822c407315d45b66f01d425204c4a56614c1` (`fix(live-monitor): preserve qualified setup closure`).

The pre-existing untracked `.build/` directory was not included.
