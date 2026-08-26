import AppKit
import SwiftUI

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
