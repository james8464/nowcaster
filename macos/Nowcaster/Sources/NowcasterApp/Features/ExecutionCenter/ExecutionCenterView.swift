import SwiftUI

struct ExecutionCenterView: View {
    let snapshot: NowcasterSnapshot

    private var presentation: ExecutionPresentation { ExecutionPresentation(snapshot: snapshot) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                statusCards
                readiness
                activity
            }
            .padding(24)
            .frame(maxWidth: 1_100, alignment: .leading)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .accessibilityIdentifier("executionCenter")
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 16) {
            Image(systemName: presentation.isFrozen ? "snowflake" : "lock.shield")
                .font(.system(size: 30, weight: .semibold))
                .foregroundStyle(presentation.isFrozen ? .orange : .secondary)
                .frame(width: 44, height: 44)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            VStack(alignment: .leading, spacing: 5) {
                Text(presentation.stateTitle).font(.largeTitle.bold())
                Text(presentation.summary).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer()
            Text(presentation.environment)
                .font(.callout.weight(.semibold))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(.quaternary, in: Capsule())
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(presentation.stateTitle). \(presentation.summary)")
    }

    private var statusCards: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 14)], spacing: 14) {
            statusCard("Broker", value: presentation.accountLabel, icon: "building.columns")
            statusCard("Reconciliation", value: presentation.reconciliationLabel, icon: "arrow.triangle.2.circlepath")
            statusCard(
                "Forward evidence",
                value: "\(presentation.observedPeriods) periods • \(presentation.closedTrades) closed trades",
                icon: "calendar.badge.clock"
            )
            statusCard(
                "Emergency state",
                value: presentation.isFrozen ? "Frozen — new orders blocked" : "Monitoring — no freeze active",
                icon: presentation.isFrozen ? "snowflake" : "checkmark.shield"
            )
        }
    }

    private func statusCard(_ title: String, value: String, icon: String) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Label(title, systemImage: icon).font(.headline)
                Text(value).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, minHeight: 76, alignment: .topLeading)
        }
        .accessibilityElement(children: .combine)
    }

    private var readiness: some View {
        GroupBox("Live-readiness gates") {
            VStack(spacing: 0) {
                ForEach(Array(presentation.readinessGates.enumerated()), id: \.element.id) { index, gate in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Image(systemName: gate.passed ? "checkmark.circle.fill" : "lock.circle")
                            .foregroundStyle(gate.passed ? .green : .secondary)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(gate.name.replacingOccurrences(of: "_", with: " ").capitalized).fontWeight(.medium)
                            Text(gate.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(gate.passed ? "Passed" : "Locked").font(.caption.weight(.semibold))
                    }
                    .padding(.vertical, 10)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("\(gate.name), \(gate.passed ? "passed" : "locked"). \(gate.detail)")
                    if index < presentation.readinessGates.count - 1 { Divider() }
                }
            }
        }
    }

    @ViewBuilder private var activity: some View {
        if let orders = snapshot.brokerOrders, !orders.isEmpty {
            GroupBox("Recent paper orders") {
                Table(orders) {
                    TableColumn("Symbol", value: \.symbol)
                    TableColumn("Side", value: \.side)
                    TableColumn("Status", value: \.status)
                    TableColumn("Quantity") { Text($0.quantity, format: .number) }
                }
                .frame(minHeight: 180)
            }
        } else {
            ContentUnavailableView(
                "No broker activity",
                systemImage: "tray",
                description: Text("Start in Shadow, then collect reconciled paper evidence. Live remains locked.")
            )
            .frame(minHeight: 180)
        }
    }
}
