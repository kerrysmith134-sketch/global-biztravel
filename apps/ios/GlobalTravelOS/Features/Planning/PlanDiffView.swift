import SwiftUI

/// 版本比较（AT-008）：`GET /v1/trips/{id}/plans/{plan_id}:diff?against={other}`。
///
/// 只读视图：不重新求解、不改任何计划。服务端给出 summary（新增/移除/换序/改时计数与
/// 行驶时间、里程差）和逐项 change（含停靠在两版里的位置/时间快照），这里只负责展示。
@MainActor
final class PlanDiffViewModel: ObservableObject {
    @Published private(set) var diff: PlanDiff?
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?

    private let tripId: String
    private let planId: String
    private let against: String
    private let environment: AppEnvironment

    init(tripId: String, planId: String, against: String, environment: AppEnvironment) {
        self.tripId = tripId
        self.planId = planId
        self.against = against
        self.environment = environment
    }

    func load() async {
        guard diff == nil else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            diff = try await environment.planRepository.planDiff(
                tripId: tripId,
                planId: planId,
                against: against
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct PlanDiffView: View {
    @StateObject private var viewModel: PlanDiffViewModel

    init(tripId: String, planId: String, against: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: PlanDiffViewModel(
                tripId: tripId,
                planId: planId,
                against: against,
                environment: environment
            )
        )
    }

    var body: some View {
        Group {
            if let errorMessage = viewModel.errorMessage {
                ContentUnavailableView(
                    "版本比较加载失败",
                    systemImage: "exclamationmark.triangle",
                    description: Text(errorMessage)
                )
            } else if let diff = viewModel.diff {
                breakdown(diff)
            } else {
                ProgressView("正在比较两版计划…")
            }
        }
        .navigationTitle("版本比较")
        .task { await viewModel.load() }
    }

    @ViewBuilder
    private func breakdown(_ diff: PlanDiff) -> some View {
        List {
            Section("摘要") {
                if let summary = diff.summary {
                    LabeledContent("变更", value: summary.displayText)
                }
                LabeledContent("行驶时间", value: diff.travelDeltaLabel)
            }
            Section("逐项变更") {
                if diff.materialStops.isEmpty {
                    Text("两版计划没有实质差异。").foregroundStyle(.secondary)
                }
                ForEach(diff.materialStops) { change in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Label(change.change.label, systemImage: change.change.glyph)
                                .font(.headline)
                            Spacer()
                            Text(change.displayName)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        Text(change.travelDescription)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}
