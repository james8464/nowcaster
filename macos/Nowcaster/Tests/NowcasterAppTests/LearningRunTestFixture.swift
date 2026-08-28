import Foundation

@testable import NowcasterApp

func syntheticLearningRunFixture() throws -> LearningRunSnapshot {
    let data = Data(
        """
        {
          "learning_run_id": "learning-fixture",
          "state": "completed",
          "evaluated_candidates": 8,
          "evaluation_budget": 16,
          "best_rule": "Prior-bar RSI is above 50",
          "best_rule_detail": {
            "rule_id": "rule-fixture",
            "strategy_id": "rsi_reversal",
            "version": "1",
            "state": "shadow",
            "rule_text": "Prior-bar RSI is above 50",
            "fitness": 1.2,
            "complexity": 2,
            "discovered_at": "2026-08-22T18:00:00Z",
            "evidence_through": "2026-08-22T17:00:00Z",
            "promotion_state": "shadow",
            "causal_audit_id": null,
            "no_repaint_badge": "passed"
          },
          "final_boundary": "2026-08-22T05:20:00Z",
          "generation": 2,
          "progress": 0.5,
          "trials": [],
          "discovered_rules": [],
          "promotion_state": "shadow",
          "causal_audit_id": null,
          "no_repaint_badge": "not_audited"
        }
        """.utf8
    )
    return try JSONDecoder.nowcaster.decode(LearningRunSnapshot.self, from: data)
}
