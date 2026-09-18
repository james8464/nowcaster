# Task 1 Report: Experimental Opportunity Engine Output

## Implementation

Implementation commit: `d0b2ec513a944bda48ea2c8fafc2f0cf6968c5e2` (`feat(live-monitor): emit experimental opportunities`).

- Added the `experimental_opportunity` live-monitor wire-event type.
- Emits a paper-only payload with the existing `TradePlan` geometry, `qualification_status: "unqualified"`, and the complete qualified-decision reason list.
- Uses only finalized, continuity-healthy bars and the existing level planner. It requires fresh matching evidence and quote data, a current directional signal, no-repaint evidence, and structurally available shorts.
- Permits an experimental output only for promotion, calibration, or contextual qualification failures; all other decision failures remain fail-closed.
- Returns after the decision audit and experimental event, so no setup, lifecycle transition, notification, health persistence, or order-related action is created for the opportunity.

## Test-first record

1. Added the paper-only emission test and safety-exclusion tests before modifying production code.
2. Confirmed the initial focused run was red because `experimental_opportunity` did not exist: `1 failed, 27 passed`.
3. Added the minimal engine and wire-event implementation, then verified the focused suites.

## Tests run

- `pytest tests/unit/test_live_monitor_engine.py -q` — `28 passed` after the first green cycle.
- `pytest tests/unit/test_live_monitor_engine.py tests/unit/test_live_monitor_levels.py -q` — `34 passed` final focused verification.
- `git diff --check` — clean.

## Scope

Only `src/live_monitor/engine.py`, `src/live_monitor/types.py`, and `tests/unit/test_live_monitor_engine.py` were included in the implementation commit. No frozen study sources were changed.

## Fix Round 1

Fix commit: `5c5f1470434413fcc9c3d8386815906c3ec75fd1` (`fix(live-monitor): constrain experimental opportunity inputs`).

- Experimental level planning now derives risk solely from the sorted, verified contiguous source-bar tail. A discontinuous historical bar cannot affect its ATR, structural invalidation, or output geometry.
- Replaced the contextual-reason prefix match with an explicit allowlist of the known contextual qualification failures. Unknown contextual-looking reasons now remain fail-closed.
- Added regression tests for both cases. The pre-fix engine run was red (`2 failed, 29 passed`): it used the historical gap in the stop calculation and emitted for an unknown contextual reason.
- Final verification: `pytest tests/unit/test_live_monitor_engine.py tests/unit/test_live_monitor_levels.py -q` — `36 passed`; `git diff --check` — clean.
