import Foundation

struct ExecutionPresentation: Sendable {
    let stateTitle: String
    let summary: String
    let environment: String
    let accountLabel: String
    let reconciliationLabel: String
    let readinessGates: [ReadinessGateSnapshot]
    let observedPeriods: Int
    let closedTrades: Int
    let isFrozen: Bool

    init(snapshot: NowcasterSnapshot) {
        let broker = snapshot.brokerStatus
        let readiness = snapshot.forwardReadiness
        environment = (broker?.environment ?? "research").capitalized
        accountLabel = broker?.accountSuffix.map { "Account ••••\($0)" } ?? "No broker account connected"
        reconciliationLabel = (broker?.unresolvedMismatches ?? 0) == 0
            ? "No unresolved reconciliation differences"
            : "\(broker?.unresolvedMismatches ?? 0) reconciliation differences require attention"
        readinessGates = readiness?.gates ?? [
            ReadinessGateSnapshot(
                name: "external_forward_evidence",
                passed: false,
                detail: "Paper evidence and external release conditions are not yet complete."
            ),
        ]
        observedPeriods = readiness?.observedPeriods ?? 0
        closedTrades = readiness?.closedTrades ?? 0
        isFrozen = snapshot.emergencyStatus?.frozen ?? false
        switch readiness?.state {
        case "armed":
            stateTitle = "Armed Pilot"
            summary = "The capped pilot arm is temporary. Every order still requires risk admission."
        case "eligible":
            stateTitle = "Pilot Eligible"
            summary = "Forward gates passed, but live remains inactive until a production-signed manual arm."
        default:
            stateTitle = "Live Locked"
            summary = "Research and paper evidence cannot guarantee future profits. Complete every gate before a capped pilot."
        }
    }
}
