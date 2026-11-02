import SwiftUI

@MainActor
final class TripOutcomeViewModel: ObservableObject {
    @Published private(set) var outcome: TripOutcome?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let tripId: String
    private let environment: AppEnvironment

    init(tripId: String, environment: AppEnvironment) {
        self.tripId = tripId
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            outcome = try await environment.tripRepository.tripOutcome(tripId: tripId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

/// 出差结果分析与客户机会复盘（PRD P1）：计划 vs 实际，机会标注待确认。
struct TripOutcomeView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TripOutcomeViewModel
    @Environment(\.dismiss) private var dismiss

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: TripOutcomeViewModel(tripId: tripId, environment: environment)
        )
    }

    var body: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if let outcome = viewModel.outcome {
                Section("拜访结果") {
                    ForEach(outcome.visitList) { item in
                        LabeledContent(
                            outcome.labelMap[item.status] ?? item.status,
                            value: "\(item.count) 次"
                        )
                    }
                    let records = (outcome.recordOutcomes ?? [String: Int]())
                        .sorted { $0.key < $1.key }
                    ForEach(records, id: \.key) { key, count in
                        LabeledContent(
                            "现场记录 · \(outcome.outcomeMap[key] ?? key)",
                            value: "\(count) 条"
                        )
                    }
                }
                Section("费用（计划 vs 实际）") {
                    if let plan = outcome.planCost {
                        LabeledContent("计划估算", value: plan.label)
                    }
                    ForEach(outcome.actualCostList) { item in
                        LabeledContent("实际 · \(item.currency)", value: item.label)
                    }
                    Text("按币种分列展示，不做汇率折算。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                let signals = outcome.signalList
                Section("客户机会复盘") {
                    if signals.isEmpty {
                        Text("本次出差没有记录到商机信号/交易条件/商业承诺。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    ForEach(signals) { signal in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(signal.kindLabel)
                                    .font(.caption.weight(.semibold))
                                Spacer()
                                Text("待确认")
                                    .font(.caption2)
                                    .foregroundStyle(.orange)
                            }
                            Text(signal.text)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 2)
                    }
                    Text("以上均为 AI 依据现场记录推断，须经人工确认后才能采信；不会写入 CRM。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Section("复盘记忆") {
                    ForEach(outcome.memorySummaries ?? []) { summary in
                        Text(summary.summary).font(.footnote)
                    }
                }
            } else if viewModel.isLoading {
                ProgressView("加载结果复盘…")
            }
        }
        .navigationTitle("结果复盘")
        .refreshable { await viewModel.load() }
        .task { await viewModel.load() }
    }
}
