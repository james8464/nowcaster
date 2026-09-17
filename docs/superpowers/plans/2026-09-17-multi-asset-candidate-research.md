# Multi-Asset Candidate Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a strict separate campaign workflow for candidate-market research without touching the frozen BTC/ETH study or claiming profitability.

**Architecture:** A versioned definition declares an asset, data identity, strategy set, and execution assumptions. A small runtime records immutable `available`, `unavailable`, or `rejected` receipts. The macOS app presents those receipts as research-only and cannot arm alerts or orders from them.

**Tech Stack:** Python 3.13, Pydantic, Typer, pytest, Swift 6/SwiftUI, Swift Testing.

**Spec:** `docs/superpowers/specs/2026-09-17-multi-asset-candidate-research.md`

## Global Constraints

- Never modify the retained study or its frozen source.
- Retain every source failure and campaign attempt atomically.
- Reject non-finalized, daily, synthetic, or forward-filled bars for intraday campaigns.
- Keep candidate campaigns separate from credentials, broker orders, and live alerts.

### Task 1: Immutable campaign contracts

**Files:** Create `src/research/candidate_campaign.py`; create `tests/unit/test_candidate_campaign.py`; modify `src/research/__init__.py`.

- [ ] Write failing tests that a WTI candidate requires a futures contract identity, session calendar, roll policy, and intraday interval; test that its identity hash changes when any of these changes.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_candidate_campaign.py -q` and confirm it fails because the module is absent.
- [ ] Implement frozen Pydantic types `CampaignSource`, `CampaignAsset`, `CandidateCampaignDefinition`, and `CampaignReceipt`; use `canonical_hash` over the full JSON definition.
- [ ] Re-run the focused test and commit `feat(research): add immutable candidate campaigns`.

### Task 2: Append-only source receipts

**Files:** Create `src/research/candidate_campaign_runtime.py`; modify `src/research/candidate_campaign.py`; create `tests/unit/test_candidate_campaign_runtime.py`.

- [ ] Write failing tests for an unavailable WTI source, a rejected daily/non-finalized CSV, and append-only duplicate protection.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_candidate_campaign_runtime.py -q` and confirm it fails.
- [ ] Implement `register_campaign()` and `inspect_campaign_source()`: verify the existing CSV contract, enforce WTI metadata, persist one fsync-backed JSONL receipt before returning, and never download or synthesize bars.
- [ ] Run both unit files and commit `feat(research): retain candidate source receipts`.

### Task 3: Research-only command and presentation

**Files:** Modify `src/cli.py`, `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`, and `README.md`; create `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/CandidateCampaignView.swift`, `tests/integration/test_candidate_campaign_cli.py`, and `macos/Nowcaster/Tests/NowcasterAppTests/CandidateCampaignPresentationTests.swift`.

- [ ] Write failing CLI and Swift tests. The command must emit an unavailable receipt for missing WTI data; the view must show “Research only” and “verified intraday contract data”, never long, short, confidence, notification, or order controls.
- [ ] Implement `strategy register-campaign --definition PATH --output-directory PATH` and a semantic SwiftUI status card. The command must not write Live Monitor tables or start any collector.
- [ ] Run the targeted Python and Swift tests and commit `feat(macos): show candidate research status`.

### Task 4: Retained WTI-unavailable campaign and verification

**Files:** Create `docs/research/multi-asset-candidate-campaign-2026-09-17.md`; modify `README.md`.

- [ ] Register one WTI campaign with no compliant source to create a retained `unavailable` receipt outside Git.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_candidate_campaign.py tests/unit/test_candidate_campaign_runtime.py tests/integration/test_candidate_campaign_cli.py -q` and `cd macos/Nowcaster && swift test --filter CandidateCampaignPresentationTests`.
- [ ] Document the source requirement, receipt identity, unavailable outcome, and the unchanged BTC/ETH study; state no profitability claim was made.
- [ ] Commit `docs(research): record unavailable WTI campaign`.

## Completion Record

All four tasks were completed on 17 September 2026. The registered WTI campaign is retained outside Git with status `unavailable`; no historical replay was run because no compliant intraday contract dataset exists locally. Focused Python tests, the focused native presentation test, Ruff, and whitespace checks passed.
