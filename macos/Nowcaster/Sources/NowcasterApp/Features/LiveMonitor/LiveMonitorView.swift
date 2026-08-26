import SwiftUI

struct LiveMonitorView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    @State private var permissionMessage: String?
    @State private var setupToTrack: LiveSetup?
    @State private var fillPrice = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if model.liveMonitor.events.isEmpty && model.liveMonitor.activeSetups.isEmpty {
                ContentUnavailableView(
                    "No Live Events",
                    systemImage: "dot.radiowaves.left.and.right",
                    description: Text("Start the monitor to warm finalized market bars. It will abstain unless a strategy has passed every evidence gate.")
                )
            } else {
                List {
                    if !model.liveMonitor.activeSetups.isEmpty {
                        Section("Active setups") {
                            ForEach(model.liveMonitor.activeSetups) { setup in setupRow(setup) }
                        }
                    }
                    if !latestAbstentions.isEmpty {
                        Section("Why Nowcaster is abstaining") {
                            ForEach(latestAbstentions) { event in eventRow(event) }
                        }
                    }
                    Section("Recent monitor activity") {
                        ForEach(model.liveMonitor.events.reversed().prefix(100)) { event in
                            eventRow(event)
                        }
                    }
                }
                .accessibilityIdentifier("liveMonitor.eventList")
            }
        }
        .sheet(item: $setupToTrack) { event in
            VStack(alignment: .leading, spacing: 16) {
                Text("Track a hypothetical fill").font(.title2).fontWeight(.semibold)
                Text("Enter the price you actually received. This records monitoring state only; it never places an order.")
                    .foregroundStyle(.secondary)
                TextField("Fill price", text: $fillPrice).textFieldStyle(.roundedBorder)
                HStack {
                    Spacer()
                    Button("Cancel") { setupToTrack = nil }
                    Button("Track") {
                        if let decimal = Decimal(string: fillPrice), decimal > 0 {
                            model.liveMonitor.track(setupID: event.id, actualFill: decimal)
                            setupToTrack = nil
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(Decimal(string: fillPrice).map { $0 <= 0 } ?? true)
                }
            }
            .padding(24)
            .frame(width: 420)
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

    private func setupRow(_ setup: LiveSetup) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(
                    "\(setup.symbol) · \(setup.posture.capitalized)",
                    systemImage: setup.posture == "short" ? "arrow.down.right" : "arrow.up.right"
                )
                .font(.headline)
                Spacer()
                Button(setup.state == "tracked" ? "Tracked" : "Track Fill") {
                    fillPrice = setup.actualFill ?? setup.entryLow
                    setupToTrack = setup
                }
                .buttonStyle(.bordered)
                .disabled(setup.state == "tracked")
            }
            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 4) {
                GridRow { Text("Entry").foregroundStyle(.secondary); Text("\(setup.entryLow)–\(setup.entryHigh)") }
                GridRow { Text("Protective stop").foregroundStyle(.secondary); Text(setup.stop) }
                GridRow { Text("Targets").foregroundStyle(.secondary); Text("\(setup.target1) / \(setup.target2)") }
            }
            .font(.callout.monospacedDigit())
            Text("Review independently before acting. Levels are hypothetical and may become invalid.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 6)
    }

    private var latestAbstentions: [LiveMonitorEvent] {
        model.liveMonitor.events.reversed().filter {
            $0.type == .decision && $0.payload["status"]?.stringValue == "abstain"
        }.prefix(5).map { $0 }
    }

    private func toggleMonitoring() async {
        if model.liveMonitor.isRunning {
            model.liveMonitor.pause()
            return
        }
        permissionMessage = await model.liveMonitor.requestNotificationPermission() ? nil : "Notifications are disabled"
        model.liveMonitor.configureNotifications(
            quietEntries: settings.silenceEntryNotifications,
            enabledCategories: enabledNotificationCategories
        )
        let credentials = try? BrokerCredentialVault().loadForSession(environment: .paper)
        await model.liveMonitor.start(
            configuration: .appConfiguration(settings: settings, snapshot: model.snapshot),
            credentials: credentials
        )
    }

    private var enabledNotificationCategories: Set<LiveNotificationCategory> {
        var result: Set<LiveNotificationCategory> = [.entry, .health]
        if settings.targetNotifications { result.insert(.target) }
        if settings.stopNotifications { result.insert(.stop) }
        if settings.closeNotifications { result.insert(.close) }
        return result
    }

    private func icon(for type: LiveMonitorEventType) -> String {
        switch type {
        case .decision, .notificationRequest: "bell.badge"
        case .setupSnapshot: "rectangle.stack.badge.person.crop"
        case .barFinalized: "chart.bar"
        case .quote: "dollarsign.arrow.circlepath"
        case .providerHealth, .heartbeat, .ready: "antenna.radiowaves.left.and.right"
        case .lifecycleTransition: "point.topleft.down.to.point.bottomright.curvepath"
        case .controlAck: "checkmark.message"
        case .configurationRejected, .fatalError: "exclamationmark.triangle"
        }
    }

    private func title(for event: LiveMonitorEvent) -> String {
        event.type.rawValue.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func summary(for event: LiveMonitorEvent) -> String {
        if event.type == .notificationRequest, let body = event.payload["body"]?.stringValue {
            return body
        }
        let symbol = event.payload["symbol"]?.stringValue
        let status = event.payload["status"]?.stringValue
        let reason = event.payload["reason"]?.stringValue
        return [symbol, status, reason].compactMap { $0 }.joined(separator: " · ").nonempty ?? "Live monitor event recorded"
    }
}

private extension String {
    var nonempty: String? { isEmpty ? nil : self }
}
