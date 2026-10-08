import Foundation
import SwiftUI

struct IntradayPaperTicket: Sendable, Identifiable {
    let brokerSymbol: String
    let direction: String
    let entry: Decimal
    let stop: Decimal
    let target: Decimal
    let units: Decimal
    let notionalGBP: Decimal
    let effectiveLeverage: Decimal
    let marginEstimateGBP: Decimal
    let estimatedCostGBP: Decimal
    let exitBy: String
    let costSource: String
    var id: String { brokerSymbol }

    init(payload: [String: String]) throws {
        guard let symbol = payload["broker_symbol"], !symbol.isEmpty,
              let direction = payload["direction"], ["long", "short"].contains(direction),
              let entry = payload["entry"].flatMap({ Decimal(string: $0) }), entry > 0,
              let stop = payload["stop"].flatMap({ Decimal(string: $0) }), stop > 0,
              let target = payload["target"].flatMap({ Decimal(string: $0) }), target > 0,
              let units = payload["units"].flatMap({ Decimal(string: $0) }), units > 0,
              let notional = payload["notional_gbp"].flatMap({ Decimal(string: $0) }), notional > 0,
              let leverage = payload["effective_leverage"].flatMap({ Decimal(string: $0) }), leverage > 0, leverage <= 1,
              let margin = payload["margin_estimate_gbp"].flatMap({ Decimal(string: $0) }), margin > 0,
              let cost = payload["estimated_roundtrip_cost_gbp"].flatMap({ Decimal(string: $0) }), cost >= 0,
              let exitBy = payload["exit_by"], !exitBy.isEmpty,
              let source = payload["cost_source"], !source.isEmpty,
              (direction == "long" && stop < entry && entry < target) ||
                (direction == "short" && target < entry && entry < stop)
        else { throw CocoaError(.coderInvalidValue) }
        brokerSymbol = symbol
        self.direction = direction
        self.entry = entry
        self.stop = stop
        self.target = target
        self.units = units
        notionalGBP = notional
        effectiveLeverage = leverage
        marginEstimateGBP = margin
        estimatedCostGBP = cost
        self.exitBy = exitBy
        costSource = source
    }
}

struct IntradayPaperReport: Sendable {
    struct Group: Sendable, Identifiable {
        let brokerSymbol: String
        let strategyID: String
        let direction: String
        let sessionDate: String
        let closedTrades: Int
        let netPnL: Decimal
        var id: String { "\(brokerSymbol):\(strategyID):\(direction):\(sessionDate)" }
    }

    struct ClosedRecord: Sendable, Identifiable {
        let brokerSymbol: String
        let direction: String
        let openedAt: String
        let exitPrice: Decimal
        let exitReason: String
        let netPnL: Decimal
        var id: String { "\(brokerSymbol):\(openedAt)" }
    }

    let generatedAt: Date
    let evidenceStatus: String
    let coverageStatus: String
    let accountQuoteCoverage: Decimal?
    let closedTrades: Int
    let openPositions: Int
    let decisionsCount: Int
    let noTradeCount: Int
    let blockedReasons: [String: Int]
    let feedGapCount: Int
    let grossPnL: Decimal
    let commission: Decimal
    let financing: Decimal
    let conversionFee: Decimal
    let netPnL: Decimal
    let winRate: Decimal?
    let netExpectancy: Decimal?
    let profitFactor: Decimal?
    let maximumDrawdown: Decimal
    let dailyLowerBound: Decimal?
    let warning: String
    let tickets: [IntradayPaperTicket]
    let groups: [Group]
    let closedRecords: [ClosedRecord]

    private struct Raw: Decodable {
        let schemaVersion: Int
        let paperOnly: Bool
        let generatedAt: String
        let evidenceStatus: String
        let coverageStatus: String
        let accountQuoteCoverage: String?
        let closedTrades: Int
        let openPositions: Int
        let decisionsCount: Int
        let noTradeCount: Int
        let blockedReasons: [String: Int]
        let feedGapCount: Int
        let grossPnlGbp: String
        let commissionGbp: String
        let financingGbp: String
        let conversionFeeGbp: String?
        let netPnlGbp: String
        let winRate: String?
        let netExpectancyGbp: String?
        let profitFactor: String?
        let maximumDrawdownGbp: String
        let dailyBlockLower95Gbp: String?
        let warning: String
        let unresolvedPositions: [[String: String]]?
        let groups: [RawGroup]
        let trades: [RawTrade]
    }

    private struct RawGroup: Decodable {
        let brokerSymbol: String
        let strategyId: String
        let direction: String
        let sessionDate: String
        let closedTrades: Int
        let netPnlGbp: String
    }

    private struct RawTrade: Decodable {
        let open: [String: String]
        let close: [String: String]
    }

    static func decode(_ data: Data) throws -> Self {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let raw = try decoder.decode(Raw.self, from: data)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let conversionFee = raw.conversionFeeGbp.flatMap { Decimal(string: $0) }
        guard raw.schemaVersion == 1, raw.paperOnly,
              raw.evidenceStatus == "insufficient_evidence",
              ["not_measured", "measured"].contains(raw.coverageStatus),
              raw.closedTrades >= 0, raw.openPositions >= 0,
              raw.decisionsCount >= 0, raw.noTradeCount >= 0, raw.feedGapCount >= 0,
              raw.blockedReasons.values.allSatisfy({ $0 >= 0 }),
              let generated = formatter.date(from: raw.generatedAt) ?? ISO8601DateFormatter().date(from: raw.generatedAt),
              let gross = Decimal(string: raw.grossPnlGbp),
              let commission = Decimal(string: raw.commissionGbp),
              let financing = Decimal(string: raw.financingGbp),
              raw.conversionFeeGbp == nil || conversionFee != nil,
              let net = Decimal(string: raw.netPnlGbp),
              let drawdown = Decimal(string: raw.maximumDrawdownGbp), drawdown >= 0,
              net == gross - commission - financing - (conversionFee ?? 0),
              raw.winRate == nil || Decimal(string: raw.winRate!) != nil,
              raw.netExpectancyGbp == nil || Decimal(string: raw.netExpectancyGbp!) != nil,
              raw.dailyBlockLower95Gbp == nil || Decimal(string: raw.dailyBlockLower95Gbp!) != nil,
              raw.accountQuoteCoverage == nil || Decimal(string: raw.accountQuoteCoverage!) != nil,
              raw.profitFactor == nil || Decimal(string: raw.profitFactor!) != nil,
              raw.trades.count == raw.closedTrades,
              raw.noTradeCount == raw.blockedReasons.values.reduce(0, +)
        else { throw CocoaError(.coderInvalidValue) }
        let tickets = try (raw.unresolvedPositions ?? []).map(IntradayPaperTicket.init(payload:))
        guard tickets.count == raw.openPositions else { throw CocoaError(.coderInvalidValue) }
        let groups = try raw.groups.map { row -> Group in
            guard row.closedTrades > 0, let net = Decimal(string: row.netPnlGbp) else {
                throw CocoaError(.coderInvalidValue)
            }
            return Group(brokerSymbol: row.brokerSymbol, strategyID: row.strategyId,
                         direction: row.direction, sessionDate: row.sessionDate,
                         closedTrades: row.closedTrades, netPnL: net)
        }
        let records = try raw.trades.map { row -> ClosedRecord in
            guard let symbol = row.open["broker_symbol"], let direction = row.open["direction"],
                  let openedAt = row.open["opened_at"],
                  let exitReason = row.close["exit_reason"],
                  let exitPrice = row.close["exit_price"].flatMap({ Decimal(string: $0) }),
                  let net = row.close["net_pnl_gbp"].flatMap({ Decimal(string: $0) }) else {
                throw CocoaError(.coderInvalidValue)
            }
            return ClosedRecord(brokerSymbol: symbol, direction: direction,
                                openedAt: openedAt, exitPrice: exitPrice,
                                exitReason: exitReason, netPnL: net)
        }
        return Self(generatedAt: generated, evidenceStatus: raw.evidenceStatus,
                    coverageStatus: raw.coverageStatus,
                    accountQuoteCoverage: raw.accountQuoteCoverage.flatMap { Decimal(string: $0) },
                    closedTrades: raw.closedTrades,
                    openPositions: raw.openPositions, decisionsCount: raw.decisionsCount,
                    noTradeCount: raw.noTradeCount, blockedReasons: raw.blockedReasons,
                    feedGapCount: raw.feedGapCount,
                    grossPnL: gross, commission: commission, financing: financing,
                    conversionFee: conversionFee ?? 0, netPnL: net,
                    winRate: raw.winRate.flatMap { Decimal(string: $0) },
                    netExpectancy: raw.netExpectancyGbp.flatMap { Decimal(string: $0) },
                    profitFactor: raw.profitFactor.flatMap { Decimal(string: $0) },
                    maximumDrawdown: drawdown,
                    dailyLowerBound: raw.dailyBlockLower95Gbp.flatMap { Decimal(string: $0) },
                    warning: raw.warning, tickets: tickets,
                    groups: groups, closedRecords: records)
    }

    func isFresh(at now: Date) -> Bool {
        now.timeIntervalSince(generatedAt) >= 0 && now.timeIntervalSince(generatedAt) < 120
    }
}

struct IntradayReportView: View {
    let directory: URL
    private var reportURL: URL { directory.appending(path: "report.json") }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { timeline in
            let report = (try? Data(contentsOf: reportURL, options: .mappedIfSafe)).flatMap {
                try? IntradayPaperReport.decode($0)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Cumulative paper performance").font(.headline)
                if let report {
                    Text(report.closedTrades == 0 ? "No closed paper trades yet" : "Net £\(report.netPnL.description) across \(report.closedTrades) closed trades")
                        .fontWeight(.medium)
                    Text("\(report.decisionsCount) evaluations · \(report.noTradeCount) stand-aside · \(report.openPositions) unresolved")
                        .font(.caption).foregroundStyle(.secondary)
                    if !report.isFresh(at: timeline.date) {
                        Text("Report is stale; monitoring may have stopped.")
                            .font(.caption).foregroundStyle(.orange)
                    }
                    DisclosureGroup("Costs and evidence") {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Gross £\(report.grossPnL.description) · Commission £\(report.commission.description) · Financing £\(report.financing.description) · Conversion £\(report.conversionFee.description)")
                            Text("Win rate: \(report.winRate.map { String(describing: $0 * 100) + "%" } ?? "undefined") · Net expectancy: \(report.netExpectancy.map { "£" + $0.description } ?? "undefined")")
                            Text("Profit factor: \(report.profitFactor.map(\.description) ?? "undefined") · Max drawdown £\(report.maximumDrawdown.description)")
                            Text("95% daily lower bound: \(report.dailyLowerBound.map { "£" + $0.description } ?? "insufficient sample")")
                            Text("Evidence: insufficient · Coverage: \(report.accountQuoteCoverage.map { String(describing: $0 * 100) + "%" } ?? "not measured") · Feed gaps: \(report.feedGapCount)")
                            Text(report.warning)
                        }
                        .font(.caption).foregroundStyle(.secondary)
                    }
                    if !report.blockedReasons.isEmpty {
                        DisclosureGroup("Why Nowcaster stood aside") {
                            ForEach(report.blockedReasons.keys.sorted(), id: \.self) { reason in
                                Text("\(reason.replacingOccurrences(of: "_", with: " ")) · \(report.blockedReasons[reason] ?? 0)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    if !report.groups.isEmpty {
                        DisclosureGroup("Results by product and rule") {
                            ForEach(report.groups) { group in
                                Text("\(group.brokerSymbol) · \(group.strategyID) · \(group.direction) · \(group.sessionDate): \(group.closedTrades) trades, net £\(group.netPnL.description)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    if !report.closedRecords.isEmpty {
                        DisclosureGroup("Closed paper trades") {
                            ForEach(report.closedRecords) { record in
                                Text("\(record.brokerSymbol) · \(record.direction) · opened \(record.openedAt) · \(record.exitReason) at \(record.exitPrice.description) · net £\(record.netPnL.description)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    ForEach(report.tickets) { ticket in
                        DisclosureGroup("Open paper ticket · \(ticket.brokerSymbol)") {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("\(ticket.direction.capitalized) · \(ticket.units.description) units · Entry \(ticket.entry.description)")
                                Text("Stop \(ticket.stop.description) · Target \(ticket.target.description) · Exit by \(ticket.exitBy)")
                                Text("Notional £\(ticket.notionalGBP.description) · Effective leverage \(ticket.effectiveLeverage.description)× · Margin estimate £\(ticket.marginEstimateGBP.description)")
                                Text("Estimated round-trip cost £\(ticket.estimatedCostGBP.description) · Terms: \(ticket.costSource)")
                            }
                            .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                } else {
                    Text("No validated paper report is available.").foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityIdentifier("tradeDesk.intradayReport")
        }
    }
}
