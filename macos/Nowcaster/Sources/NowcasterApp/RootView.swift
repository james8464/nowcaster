import AppKit
import SwiftUI

extension Notification.Name {
    static let focusGlobalSearch = Notification.Name("Nowcaster.focusGlobalSearch")
}

enum RootSidebarPresentation {
    static let sectionHeaderLeadingPadding: CGFloat = 24
}

struct RootView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    @SceneStorage("Nowcaster.destination") private var storedDestination = AppDestination.today.rawValue
    @FocusState private var searchIsFocused: Bool

    var body: some View {
        navigationLayout
        .navigationSplitViewStyle(.balanced)
        .searchable(text: $model.searchText, placement: .toolbar, prompt: "Search symbols")
        .searchFocused($searchIsFocused)
        .searchSuggestions {
            ForEach(model.searchResults) { instrument in
                Button {
                    model.selectSearchResult(instrument)
                } label: {
                    Label("\(instrument.symbol) — \(instrument.displayName)", systemImage: "chart.line.uptrend.xyaxis")
                }
            }
        }
        .toolbar { toolbar }
        .task {
            let screenshotMode = ProcessInfo.processInfo.arguments.contains { $0.hasPrefix("--destination=") }
            if !screenshotMode, let destination = AppDestination(rawValue: storedDestination) {
                model.destination = destination
            }
            await model.loadBundledSnapshot()
            if screenshotMode {
                let presentation = NowcasterWindowPresentation(arguments: ProcessInfo.processInfo.arguments)
                NSApplication.shared.keyWindow?.setContentSize(
                    NSSize(width: presentation.defaultWidth, height: presentation.defaultHeight)
                )
                NSApplication.shared.keyWindow?.center()
            }
        }
        .onChange(of: model.destination) { _, destination in storedDestination = destination.rawValue }
        .onReceive(NotificationCenter.default.publisher(for: .focusGlobalSearch)) { _ in searchIsFocused = true }
    }

    private var usesInspector: Bool {
        [.markets, .earnings, .signals, .backtests, .strategyLab].contains(model.destination)
    }

    @ViewBuilder private var navigationLayout: some View {
        if usesInspector {
            NavigationSplitView {
                sidebar
            } content: {
                destinationContent
                    .navigationTitle(model.destination.title)
                    .navigationSplitViewColumnWidth(min: 260, ideal: 300, max: 340)
            } detail: {
                inspector
            }
        } else {
            NavigationSplitView {
                sidebar
            } detail: {
                destinationContent.navigationTitle(model.destination.title)
            }
        }
    }

    private var sidebar: some View {
        List(selection: $model.destination) {
            Section {
                destinationRow(.today)
                destinationRow(.markets)
                destinationRow(.earnings)
                destinationRow(.signals)
            } header: {
                sidebarSectionHeader("Monitor")
            }
            Section {
                destinationRow(.backtests)
                destinationRow(.strategyLab)
                destinationRow(.modelLab)
            } header: {
                sidebarSectionHeader("Research")
            }
            Section {
                destinationRow(.dataQuality)
                destinationRow(.pipelineRuns)
            } header: {
                sidebarSectionHeader("System")
            }
        }
        .navigationTitle("Research")
        .navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 280)
        .accessibilityIdentifier("sidebar")
    }

    private func sidebarSectionHeader(_ title: String) -> some View {
        Text(title).padding(.leading, RootSidebarPresentation.sectionHeaderLeadingPadding)
    }

    private func destinationRow(_ destination: AppDestination) -> some View {
        Label(destination.title, systemImage: destination.symbolName)
            .tag(destination)
            .accessibilityIdentifier("sidebar.\(destination.rawValue)")
    }

    @ViewBuilder private var destinationContent: some View {
        if let snapshot = model.snapshot {
            switch model.destination {
            case .today:
                TodayView(snapshot: snapshot, selectSignal: model.selectSignal)
            case .markets:
                MarketsView(model: model, snapshot: snapshot)
            case .earnings:
                EarningsView(model: model, snapshot: snapshot)
            case .signals:
                SignalsView(model: model, snapshot: snapshot)
            case .backtests:
                BacktestsView(model: model, snapshot: snapshot)
            case .strategyLab:
                StrategyLabView(model: model, settings: settings, snapshot: snapshot)
            case .modelLab:
                ModelLabView(snapshot: snapshot)
            case .dataQuality:
                DataQualityView(snapshot: snapshot)
            case .pipelineRuns:
                PipelineRunsView(model: model, settings: settings, snapshot: snapshot)
            }
        } else {
            switch model.loadState {
            case .loading:
                ProgressView("Loading research snapshot…").frame(maxWidth: .infinity, maxHeight: .infinity)
            case let .incompatible(version):
                EmptyStateView(
                    title: "Snapshot needs an update",
                    systemImage: "arrow.triangle.2.circlepath",
                    description: "Schema \(version) is incompatible. Rebuild the native snapshot."
                )
            case let .failure(message):
                EmptyStateView(title: "Research data unavailable", systemImage: "exclamationmark.icloud", description: message)
            default:
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    @ViewBuilder private var inspector: some View {
        switch model.destination {
        case .markets:
            if let instrument = model.selectedInstrument {
                InstrumentDetailView(instrument: instrument)
            } else {
                selectionPlaceholder("Select an instrument to inspect price history and market context.")
            }
        case .earnings:
            if let forecast = model.selectedEarnings {
                EarningsDetailView(forecast: forecast)
            } else {
                selectionPlaceholder("Select a forecast to compare the model, expectation source, and actual.")
            }
        case .signals:
            if let signal = model.selectedSignal {
                SignalDetailView(signal: signal)
            } else {
                selectionPlaceholder("Select a signal to inspect evidence, catalyst, and invalidation.")
            }
        case .backtests:
            if let backtest = model.selectedBacktest {
                BacktestDetailView(backtest: backtest)
            } else {
                selectionPlaceholder("Select a backtest to inspect final-test evidence, robustness, and assumptions.")
            }
        case .strategyLab:
            if let snapshot = model.snapshot,
               let strategy = model.selectedStrategy,
               let presentation = StrategyLabPresentation(snapshot: snapshot)
                .strategies.first(where: { $0.id == strategy.id }) {
                StrategyDetailView(presentation: presentation)
                    .navigationSplitViewColumnWidth(min: 360, ideal: 480, max: 600)
            } else {
                selectionPlaceholder("Select one or more strategies; the first selected row appears here for inspection.")
            }
        default:
            selectionPlaceholder("Select a row to inspect its research evidence.")
        }
    }

    private func selectionPlaceholder(_ description: String) -> some View {
        ContentUnavailableView("No Selection", systemImage: "sidebar.right", description: Text(description))
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if model.isRunningJob {
                ProgressView().controlSize(.small).accessibilityLabel("Research job running")
            }
            ResearchStatusLabel(title: model.dataModeLabel, systemImage: "externaldrive", color: .secondary)
            if let refreshed = model.snapshot?.metadata.lastRefresh {
                Text(refreshed, style: .relative).font(.caption).foregroundStyle(.secondary)
            }
            Menu {
                Button("Rebuild all research") { Task { await model.run(.rebuildAll, configuration: settings.configuration) } }
                Button("Run full backtest") { Task { await model.run(.fullBacktest, configuration: settings.configuration) } }
                Button("Export snapshot") { Task { await model.run(.exportSnapshot, configuration: settings.configuration) } }
            } label: {
                Label("Research actions", systemImage: "ellipsis.circle")
            }
            Button {
                Task { await model.run(.rebuildAll, configuration: settings.configuration) }
            } label: {
                Label("Refresh research", systemImage: "arrow.clockwise")
            }
            .disabled(model.isRunningJob)
            .accessibilityIdentifier("toolbar.refresh")
        }
    }
}
