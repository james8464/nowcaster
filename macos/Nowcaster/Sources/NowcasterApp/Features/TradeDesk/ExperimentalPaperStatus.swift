import Foundation

/// Separate from the diagnostic desk contract: this lane can never decode as qualified.
struct ExperimentalPaperStatus: Sendable {
    let roundID: String
    let cohortID: String
    let generatedAt: Date
    let brokerSymbol: String
    let feedHealth: String
    let quoteAgeSeconds: Decimal?
    let contiguousBars: Int
    let gapCount: Int
    let noTradeReason: String
    let paperTicket: IntradayPaperTicket?

    var ticketLabel: String { "Unqualified experimental paper trade" }

    private struct Raw: Decodable {
        let schemaVersion: Int
        let paperOnly: Bool
        let qualification: String
        let roundId: String
        let cohortId: String
        let generatedAt: String
        let brokerSymbol: String
        let feedHealth: String
        let quoteAgeSeconds: String?
        let contiguousBars: Int
        let gapCount: Int
        let noTradeReason: String
        let paperTicket: [String: String]?
    }

    static func decode(_ data: Data) throws -> Self {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let raw = try decoder.decode(Raw.self, from: data)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard raw.schemaVersion == 1, raw.paperOnly, raw.qualification == "unqualified",
              !raw.roundId.isEmpty, !raw.cohortId.isEmpty, !raw.brokerSymbol.isEmpty,
              ["waiting", "healthy", "stale", "closed", "error"].contains(raw.feedHealth),
              let date = formatter.date(from: raw.generatedAt) ?? ISO8601DateFormatter().date(from: raw.generatedAt),
              raw.contiguousBars >= 0, raw.gapCount >= 0, !raw.noTradeReason.isEmpty,
              raw.quoteAgeSeconds == nil || (Decimal(string: raw.quoteAgeSeconds!) ?? -1) >= 0,
              raw.paperTicket == nil || raw.paperTicket?["qualification"] == "unqualified",
              raw.paperTicket == nil || raw.paperTicket?["broker_symbol"] == raw.brokerSymbol
        else { throw CocoaError(.coderInvalidValue) }
        let ticket = try raw.paperTicket.map(IntradayPaperTicket.init(payload:))
        return Self(roundID: raw.roundId, cohortID: raw.cohortId, generatedAt: date,
                    brokerSymbol: raw.brokerSymbol, feedHealth: raw.feedHealth,
                    quoteAgeSeconds: raw.quoteAgeSeconds.flatMap { Decimal(string: $0) },
                    contiguousBars: raw.contiguousBars, gapCount: raw.gapCount,
                    noTradeReason: raw.noTradeReason, paperTicket: ticket)
    }

    func isFresh(at now: Date) -> Bool {
        let age = now.timeIntervalSince(generatedAt)
        return age >= 0 && age <= 15 && (quoteAgeSeconds ?? 999) <= 5
    }
}
