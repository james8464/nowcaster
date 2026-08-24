# Privacy and security

Nowcaster is local-first. The macOS app has no user account, advertising SDK, telemetry, analytics beacon, cloud database, or embedded web runtime. An optional Alpaca paper connection is explicit and operator initiated.

## Data stored locally

- A generated research snapshot at `data/app/nowcaster-snapshot.json`.
- The last-known-good decoded snapshot in application memory.
- Local Python executable, repository, and snapshot paths in `UserDefaults`.
- Generated DuckDB, logs, reports, app bundles, and screenshots under ignored build/data paths.
- Separate Alpaca paper/live credentials in macOS Keychain generic-password items. Values are never stored in `UserDefaults`.
- Bounded broker order, position, reconciliation, risk, and forward-readiness evidence in local DuckDB/snapshots. Full account IDs and raw broker payloads are excluded from the native snapshot.
- Append-only Deep Research trials, aggregate fold/stress evidence, checkpoints, and resource samples in local DuckDB. Private control files are mode `0600` inside a mode `0700` local directory and are not exported.

The app persists broker credentials only in Keychain using device-only after-first-unlock accessibility. `.env` is ignored by Git. Snapshot models do not define secret-bearing fields, executable arm tokens, or full account IDs.

## Network behavior

The native application itself reads local files and launches the local research engine. Demo mode operates from bundled source snapshots. Optional live Python adapters may contact the explicitly configured public providers; provider terms and privacy policies then apply. No live mode runs silently.

## Process safety

The app invokes a fixed executable with a structured argument array and never routes configured paths through a shell. Session credentials and the Deep Research control nonce are consumed through the child environment, never command arguments, and exact-redacted from diagnostics. It surfaces non-zero exits, supports cancellation, and preserves the last-known-good snapshot after failures. Release artifacts publish SHA-256 checksums.

## Production considerations

A real-money pilot must remain locked until forward evidence, a production-signed bundled engine, hardened runtime, notarization, stapling, broker/account matching, a current readiness receipt, and a short-lived manual arm all pass. Independent security review is still an external prerequisite.
