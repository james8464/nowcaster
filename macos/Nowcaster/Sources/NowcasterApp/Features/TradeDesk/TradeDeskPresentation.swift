import Foundation

struct TradeDeskPresentation {
    struct Asset: Identifiable {
        let symbol: String
        let source: String
        let freshness: String
        let trend: String
        let posture: String
        let reason: String
        let detail: TrendAdvisorSuggestion?
        var id: String { symbol }
    }
    struct Historical: Identifiable {
        let id: String
        let symbol: String
        let completedAt: Date
        let outcome: String
        let costs = "Unavailable"
    }
    let assets: [Asset]
    let status: String
    let canPause: Bool
    let transitioning: Bool
    var action: String { canPause ? "Pause" : "Start" }

    @MainActor static func make(service: LivePaperSignalService, session: PaperSessionCoordinator, now: Date) -> Self {
        let health = service.providerHealth
        let source = health.map { "\($0.provider):\($0.feed)" }
        return .init(assets: rows(state: service.state, evidence: service.decisionEvidence,
            isRunning: service.isRunning, source: source, now: now, feedStatus: health?.title(now: now)),
            status: statusTitle(session.state),
            canPause: service.isRunning || session.state == .starting || session.state == .pausing,
            transitioning: session.state == .pausing)
    }

    static func statusTitle(_ state: PaperSessionState) -> String {
        switch state {
        case .idle: "Not started"
        case .starting: "Preparing session…"
        case .collecting: "Collecting"
        case .researching: "Researching"
        case .waiting: "Waiting for eligible data"
        case .pausing: "Pausing…"
        case .paused: "Paused"
        case .blocked: "Needs attention"
        }
    }

    static func rows(state: LivePaperSignalState?, evidence: DayTraderEvidence?, isRunning: Bool,
                     source: String?, now: Date, feedStatus: String? = nil) -> [Asset] {
        let knownSource = source == "binance:spot"
        let usableFeed = feedStatus.map { ["Healthy", "Degraded"].contains($0) } ?? true
        let live = LivePaperSignalsPresentation(state: state, isRunning: isRunning && knownSource && usableFeed, now: now)
        let contexts = evidence?.currentContexts(now: now, isRunning: isRunning && knownSource && usableFeed) ?? []
        let freshness = !knownSource ? "Unavailable" : isRunning && !usableFeed ? feedStatus! : live.status
        let failure = !knownSource ? "No verified source observations are available." : isRunning && !usableFeed
            ? "Public feed \(freshness.lowercased()). Waiting for fresh source observations."
            : live.reasons
        return ["BTCUSDT", "ETHUSDT"].map { symbol in
            let detail = live.suggestion.flatMap { $0.symbol == symbol ? $0 : nil }
            let context = contexts.first { $0.symbol == symbol }
            return Asset(symbol: symbol, source: knownSource ? "Binance spot" : "Unavailable",
                freshness: freshness,
                trend: context.map { $0.trends.map { "\($0.minutes)m \($0.direction.researchTitle)" }.joined(separator: " · ") } ?? "Unavailable",
                posture: detail == nil ? "Stand aside" : "Experimental long",
                reason: detail == nil ? (failure.isEmpty ? "No candidate clears publication checks." : failure) : "Paper research; profitability unestablished.",
                detail: detail)
        }
    }

    static func history(evidence: DayTraderEvidence?) -> [Historical] {
        (evidence?.outcomes ?? []).reversed().map {
            Historical(id: $0.id, symbol: $0.symbol, completedAt: $0.completedAt, outcome: $0.exitReason.researchTitle)
        }
    }
}
