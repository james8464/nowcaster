import Foundation

struct DiagnosticWorkflowPresentation {
    let title: String
    let sourceAge: String
    let valuation: String
    let currentSetups: [WorkflowFields]
    let historical: Bool

    static func make(_ workflow: DiagnosticWorkflow?, message: String?, isRunning: Bool, now: Date) -> Self {
        guard let workflow else {
            return Self(title: message == nil ? "Not enabled" : "Unavailable", sourceAge: "Source age unavailable",
                        valuation: "Valuation unavailable", currentSetups: [], historical: true)
        }
        let age = workflow.updatedAt.map { now.timeIntervalSince($0) }
        let stale = workflow.state == "stale" || age.map { $0 < 0 || $0 >= 15 } ?? false
        let historical = stale || !isRunning || ["disabled", "error"].contains(workflow.state)
        let title: String
        if workflow.state == "disabled" { title = "Not enabled" }
        else if workflow.state == "error" { title = "Unavailable" }
        else if stale { title = "Stale source" }
        else if !isRunning { title = "Paused · retained simulation" }
        else {
            title = ["watching": "Watching", "pending_entry": "Pending simulated entry",
                     "position_open": "Simulated position open", "pending_exit": "Pending simulated exit",
                     "limited": "Entry limits active"][workflow.state] ?? "Unavailable"
        }
        let valuation: String
        if let marked = workflow.account?.date("valuationAt") {
            let elapsed = now.timeIntervalSince(marked)
            valuation = historical || elapsed < 0 || elapsed >= 15 ? "Stale valuation · last executable mark \(Int(max(0, elapsed)))s ago"
                : "Executable mark \(Int(elapsed))s ago · simulated USDT"
        } else { valuation = "Valuation unavailable · no executable mark retained" }
        return Self(title: title, sourceAge: age.map { "Source observation \(Int(max(0, $0)))s ago" } ?? "Source age unavailable",
                    valuation: valuation, currentSetups: historical ? [] : workflow.decisions.filter {
                        $0.string("status") == "ready" && $0.date("expiresAt").map { now < $0 } ?? false
                    }, historical: historical)
    }
}
