# Task 9 implementation report

## Status

Implemented and verified. The repository now publishes a deterministic, network-free causal research profile; a compact, explicitly incomplete official-Binance live attempt; data-quality evidence; beginner-facing methodology/provider/results documentation; external resumable cache support; and CI checks for Python, Swift, schema v2 fixture drift, research determinism, secrets, and generated research-artifact cleanliness.

No backtest is described as live evidence or a profit promise. The provider-backed attempt remains `unavailable` because the diagnostic run did not download the full range. Alpaca equity history remains `unavailable` because no usable credential pair was present.

## Files changed or added

Product changes:

- `.github/workflows/ci.yml`: tracked-secret scan, deterministic research/schema drift, scoped generated-artifact cleanliness, Swift release build.
- `.gitignore`: ignore bulk live research output while permitting only the compact live summary/report.
- `Makefile`: deterministic CI research, bounded live probe, research fixture verification, and secret-scan targets.
- `README.md`: beginner-facing intraday research workflow, commands, repository layout, quote/venue and storage disclosures.
- `pyproject.toml`: exclude `docs/superpowers/plans` execution metadata from Ruff so the plan remains byte-identical to `HEAD` while a clean checkout passes repository-wide formatting.
- `src/cli.py`, `src/research/__init__.py`, `src/research/full_history.py`: `strategy research` command and CI/live orchestration.
- `src/ingestion/binance_bars.py`: deterministic external page cache, adjacent SHA-256, verified resume, retry-compatible official API ingestion.
- `src/strategies/pipeline.py`: injectable execution assumptions recorded in cohort/evidence provenance and used by static and learning backtests.
- `scripts/scan_tracked_secrets.py`: deterministic scan of Git-tracked text without printing matched secret values.
- `tests/integration/test_full_strategy_research.py`: catalog/accountability, ensemble exclusion, snapshot/report consistency, reproducibility, and all-interval live-cache regression.
- `tests/unit/test_bar_ingestion.py`: cache resume and checksum-corruption tests.
- `data/demo/intraday/research-fixture.json`: deterministic fixture descriptor only.
- `data/research/ci/{nowcaster-snapshot.json,research-summary.json,strategy-research.md}`: compact deterministic artifacts and snapshot v2.
- `data/research/live/{research-summary.json,strategy-research.md}`: compact provider-attempt evidence only. The live snapshot is ignored.
- `docs/strategy-methodology.md`, `docs/data-providers.md`, `docs/research-results.md`: methodology, exact provider identity/setup, source links, results, limitations, and risk disclosure.

Mechanical Ruff normalization required for `.venv/bin/ruff format --check .` also touched these pre-existing Task 1–8 Python/code-fence files without intended behavior changes: `src/backtest/execution.py`, `src/backtest/intraday.py`, `src/ingestion/csv_bars.py`, `src/strategies/datasets.py`, `src/strategies/indicators.py`, `src/strategies/library.py`, `src/strategies/validation.py`, `tests/integration/test_learning_mode.py`, `tests/integration/test_strategy_engine.py`, `tests/integration/test_strategy_schema.py`, `tests/unit/test_learning_grammar.py`, `tests/unit/test_learning_search.py`, `tests/unit/test_strategy_ensemble.py`, `tests/unit/test_strategy_library.py`, `tests/unit/test_strategy_no_repaint.py`, `tests/unit/test_strategy_registry.py`, and `tests/unit/test_strategy_validation.py`.

Deleted files: none.

The SDD plan has no diff. The generated native app fixture was restored after a demo regeneration proved it would discard previously committed intraday strategy sections; the Task 9 CI cleanliness check is intentionally scoped to `data/research/ci`.

## Failing-test evidence (red before green)

1. `.venv/bin/pytest tests/integration/test_full_strategy_research.py -v` initially failed in about 4.3 seconds because the CLI had no `strategy research` command. The assertion saw a nonzero Typer result with `No such command 'research'`.
2. After the first minimal implementation, the required end-to-end test passed and a broader targeted selection passed: 11 tests in 51.28 seconds.
3. Final audit exposed the 4h requirement. `.venv/bin/pytest tests/integration/test_full_strategy_research.py::test_live_research_attempts_every_enabled_crypto_interval_from_external_cache -v` failed with `assert False` because both 4h attempt rows had `attempted_start=null` (`1 failed in 2.40s`).
4. The live runner was changed to ingest all required intervals even when an interval has no scalar-compatible strategy. The same command then passed (`1 passed in 2.11s`).
5. Final `.venv/bin/pytest tests/integration/test_full_strategy_research.py -v`: `2 passed in 53.27s`.

## Implementation decisions

- The CI clock and data are fixed: cutoff `2026-08-20T00:00:00Z`, 110 generated finalized bars per exercised scalar scope, UTC timestamps, `available_at=close_timestamp`, and no network/credentials. The fixture is labelled generated software-test data throughout.
- Enabled crypto intervals are derived from configuration and are exactly `5m`, `15m`, `1h`, and `4h`. Live ingestion attempts every interval for both configured crypto mappings. Strategy evaluation remains unavailable when asset/session/context requirements are unmet.
- `BTC-USD -> BTCUSDT` and `ETH-USD -> ETHUSDT` are explicit. The docs and manifests state that these are venue-specific USDT-quoted Binance spot pairs, not composite USD prices.
- The live default is exhaustive from the official earliest bar through one fixed UTC cutoff. `--max-chunks-per-scope` is diagnostic-only and guarantees an incomplete/unavailable result. Missing live bars are never replaced by CI bars.
- Binance REST pages retry bounded transient failures, write atomically to a caller-supplied directory outside the repository, receive adjacent SHA-256 files, are checksum-verified before reuse, and ingest in deterministic sorted order.
- Decisions inherit the existing point-in-time repository: only finalized records with `available_at <= decision_timestamp`; source bar `t` cannot fill earlier than the next actionable bar; prefix audits are published. Final-test chronology and the learning trial ledger remain sealed by the existing validation/learning pipeline.
- Research execution assumptions are explicit and hashed into evidence: taker fee 10 bps, half-spread 2 bps, slippage 5 bps, latency 250 ms, 5% volume participation, zero spot funding, and unavailable new spot shorts rather than an invented borrow rate. Existing tick/lot rounding, zero-volume rejection, and adverse stop-before-target intrabar ordering remain active.
- Ensemble weights remain nonnegative, shrunk 50% toward equal weight, capped at 25% per strategy and 50% per family. Failed/unavailable entries remain visible with zero weight for auditability and are excluded from the compact positive-component list.
- The final 4h fix separates dataset availability from strategy availability: the first BTCUSDT and ETHUSDT 4h chunks were downloaded, but `crypto_cross_sectional_momentum` remains unavailable because two assets cannot satisfy its five-asset universe.
- Alpaca probing reads only presence booleans across accepted aliases. It never serializes values. No credential variable was present in the controller environment.
- The native app fixture was not overwritten. The committed research snapshot validates as schema v2 and the Swift decoder/tests remain compatible.

## Exact live-download attempts and availability

Command used for the final provider artifact and cached rerun (fresh DuckDB names differed only by `-rerun`):

```bash
.venv/bin/python -m src.cli strategy research \
  --profile live \
  --database-url duckdb:///build/research-live-final.duckdb \
  --output-dir data/research/live \
  --cache-dir /tmp/nowcaster-binance-cache \
  --cutoff 2026-08-24T00:00:00Z \
  --max-chunks-per-scope 1
```

The official endpoint was `https://api.binance.com/api/v3/klines`. Initial 5m/15m/1h calls and the final four 4h earliest/page calls returned HTTP 200. Exact request-page paths, byte sizes, SHA-256 digests, and verification booleans are in `data/research/live/research-summary.json`.

| Pair | Interval | Attempted UTC range | Stored rows | Missing expected bars | Published result |
|---|---:|---|---:|---:|---|
| BTCUSDT | 5m | 2017-08-17 04:00 to 2017-09-16 04:00 | 8,557 | 83 | Unavailable |
| BTCUSDT | 15m | same | 2,853 | 27 | Unavailable |
| BTCUSDT | 1h | same | 714 | 6 | Unavailable |
| BTCUSDT | 4h | same | 180 | 0 | Data chunk complete; strategy unavailable |
| ETHUSDT | 5m | same | 8,557 | 83 | Unavailable |
| ETHUSDT | 15m | same | 2,853 | 27 | Unavailable |
| ETHUSDT | 1h | same | 714 | 6 | Unavailable |
| ETHUSDT | 4h | same | 180 | 0 | Data chunk complete; strategy unavailable |

For every scope, `2017-09-16T04:00:00Z` through `2026-08-24T00:00:00Z` is explicitly unattempted because the declared one-chunk diagnostic cap was reached. Consequently all 19 live strategy catalog entries and the learning benchmark are unavailable; there is no provider-backed performance result.

The final external cache contains 36 JSON response payloads plus 36 adjacent checksum files (72 files, 3.9 MB). Two fresh 12 MB live DuckDBs remained under ignored `build/`. Neither cache nor databases entered Git. The live working database stored 24,608 immutable bar rows. `APCA_API_KEY_ID`, `ALPACA_API_KEY`, `APCA_API_SECRET_KEY`, and `ALPACA_API_SECRET` were all absent; the summary records only `key_present=false` and `secret_present=false` with setup instructions.

## Data-quality profile and findings

CI fixture:

- Grain: finalized provider/feed/symbol/interval/open timestamp/revision.
- 330 rows across exercised BTCUSDT 5m/15m/1h scopes; 110 per scope.
- Duplicate logical bars 0; invalid OHLCV 0; gap count 0; revision rows 0.
- UTC/finalization/cutoff valid; latest close equals fixed cutoff.
- 327 returns: mean `0.0007822084613925625`, standard deviation `0.0011596091263340587`, min `-0.0008981494199395978`, max `0.002486616597159097`, robust outliers 0.
- Volume min/median/max: `100.31495666503906` / `181.38927459716797` / `261.9962463378906`.
- 36/36 prefix audit records pass; execution timestamps are later than decisions; aggregate leakage check passes.
- Snapshot counts: 36 strategy scope records, 10 audit-visible ensemble records, one learning run, 36 causal audits. Learning ledger contains exactly two candidate rows.
- Catalog: 15 strategies evaluated on at least one compatible synthetic scope; four context-incompatible strategies explicitly unavailable. No generated performance is presented as market evidence.

Live diagnostic:

- 24,608 rows; duplicate logical bars 0; invalid OHLCV 0; revisions 0; UTC/finalization/cutoff valid.
- Six coverage requests contain gaps (83/27/6 per pair at 5m/15m/1h); both 4h first chunks have zero missing expected bars.
- Latest close `2017-09-16T04:00:00Z`, far before the `2026-08-24T00:00:00Z` cutoff because the remaining range is unattempted.
- 24,600 returns: mean `0.0000024863067231195445`, standard deviation `0.009935164887754392`, min `-0.17488742922952316`, max `0.31613809908626433`, robust outliers 356.
- Volume min/median/max: `0.0` / `6.273374080657959` / `4298.8798828125`.
- Prefix audits 0 and leakage aggregate false because no complete provider scope was eligible for evaluation. This is a failed sufficiency/coverage gate, not success.

Downstream analytical risk is stated in the summary: duplicates can double-count evidence; invalid OHLCV can create impossible fills; gaps can distort indicators/folds; late or revised timestamps can leak future information; a stale cutoff can make signals look current; outliers can dominate risk metrics; and insufficient samples can make promotion statistics meaningless. Any affected provider scope remains unavailable.

## Deterministic rerun hashes

Both CI and live profiles were rerun into fresh databases after final source/config formatting. Output summaries were byte-identical across reruns.

| Artifact / identity | Hash |
|---|---|
| Code | `a0eefafbde08445ba5ceb9432f41a8a018c79707c49a293add3eb5bdb0b5cb09` |
| CI config | `bdd5db762ac98ad464c552409781a71a944928083061cdc6979240e5bec324cc` |
| CI aggregate dataset | `f3b7a8131155c59abe7b7c6e03b66af5252e0304f8649f12aa5fa32978b1e34c` |
| CI semantic snapshot | `e9f0e33867b833d6d769272e7ba9ce1a9bf511133d8294323fa7942a672af18d` |
| CI summary file | `33d351ed195ee5f901d60be5543fbc266553812c5d0924653ba1e6d099e35b92` |
| CI snapshot file | `f73ebad40e812d2de82a41f530ba910f2e844fe526e5b6c4c952ca8cf4cbfaf8` |
| Live config | `2775d97ed7934398ce2086d2c5f71e760263d2121c635328470c30565cfeada0` |
| Live aggregate dataset | `a72666c903bc5456b73ab97c376fb807968d1ac4f5471bc12c9994c56d18da24` |
| Live semantic snapshot | `a3945aea2f3937b527bc09cdfccdc121137e28848bd8b2e1c80f1584a2790c76` |
| External cache manifest | `a9402b24e12f1c0507c459bc3f21f3135910fa691edb376532fe9ea5721e95b7` |
| Live summary file, both reruns | `4b3652cde32f0dd3367ec96033041a507102b67f0c8de5f5c34beafd2faf36ec` |

## Documentation and primary sources

The docs link directly to these original/official sources and use them only to define provider behavior, motivate hypotheses/validation, or disclose risk—not to claim profitability:

- Binance official Spot API docs: https://github.com/binance/binance-spot-api-docs
- Binance official public-data/archive tooling: https://github.com/binance/binance-public-data
- Alpaca official Historical Stock Data docs: https://docs.alpaca.markets/docs/historical-stock-data-1
- Bailey and López de Prado, Deflated Sharpe Ratio: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey et al., Probability of Backtest Overfitting: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Moskowitz, Ooi, and Pedersen, Time Series Momentum: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463
- Gatev, Goetzmann, and Rouwenhorst, pairs trading: https://www.nber.org/papers/w7032
- SEC Investor.gov day-trading risk: https://www.investor.gov/introduction-investing/investing-basics/glossary/day-trading

The official Binance, Alpaca, and Investor.gov pages were opened successfully during final verification. The SSRN/NBER exact primary landing-page links were retained; the browsing extractor returned an internal rendering error for those pages, so no unverified quotation was added.

## Test, lint, build, and artifact commands

- `.venv/bin/pytest tests/integration/test_full_strategy_research.py -v`: initial red as described above; final `2 passed in 53.27s`.
- `.venv/bin/pytest tests/integration/test_full_strategy_research.py::test_live_research_attempts_every_enabled_crypto_interval_from_external_cache -v`: red `1 failed in 2.40s`, then green `1 passed in 2.11s`.
- `.venv/bin/pytest tests/unit/test_bar_ingestion.py`: `16 passed in 0.22s`.
- An earlier ad-hoc Task 1–8 strategy/learning selection reported `132 passed in 76.72s`; its exact argv was not retained in shell history, so completion relies on the exact final full-suite command below rather than that intermediate run.
- `.venv/bin/pytest -q`: final `468 passed in 281.10s (0:04:41)`.
- `.venv/bin/ruff format .`: initially reformatted 19 pre-existing files; `.venv/bin/ruff check --fix .`: fixed three import/order findings.
- `.venv/bin/ruff format tests/integration/test_full_strategy_research.py src/research/full_history.py`: one file reformatted; `.venv/bin/ruff check --fix tests/integration/test_full_strategy_research.py src/research/full_history.py`: all checks passed.
- `.venv/bin/ruff format --check . && .venv/bin/ruff check . && git diff --check`: first detected only the restored SDD plan; after excluding execution metadata in `pyproject.toml`, final result `177 files already formatted`, `All checks passed`, diff check exit 0.
- `swift test --package-path macos/Nowcaster && swift build -c release --package-path macos/Nowcaster`: 1 XCTest plus 52 Swift Testing tests passed; release build completed with exit 0.
- `make macos-app && codesign --verify --deep --strict build/Nowcaster.app && plutil -lint build/Nowcaster.app/Contents/Info.plist`: app assembled, strict signature verification exited 0, plist `OK`.
- `make clean-generated && make demo && make sync-macos-snapshot`: all 12 deterministic demo stages completed. The resulting native fixture would remove pre-existing intraday strategy records, so that generated change was not retained; the fixture was restored exactly to `HEAD` and native tests subsequently passed.
- `make research-ci`: completed; a second fresh-database run reproduced CI summary SHA-256 `33d351ed195ee5f901d60be5543fbc266553812c5d0924653ba1e6d099e35b92` and snapshot SHA-256 `f73ebad40e812d2de82a41f530ba910f2e844fe526e5b6c4c952ca8cf4cbfaf8`.
- Final live command shown above, plus an identical invocation using `build/research-live-final-rerun.duckdb`: both completed with explicit `unavailable` status and summary SHA-256 `4b3652cde32f0dd3367ec96033041a507102b67f0c8de5f5c34beafd2faf36ec`; the second run used only verified cache pages.
- `make verify-research-fixtures`: regenerated CI research, validated snapshot schema v2, and `git diff --exit-code -- data/research/ci` exited 0 against the staged artifacts.
- `make secret-scan`: printed `Tracked-file secret scan passed` across every tracked/staged text file.
- `git diff --check`: passed.

## Dead-code audit

No daily-crypto code was deleted. `rg` found active production imports in `src/demo.py` (`src.crypto.features`, `src.crypto.models`, `src.crypto.pipeline`), daily backtest/snapshot behavior, unit callers, integration callers, and matching native fixture state. Removing it would break the shipped deterministic earnings/daily-crypto demo and is therefore not proven-dead. This is the conservative outcome required by Task 9.

## Git status

Before staging, only the Task 9 product files and the explicitly documented Ruff-normalization files above were modified/untracked. After `git add -A` plus the required forced add of this ignored SDD report, `git status --short` showed every listed product/normalization path staged and no unstaged or non-ignored untracked path. `docs/superpowers/plans/2026-08-22-intraday-strategy-learning.md` and the native app fixture have no diff. Ignored outputs include `data/research/live/nowcaster-snapshot.json`, `data/app/`, DuckDBs under `build/`, report/demo outputs, `__pycache__`, and `/tmp/nowcaster-binance-cache`; none is staged.

## Self-review

- Verified every enabled strategy appears exactly once with evaluated/rejected/unavailable/failed status and a reason where required.
- Verified the compact ensemble contains no unavailable/failed strategy and all published weights are nonnegative; policy caps/shrinkage match configuration.
- Verified point-in-time, execution-lag, prefix-invariance, final-boundary, learning-ledger, cost, and snapshot v2 paths through tests and published evidence.
- Verified live 4h dataset ingestion is not skipped merely because the only 4h strategy is context-incompatible.
- Verified hashes were generated from final source/config contents and repeated into fresh databases.
- Verified primary-source links and cautious language; no paper citation is presented as evidence of expected profit.
- Verified no credential values, bulk bars, databases, raw page cache, ignored live snapshot, or Python cache files are eligible for the commit.
- Verified the SDD plan has no checkbox or formatting diff and the native app fixture remains unchanged.

## Concerns / remaining work

1. The official Binance attempt is intentionally diagnostic, not the required exhaustive provider run: only the earliest 30 days per symbol/interval were attempted, and the remainder through the fixed cutoff is explicitly unavailable. The implementation supports unbounded resumable completion, but completing roughly nine years at four intervals still requires provider time/capacity and a durable external cache path.
2. The `/tmp/nowcaster-binance-cache` used for this run is external and may be ephemeral. Its exact compact checksums/manifests are committed, but the raw payloads must be retained or redownloaded outside Git for long-term byte-level replay.
3. Alpaca equity research is unavailable until the user supplies a usable local credential pair and confirms feed entitlement; no credential value was inspected or stored.
4. No provider-backed strategy or learning result was produced because full coverage gates did not pass. The CI fixture is only software evidence.
5. The pre-existing daily-crypto path remains because it has live callers. A future removal needs a separately scoped migration, snapshot equivalence tests, and deletion only after those callers disappear.
