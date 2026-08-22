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

    private var forcedColorScheme: ColorScheme? {
        if ProcessInfo.processInfo.arguments.contains("--ui-dark") { return .dark }
        if ProcessInfo.processInfo.arguments.contains("--ui-light") { return .light }
        return nil
    }

    private var defaultWindowSize: (width: CGFloat, height: CGFloat) {
        ProcessInfo.processInfo.arguments.contains("--ui-narrow") ? (1_080, 720) : (1_440, 900)
    }

    var body: some Scene {
        WindowGroup {
            RootView(model: model, settings: settings)
            .preferredColorScheme(forcedColorScheme)
            .frame(minWidth: 1_080, minHeight: 720)
        }
        .defaultSize(width: defaultWindowSize.width, height: defaultWindowSize.height)
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
