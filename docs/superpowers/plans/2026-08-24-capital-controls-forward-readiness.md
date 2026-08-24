# Capital Controls and Forward Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed pre-trade capital controls, emergency operations, immutable forward-evidence cohorts, expiring readiness receipts, and a native Execution Center.

**Architecture:** A pure risk engine evaluates immutable snapshots immediately before broker submission. A forward-evidence service aggregates only frozen paper cohorts and issues short-lived receipts when every statistical and operational gate passes. Snapshot schema v3 transports bounded execution/readiness projections to SwiftUI without secrets or executable broker commands.

**Tech Stack:** Python/Pydantic/SQLAlchemy/DuckDB, existing causal validation and robustness modules, Swift 6/SwiftUI, Swift Testing/XCTest.

**Spec:** `docs/superpowers/specs/2026-08-24-broker-safe-trading-design.md`

## Global Constraints

- Missing, stale, non-finite, contradictory, or unauthenticated risk input rejects the order.
- Broker acceptance never overrides local rejection; risk decisions are persisted before any submission.
- Frozen cohort identity includes provider, feed, symbol, interval, strategy/version, parameters, weights, dataset, code, config, and risk/cost-policy hashes.
- Paper fills are simulated evidence and may authorize only a locked/capped pilot receipt, never unrestricted capital.
- Any cohort mutation resets its evidence clock and trade count.
- Snapshot payloads contain no credentials, raw broker payloads, full account IDs, or executable arm token.
- UI status and emergency state are understandable without color.

---

### Task 1: Typed risk policy and pure pre-trade decision engine

**Files:**
- Create: `src/trading/risk.py`
- Modify: `src/config/settings.py`
- Modify: `config/trading.yaml`
- Test: `tests/unit/test_trading_risk.py`

**Interfaces:**
- Produces: `RiskPolicy`, `RiskContext`, `RiskDecision`, `RiskRejection`, `PreTradeRiskEngine.evaluate(intent, context)`.
- Consumes: paper execution DTOs and exact decision/cohort provenance.

- [ ] **Step 1: Write failing table-driven tests** for every admission gate at pass, reject, exact boundary, stale, missing, NaN, infinity, wrong environment/account/cohort/feed, duplicate/conflicting order, shortability, buying power, position/gross exposure, turnover, order rate, spread, price collar, daily loss, drawdown, and global freeze.

```python
@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"data_age_seconds": 31}, "market_data_stale"),
        ({"unresolved_mismatches": 1}, "reconciliation_unresolved"),
        ({"daily_pnl": Decimal("-25.01")}, "daily_loss_limit"),
        ({"provider": "binance"}, "evidence_venue_mismatch"),
    ],
)
def test_risk_engine_fails_closed(mutation, reason):
    decision = PreTradeRiskEngine().evaluate(_intent(), _context(**mutation))
    assert decision.allowed is False
    assert reason in decision.reasons
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_trading_risk.py`

- [ ] **Step 3: Implement immutable policy/context models.** Derive utilization with Decimal arithmetic; reject before comparison when any required value is missing/non-finite; sort unique rejection codes deterministically.

- [ ] **Step 4: Implement pure evaluation** with one named predicate per design gate and marketable-limit collar enforcement. No broker/network/database dependency is allowed in this module.

- [ ] **Step 5: Run focused tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_trading_risk.py
git add src/trading/risk.py src/config/settings.py config/trading.yaml tests/unit/test_trading_risk.py
git commit -m "feat: enforce fail-closed pre-trade risk"
```

### Task 2: Persist risk decisions and gate supervisor submission

**Files:**
- Modify: `src/trading/repository.py`
- Modify: `src/trading/supervisor.py`
- Test: `tests/integration/test_pretrade_admission.py`

**Interfaces:**
- Consumes: Task 1 risk engine and paper supervisor.
- Produces: durable risk decision linked one-to-one with submitted/rejected intent.

- [ ] **Step 1: Write failing integration tests** proving a rejection is persisted and never calls the broker, an admission is persisted before broker POST, broker state is refreshed immediately before evaluation, concurrent intents serialize exposure accounting, and a post-reconciliation mismatch blocks the next intent.

```python
def test_rejected_intent_is_audited_without_broker_side_effect(database):
    broker = RecordingBroker()
    outcome = _supervisor(database, broker).submit_intent(_intent(), _stale_context())
    assert outcome.status == "risk_rejected"
    assert broker.submit_calls == 0
    assert database.scalar("select count(*) from risk_decisions where allowed = false") == 1
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/integration/test_pretrade_admission.py`

- [ ] **Step 3: Add transactional persistence and supervisor gate.** The repository records canonical context/policy/decision hashes and bounded reason codes; it never persists credentials or unbounded broker objects.

- [ ] **Step 4: Run risk, supervisor, and execution regressions; commit.**

```bash
.venv/bin/pytest -q tests/unit/test_trading_risk.py tests/integration/test_pretrade_admission.py tests/integration/test_trading_supervisor.py tests/unit/test_execution_engine.py
git add src/trading/repository.py src/trading/supervisor.py tests/integration/test_pretrade_admission.py
git commit -m "feat: gate broker effects on audited risk"
```

### Task 3: Freeze and separately confirmed flatten workflows

**Files:**
- Create: `src/trading/emergency.py`
- Modify: `src/trading/supervisor.py`
- Modify: `src/cli.py`
- Test: `tests/integration/test_trading_emergency.py`
- Modify: `tests/integration/test_trading_cli.py`

**Interfaces:**
- Produces: `EmergencyController.freeze(reason)`, `flatten(confirmation)`, and persisted `EmergencyOutcome`.
- Consumes: broker cancellation/position/close operations and reconciliation.

- [ ] **Step 1: Write failing tests** for freeze idempotency, cancel races, no-new-order behavior, flatten confirmation mismatch, partial close, close rejection, network ambiguity, timeout, restart during flatten, and success only after zero-position reconciliation.

```python
def test_flatten_never_reports_success_from_submission_acceptance(database):
    broker = BrokerWithUnchangedPositionAfterClose()
    outcome = _controller(database, broker).flatten(_valid_confirmation())
    assert outcome.status == "unresolved"
    assert outcome.remaining_positions == 1
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/integration/test_trading_emergency.py`

- [ ] **Step 3: Implement freeze first, then flatten.** Freeze blocks admission and cancels entries. Flatten requires exact account suffix plus phrase, creates only close effects, and loops through bounded reconciliation attempts using an injected clock/sleep.

- [ ] **Step 4: Expose `trading flatten` with interactive or explicit confirmation** while keeping it unavailable from unattended default commands.

- [ ] **Step 5: Run tests and commit.**

```bash
.venv/bin/pytest -q tests/integration/test_trading_emergency.py tests/integration/test_trading_cli.py
git add src/trading/emergency.py src/trading/supervisor.py src/cli.py tests/integration/test_trading_emergency.py tests/integration/test_trading_cli.py
git commit -m "feat: add reconciled freeze and flatten controls"
```

### Task 4: Immutable forward cohorts and daily evidence

**Files:**
- Create: `src/trading/forward.py`
- Modify: `src/trading/repository.py`
- Test: `tests/unit/test_forward_evidence.py`
- Test: `tests/integration/test_forward_evidence_store.py`

**Interfaces:**
- Produces: `ForwardCohortIdentity`, `ForwardDailyEvidence`, `ForwardEvidenceBuilder.close_period`.
- Consumes: strategy/ensemble causal receipts, broker events, reconciliations, risk decisions, and configured live-cost model.

- [ ] **Step 1: Write failing identity tests** proving each provider/feed/symbol/interval/strategy/version/parameter/weight/dataset/code/config/risk/cost mutation changes the cohort hash while receipt-only timestamps do not.

- [ ] **Step 2: Write failing daily-evidence tests** for closed-trade counting, observed paper fill calculations, separate stressed live costs, session/calendar-day boundaries, drawdown, rejection/health counts, and unavailable status on incomplete reconciliation.

```python
def test_feed_change_resets_forward_cohort() -> None:
    assert _identity(feed="iex").cohort_hash != _identity(feed="sip").cohort_hash
```

- [ ] **Step 3: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_forward_evidence.py tests/integration/test_forward_evidence_store.py`

- [ ] **Step 4: Implement exact cohort hashing and period closure.** Never mutate a closed period; a duplicate closure must byte-match or fail. Paper cost and stressed cost are stored as distinct fields.

- [ ] **Step 5: Run tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_forward_evidence.py tests/integration/test_forward_evidence_store.py
git add src/trading/forward.py src/trading/repository.py tests/unit/test_forward_evidence.py tests/integration/test_forward_evidence_store.py
git commit -m "feat: seal frozen forward trading evidence"
```

### Task 5: Readiness evaluation and expiring receipts

**Files:**
- Create: `src/trading/readiness.py`
- Modify: `src/trading/repository.py`
- Test: `tests/unit/test_live_readiness.py`
- Test: `tests/integration/test_readiness_receipts.py`

**Interfaces:**
- Produces: `ReadinessGate`, `ReadinessEvaluation`, `ReadinessReceipt`, `ReadinessEvaluator.evaluate(cohort, as_of)`.
- Consumes: Task 4 evidence and existing robustness/causal contracts.

- [ ] **Step 1: Write failing gate tests** for 59/60/61 equity sessions, 89/90/91 crypto days, 99/100/101 trades, unresolved mismatch, mutation, causal/data failure, net/stressed edge, doubled costs, drawdown, bootstrap/DSR/PBO/stability/neighborhood, slippage-model error, operational termination, and receipt expiry/invalidation.

```python
def test_equity_readiness_requires_both_60_sessions_and_100_closed_trades() -> None:
    assert _evaluate(sessions=60, trades=99).status == "locked"
    assert _evaluate(sessions=59, trades=100).status == "locked"
    assert _evaluate(sessions=60, trades=100).gate("minimum_forward_observations").passed
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_live_readiness.py tests/integration/test_readiness_receipts.py`

- [ ] **Step 3: Implement fail-closed evaluation.** Reuse authenticated robustness evidence only when its exact cohort/fold/policy hashes match; do not recompute optimistic substitutes from summary metrics.

- [ ] **Step 4: Seal 24-hour receipts** containing all gate results and evidence hashes. Repository lookup returns invalid after expiry or any later breaker/mismatch for the cohort.

- [ ] **Step 5: Run tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_live_readiness.py tests/integration/test_readiness_receipts.py tests/unit/test_strategy_validation.py
git add src/trading/readiness.py src/trading/repository.py tests/unit/test_live_readiness.py tests/integration/test_readiness_receipts.py
git commit -m "feat: issue fail-closed readiness receipts"
```

### Task 6: Snapshot schema v3 execution/readiness projection

**Files:**
- Modify: `src/app_snapshot/models.py`
- Modify: `src/app_snapshot/builder.py`
- Modify: `src/demo.py`
- Modify: `scripts/synchronize_snapshot_fixture.py`
- Modify: `scripts/verify_snapshot_fixture_parity.py`
- Modify: `tests/integration/test_app_snapshot_export.py`
- Modify: `tests/unit/test_app_snapshot.py`

**Interfaces:**
- Produces bounded snapshot sections `broker_status`, `broker_positions`, `broker_orders`, `broker_events`, `risk_status`, `forward_readiness`, and `emergency_status` under schema version 3.
- Consumes: Tasks 1–5 projections.

- [ ] **Step 1: Write failing snapshot tests** proving bounded rows, suffix-only account identity, no raw payload/secret/arm token, explicit UTC, finite values, stable sort, locked defaults, and incompatibility of malformed execution state.

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py`

- [ ] **Step 3: Add strict v3 models and builder projections.** Cap positions/orders/events/gates to documented limits and include truncation counts. Default fixture state is `research`/`live_locked` with no account.

- [ ] **Step 4: Update synchronization/parity to compare the new sections read-only.** A mutated readiness gate or emergency state must fail parity.

- [ ] **Step 5: Run Python snapshot/parity tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py tests/unit/test_snapshot_fixture_parity.py
git add src/app_snapshot src/demo.py scripts tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py
git commit -m "feat: export bounded execution readiness state"
```

### Task 7: Swift schema v3 and Execution Center presentation contracts

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppDestination.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/RootView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/ExecutionCenter/ExecutionCenterView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/ExecutionCenter/ExecutionPresentation.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/ExecutionCenterTests.swift`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`

**Interfaces:**
- Produces native decoding and read-only presentation for all schema-v3 sections.
- Consumes: Task 6 fixture.

- [ ] **Step 1: Write failing decoder tests** for complete v3, malformed/oversized execution sections, literal-Z timestamps, unknown statuses, and secret-shaped forbidden fields.

- [ ] **Step 2: Write failing presentation tests** for Research, Shadow, Paper, Live Locked, and Armed language; partial-fill versus filled; stale/reconciliation breakers; risk utilization; readiness reasons; and non-color accessibility descriptions.

```swift
@Test func liveLockedNeverUsesReadyOrRecommendationCopy() throws {
    let presentation = ExecutionPresentation(snapshot: .liveLockedFixture)
    #expect(presentation.stateTitle == "Live Locked")
    #expect(!presentation.summary.localizedCaseInsensitiveContains("recommended"))
}
```

- [ ] **Step 3: Run and verify RED.**

Run: `swift test --package-path macos/Nowcaster --filter ExecutionCenterTests`

- [ ] **Step 4: Implement strict models and presentation.** Follow existing semantic colors, materials, spacing, SF Symbols, tables, and accessibility patterns. Add Execution Center under System.

- [ ] **Step 5: Run complete Swift tests and commit.**

```bash
swift test --package-path macos/Nowcaster
git add macos/Nowcaster
git commit -m "feat: add native execution center"
```

### Task 8: Native paper controls, emergency confirmation, and visual verification

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/ExecutionCenter/ExecutionCenterView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/ExecutionCenter/FlattenConfirmationView.swift`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/AppModelTests.swift`
- Modify: `scripts/capture_macos_app.swift`

**Interfaces:**
- Produces typed paper start/stop/reconcile/freeze/flatten jobs; no live-arm job in this plan.
- Consumes: Python CLI from paper plan and Task 7 view.

- [ ] **Step 1: Write failing invocation tests** proving typed arguments, no shell, no secret arguments, no live environment, and separate flatten confirmation containing account suffix plus exact phrase.

- [ ] **Step 2: Write failing model tests** proving failures remain durable, freeze disables new actions, flatten acceptance is not presented as completion, and refresh reloads the configured snapshot.

- [ ] **Step 3: Run and verify RED.**

Run: `swift test --package-path macos/Nowcaster --filter 'EngineRunnerTests|AppModelTests|ExecutionCenterTests'`

- [ ] **Step 4: Implement controls and confirmation sheet.** Default focus/cancel behavior must prevent accidental destructive confirmation; all controls have stable accessibility labels and keyboard access.

- [ ] **Step 5: Capture real native light/dark wide/narrow Execution Center screenshots** and visually inspect state, clipping, spacing, focus, destructive affordance, and non-color status.

- [ ] **Step 6: Run UI/release verification and commit.**

```bash
swift test --package-path macos/Nowcaster
swift build -c release --package-path macos/Nowcaster
make macos-app macos-screenshots macos-ui-test
git add macos/Nowcaster scripts/capture_macos_app.swift docs/images/macos
git commit -m "feat: operate paper safety controls natively"
```

### Task 9: Readiness documentation and plan-wide verification

**Files:**
- Modify: `README.md`
- Create: `docs/live-readiness.md`
- Modify: `docs/paper-trading-operations.md`
- Modify: `docs/native_verification.md`
- Modify: `Makefile`

**Interfaces:**
- Produces `make verify-trading-readiness` and beginner documentation.
- Consumes: all plan tasks.

- [ ] **Step 1: Document every readiness gate and reset condition** with examples showing why 60 sessions without 100 trades, positive return without robustness, or perfect paper fills remain locked.

- [ ] **Step 2: Add verification target** covering risk, emergency, forward, readiness, snapshot parity, Swift execution tests, and secret scan.

- [ ] **Step 3: Run complete verification.**

Run: `.venv/bin/pytest -q`

Run: `.venv/bin/ruff format --check . && .venv/bin/ruff check . && git diff --check`

Run: `swift test --package-path macos/Nowcaster && swift build -c release --package-path macos/Nowcaster`

Run: `make verify-research-fixtures verify-swift-fixture-parity verify-paper-trading verify-trading-readiness secret-scan macos-app`

- [ ] **Step 4: Commit.**

```bash
git add README.md docs Makefile
git commit -m "docs: define forward live-readiness gates"
```
