import AppKit
import Foundation
import SwiftUI

struct IntradayDeskStatus: Decodable {
    struct Market: Decodable, Identifiable {
        let market: String
        let brokerSymbol: String?
        let product: String?
        let eligibility: String
        let reason: String?
        var id: String { market }
    }
    struct Opportunity: Decodable, Identifiable {
        let market: String
        let brokerSymbol: String
        let strategyId: String
        let direction: String
        let decidedAt: String
        let entryAt: String
        let entry: String
        let stop: String
        let target: String
        let exitBy: String
        let estimatedRoundtripCost: String
        let explanation: String
        let paperOnly: Bool
        var id: String { "\(brokerSymbol):\(strategyId):\(exitBy)" }
    }
    struct Position: Decodable, Identifiable {
        let market: String
        let brokerSymbol: String
        let direction: String
        let entry: String
        let stop: String
        let target: String
        let exitBy: String
        let paperOnly: Bool
        var id: String { brokerSymbol }
    }
    let schemaVersion: Int
    let paperOnly: Bool
    let generatedAt: String
    let feedHealth: String
    let evidenceStatus: String
    let markets: [Market]
    let opportunities: [Opportunity]
    let paperPositions: [Position]
    let noTradeReason: String

    static func decode(_ data: Data) throws -> Self {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let value = try decoder.decode(Self.self, from: data)
        guard value.schemaVersion == 1, value.paperOnly,
              ["not_configured", "inventory_verified", "healthy", "stale", "error"].contains(value.feedHealth),
              ["not_supported", "under_observation", "prospectively_supported"].contains(value.evidenceStatus),
              (value.opportunities.isEmpty || value.feedHealth == "healthy"),
              (!value.opportunities.isEmpty || !value.noTradeReason.isEmpty),
              value.opportunities.allSatisfy(\.paperOnly),
              value.paperPositions.allSatisfy(\.paperOnly),
              value.opportunities.allSatisfy({ idea in
                  guard let entry = Decimal(string: idea.entry), let stop = Decimal(string: idea.stop),
                        let target = Decimal(string: idea.target), entry > 0, stop > 0, target > 0,
                        value.markets.contains(where: { $0.market == idea.market && $0.brokerSymbol == idea.brokerSymbol && ["diagnostic", "paper_eligible"].contains($0.eligibility) }) else { return false }
                  return idea.direction == "long" ? stop < entry && entry < target :
                      idea.direction == "short" && target < entry && entry < stop
              }) else {
            throw CocoaError(.coderInvalidValue)
        }
        return value
    }

    func isFresh(at now: Date) -> Bool {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = formatter.date(from: generatedAt) ?? ISO8601DateFormatter().date(from: generatedAt)
        guard let date else { return false }
        return now.timeIntervalSince(date) >= 0 && now.timeIntervalSince(date) < 120
    }
}

struct IntradayResearchView: View {
    let service: OandaPaperService
    private var directory: URL { AppStorageLocations.root.appending(path: "IntradayResearch", directoryHint: .isDirectory) }
    private var statusURL: URL { directory.appending(path: "summary.json") }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { timeline in
            let status = (try? Data(contentsOf: statusURL, options: .mappedIfSafe)).flatMap { try? IntradayDeskStatus.decode($0) }
            let fresh = status?.isFresh(at: timeline.date) ?? false
            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Label("Live practice indicator", systemImage: "chart.xyaxis.line")
                            .font(.headline)
                        Spacer()
                        Text("PAPER ONLY").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    }
                    HStack {
                        Button(service.isRunning ? "Pause Monitoring" : "Start Monitoring") {
                            if service.isRunning { service.pause() } else { service.start() }
                        }
                        .accessibilityIdentifier("tradeDesk.oandaMonitoring")
                        if service.isRunning {
                            Label("Running with window closed", systemImage: "circle.fill")
                                .font(.caption).foregroundStyle(.green)
                        }
                    }
                    if let message = service.message { Text(message).font(.caption).foregroundStyle(.secondary) }
                    Text("Germany 30 demo · US 500 · EUR/USD · West Texas oil")
                        .foregroundStyle(.secondary)
                    Text("Exact practice products are checked on Start. Without verified costs and a selected rule, setups are diagnostic only and no paper trade is opened.")
                        .font(.caption).foregroundStyle(.secondary)
                    if let status {
                        Label(fresh ? status.feedHealth.replacingOccurrences(of: "_", with: " ").capitalized : "Status is stale — stand aside",
                              systemImage: fresh && status.feedHealth == "healthy" ? "checkmark.circle" : "pause.circle")
                            .foregroundStyle(.secondary)
                        if fresh && status.feedHealth == "healthy" {
                            ForEach(status.opportunities) { idea in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("Experimental \(idea.direction.capitalized) · \(idea.brokerSymbol)").fontWeight(.semibold)
                                    Text("Entry \(idea.entry) · Stop \(idea.stop) · Target \(idea.target) · Exit by \(idea.exitBy)")
                                        .font(.caption.monospacedDigit())
                                    Text("Decided \(idea.decidedAt) · Paper entry \(idea.entryAt) · Estimated round-trip cost \(idea.estimatedRoundtripCost)")
                                        .font(.caption).foregroundStyle(.secondary)
                                    Text(idea.explanation).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        ForEach(status.paperPositions) { position in
                            Text("Open paper position: \(position.direction) \(position.brokerSymbol) · exit by \(position.exitBy)")
                                .font(.caption)
                            if !fresh || status.feedHealth != "healthy" {
                                Text("Feed unavailable; this paper position may be unresolved.")
                                    .font(.caption).foregroundStyle(.orange)
                            }
                        }
                        if !fresh || status.opportunities.isEmpty {
                            Text(fresh ? status.noTradeReason : "No fresh demo-feed assessment is available. No trade.")
                                .foregroundStyle(.secondary)
                        }
                        Text("Evidence: \(status.evidenceStatus.replacingOccurrences(of: "_", with: " ").capitalized)")
                            .font(.caption).foregroundStyle(.secondary)
                        DisclosureGroup("Market checks") {
                            ForEach(status.markets) { market in
                                Text("\(market.market) · \(market.brokerSymbol ?? "unconfirmed") · \(market.eligibility.replacingOccurrences(of: "_", with: " "))")
                                    .font(.caption)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                if let reason = market.reason {
                                    Text(reason).font(.caption).foregroundStyle(.secondary)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        }.font(.caption)
                        if FileManager.default.fileExists(atPath: directory.path) {
                            Button("Show Research Folder") { NSWorkspace.shared.open(directory) }
                                .buttonStyle(.link)
                        }
                    } else {
                        Text("No OANDA practice demo feed is configured. No trade.")
                            .foregroundStyle(.secondary)
                    }
                    Text("Historical candle simulations are exploratory. Paper results are not a real-money recommendation.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .accessibilityIdentifier("tradeDesk.intradayResearch")
        }
    }
}
