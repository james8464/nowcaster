# Experimental Opportunity Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface finalized-data, simulated entry/stop/target/expiry plans as explicitly experimental research opportunities without weakening any qualified-alert or trading guardrail.

**Architecture:** The Python live-monitor engine will emit a distinct `experimental_opportunity` event only after finalized bars, current directional evidence, healthy/fresh market data, and feasible deterministic levels exist. It will preserve every reason that prevents qualification, write no setup or lifecycle state, send no notification, and make no broker call. The SwiftUI monitor will render those records in their own section with an unambiguous paper-only label and the same hypothetical levels.

**Tech Stack:** Python 3.13, Pydantic, pytest; Swift 6, SwiftUI, Swift Testing.

**Spec:** User-approved bounded design from the 18 September 2026 conversation: an Experimental Opportunity Feed with hypothetical entry, stop, targets, expiry and recorded outcome context; it must never claim qualification, profitability, or execute orders.

## Global Constraints

- Use only finalized market bars and the existing deterministic trade-level planner.
- Never relax `evaluate_alert_eligibility`, promotion, calibration, no-repaint, shortability, readiness, notification, or broker-order gates.
- Emit no experimental item when data is stale/unhealthy, a direction is absent, a short is structurally unavailable, or levels are infeasible.
- Persist the decision audit record only; do not create a tracked setup, lifecycle transition, notification request, or order from an experimental item.
- Mark every UI element and wire payload as `experimental_paper_only`.

---

### Task 1: Model the experimental engine output

**Files:**
- Modify: `src/live_monitor/types.py`
- Modify: `src/live_monitor/engine.py`
- Test: `tests/unit/test_live_monitor_engine.py`

**Interfaces:**
- Produces `experimental_opportunity` wire events with the existing `TradePlan` geometry plus `experimental_paper_only: true`, `qualification_reasons`, and `qualification_status: "unqualified"`.
- Consumes `EligibilityEvidence`, `MarketQuote`, and finalized decision bars already passed to `LiveMonitorEngine._decide`.

- [ ] **Step 1: Write failing engine tests**

```python
def test_unqualified_fresh_directional_evidence_emits_paper_only_opportunity() -> None:
    events = engine_with(evidence(promoted=False)).accept_market_event(finalized_bar)
    opportunity = next(event for event in events if event.event_type == "experimental_opportunity")
    assert opportunity.payload["experimental_paper_only"] is True
    assert opportunity.payload["qualification_status"] == "unqualified"
    assert "promotion_required" in opportunity.payload["qualification_reasons"]
```

- [ ] **Step 2: Run the test and confirm it fails because no experimental event exists**

Run: `pytest tests/unit/test_live_monitor_engine.py -q`

- [ ] **Step 3: Add the minimal event type and engine helper**

```python
def _experimental_opportunity(...):
    return self.emit("experimental_opportunity", {
        **plan.model_dump(mode="json"),
        "experimental_paper_only": True,
        "qualification_status": "unqualified",
        "qualification_reasons": list(decision.reasons),
    }, emitted_at=now)
```

- [ ] **Step 4: Add negative tests for stale/unhealthy/no-direction/unsupported-short/infeasible-level states**

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest tests/unit/test_live_monitor_engine.py tests/unit/test_live_monitor_levels.py -q`

### Task 2: Decode and present experimental opportunities in macOS

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/LiveMonitorModels.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Services/LiveMonitorService.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/LiveMonitor/LiveMonitorView.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/LiveMonitorModelsTests.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/LiveMonitorPresentationTests.swift`

**Interfaces:**
- Consumes `experimental_opportunity` events emitted in Task 1.
- Produces `experimentalOpportunities: [ExperimentalOpportunity]` as a view-only, immutable projection.

- [ ] **Step 1: Write failing Swift tests for event decoding and paper-only presentation**

```swift
@Test func experimentalOpportunityRequiresPaperOnlyMarker() throws {
    #expect(ExperimentalOpportunity(payload: payload, updatedAt: .now)?.isPaperOnly == true)
}
```

- [ ] **Step 2: Run the native test and confirm it fails because the model is absent**

Run: `swift test --filter LiveMonitorModelsTests`

- [ ] **Step 3: Add a strict `ExperimentalOpportunity` parser and service projection**

- [ ] **Step 4: Render a dedicated “Experimental opportunities” section**

Use a `ContentUnavailableView` explanation when no items exist. Rows show asset, long/short research posture, entry range, protective stop, targets, expiry, and the concise reasons it is not qualified. Do not provide a trade or notification action.

- [ ] **Step 5: Run native focused tests and commit**

Run: `swift test --filter LiveMonitor`

### Task 3: Document the boundary and verify release behavior

**Files:**
- Modify: `README.md`
- Create: `docs/research/experimental-opportunity-feed-2026-09-18.md`

- [ ] **Step 1: Document eligibility, output, non-repaint, and non-trading boundaries**

- [ ] **Step 2: Run Python and Swift focused suites**

Run: `pytest tests/unit/test_live_monitor_engine.py tests/unit/test_live_monitor_levels.py tests/integration/test_live_monitor_cli.py -q && swift test`

- [ ] **Step 3: Build the macOS app and verify the bundle starts locally**

Run: `make macos-app && codesign --verify --deep --strict build/Nowcaster.app`

- [ ] **Step 4: Commit, merge the isolated branch, and push `main`**

