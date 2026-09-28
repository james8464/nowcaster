import AppKit
import SwiftUI

struct PaperSessionMenu: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
        TimelineView(.periodic(from: .now, by: 1)) { timeline in
            let presentation = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: timeline.date)
            Text("Paper research · \(presentation.status)")
            ForEach(presentation.assets) { row in
                Text("\(row.symbol): \(row.posture) · \(row.freshness)")
            }
        }
        PaperSessionMenuCommands(model: model, settings: settings)
        Divider()
        Button("Quit Nowcaster") { NSApplication.shared.terminate(nil) }
        }.padding().frame(width: 320)
    }
}

struct PaperSessionMenuCommands: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Button("Open Nowcaster") { openWindow(id: "main"); NSApplication.shared.activate(ignoringOtherApps: true) }
        let presentation = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: Date())
        Button(presentation.canPause ? "Pause Paper Session" : "Start Paper Session") {
            Task {
                let current = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: Date())
                if current.canPause { await model.paperSession.pause() }
                else if model.livePaperSignals.directory == nil && model.paperSession.preferences.source == nil {
                    openWindow(id: "main"); model.showingPaperSetup = true
                } else { model.paperSession.configure(settings.configuration); await model.paperSession.start() }
            }
        }.disabled(presentation.transitioning || (!presentation.canPause && model.livePaperSignals.isBusy))
        Button("Set Up Paper Desk…") { openWindow(id: "main"); model.showingPaperSetup = true }
        Button("About Paper Research…") { openWindow(id: "main"); model.showingPaperHelp = true }
    }
}

struct LiveMonitorMenu: View {
    @Bindable var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Label(model.liveMonitor.status.label, systemImage: model.liveMonitor.status.symbol)
        if let event = model.liveMonitor.latestEvent {
            Text(event.type.rawValue.replacingOccurrences(of: "_", with: " ").capitalized)
        }
        Divider()
        Button("Open Nowcaster") { openWindow(id: "main") }
        Button(model.liveMonitor.isRunning ? "Pause Monitoring" : "Monitoring Stopped") {
            model.liveMonitor.pause()
        }
        .disabled(!model.liveMonitor.isRunning)
        Divider()
        Button("Quit Nowcaster") { NSApplication.shared.terminate(nil) }
    }
}
