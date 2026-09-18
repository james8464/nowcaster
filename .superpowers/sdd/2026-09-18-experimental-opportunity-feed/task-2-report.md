# Task 2 report: experimental opportunity presentation

## Red proof

Command: `swift test --filter LiveMonitorModelsTests`

The new tests failed before production code existed:

```text
LiveMonitorModelsTests.swift:135:36: error: cannot find 'ExperimentalOpportunity' in scope
LiveMonitorPresentationTests.swift:49:24: error: cannot find 'ExperimentalOpportunityPresentation' in scope
error: Build failed
```

## Green proof

Command: `swift test --filter LiveMonitor`

Exact result:

```text
Build complete! (4.47 secs)
◇ Test run started.
✔ Test typedActiveSetupSurvivesDiagnosticEventEvictionAndAppliesTrackingState() passed after 0.001 seconds.
✔ Test liveMonitorColdStartGraceDoesNotRelaxReadyHeartbeatSupervision() passed after 0.001 seconds.
✔ Test deepResearchAlwaysReservesProcessorsForLiveMonitoringAndTheSystem() passed after 0.001 seconds.
✔ Test liveMonitorHealthUsesTextAndSymbolsRatherThanColorAlone() passed after 0.001 seconds.
✔ Test liveNotificationAuthorizationProjectsOnlyAnAllowedBoolean() passed after 0.001 seconds.
✔ Test notificationPolicyDeduplicatesAndSuppressesOnlyForegroundOrQuietEntryEvents() passed after 0.001 seconds.
✔ Test mixedProviderHealthUsesWorstSeverityInsteadOfLastWriter() passed after 0.001 seconds.
✔ Test experimentalOpportunityRequiresTheCompletePaperOnlyWirePayload() passed after 0.001 seconds.
✔ Test experimentalOpportunityPresentationIsExplicitlyPaperOnlyAndViewOnly() passed after 0.001 seconds.
✔ Test bundledEngineUsesItsNativeCLIContract() passed after 0.001 seconds.
✔ Test invocationKeepsCredentialsInBootstrapRatherThanArgumentsOrEnvironment() passed after 0.001 seconds.
✔ Test liveMonitorDecoderRejectsUnknownSchemaTypeAndNonZuluTime() passed after 0.002 seconds.
✔ Test liveMonitorDecoderIsIncrementalStrictAndBoundsLines() passed after 0.003 seconds.
✔ Test decisionPresentationSurfacesCostAdjustedEdgeAndDrift() passed after 0.003 seconds.
✔ Test monitorSettingsPersistWatchlistsWithoutSecrets() passed after 0.003 seconds.
✔ Test run with 15 tests in 0 suites passed after 0.004 seconds.
```

`git diff --check` also passed.

## Scope and safeguards

- The parser requires every `TradePlan` payload field, the explicit `experimental_paper_only: true` marker, `qualification_status: "unqualified"`, and a string-only qualification-reason array.
- Experimental records are projected separately from active setups. They do not use lifecycle transitions, notifications, fill tracking, broker controls, or order actions.
- The monitor shows them only in the dedicated Experimental opportunities section, labelled paper-only; it limits displayed blockers to three concise reasons.

## Concern

The worktree has a pre-existing untracked `docs/superpowers/plans/2026-09-18-experimental-opportunity-feed.md`; it is intentionally excluded from this task's commit.
