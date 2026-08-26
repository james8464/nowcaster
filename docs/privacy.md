# Privacy and security

Nowcaster is local-first. The macOS app has no user account, advertising SDK, telemetry, analytics beacon, cloud database, or embedded web runtime. Optional Alpaca paper trading and notification-only live market monitoring are explicit and operator initiated.

## Data stored locally

- A generated research snapshot at `data/app/nowcaster-snapshot.json`.
- The last-known-good decoded snapshot in application memory.
- Local Python executable, repository, and snapshot paths in `UserDefaults`.
- Generated DuckDB, logs, reports, app bundles, and screenshots under ignored build/data paths.
- Separate Alpaca paper/live credentials in macOS Keychain generic-password items. Values are never stored in `UserDefaults`.
- Bounded broker order, position, reconciliation, risk, and forward-readiness evidence in local DuckDB/snapshots. Full account IDs and raw broker payloads are excluded from the native snapshot.
- Append-only Deep Research trials, aggregate fold/stress evidence, checkpoints, and resource samples in local DuckDB. Private control files are mode `0600` inside a mode `0700` local directory and are not exported.
- Live Monitor sessions, finalized-bar identities, hypothetical plans, lifecycle transitions, and notification receipts in local DuckDB. System notification text contains no credentials or account identifiers.

The app persists broker credentials only in Keychain using device-only after-first-unlock accessibility. `.env` is ignored by Git. Snapshot models do not define secret-bearing fields, executable arm tokens, or full account IDs.

## Network behavior

The native application itself reads local files and launches the local research engine. Demo mode operates from bundled source snapshots. When the user starts Live Monitor, its Python helper opens fixed WebSocket connections to Alpaca market data and/or Binance Spot. Provider terms and privacy policies then apply. No live mode runs silently, and monitoring ends when its visible app process ends.

## Process safety

The app invokes a fixed executable with a structured argument array and never routes configured paths through a shell. Live Monitor credentials use a single private stdin bootstrap; Deep Research control values use their bounded private channel. Secrets never enter command arguments, preferences, snapshots, notifications, or Git. The app drains child diagnostics, surfaces only a safe exit status, supports cancellation, and preserves the last-known-good snapshot after failures. Release artifacts publish SHA-256 checksums.

## Production considerations

A real-money pilot must remain locked until forward evidence, a production-signed bundled engine, hardened runtime, notarization, stapling, broker/account matching, a current readiness receipt, and a short-lived manual arm all pass. Independent security review is still an external prerequisite.
