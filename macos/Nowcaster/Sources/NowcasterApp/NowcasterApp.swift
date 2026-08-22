import AppKit
import SwiftUI

@main
struct NowcasterApp: App {
    @State private var settings = AppSettings()
    @State private var model = AppModel()

    init() {
        guard let iconURL = Bundle.module.url(forResource: "AppIcon", withExtension: "png"),
              let icon = NSImage(contentsOf: iconURL)
        else { return }
        NSApplication.shared.applicationIconImage = icon
    }

    var body: some Scene {
        WindowGroup {
            RootView(model: model, settings: settings)
            .frame(minWidth: 1_080, minHeight: 720)
        }
        .defaultSize(width: 1_280, height: 820)
        .commands {
            SidebarCommands()
            CommandMenu("Research") {
                Button("Refresh Research") {
                    Task { await model.run(.rebuildAll, configuration: settings.configuration) }
                }
                .keyboardShortcut("r")
                Button("Search Symbols") {
                    NotificationCenter.default.post(name: .focusGlobalSearch, object: nil)
                }
                .keyboardShortcut("f")
                Divider()
                Button("Run Full Backtest") {
                    Task { await model.run(.fullBacktest, configuration: settings.configuration) }
                }
                .keyboardShortcut("b", modifiers: [.command, .shift])
                Button("Export Snapshot") {
                    Task { await model.run(.exportSnapshot, configuration: settings.configuration) }
                }
                .keyboardShortcut("e", modifiers: [.command, .shift])
            }
        }

        Settings {
            SettingsView(settings: settings)
        }
    }
}
