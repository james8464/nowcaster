# Native release verification

## Audited artifact

- Audit date: 22 August 2026
- Repository: `https://github.com/james8464/nowcaster.git`
- Audited engine revision: `b3024cc2bcddf21f9fbab074e4a06405080972a7`
- Snapshot schema: `1`
- Snapshot mode: `demo_real_snapshot`
- Bundled snapshot SHA-256: `c415ce2dabea2b6cfed92454a25b3da64dbbf23ceb90721e755988fab8357c42`
- Release archive SHA-256: `8e1d85e0dd07c5f95e385eb038576b7ca86eafd0359e68762eb65c75fb47a071`

The snapshot revision is the committed engine code that generated the artifact. Documentation and the generated fixture are committed afterward without changing that engine.

## Clean verification

The generated database, app snapshot, and reports were removed before the authoritative run.

| Check | Evidence |
|---|---|
| Ruff | 125 files formatted; static checks passed |
| Python | 118 tests passed; 87% statement coverage |
| Demo pipeline | 12 stages run; 0 reused; 0 failed |
| Swift | 18 Swift Testing cases plus 1 XCTest passed |
| Native assembly | Release build completed; ad-hoc code signature verified |
| UI smoke | Signed app launched and exposed a real Nowcaster window |
| Property list | `plutil` reported `OK` |
| Screenshot matrix | 18 captures: all eight destinations light/dark plus two narrow layouts |

Commands:

```bash
make clean-generated
make lint
.venv/bin/pytest --cov=src --cov-report=term-missing -q
make demo
make sync-macos-snapshot
make macos-test
make macos-app
make macos-ui-test
make macos-screenshots
make release-archive
codesign --verify --deep --strict build/Nowcaster.app
plutil -lint build/Nowcaster.app/Contents/Info.plist
```

`spctl --assess` exits `3` for the local artifact because it is deliberately ad-hoc signed and not notarized. The release workflow conditionally uses Developer ID signing and notarization only when Apple credentials are configured; otherwise the checksumed archive remains a local/research build.

## Source integrity and database invariants

All 18 files declared across the SEC, price, crypto, Wikimedia, and macro manifests matched their SHA-256 hashes.

Key normalized row counts:

| Table | Rows |
|---|---:|
| SEC company quarters | 155 |
| Wikimedia daily observations | 12,210 |
| Adjusted equity/crypto/benchmark prices | 34,183 |
| Point-in-time equity feature rows | 6,604 |
| Crypto feature rows | 7,355 |
| Equity forecasts | 2,000 |
| Equity variant signals | 2,000 |
| Crypto daily signals | 6,505 |
| Event-study result rows | 8,000 |
| Crypto backtest positions/curve rows | 230 / 230 |

The audit found zero duplicate natural keys for companies, instruments, financial quarters, prices, features, forecasts, crypto features, signals, or backtest positions. It also found zero equity cutoff violations, crypto clock violations, nonpositive revenue forecasts, overlapping model folds, overlapping development/final periods, execution-lag violations, negative costs, exposure-cap violations, orphan position/signal joins, missing critical source labels, failed pipeline runs, or development labels crossing the final-test boundary.

## Native snapshot contract

The checked-in first-launch resource contains 5 instruments, 1,000 earnings rows, 2,000 research signals, 16 model diagnostics, 3 backtest presentations, 0 unresolved quality issues, and 12 pipeline records. Swift rejects incompatible schema versions, decodes extensible enums, and retains the last-known-good snapshot if a refresh fails.

Accessibility inspection exposed the native sidebar and its eight destinations, searchable toolbar, refresh control, core content, tables, and chart alternatives. Directional meaning is available through text and symbols without relying on color.

## Measured results without promotion bias

| System | Readiness | Development Sharpe | Final-test Sharpe | Full Sharpe | Full return | Trades | Max drawdown | Profitable subperiods |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC-USD ensemble | Research only | 0.769 | 0.571 | 0.691 | 89.3% | 173 | -26.1% | 66.7% |
| ETH-USD ensemble | Not ready | 0.152 | 0.593 | 0.258 | 10.7% | 57 | -16.8% | 42.9% |

BTC fails the 75% profitable-subperiod gate. ETH additionally fails sample, development-Sharpe, bootstrap, deflated-Sharpe, and stability gates. The equity event system is research-only and its [0,+3] top-minus-bottom abnormal-return spread is -0.045%. No bundled strategy is decision-ready.

These results model daily bars and declared frictions, not live fills, capacity, taxes, exchange failures, or guaranteed borrow. They are educational research, not investment advice, and do not establish future profitability.
