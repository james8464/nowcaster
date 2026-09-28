import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct LivePaperSignalsPresentation {
    let status: String
    let suggestion: TrendAdvisorSuggestion?
    let reasons: String

    init(state: LivePaperSignalState?, isRunning: Bool, now: Date) {
        suggestion = state?.currentSuggestion(now: now, isRunning: isRunning)
        if !isRunning { status = "Stopped" }
        else if let state, now < state.updatedAt { status = "Unavailable" }
        else if let state, now.timeIntervalSince(state.updatedAt) >= 15 { status = "Stale" }
        else if state?.kind == "published", suggestion == nil { status = "Stale" }
        else {
            status = ["warming": "Warming up", "published": "Research posture available", "abstaining": "Stand aside",
                      "failed": "Feed unavailable", "stale": "Stale", "stopped": "Stopped"][state?.kind ?? ""] ?? "Connecting"
        }
        if !isRunning { reasons = "Start collection to evaluate fresh market observations." }
        else if status == "Stale" { reasons = "Fresh evidence is unavailable. Earlier levels are no longer current." }
        else { reasons = state?.reasons.map { $0.replacingOccurrences(of: "_", with: " ") }.joined(separator: " · ") ?? "Waiting for a checked feed observation." }
    }
}

struct LivePaperSignalsView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    private enum ImportKind { case directory, calendar }
    @State private var importKind: ImportKind = .directory
    @State private var choosingFile = false
    @State private var showingHistory = false
    @State private var selectionMessage: String?

    private var service: LivePaperSignalService { model.livePaperSignals }

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Live paper signals", systemImage: "waveform.path.ecg").font(.headline)
                    Spacer()
                    if service.isBusy { ProgressView().controlSize(.small) }
                }
                Text("Automatic trend research for Bitcoin and Ether · Binance spot")
                    .foregroundStyle(.secondary)
                if let evidence = service.notificationEvidence {
                    GroupBox("Historical notification evidence") {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("\(evidence.suggestion.symbol) · Retained paper research").font(.headline)
                            Text("This is the original saved evidence, not a current signal.").font(.caption).foregroundStyle(.secondary)
                            LabeledContent("Research folder", value: service.notificationEvidenceDirectory?.lastPathComponent ?? "Unavailable")
                            LabeledContent("Protocol", value: String(evidence.notification.protocolHash.prefix(12)))
                            LabeledContent("Candidate", value: String(evidence.notification.candidateHash.prefix(12)))
                            LabeledContent("Notification", value: String(evidence.notification.materialKey.prefix(12)))
                            LabeledContent("Decision", value: evidence.suggestion.decisionAt.formatted(date: .abbreviated, time: .standard))
                            LabeledContent("Expired after", value: evidence.notification.expiresAt.formatted(date: .abbreviated, time: .standard))
                            LabeledContent("Original research zone", value: "\(evidence.suggestion.entryLow ?? "—") – \(evidence.suggestion.entryHigh ?? "—")")
                            LabeledContent("Original invalidation", value: evidence.suggestion.invalidation ?? "—")
                            LabeledContent("Original research target", value: evidence.suggestion.target ?? "—")
                            LabeledContent("Delivery record", value: evidence.outcome.capitalized)
                            if let directory = service.notificationEvidenceDirectory {
                                Button("Show Original Evidence Folder", systemImage: "folder") { NSWorkspace.shared.open(directory) }
                            }
                        }.textSelection(.enabled)
                    }.accessibilityIdentifier("paperSignals.notificationEvidence")
                } else if let message = service.notificationEvidenceMessage {
                    Label(message, systemImage: "doc.text.magnifyingglass").font(.caption).foregroundStyle(.secondary)
                }
                ViewThatFits(in: .horizontal) {
                    HStack { controls }
                    VStack(alignment: .leading) { controls }
                }
                Text(service.directory?.lastPathComponent ?? "Create a paper desk, or open an existing registered research folder.")
                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    status(now: context.date)
                }
                Toggle("Notify me about new paper research", isOn: Binding(
                    get: { service.notificationsEnabled },
                    set: { enabled in Task { await service.setNotificationsEnabled(enabled) } }))
                    .toggleStyle(.switch)
                    .accessibilityIdentifier("paperSignals.notifications")
                Text("Paper-only research — not a trade instruction. No account connection or trades. Results are not proof of profitability.")
                    .font(.caption).foregroundStyle(.secondary)
                if let message = selectionMessage ?? service.message {
                    Label(message, systemImage: message.hasPrefix("Calendar evidence retained") ? "checkmark.circle" : "info.circle")
                        .font(.caption).foregroundStyle(.secondary)
                }
                DisclosureGroup("Recent evidence (\(service.events.count))", isExpanded: $showingHistory) {
                    if service.events.isEmpty {
                        Text("No retained events loaded.").foregroundStyle(.secondary)
                    } else {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 8) {
                                ForEach(service.events.reversed()) { event in
                                    VStack(alignment: .leading, spacing: 2) {
                                        HStack {
                                            Text(event.kind.replacingOccurrences(of: "_", with: " ").capitalized)
                                            Spacer()
                                            Text(event.at, format: .dateTime.month().day().hour().minute().second())
                                                .foregroundStyle(.secondary)
                                        }
                                        if let detail = event.detail {
                                            Text(detail.replacingOccurrences(of: "_", with: " ")).foregroundStyle(.secondary)
                                        }
                                    }.font(.caption)
                                    Divider()
                                }
                            }
                        }.frame(maxHeight: 160)
                    }
                }.accessibilityIdentifier("paperSignals.history")
            }.padding(4)
        }
        .accessibilityIdentifier("strategyLab.livePaperSignals")
        // A single presenter avoids two fileImporter modifiers competing for
        // the same macOS view's presentation slot (the folder picker was lost).
        .fileImporter(isPresented: $choosingFile,
                      allowedContentTypes: importKind == .directory ? [.folder] : [.json]) { result in
            switch result {
            case let .success(file):
                selectionMessage = nil
                switch importKind {
                case .directory:
                    Task { await service.open(directory: file, sourceRoot: settings.configuration.projectRoot,
                                              sourcePython: settings.configuration.pythonExecutable) }
                case .calendar:
                    Task { await service.importCalendar(file) }
                }
            case let .failure(error): selectionMessage = error.localizedDescription
            }
        }
        .onChange(of: model.paperResearchEvidenceRequested, initial: true) { _, requested in
            if requested { showingHistory = true; model.paperResearchEvidenceRequested = false }
        }
    }

    @ViewBuilder private var controls: some View {
        if service.directory == nil {
            Button("Set Up Paper Desk", systemImage: "plus.circle") {
                selectionMessage = nil
                Task { await service.createOrResumeDesk(sourceRoot: settings.configuration.projectRoot,
                                                       sourcePython: settings.configuration.pythonExecutable) }
            }.disabled(service.isRunning || service.isBusy).accessibilityIdentifier("paperSignals.setup")
        }
        Button("Choose Research Folder…", systemImage: "folder") { importKind = .directory; choosingFile = true }
            .disabled(service.isRunning || service.isBusy)
        PaperSessionAction(model: model, settings: settings)
        if let directory = service.directory {
            Button("Import Calendar…", systemImage: "calendar.badge.plus") { importKind = .calendar; choosingFile = true }
                .disabled(service.isBusy)
            Button("Show Evidence", systemImage: "doc.text.magnifyingglass") {
                NSWorkspace.shared.open(directory)
            }
        }
    }

    @ViewBuilder private func status(now: Date) -> some View {
        let presentation = LivePaperSignalsPresentation(state: service.state, isRunning: service.isRunning, now: now)
        VStack(alignment: .leading, spacing: 6) {
            LabeledContent("Collection", value: presentation.status)
            LabeledContent("Provider health", value: service.isRunning ? service.providerHealth?.title(now: now) ?? "Awaiting evidence" : "Not collecting")
            if let last = service.providerHealth?.lastSuccessfulObservationAt {
                LabeledContent("Last source observation") {
                    Text(last, style: .relative).monospacedDigit()
                }
            }
            if let evaluation = service.state?.evaluatedAt ?? service.events.last(where: { $0.kind == "evaluated" })?.at {
                LabeledContent("Last evaluation", value: evaluation.formatted(date: .abbreviated, time: .standard))
            }
            if let suggestion = presentation.suggestion {
                Text("\(suggestion.symbol) · Long research").font(.headline)
                LabeledContent("Research entry zone", value: "\(suggestion.entryLow ?? "—") – \(suggestion.entryHigh ?? "—")")
                LabeledContent("Invalidation", value: suggestion.invalidation ?? "—")
                LabeledContent("Research target", value: suggestion.target ?? "—")
                LabeledContent("Expires", value: suggestion.expiresAt.formatted(date: .omitted, time: .standard))
                Text("Research ends when invalidation or target is reached, trend alignment is lost, or evidence expires.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Label("Stand aside", systemImage: "pause.circle").font(.headline)
                Text(presentation.reasons.isEmpty ? "No candidate clears the current research checks." : presentation.reasons)
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.textSelection(.enabled)
    }
}
