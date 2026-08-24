import Foundation
import Testing

@testable import NowcasterApp

private let completeV2Payload = Data(
    """
    {
      "schema_version": 3,
      "metadata": {
        "generated_at": "2026-08-22T12:34:56.123456Z",
        "git_commit": "abc123",
        "data_mode": "strategy_provider_data",
        "source_posture": "Source-backed strategy bars: binance/spot",
        "expectation_mode": "unavailable",
        "last_refresh": "2026-08-22T12:34:56Z"
      },
      "overview": {
        "company_count": 0,
        "instrument_count": 1,
        "company_quarter_count": 0,
        "alternative_observation_count": 0,
        "forecast_count": 0,
        "signal_count": 0,
        "event_window_count": 0,
        "quality_issue_count": 0,
        "forecast_mae_improvement": null,
        "alternative_incremental_mae_improvement": null,
        "event_spread": null
      },
      "instruments": [],
      "earnings": [],
      "signals": [],
      "model_diagnostics": [],
      "backtests": [],
      "quality_issues": [],
      "pipeline_runs": [],
      "strategies": [{
        "strategy_id": "rsi_reversal",
        "version": "1.0.0-abc",
        "family": "mean_reversion",
        "dataset_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "mode": "paper",
        "cohort_id": "cohort-paper-d",
        "state": "evaluated",
        "weight": 0.35,
        "development_metrics": {"sharpe": 1.25, "dsr_probability": null},
        "final_test_metrics": {"sharpe": 0.5, "maximum_drawdown": -0.12},
        "warnings": ["Historical evidence is not live proof"],
        "generation": 2,
        "progress": 1.0,
        "complexity": 3,
        "promotion_state": "research_only",
        "causal_audit_passed": true,
        "no_repaint_badge": "passed",
        "latest_run_at": "2026-08-22T12:00:00Z"
      }],
      "ensemble_components": [{
        "strategy_id": "rsi_reversal",
        "version": "1.0.0-abc",
        "family": "mean_reversion",
        "dataset_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "mode": "paper",
        "cohort_id": "cohort-paper-d",
        "effective_at": "2026-08-22T12:05:00Z",
        "weight": 0.35,
        "contribution": -0.18,
        "evidence": {"trial_count": 4, "gate": "development_only", "cost_survived": true}
      }],
      "dataset_coverage": [{
        "dataset_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "provider": "binance",
        "feed": "spot",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "requested_start": "2026-08-20T00:00:00Z",
        "requested_end": "2026-08-22T00:00:00Z",
        "coverage_start": "2026-08-20T00:00:00Z",
        "coverage_end": "2026-08-21T23:55:00Z",
        "row_count": 576,
        "gaps": [{
          "start": "2026-08-21T01:00:00Z",
          "end": "2026-08-21T01:10:00Z",
          "missing_bars": 2
        }],
        "complete": false,
        "calendar_id": "24/7",
        "calendar_version": "always-open-v1"
      }],
      "learning_runs": [{
        "learning_run_id": "learn-1",
        "state": "completed",
        "evaluated_candidates": 2,
        "evaluation_budget": 4,
        "best_rule": "RSI from the prior bar is above 50",
        "best_rule_detail": {
          "rule_id": "rule-1",
          "strategy_id": "learned-rsi",
          "version": "1.0.0",
          "state": "shadow",
          "rule_text": "RSI from the prior bar is above 50",
          "fitness": 0.25,
          "complexity": 3,
          "discovered_at": "2026-08-22T10:00:00Z",
          "evidence_through": "2026-08-22T11:00:00Z",
          "promotion_state": "shadow",
          "causal_audit_id": "audit-1",
          "no_repaint_badge": "passed"
        },
        "final_boundary": "2026-08-23T00:00:00Z",
        "generation": 3,
        "progress": 0.5,
        "trials": [{
          "trial_id": "trial-1",
          "candidate_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
          "status": "succeeded",
          "fitness": 0.25,
          "evaluated_at": "2026-08-22T10:00:00Z",
          "rule_text": "RSI from the prior bar is above 50",
          "complexity": 3,
          "error_summary": null
        }],
        "discovered_rules": [{
          "rule_id": "rule-1",
          "strategy_id": "learned-rsi",
          "version": "1.0.0",
          "state": "shadow",
          "rule_text": "RSI from the prior bar is above 50",
          "fitness": 0.25,
          "complexity": 3,
          "discovered_at": "2026-08-22T10:00:00Z",
          "evidence_through": "2026-08-22T11:00:00Z",
          "promotion_state": "shadow",
          "causal_audit_id": "audit-1",
          "no_repaint_badge": "passed"
        }],
        "promotion_state": "shadow",
        "causal_audit_id": "audit-1",
        "no_repaint_badge": "passed"
      }],
      "causal_audits": [{
        "audit_id": "audit-1",
        "dataset_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "strategy_id": "rsi_reversal",
        "version": "1.0.0-abc",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "mode": "paper",
        "audited_at": "2026-08-22T12:10:00Z",
        "passed": true,
        "outer_block_consumed": false,
        "details": {"prefix_invariant": true, "observations": 576},
        "no_repaint_badge": "passed"
      }]
    }
    """.utf8
)

@Test func decodesBundledSchemaV2FixtureAndPreservesLegacySections() throws {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
    #expect(snapshot.schemaVersion == 3)
    #expect(snapshot.instruments.contains { $0.assetClass == .crypto })
    #expect(snapshot.backtests.contains { $0.assetClass == .crypto })
    #expect(!snapshot.strategies.isEmpty)
}

@Test func decodesEverySchemaV2ResearchSectionFromSnakeCase() throws {
    let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: completeV2Payload)

    let strategy = try #require(snapshot.strategies.first)
    #expect(strategy.strategyId == "rsi_reversal")
    #expect(strategy.developmentMetrics["sharpe"] == 1.25)
    #expect(strategy.finalTestMetrics["maximum_drawdown"] == -0.12)
    #expect(strategy.noRepaintBadge == .passed)
    #expect(strategy.datasetHash == String(repeating: "d", count: 64))
    #expect(strategy.mode == "paper")
    #expect(strategy.cohortId == "cohort-paper-d")
    #expect(strategy.id.contains("cohort-paper-d"))

    let component = try #require(snapshot.ensembleComponents.first)
    #expect(component.contribution == -0.18)
    #expect(component.datasetHash == strategy.datasetHash)
    #expect(component.cohortId == strategy.cohortId)
    #expect(component.evidence["gate"] == .string("development_only"))
    #expect(component.evidence["cost_survived"] == .bool(true))

    let coverage = try #require(snapshot.datasetCoverage.first)
    #expect(coverage.calendarVersion == "always-open-v1")
    #expect(coverage.gaps.first?.missingBars == 2)

    let run = try #require(snapshot.learningRuns.first)
    #expect(run.bestRule == "RSI from the prior bar is above 50")
    #expect(run.bestRuleDetail?.ruleId == "rule-1")
    #expect(run.finalBoundary > snapshot.metadata.generatedAt)
    #expect(run.trials.first?.candidateHash.count == 64)
    #expect(run.discoveredRules.first?.noRepaintBadge == .passed)

    let audit = try #require(snapshot.causalAudits.first)
    #expect(audit.outerBlockConsumed == false)
    #expect(audit.details["prefix_invariant"] == .bool(true))
}

@Test func repositoryAcceptsOnlySchemaV2() async throws {
    let repository = SnapshotRepository()
    let snapshot = try await repository.load(data: completeV2Payload)
    #expect(snapshot.schemaVersion == 3)

    await #expect(throws: SnapshotRepositoryError.incompatibleSchema(1)) {
        try await repository.load(data: Data("{\"schema_version\":1}".utf8))
    }
}

@Test func repositoryRejectsMalformedSchemaV2() async {
    let malformed = Data(
        """
        {"schema_version":3,"metadata":{"generated_at":"not-a-date"}}
        """.utf8
    )
    await #expect(throws: SnapshotRepositoryError.self) {
        try await SnapshotRepository().load(data: malformed)
    }
}

@Test func learningRunRequiresAStringSummaryAndFinalBoundary() {
    let missingBoundary = Data(
        """
        {"learning_run_id":"learn-1","state":"completed","evaluated_candidates":0,
        "evaluation_budget":1,"best_rule":null}
        """.utf8
    )
    #expect(throws: DecodingError.self) {
        try JSONDecoder.nowcaster.decode(LearningRunSnapshot.self, from: missingBoundary)
    }

    let objectSummary = Data(
        """
        {"learning_run_id":"learn-1","state":"completed","evaluated_candidates":0,
        "evaluation_budget":1,"best_rule":{"rule":"wrong"},"final_boundary":"2026-08-23T00:00:00Z"}
        """.utf8
    )
    #expect(throws: DecodingError.self) {
        try JSONDecoder.nowcaster.decode(LearningRunSnapshot.self, from: objectSummary)
    }
}

@Test func datesDecodeWithAndWithoutFractionalSeconds() throws {
    let data = Data(
        """
        {"generated_at":"2026-08-22T12:34:56.123456Z","git_commit":"abc","data_mode":"demo",\
        "source_posture":"fixture","expectation_mode":"proxy","last_refresh":"2026-08-22T12:34:56Z"}
        """.utf8
    )
    let metadata = try JSONDecoder.nowcaster.decode(SnapshotMetadata.self, from: data)
    #expect(metadata.lastRefresh != nil)
}

@Test func schemaV2InstantsRequireZuluUTCWhileLegacyDatesRemainDateOnlyCompatible() async throws {
    let repository = SnapshotRepository()
    let offset = Data(
        String(decoding: completeV2Payload, as: UTF8.self)
            .replacingOccurrences(of: "2026-08-23T00:00:00Z", with: "2026-08-23T00:00:00+00:00")
            .utf8
    )
    await #expect(throws: SnapshotRepositoryError.self) {
        try await repository.load(data: offset)
    }

    let dateOnlyInstant = Data(
        String(decoding: completeV2Payload, as: UTF8.self)
            .replacingOccurrences(of: "2026-08-23T00:00:00Z", with: "2026-08-23")
            .utf8
    )
    await #expect(throws: SnapshotRepositoryError.self) {
        try await repository.load(data: dateOnlyInstant)
    }

    let legacy = try JSONDecoder.nowcaster.decode(
        PricePoint.self,
        from: Data("{\"date\":\"2026-08-23\",\"close\":100,\"volume\":null}".utf8)
    )
    #expect(legacy.close == 100)
}

@Test func repositoryRejectsOversizedInputBeforeSnapshotDecoding() async {
    let oversized = Data(repeating: 0x20, count: SnapshotDecodingLimits.maximumSnapshotBytes + 1)
    await #expect(throws: SnapshotRepositoryError.self) {
        try await SnapshotRepository().load(data: oversized)
    }
}

@Test func evidenceDecoderRejectsDeepLargeAndOversizedValues() {
    func component(evidence: String) -> Data {
        Data(
            """
            {"strategy_id":"rsi","version":"1","family":"mean_reversion",\
            "dataset_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",\
            "symbol":"BTCUSDT","interval":"5m","mode":"paper","cohort_id":"cohort-d",\
            "effective_at":"2026-08-22T12:05:00Z","weight":0.5,"contribution":0.1,\
            "evidence":\(evidence)}
            """.utf8
        )
    }

    var nested = "true"
    for _ in 0 ... SnapshotDecodingLimits.maximumEvidenceDepth {
        nested = "{\"nested\":\(nested)}"
    }
    #expect(throws: DecodingError.self) {
        try JSONDecoder.nowcaster.decode(EnsembleComponentSnapshot.self, from: component(evidence: nested))
    }

    let collection = "[" + Array(
        repeating: "0",
        count: SnapshotDecodingLimits.maximumCollectionLength + 1
    ).joined(separator: ",") + "]"
    #expect(throws: DecodingError.self) {
        try JSONDecoder.nowcaster.decode(
            EnsembleComponentSnapshot.self,
            from: component(evidence: "{\"items\":\(collection)}")
        )
    }

    let oversizedString = String(repeating: "x", count: SnapshotDecodingLimits.maximumStringBytes + 1)
    #expect(throws: DecodingError.self) {
        try JSONDecoder.nowcaster.decode(
            EnsembleComponentSnapshot.self,
            from: component(evidence: "{\"text\":\"\(oversizedString)\"}")
        )
    }
}
