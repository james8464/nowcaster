import Charts
import SwiftUI

struct ModelLabView: View {
    let snapshot: NowcasterSnapshot
    @State private var selectedHorizon: Int?

    private var horizons: [Int] { Array(Set(snapshot.modelDiagnostics.map(\.horizonDays))).sorted() }
    private var rows: [ModelDiagnosticSnapshot] {
        guard let selectedHorizon else { return snapshot.modelDiagnostics }
        return snapshot.modelDiagnostics.filter { $0.horizonDays == selectedHorizon }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Horizon", selection: $selectedHorizon) {
                    Text("All horizons").tag(Int?.none)
                    ForEach(horizons, id: \.self) { Text("\($0)d").tag(Int?.some($0)) }
                }
                .frame(width: 210)
                Spacer()
                Text("Out-of-sample diagnostics").foregroundStyle(.secondary)
            }
            .padding()
            Divider()
            VSplitView {
                Table(rows) {
                    TableColumn("Model") { item in Text(item.modelName).fontWeight(.medium) }
                        .width(min: 100, ideal: 140)
                    TableColumn("Ablation") { item in Text(item.ablation.replacingOccurrences(of: "_", with: " ").capitalized) }
                        .width(min: 120, ideal: 170)
                    TableColumn("Horizon") { item in Text("\(item.horizonDays)d") }
                        .width(70)
                    TableColumn("Observations") { item in Text(item.observations.formatted()).monospacedDigit() }
                        .width(90)
                    TableColumn("MAE") { item in Text(ResearchFormatting.compactNumber(item.mae)).monospacedDigit() }
                        .width(100)
                    TableColumn("RMSE") { item in Text(ResearchFormatting.compactNumber(item.rmse)).monospacedDigit() }
                        .width(100)
                    TableColumn("MAPE") { item in Text(ResearchFormatting.percentage(item.mape)).monospacedDigit() }
                        .width(90)
                }
                .frame(minHeight: 240)
                VStack(alignment: .leading, spacing: 10) {
                    Text("Error by horizon and ablation").font(.headline)
                    Chart(rows) { item in
                        BarMark(
                            x: .value("Model and ablation", "\(item.modelName) · \(item.ablation)"),
                            y: .value("MAE", item.mae)
                        )
                        .foregroundStyle(by: .value("Horizon", "\(item.horizonDays)d"))
                    }
                    .chartXAxis(.hidden)
                    .frame(minHeight: 180)
                    Text("Lower error is better. Every result shown here is generated out of sample within its declared horizon.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .padding()
            }
        }
        .accessibilityIdentifier("modelLab.view")
    }
}
