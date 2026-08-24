# Task 8 report: native SwiftUI Strategy Lab

## Outcome

Task 8 is complete. The macOS app now strictly consumes snapshot schema v2, exposes typed strategy evaluation/learning/export requests, streams bounded JSONL progress while the engine is running, and provides a native Strategy Lab with plural selection, a detail inspector, bounded-learning evidence, explicit uncertainty, and no broker-order surface.

The implementation is HIG-aligned: it extends the existing macOS design system using `NavigationSplitView`, `Table`, Charts, semantic colors/materials, SF Symbols, native controls, keyboard selection, dynamic type, VoiceOver labels, and standard progress views.

## Files changed

- `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift` — added exact schema-v2 strategy, ensemble-component, coverage/gap/calendar, learning-run/trial/rule, and causal-audit DTOs while preserving legacy sections.
- `macos/Nowcaster/Sources/NowcasterApp/Services/SnapshotRepository.swift` — accepts and validates schema v2 only; rejects v1, unknown, and malformed payloads.
- `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift` — added typed strategy modes/providers/intervals/assets and safe evaluate, learn, and export invocations matching the Task 7 CLI.
- `macos/Nowcaster/Sources/NowcasterApp/Services/EngineRunner.swift` — added incremental partial-line JSONL decoding, bounded events/diagnostics, cancellation, and nonzero-exit handling.
- `macos/Nowcaster/Sources/NowcasterApp/AppDestination.swift`, `AppModel.swift`, and `RootView.swift` — added Strategy Lab navigation, plural selection, inspector state, scoped engine actions, and readable adaptive sidebar presentation.
- `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift` — added the native strategy table, signed-contribution research posture, action bar, and presentation models.
- `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyDetailView.swift` — added research posture, weight/contribution/progress, readiness, warnings, causal/no-repaint gates, separated development/final metrics, chart alternative, coverage provenance, and uncertainty disclosures.
- `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/LearningWorkspaceView.swift` — added bounded run/trial/rule progress, final boundary, generation/complexity, and empty states.
- `macos/Nowcaster/Sources/NowcasterApp/NowcasterApp.swift` — added deterministic wide/narrow screenshot sizing and Strategy Lab export command support.
- `macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json` — minimally upgraded the bundled demo to valid schema v2 research evidence; Task 9 remains responsible for the full research fixture.
- `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`, `EngineRunnerTests.swift`, and `StrategyLabTests.swift` — added decoding, request safety, incremental progress, presentation, navigation, selection, empty-state, no-repaint, accessibility, and responsive-layout coverage.
- `scripts/capture_macos_app.swift` — added Strategy Lab light/dark and wide/narrow captures plus a focused capture option.

## Behavior delivered

### Strict schema-v2 snapshot contract

- All Task 7 v2 research sections decode with snake-case conversion and the existing ISO-8601 date strategy, including fractional seconds.
- `LearningRunSnapshot.bestRule` is `String?`, `bestRuleDetail` remains separate, and `finalBoundary` is required.
- Every legacy snapshot field remains present inside v2. Schema v1 and incompatible versions are rejected before payload decoding; malformed v2 and invalid bounded values are rejected as unreadable.
- Recursive JSON evidence is represented by a bounded typed value rather than untyped arbitrary objects.

### Safe typed engine boundary

- Evaluation repeats `--strategy-id` and passes validated provider, feed, symbol, interval, and mode arguments as literal `Process` arguments; values are never shell-interpolated.
- Learning requires a matching typed asset context, selected strategy IDs, a valid interval, and a positive evaluation budget. It maps exactly to `strategy learn ... --evaluation-budget`.
- Export maps to `strategy export --output`. Existing rebuild and full-backtest actions retain their established argument behavior.
- Output is consumed incrementally with `FileHandle.availableData`; complete JSONL records can reach the UI before process exit, partial lines are retained, event buffering and diagnostics are bounded, cancellation terminates the child, and nonzero exits retain bounded diagnostic context.
- No API permits arbitrary executable or command-string construction from the Strategy Lab.

### Native Strategy Lab

- Research navigation contains `.strategyLab`; table selection supports plural evaluation while the first selected strategy drives the right-side inspector.
- Signed current ensemble contribution is rendered as **Long research**, **Short research**, or **Abstain** with symbols and text. It is explicitly labeled as a contribution/research posture and not a trade instruction.
- The inspector separates development from sealed-final metrics and presents weight, current contribution, progress, generation, complexity, promotion/readiness, warnings, causal/no-repaint status, evidence gates, exact coverage timestamps, provider/feed, calendar provenance, gaps, and profit-not-promised uncertainty language.
- The learning workspace presents bounded budget progress, run state, final boundary, selected rule, trials, generation/complexity, no-repaint state, and actionable empty states. Progress uses the standard macOS control and remains understandable without animation.
- Evaluate Selected, Learn, and Export use typed jobs, disable during incompatible/running states, and expose descriptive accessibility identifiers, labels, and help. No broker order placement exists.
- Narrow presentation uses compact icon actions and stacked metric sections; wide presentation keeps paired metric cards. Light and dark modes use only semantic SwiftUI styles.

## TDD evidence

### Prescribed decoding/request RED/GREEN

- RED: `swift test --filter 'SnapshotDecodingTests|EngineRunnerTests'` — compilation failed on the intentionally missing v2 DTOs, typed asset context/jobs, and incremental decoder.
- GREEN: the same focused slice — **14 passed**.

### Prescribed Strategy Lab RED/GREEN

- RED: `swift test --filter StrategyLabTests` — compilation failed on the intentionally missing Strategy Lab destination, selection/presentation models, and accessibility contracts.
- GREEN after native feature implementation — all Strategy Lab tests passed.

### Hardening RED/GREEN

- A real subprocess timing regression proved the original read path emitted progress only at EOF; it failed before switching to incremental `availableData`, then passed with a completed JSON event observed more than 500 ms before process completion.
- Typed learning configuration, missing-asset fail-closed behavior, bounded emitted diagnostics, progress wording, no-repaint failure labels, and wide/narrow window/layout contracts each failed before their corresponding implementation fix and passed afterward.
- Visual QA found clipped native section headers and a truncated Budget selection in narrow captures. The focused `narrowNavigationAndBudgetControlsReserveReadableSpace` contract failed first, then passed after the native inset and compact numeric-picker fixes.

## Visual QA

The signed app was built with `scripts/build_macos_app.sh`, all four Strategy Lab states were recaptured, and the actual PNGs were inspected:

- `build/task8-captures/strategyLab-light.png` — 2880×1800, clear wide hierarchy; complete toolbar controls, paired metric cards, chart, and provenance.
- `build/task8-captures/strategyLab-dark.png` — 2880×1800, semantic dark contrast remains readable across table, inspector, badges, chart, and secondary copy.
- `build/task8-captures/strategyLab-light-narrow.png` — 1800×1400, compact actions, numeric Budget, complete sidebar headers, readable learning progress, and stacked detail evidence without clipping.
- `build/task8-captures/strategyLab-dark-narrow.png` — 1800×1400, the same adaptive hierarchy and complete labels with appropriate dark-mode contrast.

Final observation: all reported clipping/truncation was corrected; no overlap, cut-off primary labels, padding break, or color-only status was observed in the final captures. This is a visual HIG-alignment assessment, not an Apple certification claim.

## Final verification

- `cd macos/Nowcaster && swift test` — **1 XCTest + 37 Swift Testing tests passed (38 total)** in the final run.
- `cd macos/Nowcaster && swift build -c release` — **passed**.
- `.venv/bin/pytest tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py tests/integration/test_native_snapshot_demo.py -q` — **16 passed in 67.20s**.
- `scripts/build_macos_app.sh` — **passed**; app rebuilt and signed at `build/Nowcaster.app`.
- `xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app build/task8-captures --strategy-lab-only` — **4 screenshots captured**.
- `xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app build/task8-smoke --verify-only` — **Nowcaster UI smoke test passed**.
- `git diff --check` — **passed**.

## Self-review and remaining concerns

- Snapshot compatibility is deliberately strict: the app will show its incompatible/unavailable state until a schema-v2 snapshot is regenerated.
- CLI inputs are enum-validated or scoped to snapshot-derived values and remain discrete process arguments. Strategy IDs containing shell metacharacters are tested as literal values.
- Live event and diagnostic histories are bounded at decoder/stream/model boundaries; partial and terminal lines are covered.
- Development and sealed-final evidence are never visually blended, and current signed contribution is never described as an order or profit expectation.
- The bundled v2 fixture is intentionally small for Task 8 UI/tests. Task 9 will generate the full research fixture.
- No credentialed live provider call was made, and no broker integration or order placement was added.

## Review fix round 1/5 addendum

### Outcome

All six Important review findings are resolved without changing the progress ledger. Strategy and ensemble rows now carry `dataset_hash`, `mode`, and cohort identity; joins and native row IDs use the exact research context, with causal audit matching through the exact fields available to the audit schema. Plural evaluation fails closed unless every selected row shares one dataset/provider/feed/symbol/interval/mode/cohort context, and bounded learning additionally requires exactly one unique strategy.

Typed requests deduplicate strategy IDs, enforce a `1...100` learning budget, and reuse stored CSV datasets without requiring `csv_path`. Schema-v2 instants accept literal `Z` UTC only while legacy date-only fields remain supported. Snapshot input, recursive depth, aggregate nodes, collection sizes, keys, and strings are bounded before/during native decoding; Python evidence and audit-detail models enforce the matching structural limits.

Early stream cancellation now persists a termination request, retains/cancels the worker, and checks cancellation immediately before and after launch. Successful evaluate/learn jobs stream live determinate progress, automatically export the Task 7 snapshot, and reload it. Structured Task 7 errors outrank generic nonzero diagnostics and remain visible. Cached data remains on refresh failure with an accessible stale/incompatible banner.

### TDD checkpoints

- RED: the prescribed Swift slice failed to compile on the intentionally absent context DTOs, bounded decoder, homogeneous selection resolution, early-launch gate, live progress/outcome state, and stale-banner contracts.
- RED: Python snapshot tests failed on the absent context fields/exact joins and literal-`Z`/bounded evidence contracts.
- RED: the real early-cancel subprocess test proved a cancellation arriving before worker registration could otherwise launch work; the marker-file assertion now remains empty deterministically.
- RED: the complete Python run exposed persisted internal gap timestamps using `+00:00`; export failed under the strict wire model. Normalizing stored timestamps to UTC datetime objects before public-model validation made the focused regression and full suite pass without weakening literal-`Z` input validation.
- RED visual: stale banner placement initially occluded sidebar rows, action controls, and the inspector title; after moving it into the root vertical layout, macOS 26's automatic top-edge fade still obscured wide-dark content. The final guarded edge-effect treatment makes every column begin visibly below the banner.
- GREEN: `swift test --filter 'SnapshotDecodingTests|EngineRunnerTests|StrategyLabTests|AppModelTests'` — **37 passed**.
- GREEN: focused Python snapshot compatibility — **19 passed in 70.29s**.

### Final verification

- `cd macos/Nowcaster && swift test` — **1 XCTest + 48 Swift Testing tests passed (49 total)** in the final post-visual-fix run.
- `cd macos/Nowcaster && swift build -c release` — **passed** after the final layout change.
- `.venv/bin/pytest -q` — **464 passed in 149.40s**.
- Ruff check and format-check for all changed Python files — **passed**.
- `scripts/build_macos_app.sh` — **passed**; `build/Nowcaster.app` rebuilt and signed.
- `xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app build/task8-fix1-captures-final --strategy-lab-only` — **4 normal screenshots captured**.
- The same command with `build/task8-fix1-stale-captures-final --stale-banner` — **4 stale-banner screenshots captured**.
- `xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app build/task8-fix1-smoke-final --verify-only` — **Nowcaster UI smoke test passed**.
- `git diff --check` — **passed**.

### Visual QA observations

- `build/task8-fix1-captures-final/strategyLab-light.png` and `strategyLab-dark.png`: native wide hierarchy, exact action status, Budget `20`, bounded progress, table/inspector labels, and semantic contrast are readable.
- `build/task8-fix1-captures-final/strategyLab-light-narrow.png` and `strategyLab-dark-narrow.png`: Monitor/Research/System headers are complete, action controls and Budget remain untruncated, and progress/evidence sections do not overlap.
- `build/task8-fix1-stale-captures-final/strategyLab-light.png` and `strategyLab-dark.png`: full-width stale banner, Refresh action, center action toolbar, and complete `Rsi Reversal` inspector title are visible in separate rows; cached research remains visible.
- `build/task8-fix1-stale-captures-final/strategyLab-light-narrow.png` and `strategyLab-dark-narrow.png`: banner title/message/action fit without clipping and all three columns start below it.

Final assessment remains HIG-aligned, not Apple-certified. No broker action was introduced. `cohort_id` remains nullable only for legacy snapshot compatibility; newly built v2 strategy/ensemble outputs populate it when cohort evidence exists. Audit rows do not invent cohort identity because the Task 7 audit store has no cohort column; they match exactly through dataset/strategy/version/symbol/interval/mode.

## Review fix round 2/5 addendum

### Outcome

All three re-review findings are resolved while preserving the progress ledger. The typed export job now accepts an optional database URL and emits Task 7's literal argument sequence `strategy export [--database-url VALUE] --output PATH`; there is still no shell interpolation or arbitrary command construction. Evaluation follows with an export against the originating `StrategyAssetContext.databaseURL`, while learning uses the database URL from its validated configured asset context. The literal-value test includes spaces and a semicolon.

Snapshot regeneration policy is explicit. Evaluation and learning export from their scoped research database. Rebuild and full backtest have no originating strategy context, so they export from Task 7's configured/default database, always target `EngineConfiguration.snapshotURL`, then reload that exact URL. Direct export is terminal and does not recursively export. Tests exercise a custom snapshot path for both rebuild and full backtest, verify the primary/export job sequence and reloaded commit, and prove a structured export error remains preferred over the generic nonzero exit.

Screenshot presentation now waits for the actual restored/new native window before applying its explicit content size. The capture harness terminates prior instances, launches a new instance, polls the owning process's window until three stable samples match the requested logical dimensions within three points, validates PNG pixel dimensions at the display backing scale, rejects byte-identical wide/narrow output, and cleans up even on failure. This prevents a restored narrow window from being mislabeled as wide (or vice versa).

### TDD checkpoints

- RED export/refresh: the focused Swift slice failed to compile because `.exportSnapshot` had no database context and the new auto-export sequence/custom-path expectations could not be expressed.
- RED presentation: `screenshotPresentationResizesAnExplicitRestoredWindow` failed to compile before the explicit-window sizing API existed.
- RED capture truth: the hardened harness rejected a nominal narrow capture with `expected 900x700; got 1440x900`, proving the previous key-window timing could silently retain restored dimensions. The earlier stale light-wide artifact was 1800x1400 and byte-identical to stale light-narrow rather than the required 2880x1800.
- GREEN: `swift test --filter 'learningAndExportArgumentsMatchTheTask7CLI|successfulStrategyJobStreamsProgressThenExportsAndReloads|successfulLearningExportsFromItsConfiguredAssetDatabase|rebuildAndFullBacktestExportThenReloadTheConfiguredSnapshotPath|structuredExportFailurePersistsAfterSuccessfulRebuild'` — **5 passed**.
- GREEN: `swift test --filter 'screenshotPresentationResizesAnExplicitRestoredWindow|screenshotWindowContractCreatesDistinctUsableWideAndNarrowLayouts'` — **2 passed**.

### Final verification

- `cd macos/Nowcaster && swift test` — **1 XCTest + 52 Swift Testing tests passed (53 total)** in the fresh final rerun. An immediately preceding full run had one load-sensitive failure in the existing incremental-process timing threshold; that test passed in isolation and in the complete final rerun.
- `cd macos/Nowcaster && swift build -c release` — **passed**.
- `.venv/bin/pytest -q` — **464 passed in 221.33s**.
- `scripts/build_macos_app.sh` — **passed**; the release app was rebuilt and signed at `build/Nowcaster.app`.
- `xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app build/task8-fix2-smoke --verify-only` — **Nowcaster UI smoke test passed** with the asserted narrow window size.
- `xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app build/task8-fix2-captures-final --strategy-lab-only` — **4 normal screenshots captured** with validated dimensions and nonidentity.
- The same capture with `build/task8-fix2-stale-captures-final --stale-banner` — **4 stale screenshots captured** with validated dimensions and nonidentity.
- `git diff --check` — **passed** before the report update and is rerun immediately before commit.

### Capture truth and visual QA

- Normal light/dark wide: `build/task8-fix2-captures-final/strategyLab-light.png` and `strategyLab-dark.png`, each **2880x1800**.
- Normal light/dark narrow: the corresponding `-narrow.png` files, each **1800x1400**.
- Stale light/dark wide: `build/task8-fix2-stale-captures-final/strategyLab-light.png` and `strategyLab-dark.png`, each **2880x1800**.
- Stale light/dark narrow: the corresponding `-narrow.png` files, each **1800x1400**.
- All eight SHA-256 values are distinct. In particular, stale light-wide is `61973308...` and stale light-narrow is `e7561e55...`; they are neither dimensionally nor byte identical.

The actual final PNGs were inspected at original detail. Normal and stale states are fully loaded. In stale wide light/dark, the banner remains a separate full-width row and does not obscure the center action toolbar or the `Rsi Reversal` inspector title. In all narrow states, Monitor/Research/System headers, Budget `20`, progress, and evidence labels are complete. Light/dark hierarchy and semantic contrast are readable, with no new overlap or clipping observed. This remains a HIG-aligned visual assessment, not Apple certification.

### Remaining concern

The existing subprocess incremental-timing assertion is sensitive to machine load because it compares a 500 ms scheduling threshold. Its isolated rerun and complete final run passed, and this round did not alter the stream implementation; the first full-run miss is recorded rather than hidden.
