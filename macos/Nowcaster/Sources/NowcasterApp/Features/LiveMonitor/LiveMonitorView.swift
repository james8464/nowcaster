import SwiftUI

struct LiveMonitorView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    @State private var permissionMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if model.liveMonitor.events.isEmpty {
                ContentUnavailableView(
                    "No Live Events",
                    systemImage: "dot.radiowaves.left.and.right",
                    description: Text("Start the monitor to warm finalized market bars. It will abstain unless a strategy has passed every evidence gate.")
                )
            } else {
                List(model.liveMonitor.events.reversed()) { event in
                    eventRow(event)
                }
                .accessibilityIdentifier("liveMonitor.eventList")
            }
        }
        .safeAreaInset(edge: .bottom) {
            Text("Research notifications only — Nowcaster cannot place orders or guarantee profit. Monitoring stops while this Mac sleeps, is offline, or the app is quit.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .padding(12)
                .frame(maxWidth: .infinity)
                .background(.thinMaterial)
        }
    }

    private var header: some View {
        HStack(spacing: 16) {
            Label(model.liveMonitor.status.label, systemImage: model.liveMonitor.status.symbol)
                .font(.headline)
                .accessibilityLabel("Live Monitor status: \(model.liveMonitor.status.label)")
            VStack(alignment: .leading) {
                Text("\(settings.normalizedStocks.count) stocks · \(settings.normalizedCrypto.count) crypto")
                Text("Confirmed 5-minute decisions · 1-minute risk monitoring")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let permissionMessage { Text(permissionMessage).font(.caption).foregroundStyle(.secondary) }
            Button(model.liveMonitor.isRunning ? "Pause" : "Start Monitoring") {
                Task { await toggleMonitoring() }
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("liveMonitor.toggle")
        }
        .padding(20)
    }

    @ViewBuilder private func eventRow(_ event: LiveMonitorEvent) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon(for: event.type)).frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                Text(title(for: event)).fontWeight(.medium)
                Text(summary(for: event)).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
            Spacer()
            Text(event.emittedAt, style: .time).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }

    private func toggleMonitoring() async {
        if model.liveMonitor.isRunning {
            model.liveMonitor.pause()
            return
        }
        permissionMessage = await model.liveMonitor.requestNotificationPermission() ? nil : "Notifications are disabled"
        let credentials = try? BrokerCredentialVault().loadForSession(environment: .paper)
        await model.liveMonitor.start(
            configuration: .appConfiguration(settings: settings, snapshot: model.snapshot),
            credentials: credentials
        )
    }

    private func icon(for type: LiveMonitorEventType) -> String {
        switch type {
        case .decision, .notificationRequest: "bell.badge"
        case .barFinalized: "chart.bar"
        case .quote: "dollarsign.arrow.circlepath"
        case .providerHealth, .heartbeat, .ready: "antenna.radiowaves.left.and.right"
        case .lifecycleTransition: "point.topleft.down.to.point.bottomright.curvepath"
        case .configurationRejected, .fatalError: "exclamationmark.triangle"
        }
    }

    private func title(for event: LiveMonitorEvent) -> String {
        event.type.rawValue.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func summary(for event: LiveMonitorEvent) -> String {
        let symbol = event.payload["symbol"]?.stringValue
        let status = event.payload["status"]?.stringValue
        let reason = event.payload["reason"]?.stringValue
        return [symbol, status, reason].compactMap { $0 }.joined(separator: " · ").nonempty ?? "Live monitor event recorded"
    }
}

private extension String {
    var nonempty: String? { isEmpty ? nil : self }
}
