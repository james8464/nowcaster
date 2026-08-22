import SwiftUI

struct EarningsDetailModel: Sendable {
    let forecast: EarningsSnapshot

    var expectationTitle: String {
        switch forecast.expectationMode {
        case "expectation_proxy": "Seasonal expectation proxy"
        default: "Imported analyst expectation"
        }
    }
}

struct EarningsView: View {
    @Bindable var model: AppModel
    let snapshot: NowcasterSnapshot

    var body: some View {
        Table(snapshot.earnings, selection: $model.selectedEarningsID) {
            TableColumn("Company") { forecast in
                VStack(alignment: .leading, spacing: 1) {
                    Text(forecast.companyId).fontWeight(.medium)
                    Text(forecast.fiscalQuarter).font(.caption2).foregroundStyle(.secondary)
                }
            }
            .width(min: 85, ideal: 105)
            TableColumn("Event basis") { forecast in Text(forecast.earningsDate, style: .date) }
                .width(min: 90, ideal: 110)
            TableColumn("Variant") { forecast in
                Text(ResearchFormatting.percentage(forecast.variant)).monospacedDigit()
            }
            .width(min: 75, ideal: 90)
        }
        .accessibilityIdentifier("earnings.table")
    }
}
