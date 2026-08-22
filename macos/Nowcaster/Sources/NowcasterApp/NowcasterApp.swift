import SwiftUI

@main
struct NowcasterApp: App {
    var body: some Scene {
        WindowGroup {
            NavigationSplitView {
                List(AppDestination.allCases) { destination in
                    Label(destination.title, systemImage: destination.symbolName)
                }
                .navigationTitle("Research")
            } detail: {
                ContentUnavailableView(
                    "Preparing Nowcaster",
                    systemImage: "waveform.path.ecg",
                    description: Text("The native research workspace is ready for its data contract.")
                )
            }
            .frame(minWidth: 1_080, minHeight: 720)
        }
        .defaultSize(width: 1_280, height: 820)

        Settings {
            Form {
                Text("Engine settings will appear after the native data bridge is configured.")
            }
            .padding()
            .frame(width: 520)
        }
    }
}
