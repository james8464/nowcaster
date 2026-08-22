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
            TableColumn("Cutoff") { forecast in Text(forecast.forecastCutoffDate, style: .date) }
                .width(min: 90, ideal: 110)
            TableColumn("Horizon") { forecast in Text("\(forecast.horizonDays)d") }
                .width(65)
            TableColumn("Forecast") { forecast in
                Text(ResearchFormatting.compactNumber(forecast.forecastRevenue)).monospacedDigit()
            }
            .width(min: 85, ideal: 100)
            TableColumn("Expectation") { forecast in
                Text(ResearchFormatting.compactNumber(forecast.expectationRevenue)).monospacedDigit()
            }
            .width(min: 85, ideal: 100)
            TableColumn("Variant") { forecast in
                Text(ResearchFormatting.percentage(forecast.variant)).monospacedDigit()
            }
            .width(min: 75, ideal: 90)
            TableColumn("Expectation type") { forecast in
                Text(EarningsDetailModel(forecast: forecast).expectationTitle)
            }
            .width(min: 160, ideal: 190)
        }
        .accessibilityIdentifier("earnings.table")
    }
}
