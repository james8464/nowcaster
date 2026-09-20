# Research Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, paper-only crypto research protocol that retains provider health and data quality, evaluates fixed walk-forward candidates without leakage, and exposes experimental research status plus an explainable Trend Advisor posture to the macOS app.

**Architecture:** New `src/research/round_two_*` modules own immutable round contracts, append-only evidence, data-quality segmentation, and sealed evaluation. A command-line runner produces a bounded JSON report/snapshot; `trend_advisor` derives a causal, deterministic research posture only from retained experimental candidates and finalized quality-clean evidence. New Swift models and views present both separately from qualified live-monitor setups. Existing `study-001-20260908` code and files are not read or written by the new runtime.

**Tech Stack:** Python 3.13, Pydantic 2, Pandas/NumPy, existing strategy registry and historical replay execution model, JSONL/fsync evidence records, Swift 6/SwiftUI, Swift Testing, pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-research-round-2-design.md`

## Global Constraints

- Research Round 2 is independent of and must not modify `study-001-20260908`, its frozen checkout, registry, ledger, reports, rules, positions, alerts, or campaign counts.
- Initial universe is Binance spot `BTCUSDT` and `ETHUSDT`; directions are long or abstain only, and no code may infer shortability from spot data.
- Persist an append-only, hash-bound protocol and ledger; changed protocol, strategy, source, cost, or sealed-test identity requires a new round.
- Use only finalized observations with `available_at <= decision_at`; do not gap-fill, backfill, or use a later revision to support an earlier decision.
- No credentials in source, snapshots, logs, preferences, command arguments, or Git. `PremiumProviderAdapter` is an interface only until a separately configured provider is added.
- Every output is simulated research only. It must never create an order, a notification request, a qualified setup, a lifecycle record, or a profitability claim.
- Default quality rules: 1-minute interval, at least 99.5% expected-bar coverage per evaluated fold, quote/bar age at most 15 seconds, observed spread at most 25 bps, continuous 60-minute warm-up after a gap, and no unresolved provider error in the decision interval.
- Default evaluation schedule: 90 UTC days train, 30 UTC days validation, 30 UTC days sealed test, stepping 30 UTC days; tests may instantiate smaller schedules explicitly.

## Review Focus

- Conflicting duplicate provider source keys must be rejected rather than silently choosing the more favorable observation (Task 2).
- A bar that arrived after its decision time must never enter strategy features or a fold result (Tasks 2 and 3).
- A gap just before an otherwise favorable decision must reset warm-up and force abstention (Task 2).
- A candidate must not reuse a previously sealed test window under the same round identity (Task 3).
- A malformed app payload attempting `qualified`, an order identifier, or a short spot posture must be discarded by the native parser (Task 5).

---

## File structure

- Create `src/research/round_two_contracts.py`: frozen Pydantic protocol, provider/source identity, observation, schedule, candidate status, canonical hash, and report models.
- Create `src/research/round_two_registry.py`: directory layout, atomic manifest creation, JSONL append/read helpers, and restart identity validation.
- Create `src/research/round_two_quality.py`: observation validation, duplicate/conflict checks, contiguous segment construction, per-fold coverage/freshness/spread/health gates, and append-only quality events.
- Create `src/research/round_two_walkforward.py`: causal signal evaluation, rolling fold boundaries, candidate/baseline comparison, sealed-test ledger protection, and conservative status decision.
- Create `src/research/round_two_runtime.py`: round registration, ingestion, evaluation orchestration, JSON report/snapshot serialization, and no-op premium adapter contract.
- Create `src/research/trend_advisor.py`: deterministic, causal paper-only trend posture derived from a Round 2 experimental candidate and finalized observations.
- Create `scripts/run_research_round_two.py`: `register`, `ingest`, `evaluate`, and `status` commands without any credentials.
- Create `tests/unit/test_round_two_contracts.py`, `tests/unit/test_round_two_quality.py`, `tests/unit/test_round_two_walkforward.py`, and `tests/integration/test_research_round_two_cli.py`: deterministic unit and command-level coverage.
- Create `macos/Nowcaster/Sources/NowcasterApp/Models/ResearchRoundModels.swift`: strict `ResearchRoundSnapshot` decoding and fail-closed experimental result model.
- Create `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/ResearchRoundView.swift`: a research-only status card and candidate rows with no trade-action controls.
- Create `macos/Nowcaster/Sources/NowcasterApp/Models/TrendAdvisorModels.swift` and `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/TrendAdvisorView.swift`: strict paper-only posture decoding and a read-only explanatory view.
- Modify `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`, `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`, and `macos/Nowcaster/Tests/NowcasterAppTests/ResearchRoundModelsTests.swift`: load, present, and test a user-selected report while keeping it separate from Live Monitor.
- Modify `README.md` and create `docs/research/research-round-2.md`: beginner documentation, protocol limits, and premium-provider boundary.

### Task 1: Immutable protocol and append-only round registry

**Files:**
- Create: `src/research/round_two_contracts.py`
- Create: `src/research/round_two_registry.py`
- Test: `tests/unit/test_round_two_contracts.py`

**Interfaces:**
- Produces `ResearchRoundProtocol`, `RoundSource`, `RoundCandidate`, `WalkForwardSchedule`, `RoundObservation`, `RoundStatus`, `RoundReport`, `register_round(protocol: ResearchRoundProtocol, directory: Path) -> Path`, and `load_round_protocol(directory: Path) -> ResearchRoundProtocol`.
- Consumed by Tasks 2–4.

- [ ] **Step 1: Write failing contract tests**

```python
def test_protocol_hash_is_stable_and_changed_restart_is_refused(tmp_path):
    protocol = ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_START)
    directory = register_round(protocol, tmp_path / "round-002")
    assert load_round_protocol(directory).identity_hash == protocol.identity_hash
    changed = protocol.model_copy(update={"maximum_spread_bps": 24})
    with pytest.raises(ValueError, match="protocol identity"):
        register_round(changed, directory)

def test_spot_protocol_rejects_short_candidate_and_unknown_symbol():
    with pytest.raises(ValueError, match="spot direction"):
        ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_START).model_copy(
            update={"candidates": (RoundCandidate(symbol="BTCUSDT", strategy_id="ema", direction="short"),)}
        ).validated()
```

- [ ] **Step 2: Run the focused test to verify RED**

Run: `pytest tests/unit/test_round_two_contracts.py -q`

Expected: FAIL because `round_two_contracts` and registry APIs do not exist.

- [ ] **Step 3: Implement the minimal frozen contracts and registry**

```python
class ResearchRoundProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    round_id: str
    source: RoundSource
    symbols: tuple[Literal["BTCUSDT", "ETHUSDT"], ...]
    candidates: tuple[RoundCandidate, ...]
    schedule: WalkForwardSchedule
    maximum_spread_bps: Decimal = Decimal("25")
    minimum_coverage: Decimal = Decimal("0.995")

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

def register_round(protocol: ResearchRoundProtocol, directory: Path) -> Path:
    manifest = directory / "protocol.json"
    if manifest.exists() and load_round_protocol(directory).identity_hash != protocol.identity_hash:
        raise ValueError("protocol identity does not match retained round")
    # Write only the first canonical manifest and fsync its directory.
```

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `pytest tests/unit/test_round_two_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the protocol boundary**

```bash
git add src/research/round_two_contracts.py src/research/round_two_registry.py tests/unit/test_round_two_contracts.py
git commit -m "feat: add immutable Research Round 2 protocol"
```

### Task 2: Causal observation ingestion and quality ledger

**Files:**
- Create: `src/research/round_two_quality.py`
- Modify: `src/research/round_two_registry.py`
- Test: `tests/unit/test_round_two_quality.py`

**Interfaces:**
- Consumes `ResearchRoundProtocol`, `RoundObservation`, and round directory from Task 1.
- Produces `append_observations(directory: Path, protocol: ResearchRoundProtocol, observations: Sequence[RoundObservation]) -> QualitySummary`, `eligible_segments(observations: Sequence[RoundObservation], protocol: ResearchRoundProtocol) -> tuple[EligibleSegment, ...]`, and `QualitySummary.reasons_for(decision_at: datetime) -> tuple[str, ...]`.
- Consumed by Tasks 3 and 4.

- [ ] **Step 1: Write failing quality tests**

```python
def test_conflicting_duplicate_source_key_is_rejected(tmp_path):
    protocol, directory = registered_round(tmp_path)
    first = observation(source_key="binance:42", close="100", available_at=UTC_T)
    conflicting = observation(source_key="binance:42", close="101", available_at=UTC_T)
    append_observations(directory, protocol, [first])
    with pytest.raises(ValueError, match="conflicting source key"):
        append_observations(directory, protocol, [conflicting])

def test_gap_and_late_bar_force_warmup_abstention(tmp_path):
    protocol, directory = registered_round(tmp_path)
    append_observations(directory, protocol, continuous_bars(minutes=60))
    append_observations(directory, protocol, [observation(at=UTC_T + timedelta(minutes=62))])
    summary = eligible_segments(load_observations(directory), protocol)
    assert "continuity_warmup" in summary.reasons_for(UTC_T + timedelta(minutes=63))
    assert "available_after_decision" in validate_observation(observation(available_at=UTC_T + timedelta(minutes=2)), UTC_T)
```

- [ ] **Step 2: Run the focused test to verify RED**

Run: `pytest tests/unit/test_round_two_quality.py -q`

Expected: FAIL because ingestion and quality APIs do not exist.

- [ ] **Step 3: Implement append-only validation and gates**

```python
def append_observations(directory: Path, protocol: ResearchRoundProtocol, observations: Sequence[RoundObservation]) -> QualitySummary:
    existing = {item.source_key: item for item in load_observations(directory)}
    for item in observations:
        item.validate_for(protocol)
        if item.source_key in existing and existing[item.source_key] != item:
            raise ValueError("conflicting source key")
    append_jsonl_fsync(directory / "observations.jsonl", [item.model_dump(mode="json") for item in observations])
    return summarize_quality(load_observations(directory), protocol)

def eligible_segments(observations: Sequence[RoundObservation], protocol: ResearchRoundProtocol) -> QualitySummary:
    # Split on expected one-minute discontinuities; each later decision needs 60 clean minutes.
    # Record stale, late, invalid-spread, and provider-error reasons without repairing them.
```

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `pytest tests/unit/test_round_two_quality.py -q`

Expected: PASS.

- [ ] **Step 5: Commit quality evidence handling**

```bash
git add src/research/round_two_quality.py src/research/round_two_registry.py tests/unit/test_round_two_quality.py
git commit -m "feat: add Research Round 2 quality ledger"
```

### Task 3: Fixed walk-forward candidate evaluation

**Files:**
- Create: `src/research/round_two_walkforward.py`
- Test: `tests/unit/test_round_two_walkforward.py`

**Interfaces:**
- Consumes protocol, eligible observations, and quality summary from Tasks 1–2 plus `StrategyRegistry` and `HistoricalReplay`.
- Produces `evaluate_round(protocol: ResearchRoundProtocol, observations: Sequence[RoundObservation], quality: QualitySummary, registry: StrategyRegistry) -> tuple[CandidateResult, ...]`, `WalkForwardFold`, and `SealedTestReceipt`.
- Consumed by Task 4.

- [ ] **Step 1: Write failing no-leakage and sealed-test tests**

```python
def test_selection_uses_train_validation_only_and_retains_rejected_candidates():
    results = evaluate_round(protocol_with_small_schedule(), observations_with_late_test_winner(), quality_ok(), registry())
    candidate = next(item for item in results if item.candidate.strategy_id == "ema")
    assert candidate.selected_parameters == {"fast": 5}
    assert candidate.status == "rejected"
    assert candidate.sealed_test.net_return > 0
    assert "validation_lower_edge" in candidate.reasons

def test_same_sealed_window_cannot_be_evaluated_twice(tmp_path):
    receipt = persist_sealed_test_receipt(tmp_path, round_hash="a", fold_id="2026-03-01")
    with pytest.raises(ValueError, match="sealed test already evaluated"):
        persist_sealed_test_receipt(tmp_path, round_hash="a", fold_id=receipt.fold_id)
```

- [ ] **Step 2: Run the focused test to verify RED**

Run: `pytest tests/unit/test_round_two_walkforward.py -q`

Expected: FAIL because the evaluator and sealed-test receipt APIs do not exist.

- [ ] **Step 3: Implement causal rolling evaluation**

```python
def evaluate_round(...):
    folds = protocol.schedule.folds(observations)
    for fold in folds:
        train = causal_finalized_slice(observations, fold.train_start, fold.train_end)
        validation = causal_finalized_slice(observations, fold.train_end, fold.validation_end)
        sealed = causal_finalized_slice(observations, fold.validation_end, fold.test_end)
        # Select parameters from train only, choose eligibility from validation only,
        # then execute the sealed test once with next-observation fills and declared costs.
```

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `pytest tests/unit/test_round_two_walkforward.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the evaluator**

```bash
git add src/research/round_two_walkforward.py tests/unit/test_round_two_walkforward.py
git commit -m "feat: add sealed walk-forward round evaluation"
```

### Task 4: Runtime, report, and credential-free CLI

**Files:**
- Create: `src/research/round_two_runtime.py`
- Create: `scripts/run_research_round_two.py`
- Modify: `src/research/__init__.py`
- Test: `tests/integration/test_research_round_two_cli.py`

**Interfaces:**
- Consumes Task 1 registry, Task 2 ingestion, and Task 3 evaluator.
- Produces commands `register`, `ingest`, `evaluate`, and `status`; `PremiumProviderAdapter` protocol; `write_round_report(directory: Path) -> Path`; and bounded `research-round-2-summary.json`.
- Consumed by Task 5 and documentation.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_register_ingest_evaluate_and_status_are_paper_only(tmp_path, capsys):
    assert main(["register", "--directory", str(tmp_path), "--starts-at", "2026-01-01T00:00:00Z"]) == 0
    assert main(["ingest", "--directory", str(tmp_path), "--input", str(FIXTURE)]) == 0
    assert main(["evaluate", "--directory", str(tmp_path)]) == 0
    payload = json.loads((tmp_path / "research-round-2-summary.json").read_text())
    assert payload["paper_only"] is True
    assert payload["qualification_status"] == "unqualified"
    assert "order" not in json.dumps(payload).lower()

def test_premium_adapter_requires_explicit_configuration():
    with pytest.raises(RuntimeError, match="not configured"):
        UnconfiguredPremiumProviderAdapter().observations(("BTCUSDT",))
```

- [ ] **Step 2: Run the integration test to verify RED**

Run: `pytest tests/integration/test_research_round_two_cli.py -q`

Expected: FAIL because the runner and runtime do not exist.

- [ ] **Step 3: Implement orchestration and report serialization**

```python
class PremiumProviderAdapter(Protocol):
    provider_identity: str
    def observations(self, symbols: tuple[str, ...]) -> tuple[RoundObservation, ...]: ...

class UnconfiguredPremiumProviderAdapter:
    def observations(self, symbols: tuple[str, ...]) -> tuple[RoundObservation, ...]:
        raise RuntimeError("premium provider is not configured")

def write_round_report(directory: Path) -> Path:
    report = build_round_report(directory)
    report_path = directory / "research-round-2-summary.json"
    write_atomic_json(report_path, report.model_dump(mode="json"))
    return report_path
```

- [ ] **Step 4: Run integration tests to verify GREEN**

Run: `pytest tests/integration/test_research_round_two_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit runtime and CLI**

```bash
git add src/research/round_two_runtime.py scripts/run_research_round_two.py src/research/__init__.py tests/integration/test_research_round_two_cli.py
git commit -m "feat: add Research Round 2 runner"
```

### Task 5: Fail-closed macOS research display

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Models/ResearchRoundModels.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/ResearchRoundView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/ResearchRoundModelsTests.swift`

**Interfaces:**
- Consumes `research-round-2-summary.json` from Task 4.
- Produces `ResearchRoundSnapshot`, `ResearchRoundCandidate`, `ResearchRoundPresentation`, and a read-only `ResearchRoundView`.
- Does not consume or mutate `LiveMonitorService`, `LiveSetup`, broker, notification, or order types.

- [ ] **Step 1: Write failing Swift decoding and presentation tests**

```swift
@Test func rejectsQualifiedOrderOrShortSpotCandidate() throws {
    var payload = validResearchRoundPayload
    payload["qualification_status"] = "qualified"
    #expect(throws: SnapshotValidationError.self) {
        try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self, from: payload.data)
    }
    payload = validResearchRoundPayload
    payload["candidates"] = [["symbol": "BTCUSDT", "direction": "short"]]
    #expect(throws: SnapshotValidationError.self) {
        try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self, from: payload.data)
    }
}
```

- [ ] **Step 2: Run the native test to verify RED**

Run: `cd macos/Nowcaster && swift test --filter ResearchRoundModelsTests`

Expected: FAIL because the round snapshot types and view do not exist.

- [ ] **Step 3: Implement strict decoding and a non-actionable view**

```swift
struct ResearchRoundSnapshot: Decodable, Sendable {
    let paperOnly: Bool
    let qualificationStatus: String
    let providerHealth: ResearchRoundProviderHealth
    let candidates: [ResearchRoundCandidate]

    func validate() throws {
        guard paperOnly, qualificationStatus == "unqualified" else {
            throw SnapshotValidationError.invalidResearchEvidence("Research Round 2 must remain unqualified paper research")
        }
    }
}
```

`ResearchRoundView` uses `ContentUnavailableView` for abstentions and `LabeledContent` for provider health, coverage, reasons, and paper-only candidate results. It must contain no Button that starts monitoring, tracks fills, sends a notification, or opens Execution Center.

- [ ] **Step 4: Run native tests to verify GREEN**

Run: `cd macos/Nowcaster && swift test --filter ResearchRoundModelsTests`

Expected: PASS.

- [ ] **Step 5: Commit the macOS research view**

```bash
git add macos/Nowcaster/Sources/NowcasterApp/Models/ResearchRoundModels.swift macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/ResearchRoundView.swift macos/Nowcaster/Sources/NowcasterApp/AppModel.swift macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift macos/Nowcaster/Tests/NowcasterAppTests/ResearchRoundModelsTests.swift
git commit -m "feat: show Research Round 2 evidence in macOS app"
```

### Task 6: Documentation, fixtures, and full verification

**Files:**
- Create: `docs/research/research-round-2.md`
- Modify: `README.md`
- Modify: `data/demo/intraday/research-fixture.json`
- Modify: relevant Python and Swift tests from Tasks 1–5 only if the final fixture requires a schema extension.

**Interfaces:**
- Consumes report schema from Task 4 and presentation model from Task 5.
- Produces a documented fixture and beginner explanation of the paper-only limits.

- [ ] **Step 1: Write failing documentation/fixture assertions**

```python
def test_research_round_documentation_never_claims_profitability():
    text = Path("docs/research/research-round-2.md").read_text()
    assert "not proof of profitability" in text
    assert "paper-only" in text

def test_round_fixture_is_unqualified_and_has_provider_health():
    payload = json.loads(Path("data/demo/intraday/research-fixture.json").read_text())
    assert payload["research_round_2"]["paper_only"] is True
    assert payload["research_round_2"]["qualification_status"] == "unqualified"
```

- [ ] **Step 2: Run the targeted tests to verify RED**

Run: `pytest tests/unit/test_documentation.py tests/unit/test_app_snapshot.py -q`

Expected: FAIL because the documented fixture contract does not exist.

- [ ] **Step 3: Document operation and add the bounded fixture**

Explain the source identity, quality thresholds, no-gap-fill rule, walk-forward windows, sealed-test receipt, all-candidate retention, provider configuration boundary, and the difference between experimental research and an actionable alert. Add only an unqualified paper-only fixture with a visible data-quality reason; do not fabricate a profitable candidate.

- [ ] **Step 4: Run targeted tests to verify GREEN**

Run: `pytest tests/unit/test_documentation.py tests/unit/test_app_snapshot.py -q && cd macos/Nowcaster && swift test`

Expected: PASS.

- [ ] **Step 5: Run project verification and package the app**

Run: `pytest -q && make macos-app && codesign --verify --deep --strict build/Nowcaster.app`

Expected: all Python tests pass, macOS app builds, and code-signature verification exits 0. Record any unrelated pre-existing failure without claiming a clean suite.

- [ ] **Step 6: Commit documentation and verification fixture**

```bash
git add README.md docs/research/research-round-2.md data/demo/intraday/research-fixture.json tests
git commit -m "docs: explain Research Round 2 evidence limits"
```

### Task 7: Causal, paper-only Trend Advisor

**Files:**
- Create: `src/research/trend_advisor.py`
- Modify: `src/research/round_two_runtime.py`
- Create: `tests/unit/test_trend_advisor.py`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Models/TrendAdvisorModels.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/TrendAdvisorView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/ResearchRoundModels.swift`, `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`, and `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/TrendAdvisorModelsTests.swift`

**Interfaces:**
- Consumes only `CandidateResult`/round identity, a `QualitySummary`, and finalized observations from Tasks 1–4.
- Produces `TrendAdvisorSuggestion` with `posture` (`long_research`, `short_research`, or `stand_aside`), entry zone, invalidation, targets, expiry, reason codes, causal timestamps, and fixed evidence identities.
- Extends the bounded Research Round 2 report with an optional `trend_advisor` payload. It never consumes Live Monitor, notification, broker, order, or lifecycle types.

- [ ] **Step 1: Write failing causal and fail-closed tests**

```python
def test_emits_long_research_only_for_clean_finalized_experimental_candidate():
    suggestion = advise(protocol, experimental_candidate(), clean_quality(), finalized_uptrend())
    assert suggestion.posture == "long_research"
    assert suggestion.paper_only is True
    assert suggestion.entry_low < suggestion.entry_high < suggestion.target

def test_spot_short_and_rejected_or_gapped_candidate_stand_aside():
    assert advise(spot_protocol(), short_candidate(), clean_quality(), bars()).posture == "stand_aside"
    assert "spot_short_unsupported" in advise(spot_protocol(), short_candidate(), clean_quality(), bars()).reasons
    assert advise(spot_protocol(), rejected_candidate(), gapped_quality(), bars()).posture == "stand_aside"
```

Run: `pytest tests/unit/test_trend_advisor.py -q`

Expected: FAIL because no Trend Advisor exists.

- [ ] **Step 2: Implement deterministic causal posture selection**

`TrendAdvisor` must require all of: `experimental_paper_only` status; available/finalized data at or before the decision timestamp; no quality exclusion; an upward/downward aligned moving-average slope; declared trend-strength threshold; bounded realised volatility and liquidity; and confirmation from the selected candidate. It derives entry/invalidation/target from the hash-bound candidate barriers and the last executable observation, never from future bars. Missing, stale, gapped, weak, contradictory, or non-executable evidence produces `stand_aside` with reasons. Binance spot short requests always produce `stand_aside`.

- [ ] **Step 3: Extend the bounded runtime report**

Include `trend_advisor` only as a paper-only, `unqualified` structure. Persist the round hash, source identity, candidate identity, decision/availability timestamps, expiry, and reason codes. Reject any payload that contains an order, notification, lifecycle, `qualified` label, or an executable short under the spot protocol.

- [ ] **Step 4: Implement the strict native read-only display**

Decode the posture fail-closed. Present `Stand aside` prominently when it cannot be established, and otherwise display the research posture, timeframe, evidence summary, entry zone, invalidation, target, expiry, and close reasons under the exact label "Paper-only trend research — not a trade instruction." Do not add a button, notification, or execution route.

- [ ] **Step 5: Run focused verification**

Run: `pytest tests/unit/test_trend_advisor.py tests/integration/test_research_round_two_cli.py -q && cd macos/Nowcaster && swift test --filter TrendAdvisorModelsTests`

Expected: PASS. Record any pre-existing platform limitation without calling this a clean full suite.

- [ ] **Step 6: Commit Trend Advisor**

```bash
git add src/research/trend_advisor.py src/research/round_two_runtime.py tests/unit/test_trend_advisor.py macos/Nowcaster/Sources/NowcasterApp/Models/TrendAdvisorModels.swift macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/TrendAdvisorView.swift macos/Nowcaster/Sources/NowcasterApp/Models/ResearchRoundModels.swift macos/Nowcaster/Sources/NowcasterApp/AppModel.swift macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift macos/Nowcaster/Tests/NowcasterAppTests/TrendAdvisorModelsTests.swift
git commit -m "feat: add paper-only Trend Advisor"
```

## Plan self-review

- Spec coverage: Task 1 implements immutable protocol/identity; Task 2 implements provenance and fail-closed data quality; Task 3 implements fixed causal walk-forward evaluation, baselines, retention, costs, and sealed testing; Task 4 implements the isolated runtime/provider boundary/report; Task 5 implements separate native research status; Task 6 implements beginner documentation and end-to-end verification; Task 7 implements the separately presented causal Trend Advisor posture.
- Placeholder scan: no deferred or unspecified implementation steps remain; every code step names its types, commands, tests, and expected result.
- Type consistency: `ResearchRoundProtocol`, `RoundObservation`, `QualitySummary`, `CandidateResult`, and `ResearchRoundSnapshot` are introduced before their consumers and preserve paper-only/unqualified status across Python and Swift.
- Review focus coverage: Tasks 2, 3, and 5 contain an explicit test for each listed failure mode.
