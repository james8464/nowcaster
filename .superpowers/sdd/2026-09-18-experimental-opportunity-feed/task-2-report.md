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

## Review fix round 1

### Red proof

Command: `swift test --filter LiveMonitorModelsTests`

```text
✘ Test experimentalOpportunityRequiresTheCompletePaperOnlyWirePayload() recorded an issue at LiveMonitorModelsTests.swift:150:5: Expectation failed: ExperimentalOpportunity(payload: unsafePayload, updatedAt: .now) == nil
↳ posture: "neutral"
✘ Test experimentalOpportunityRequiresTheCompletePaperOnlyWirePayload() recorded an issue at LiveMonitorModelsTests.swift:153:5: Expectation failed: ExperimentalOpportunity(payload: unsafePayload, updatedAt: .now) == nil
↳ qualificationReasons: []
✘ Test run with 7 tests in 0 suites failed after 0.004 seconds with 2 issues.
```

### Green proof

Command: `swift test --filter LiveMonitor`

```text
Build complete! (4.28 secs)
✔ Test experimentalOpportunityRequiresTheCompletePaperOnlyWirePayload() passed after 0.001 seconds.
✔ Test experimentalOpportunityPresentationIsExplicitlyPaperOnlyAndViewOnly() passed after 0.001 seconds.
✔ Test run with 15 tests in 0 suites passed after 0.004 seconds.
```

The strict parser now rejects every direction except `long` and `short`, and rejects empty `qualification_reasons` arrays. `git diff --check` passed.
