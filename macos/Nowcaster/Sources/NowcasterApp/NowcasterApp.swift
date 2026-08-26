import AppKit
import SwiftUI

@MainActor
final class NowcasterApplicationDelegate: NSObject, NSApplicationDelegate {
    weak var liveMonitor: LiveMonitorController?
    private var terminationPending = false

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let liveMonitor, liveMonitor.isRunning else { return .terminateNow }
        guard !terminationPending else { return .terminateLater }
        terminationPending = true
        Task { @MainActor in
            await liveMonitor.shutdownForApplicationTermination()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

struct NowcasterWindowPresentation: Sendable {
    let defaultWidth: CGFloat
    let defaultHeight: CGFloat
    let minimumWidth: CGFloat = 820
    let minimumHeight: CGFloat = 620

    init(arguments: [String]) {
        if arguments.contains("--ui-narrow") {
            defaultWidth = 900
            defaultHeight = 700
        } else {
            defaultWidth = 1_440
            defaultHeight = 900
        }
    }

    @MainActor func apply(to window: NSWindow) {
        window.setContentSize(NSSize(width: defaultWidth, height: defaultHeight))
        window.center()
    }
}

@main
struct NowcasterApp: App {
    @NSApplicationDelegateAdaptor(NowcasterApplicationDelegate.self) private var appDelegate
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

    private var windowPresentation: NowcasterWindowPresentation {
        NowcasterWindowPresentation(arguments: ProcessInfo.processInfo.arguments)
    }

    var body: some Scene {
        WindowGroup(id: "main") {
            RootView(model: model, settings: settings)
            .onAppear { appDelegate.liveMonitor = model.liveMonitor }
            .preferredColorScheme(forcedColorScheme)
            .frame(minWidth: windowPresentation.minimumWidth, minHeight: windowPresentation.minimumHeight)
        }
        .defaultSize(width: windowPresentation.defaultWidth, height: windowPresentation.defaultHeight)
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
                    Task { await model.run(.exportSnapshot(databaseURL: nil), configuration: settings.configuration) }
                }
                .keyboardShortcut("e", modifiers: [.command, .shift])
            }
        }

        MenuBarExtra("Nowcaster Live Monitor", systemImage: model.liveMonitor.status.symbol) {
            LiveMonitorMenu(model: model)
        }

        Settings {
            SettingsView(settings: settings)
        }
    }
}
