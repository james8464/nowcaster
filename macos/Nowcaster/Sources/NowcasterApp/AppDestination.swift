import Foundation

enum AppDestination: String, CaseIterable, Identifiable, Sendable {
    case tradeDesk
    case history
    case research
    case today
    case markets
    case earnings
    case signals
    case liveMonitor
    case backtests
    case strategyLab
    case modelLab
    case dataQuality
    case pipelineRuns
    case executionCenter

    var id: String { rawValue }
    static let primaryDestinations: [Self] = [.tradeDesk, .markets, .history, .research]
    static var advancedDestinations: [Self] { allCases.filter { !primaryDestinations.contains($0) } }

    var title: String {
        switch self {
        case .tradeDesk: "Trade Desk"
        case .history: "History"
        case .research: "Research"
        case .today: "Today"
        case .markets: "Markets"
        case .earnings: "Earnings"
        case .signals: "Signals"
        case .liveMonitor: "Live Monitor"
        case .backtests: "Backtests"
        case .strategyLab: "Strategy Lab"
        case .modelLab: "Model Lab"
        case .dataQuality: "Data Quality"
        case .pipelineRuns: "Pipeline Runs"
        case .executionCenter: "Execution Center"
        }
    }

    var symbolName: String {
        switch self {
        case .tradeDesk: "chart.line.uptrend.xyaxis.circle"
        case .history: "clock.arrow.circlepath"
        case .research: "flask"
        case .today: "sparkles"
        case .markets: "chart.line.uptrend.xyaxis"
        case .earnings: "calendar.badge.clock"
        case .signals: "waveform.path.ecg"
        case .liveMonitor: "dot.radiowaves.left.and.right"
        case .backtests: "chart.xyaxis.line"
        case .strategyLab: "point.3.connected.trianglepath.dotted"
        case .modelLab: "slider.horizontal.3"
        case .dataQuality: "checkmark.shield"
        case .pipelineRuns: "clock.arrow.trianglehead.counterclockwise.rotate.90"
        case .executionCenter: "shield.lefthalf.filled.badge.checkmark"
        }
    }
}
