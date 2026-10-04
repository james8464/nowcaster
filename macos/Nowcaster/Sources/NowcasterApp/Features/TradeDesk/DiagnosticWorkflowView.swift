import SwiftUI

struct DiagnosticWorkflowView: View {
    @Bindable var service: LivePaperSignalService
    let transitioning: Bool

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { timeline in
            let workflow = service.workflow
            let presentation = DiagnosticWorkflowPresentation.make(workflow, message: service.workflowMessage,
                isRunning: service.isRunning, now: timeline.date)
            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(presentation.title).fontWeight(.semibold)
                            .accessibilityIdentifier("diagnosticWorkflow.state")
                        Spacer()
                        if service.workflowBusy { ProgressView().controlSize(.small).accessibilityLabel("Enabling diagnostic simulator") }
                        else if workflow == nil || workflow?.state == "disabled" {
                            Button("Enable Diagnostic Simulator") { Task { await service.enableWorkflow() } }
                                .disabled(service.selectedSource == nil || service.isBusy || transitioning)
                                .accessibilityIdentifier("diagnosticWorkflow.enable")
                        }
                    }
                    Text("Hypothetical fills after modeled fees and slippage · BTC / ETH spot · long / flat")
                        .font(.caption).foregroundStyle(.secondary)
                    Label("Not cleared for real-money copying", systemImage: "hand.raised")
                        .font(.caption).foregroundStyle(.orange)
                        .accessibilityIdentifier("diagnosticWorkflow.copyReadiness")
                    if let message = service.workflowMessage {
                        Label(message, systemImage: "exclamationmark.circle").foregroundStyle(.secondary)
                    }
                    if let workflow, let account = workflow.account {
                        Text(presentation.sourceAge).font(.caption).foregroundStyle(.secondary)
                        accountSummary(account, presentation: presentation, historical: presentation.historical || account.date("valuationAt").map { timeline.date.timeIntervalSince($0) >= 15 } ?? true)
                        Text(presentation.valuation).font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("diagnosticWorkflow.valuation")
                        if let position = workflow.positions.first {
                            let symbol = position.fields("origin")?.string("symbol") ?? "Unavailable"
                            Text("\(presentation.historical ? "Retained paper position" : "Paper position"): \(symbol) · \(position.string("quantity") ?? "—") units")
                            Text("Stop \(money(position, "stop")) · target \(money(position, "target")) · unrealized \(money(account, "unrealizedPnl")) USDT")
                                .font(.caption).foregroundStyle(.secondary)
                        } else if let pending = account.fields("pendingEntry") {
                            Text("\(presentation.historical ? "Retained intent" : "Pending intent"): \(pending.string("symbol") ?? "—") · \(setupName(pending.string("setup")))")
                            Text("A later usable quote is required for a simulated fill.").font(.caption).foregroundStyle(.secondary)
                        } else if let setup = presentation.currentSetups.first {
                            Text("Observed hypothesis: \(setup.string("symbol") ?? "—") · \(setupName(setup.string("setup")))")
                        } else { Text("Stand aside · watching for a supported setup").foregroundStyle(.secondary) }
                        if let exit = account.fields("pendingExit") {
                            Text("Retained exit trigger: \(plain(exit.string("reason") ?? "unavailable")) · waiting for a usable bid")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let review = workflow.review {
                            Text("Completed: \(review.count("completedTrades")) · wins \(review.count("wins")) · losses \(review.count("losses"))")
                                .font(.caption)
                        }
                        DisclosureGroup("Setup evidence and post-trade review") {
                            details(workflow, account: account, historical: presentation.historical, now: timeline.date)
                        }.accessibilityIdentifier("diagnosticWorkflow.details")
                    } else {
                        if presentation.recovery == .investigateAndReload {
                            Label(presentation.backendErrorReasons.isEmpty ? "Retained diagnostic evidence is unavailable."
                                  : presentation.backendErrorReasons.map(plain).joined(separator: ", "), systemImage: "exclamationmark.circle")
                                .foregroundStyle(.secondary).accessibilityIdentifier("diagnosticWorkflow.backendError")
                            Text("Inspect Data → Show Evidence Folder and retained logs. After resolving the evidence issue, choose the same source again to retry status. Preserve the journal; do not reset it.")
                                .foregroundStyle(.secondary)
                        } else {
                            Text("Choose a registered source, then enable explicitly. Start the paper session to advance the simulator.")
                                .foregroundStyle(.secondary)
                        }
                    }
                    Text("Diagnostic paper simulation. Results do not qualify research suggestions. No orders or notifications are enabled.")
                        .font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            } label: { Label("Diagnostic simulator", systemImage: "flask") }
            .accessibilityIdentifier("diagnosticWorkflow.card")
        }
    }

    private func accountSummary(_ account: WorkflowFields, presentation: DiagnosticWorkflowPresentation, historical: Bool) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 5) {
            GridRow { Text("Simulated cash"); Text("\(money(account, "cash")) USDT").monospacedDigit() }
            GridRow { Text(historical ? "Equity at last mark" : "Simulated equity"); Text("\(money(account, "equity")) USDT").monospacedDigit() }
            GridRow { Text("Completed trade net P&L"); Text("\(money(presentation.completedPnl)) USDT").monospacedDigit() }
            if let partial = presentation.openPositionRealizedPnl, partial != 0 {
                GridRow { Text("Open position realized P&L"); Text("\(money(partial)) USDT").monospacedDigit() }
            }
            GridRow { Text("Fees paid"); Text("\(money(account, "fees")) USDT").monospacedDigit() }
        }.font(.callout).accessibilityIdentifier("diagnosticWorkflow.account")
    }

    private func details(_ workflow: DiagnosticWorkflow, account: WorkflowFields, historical: Bool, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Fixed policy: breakout or pullback/reclaim; one position; modeled entry loss ≤0.5% of equity; notional ≤25%; participation ≤1% of reported bar volume and displayed size.")
            Text("Limits: 2% realized UTC-day loss; six daily entries; three losing closes trigger a 60-minute cooldown. No shorts or leverage.")
            Text("Maximum drawdown \(percent(account, "maximumDrawdown")) · modeled slippage \(money(account, "slippageCost")) USDT")
            ForEach(Array(workflow.decisions.enumerated()), id: \.offset) { _, decision in
                let retained = historical || decision.date("expiresAt").map { $0 <= now } ?? true
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(decision.string("symbol") ?? "—"): \(retained ? "retained evidence" : plain(decision.string("status") ?? "unavailable"))")
                    Text(decision.reasons.isEmpty ? "\(setupName(decision.string("setup"))) · fixed cost and context checks retained" : decision.reasons.map(plain).joined(separator: ", "))
                    Text("Session: \(plain(decision.string("session") ?? "unavailable")) · calendar evidence \(decision.values["calendarAvailable"] == .bool(true) ? "available" : "unavailable")")
                    if !retained, decision.string("status") == "ready" {
                        Text("Hypothesis levels: entry \(money(decision, "entry")) · stop \(money(decision, "stop")) · target \(money(decision, "target")) USDT")
                    }
                }
            }
            if !workflow.reasons.isEmpty { Text(workflow.reasons.map(plain).joined(separator: ", ")) }
            if let review = workflow.review {
                ForEach(Array(review.rows("setups").enumerated()), id: \.offset) { _, setup in
                    Text("\(setupName(setup.string("setup"))): \(setup.count("completed")) completed · \(setup.count("wins")) wins / \(setup.count("losses")) losses · net \(money(setup, "netPnl")) USDT")
                }
            }
            if let last = workflow.recentTrades.last {
                Text("Last close: \(last.string("symbol") ?? "—") · \(plain(last.string("reason") ?? "unavailable")) · net \(money(last, "netPnl")) USDT after costs")
                if let date = last.date("exitAt") { Text(date, format: .dateTime.year().month().day().hour().minute().second()) }
            } else { Text("No completed simulated trades. Open positions are excluded from wins and losses.") }
            Text("Scheduled catalysts require current imported calendar coverage. News interpretation is unavailable. Missing quotes can delay a simulated exit.")
            Text("Pause stops processing; a closed window continues while Nowcaster runs. Quit or sleep stops progress. No background daemon is installed.")
        }.font(.caption).foregroundStyle(.secondary).padding(.top, 8)
    }

    private func money(_ row: WorkflowFields, _ key: String) -> String {
        money(row.decimal(key))
    }
    private func money(_ amount: Decimal?) -> String {
        guard let value = amount else { return "Unavailable" }
        let formatter = NumberFormatter(); formatter.numberStyle = .decimal
        formatter.minimumFractionDigits = 2; formatter.maximumFractionDigits = 2
        return formatter.string(from: NSDecimalNumber(decimal: value)) ?? "Unavailable"
    }
    private func percent(_ row: WorkflowFields, _ key: String) -> String {
        guard let value = row.decimal(key) else { return "Unavailable" }
        let formatter = NumberFormatter(); formatter.numberStyle = .percent; formatter.maximumFractionDigits = 2
        return formatter.string(from: NSDecimalNumber(decimal: value)) ?? "Unavailable"
    }
    private func plain(_ value: String) -> String { value.replacingOccurrences(of: "_", with: " ") }
    private func setupName(_ value: String?) -> String {
        switch value { case "breakout": "Breakout"; case "pullback_reclaim": "Pullback / reclaim"; default: "Setup unavailable" }
    }
}
