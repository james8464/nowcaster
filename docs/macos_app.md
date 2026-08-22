# Native macOS application

## Product boundary

Nowcaster is a local research monitor. The SwiftUI application displays a typed snapshot and may ask the local Python engine to rebuild it. It does not connect to a brokerage, submit orders, run continuously in the background, or represent a posture as financial advice.

## Navigation

- **Today**: evidence briefing, explicit caveats, and highest-ranked research.
- **Markets**: sortable instruments with native price charts and disclosure tables.
- **Earnings**: model forecast versus the labelled expectation source and reported actual.
- **Signals**: posture, evidence, catalyst, invalidation, calibration, and eligibility.
- **Backtests**: development/final-test separation, assumptions, curves, robustness, and warnings.
- **Model Lab**: fold, horizon, calibration, and feature-ablation diagnostics.
- **Data Quality**: prioritized provenance and chronology issues.
- **Pipeline Runs**: local engine controls, progress, cancellation, and recovery.

The structure uses native sidebars, toolbars, tables, master-detail navigation, SF Symbols, semantic colors, Dynamic Type-compatible text, and keyboard search. Light/dark appearance and narrow layouts are captured under `docs/images/macos/`.

## Local engine configuration

The app accepts only local executable, repository, and snapshot paths. Commands are launched with `Process.executableURL` and an argument array; no shell parses user-controlled text. Only those paths are persisted in `UserDefaults`. API keys remain in the Python process environment or `.env` and are never copied into the snapshot.

Research actions are explicit:

- **Rebuild all research** runs the deterministic pipeline.
- **Run full backtest** refreshes model and robustness outputs.
- **Export snapshot** republishes the strict native contract.
- **Cancel** terminates the current child process.

An unsuccessful job leaves the last-known-good snapshot visible with an error state.

## Packaging

```bash
make macos-test
make macos-app
codesign --verify --deep --strict build/Nowcaster.app
make release-archive
```

Local builds are ad-hoc signed. A GitHub tag matching `v*` runs the release workflow; Developer ID signing and notarization occur only when the corresponding secrets are configured. The archive is always accompanied by a SHA-256 file.

## Accessibility and QA

Charts have concise VoiceOver summaries and tabular data alternatives. Direction is communicated by text and symbol as well as color. Stable identifiers cover the sidebar, primary tables, toolbar actions, and core content. Run:

```bash
make macos-ui-test
make macos-screenshots
```

The first command launches the assembled app and requires a real window. The second captures all eight destinations in both appearances plus narrow-window cases.
