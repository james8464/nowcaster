import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct TradeDeskView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    @State private var selectedSymbol: String?
    @State private var importing = false
    @State private var calendarImport = false
    @State private var importMessage: String?

    var body: some View {
        GeometryReader { geometry in
            if geometry.size.width >= 850, let selectedSymbol {
                HSplitView {
                    desk.frame(minWidth: 410)
                    ScrollView { TradeAssetDetailView(model: model, symbol: selectedSymbol) { self.selectedSymbol = nil } }
                        .frame(minWidth: 280, idealWidth: 350, maxWidth: 500)
                }
            } else {
                ScrollView {
                    if let selectedSymbol {
                        TradeAssetDetailView(model: model, symbol: selectedSymbol) { self.selectedSymbol = nil }
                        Divider()
                    }
                    desk
                }
            }
        }
        .accessibilityIdentifier("tradeDesk.workspace")
        .fileImporter(isPresented: $importing, allowedContentTypes: calendarImport ? [.json] : [.folder]) { result in
            switch result {
            case let .success(url):
                importMessage = nil
                Task {
                    if calendarImport { await model.livePaperSignals.importCalendar(url) }
                    else {
                        model.paperSession.configure(settings.configuration)
                        await model.paperSession.chooseSource(directory: url)
                    }
                }
            case let .failure(error): importMessage = error.localizedDescription
            }
        }
        .task {
            model.paperSession.configure(settings.configuration)
            await model.paperSession.loadSelectedSource(defaultDirectory: LivePaperSignalService.defaultDirectory)
        }
    }

    private var desk: some View {
        VStack(alignment: .leading, spacing: 20) {
            PaperSessionHeader(model: model, settings: settings)
            IntradayResearchView(service: model.oandaPaper)
            DiagnosticWorkflowView(service: model.livePaperSignals,
                transitioning: model.paperSession.state == .starting || model.paperSession.state == .pausing)
            HStack {
                Text("Connected market coverage").fontWeight(.semibold)
                Spacer()
                Button("Set Up…") { model.showingPaperSetup = true }
                    .accessibilityIdentifier("tradeDesk.setup")
                Menu("Data") {
                    Button("Choose Research Folder…") { calendarImport = false; importing = true }
                        .disabled(!model.paperSession.canSelectSource || model.livePaperSignals.isBusy)
                    Button("Import Calendar…") { calendarImport = true; importing = true }
                        .disabled(model.livePaperSignals.directory == nil || model.livePaperSignals.isBusy)
                    if let directory = model.livePaperSignals.directory {
                        Button("Show Evidence Folder") { NSWorkspace.shared.open(directory) }
                    }
                }.accessibilityIdentifier("tradeDesk.data")
            }
            Text("Separate legacy study · Bitcoin and Ether · Binance spot · long / stand aside").foregroundStyle(.secondary)
            TimelineView(.periodic(from: .now, by: 1)) { timeline in
                let presentation = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: timeline.date)
                VStack(spacing: 0) {
                    ForEach(presentation.assets) { row in
                        Button { selectedSymbol = row.symbol } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                HStack {
                                    Text(row.symbol).fontWeight(.semibold)
                                    Spacer()
                                    Label(row.posture, systemImage: row.detail == nil ? "pause.circle" : "flask")
                                }
                                Text("\(row.source) · \(row.freshness)").foregroundStyle(.secondary)
                                Text("Trend: \(row.trend)").foregroundStyle(.secondary)
                                Text(row.reason).fixedSize(horizontal: false, vertical: true)
                            }.padding(14).frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .background(selectedSymbol == row.symbol ? Color.accentColor.opacity(0.10) : Color(nsColor: .controlBackgroundColor))
                        .accessibilityIdentifier("tradeDesk.asset.\(row.symbol)")
                        .accessibilityLabel("\(row.symbol), \(row.posture), \(row.source), \(row.freshness), trend \(row.trend). Show details")
                        Divider()
                    }
                }
            }
            if let message = importMessage ?? model.livePaperSignals.message {
                Label(message, systemImage: message.hasPrefix("Calendar evidence retained") ? "checkmark.circle" : "info.circle")
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Button("About This Research…") { model.showingPaperHelp = true }
            Spacer(minLength: 0)
        }.padding(20)
    }
}

struct PaperSessionHeader: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Paper research").fontWeight(.semibold)
                    Text(TradeDeskPresentation.statusTitle(model.paperSession.state)).accessibilityIdentifier("paperSession.status")
                }
                Spacer()
                if model.paperSession.state == .starting || model.paperSession.state == .pausing {
                    ProgressView().controlSize(.small).accessibilityLabel("Session operation in progress")
                }
                PaperSessionAction(model: model, settings: settings)
            }
            if model.backgroundResearch.isPreparing {
                Text("Preparing the registered research workspace. First startup can take about 25 seconds; Pause remains available.")
            } else if model.backgroundResearch.isStarting {
                Text("Starting the research worker and checking ownership…")
            }
            if case let .blocked(reason) = model.paperSession.state {
                Label(reason, systemImage: "exclamationmark.circle")
            } else if let explanation = model.paperSession.explanation {
                Label(explanation, systemImage: "info.circle")
            }
            if model.livePaperSignals.directory == nil {
                Text("Set up a desk to retain public observations.").foregroundStyle(.secondary)
            }
        }.fixedSize(horizontal: false, vertical: true)
    }
}

struct PaperSessionAction: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    var body: some View {
        let presentation = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: Date())
        Button(presentation.action, systemImage: presentation.canPause ? "pause.fill" : "play.fill") {
            Task {
                let current = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: Date())
                if current.canPause { await model.paperSession.pause() }
                else if model.livePaperSignals.directory == nil && model.paperSession.preferences.source == nil { model.showingPaperSetup = true }
                else { model.paperSession.configure(settings.configuration); await model.paperSession.start() }
            }
        }
        .buttonStyle(.borderedProminent)
        .disabled(presentation.transitioning || (!presentation.canPause && model.livePaperSignals.isBusy))
        .accessibilityIdentifier("paperSession.action")
    }
}
