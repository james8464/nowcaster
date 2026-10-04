# Copy-readiness integrity

The diagnostic simulator remains paper-only. A real-money readiness receipt must not be issued from repeated, overlapping, future, incomplete, or stale forward periods. The existing policy thresholds remain unchanged; this change validates that the evidence counted toward those thresholds is chronological and complete.

For a receipt, every forward period must match the frozen cohort; begin before it ends; end no later than evaluation time; be closed between its end and evaluation time; have non-overlapping boundaries and a unique period start; contain finite paper and stressed returns and drawdown; and the latest period must end within the receipt lifetime. A receipt expires no later than 24 hours after the latest period ended. Crypto observations must cover consecutive UTC calendar days. Equity observations may skip closed-market days, so they require non-overlap and freshness but not calendar-day continuity. Invalid observations fail a dedicated gate before any receipt can be issued. Missing returns never become zero-valued favorable evidence.

The current prospective BTC/ETH study remains isolated and unchanged. Its failed historical screen, losses, and retained interruptions disqualify it from a real-money copy-readiness claim, regardless of this code change. Paper fills do not establish executable fills.

## Verification

Add red/green unit cases for duplicate and overlapping periods, future and stale evidence, missing and non-finite returns, and crypto day gaps. Run targeted tests and the existing readiness integration tests. Then run the relevant full tests and push only after verification.
