# Live pilot operations

Before a pilot, verify the current receipt and cohort in Execution Center, confirm the broker dashboard independently, validate the production signature/notarization, and read the displayed maximum position, gross exposure, and daily loss. Enter the exact account suffix and loss acknowledgement to arm. The arm expires after at most 30 minutes and never survives an app/engine restart.

During a pilot, treat any stale data, reconciliation difference, unknown event, stream outage, unexpected rejection, or health breaker as an incident. Freeze first. Compare the local audit trail with the broker dashboard. Flatten only with separate confirmation, and do not call the incident resolved until the broker reports no positions and no open orders.

After an incident, preserve logs and the DuckDB audit database, rotate affected credentials in Keychain, invalidate readiness, document the broker-confirmed outcome, and require a new unchanged forward cohort where appropriate. Never increase a limit to work around a rejection.

Local/ad-hoc builds intentionally remain Live Locked. Production release credentials and independent security review are external conditions, not test fixtures.
