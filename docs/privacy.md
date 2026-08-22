# Privacy and security

Nowcaster is local-first. The macOS app has no user account, advertising SDK, telemetry, analytics beacon, cloud database, brokerage connection, or embedded web runtime.

## Data stored locally

- A generated research snapshot at `data/app/nowcaster-snapshot.json`.
- The last-known-good decoded snapshot in application memory.
- Local Python executable, repository, and snapshot paths in `UserDefaults`.
- Generated DuckDB, logs, reports, app bundles, and screenshots under ignored build/data paths.

The app does not persist API keys, exchange keys, brokerage credentials, positions, or orders. `.env` is ignored by Git. Snapshot models do not define secret-bearing fields.

## Network behavior

The native application itself reads local files and launches the local research engine. Demo mode operates from bundled source snapshots. Optional live Python adapters may contact the explicitly configured public providers; provider terms and privacy policies then apply. No live mode runs silently.

## Process safety

The app invokes a fixed executable with a structured argument array and never routes configured paths through a shell. It surfaces non-zero exits, supports cancellation, and preserves the last-known-good snapshot after failures. Release artifacts publish SHA-256 checksums.

## Production considerations

Anyone adding brokerage or licensed market-data integrations should use Keychain-backed credentials, least-privilege entitlements, explicit consent, data-retention controls, audit logging, authenticated services, and an independent security review. None of those integrations are present here.
