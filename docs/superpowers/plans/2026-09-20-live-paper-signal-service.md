# Live Paper Signal Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automatic, local, paper-only live signal service that gathers finalized public spot data, evaluates fresh causal research postures, and optionally notifies the user without creating executable trading advice.

**Architecture:** A new Python service owns feed ingestion, append-only signal events, material-change suppression, and round report publication. The existing macOS engine runner supervises it and shows an isolated Strategy Lab control/status/history surface; notification payloads are generated locally only after user opt-in. The service reads/writes only a separately registered Round 2 directory.

**Tech Stack:** Python 3.13, existing Research Round 2 contracts/quality/runtime, JSONL/fsync, Swift 6/SwiftUI, Swift Testing, pytest.

**Spec:** `docs/superpowers/specs/2026-09-20-live-paper-signal-service-design.md`

## Global Constraints

- Initial live source: public Binance spot BTCUSDT/ETHUSDT, long research or Stand aside only.
- No credentials, broker, order, qualified alert, lifecycle, or frozen-study modification.
- Use finalized observations with causal availability and append-only evidence; gaps/stale/errors abstain.
- Notifications require explicit user opt-in, material fresh posture, and contain paper-only wording only.
- Future broker adapter is a disabled interface with no executable implementation.

## Review Focus

- A reconnect must not publish until continuous warm-up has completed.
- A late/future/non-final observation must never yield a posture or notification.
- Same posture/evidence must be suppressed; materially changed fresh posture may publish once.
- Expired provider health/suggestion must display stale, never current.
- No service or notification type may reference broker/order/qualified-alert models.

### Task 1: Live signal contracts and append-only event ledger

**Files:** Create `src/research/live_paper_signals.py`; tests `tests/unit/test_live_paper_signals.py`.

**Interfaces:** `LiveSignalEvent`, `LiveSignalState`, `SignalEventLedger(directory)`, and `should_publish(previous, suggestion, now) -> bool`.

- [ ] **Step 1: Write failing event/state tests**
```python
def test_ledger_refuses_protocol_mismatch_and_preserves_events(tmp_path):
    ledger = SignalEventLedger(tmp_path, protocol_hash="a" * 64)
    ledger.append(LiveSignalEvent.started(now=UTC_NOW))
    assert ledger.events()[-1].kind == "started"
    with pytest.raises(ValueError, match="protocol"):
        SignalEventLedger(tmp_path, protocol_hash="b" * 64)

def test_material_change_and_expiry_control_publication():
    assert should_publish(None, fresh_long(), UTC_NOW)
    assert not should_publish(fresh_long(), fresh_long(), UTC_NOW)
    assert not should_publish(None, expired_long(), UTC_NOW)
```
- [ ] **Step 2: Run RED** `pytest tests/unit/test_live_paper_signals.py -q`
- [ ] **Step 3: Implement immutable events** with `extra="forbid"`, bounded fields, protocol-hash binding, fsync JSONL appends, and exact posture/candidate/levels/expiry comparison. Persist only research state and notification outcome; reject action-shaped values.
- [ ] **Step 4: Run GREEN** `pytest tests/unit/test_live_paper_signals.py -q`
- [ ] **Step 5: Commit** `git add src/research/live_paper_signals.py tests/unit/test_live_paper_signals.py && git commit -m "feat: add live paper signal ledger"`

### Task 2: Finalized public spot feed and causal service loop

**Files:** Create `src/research/live_paper_signal_runtime.py`, `scripts/run_live_paper_signals.py`; modify `src/research/__init__.py`; tests `tests/integration/test_live_paper_signal_runtime.py`.

**Interfaces:** `FinalizedSpotFeed`, `LivePaperSignalRunner.run_once(directory) -> LiveSignalState`, CLI `start`, `run-once`, `status`, `stop`.

- [ ] **Step 1: Write failing runner tests**
```python
def test_run_once_ingests_finalized_causal_bar_then_records_abstention(tmp_path, feed):
    state = LivePaperSignalRunner(feed).run_once(registered_round(tmp_path))
    assert state.kind in {"warming", "abstaining", "published"}
    assert all(item.available_at <= state.evaluated_at for item in load_observations(tmp_path))

def test_late_or_nonfinal_feed_data_creates_no_notification_event(tmp_path, feed):
    feed.rows = [nonfinal_bar(), late_bar()]
    state = LivePaperSignalRunner(feed).run_once(registered_round(tmp_path))
    assert state.kind == "abstaining"
    assert "notification" not in {event.kind for event in SignalEventLedger(tmp_path).events()}
```
- [ ] **Step 2: Run RED** `pytest tests/integration/test_live_paper_signal_runtime.py -q`
- [ ] **Step 3: Implement bounded runner**: feed adapter validates source/finality before `append_observations`; runner evaluates only after append, records provider/reconnect/gap outcome, publishes report + advisor only when fresh, and never opens network/account execution paths. `run_once` supports deterministic test feeds; `start` is a user-started polling loop with stop-file control.
- [ ] **Step 4: Run GREEN** `pytest tests/integration/test_live_paper_signal_runtime.py -q`
- [ ] **Step 5: Commit** `git add src/research/live_paper_signal_runtime.py scripts/run_live_paper_signals.py src/research/__init__.py tests/integration/test_live_paper_signal_runtime.py && git commit -m "feat: add live paper signal runner"`

### Task 3: Opt-in notification boundary and disabled broker interface

**Files:** Create `src/research/live_paper_notifications.py`, `src/research/broker_execution_adapter.py`; tests `tests/unit/test_live_paper_notifications.py`.

- [ ] **Step 1: Write failing notification/broker tests**
```python
def test_notification_is_opt_in_fresh_and_non_actionable():
    event = build_notification(fresh_long(), enabled=True, now=UTC_NOW)
    assert "Paper-only research posture" in event.body
    assert "buy" not in event.body.lower()
    assert build_notification(expired_long(), enabled=True, now=UTC_NOW) is None

def test_broker_boundary_is_disabled_without_execution_surface():
    with pytest.raises(RuntimeError, match="disabled"):
        DisabledBrokerExecutionAdapter().capabilities()
```
- [ ] **Step 2: Run RED** `pytest tests/unit/test_live_paper_notifications.py -q`
- [ ] **Step 3: Implement** bounded notification record/payload with no price/order/size language, cooldown key, and disabled broker protocol whose only public operation fails closed.
- [ ] **Step 4: Run GREEN** `pytest tests/unit/test_live_paper_notifications.py -q`
- [ ] **Step 5: Commit** `git add src/research/live_paper_notifications.py src/research/broker_execution_adapter.py tests/unit/test_live_paper_notifications.py && git commit -m "feat: add paper-only notification boundary"`

### Task 4: Strict macOS service models, supervision, and notifications

**Files:** Create `macos/Nowcaster/Sources/NowcasterApp/Models/LivePaperSignalModels.swift`, `Services/LivePaperSignalService.swift`; modify `AppModel.swift`, `Services/NotificationService.swift`; tests `macos/Nowcaster/Tests/NowcasterAppTests/LivePaperSignalModelsTests.swift`.

- [ ] **Step 1: Write failing strict-decoder tests**
```swift
@Test func rejectsActionFieldsAndExpiredPublishedState() throws {
    #expect(throws: SnapshotValidationError.self) { try decode(actionShapedServicePayload) }
    #expect(throws: SnapshotValidationError.self) { try decode(expiredPublishedPayload) }
}
```
- [ ] **Step 2: Run RED** `cd macos/Nowcaster && swift test --filter LivePaperSignalModelsTests`
- [ ] **Step 3: Implement** a separate process controller invoking only `run_live_paper_signals.py`, strict event/status decoding, user-controlled start/stop, notification authorization toggle, and local notification delivery only for an accepted fresh event. No `LiveMonitor`, `BrokerCredentials`, `ExecutionCenter`, or qualified-alert import.
- [ ] **Step 4: Run GREEN** `cd macos/Nowcaster && swift test --filter LivePaperSignalModelsTests`
- [ ] **Step 5: Commit** `git add macos/Nowcaster/Sources/NowcasterApp macos/Nowcaster/Tests/NowcasterAppTests/LivePaperSignalModelsTests.swift && git commit -m "feat: supervise live paper signals in macOS app"`

### Task 5: Strategy Lab control/status/history UI and documentation

**Files:** Create `Features/StrategyLab/LivePaperSignalsView.swift`; modify `StrategyLabView.swift`, `README.md`, `docs/research/live-paper-signals.md`; tests `tests/unit/test_live_paper_signal_docs.py` and native UI/model tests.

- [ ] **Step 1: Write failing documentation/UI assertions**
```python
def test_live_signal_docs_state_paper_only_and_no_profit_claim():
    text = Path("docs/research/live-paper-signals.md").read_text()
    assert "paper-only" in text and "not proof of profitability" in text
```
- [ ] **Step 2: Run RED** `pytest tests/unit/test_live_paper_signal_docs.py -q`
- [ ] **Step 3: Implement** HIG-consistent separate view with Start/Stop, source age/health, last evaluation, posture/Stand aside reasons, bounded history, notification toggle, and explicit research-only copy. No buttons for execution, broker, order, or qualified alert.
- [ ] **Step 4: Run GREEN** `pytest tests/unit/test_live_paper_signal_docs.py -q && cd macos/Nowcaster && swift test`
- [ ] **Step 5: Run final verification** `pytest -q && NOWCASTER_BUILD_PYTHON=/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python make macos-app && codesign --verify --deep --strict build/Nowcaster.app`
- [ ] **Step 6: Commit** `git add README.md docs/research/live-paper-signals.md macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab tests && git commit -m "docs: explain live paper signals"`

## Plan self-review

- Spec coverage: Task 1 records protocol-bound live evidence; Task 2 supplies causal public feed collection and service cadence; Task 3 isolates opt-in notification/future broker boundary; Task 4 provides strict native supervision; Task 5 supplies user controls, documentation, and full verification.
- Review focus: Tasks 1–4 each pin the listed stale, gap, duplicate, expiry, and action-boundary failures.
- No placeholders: all components, commands, test entry points, and failure behavior are named.
