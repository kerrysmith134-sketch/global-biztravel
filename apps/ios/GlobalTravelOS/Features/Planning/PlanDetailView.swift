import SwiftUI

@MainActor
final class PlanDetailViewModel: ObservableObject {
    @Published private(set) var plan: PlanVersionDetail?
    @Published private(set) var isLoading = false
    @Published private(set) var activating = false
    @Published var errorMessage: String?
    @Published var statusMessage: String?

    let tripId: String
    let planId: String
    private let tripVersion: Int
    private let environment: AppEnvironment

    init(tripId: String, planId: String, tripVersion: Int, environment: AppEnvironment) {
        self.tripId = tripId
        self.planId = planId
        self.tripVersion = tripVersion
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            plan = try await environment.planRepository.planDetail(
                tripId: tripId,
                planId: planId
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func activate() async {
        guard let plan else { return }
        activating = true
        errorMessage = nil
        defer { activating = false }
        do {
            let activation = try await environment.activatePlanUseCase.activate(
                tripId: tripId,
                planId: plan.id,
                tripVersion: tripVersion
            )
            statusMessage = "已激活 \(plan.versionLabel)，替换了 \(activation.switchedFrom)"
            await load()
        } catch let error as PlanActivationError {
            errorMessage = error.message
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct PlanDetailView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: PlanDetailViewModel

    init(tripId: String, planId: String, tripVersion: Int, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: PlanDetailViewModel(
                tripId: tripId,
                planId: planId,
                tripVersion: tripVersion,
                environment: environment
            )
        )
    }

    var body: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if let message = viewModel.statusMessage {
                Section {
                    Label(message, systemImage: "checkmark.seal")
                        .font(.footnote)
                        .foregroundStyle(.green)
                }
            }
            if let plan = viewModel.plan {
                Section("计划") {
                    LabeledContent("版本", value: plan.versionLabel)
                    LabeledContent("状态", value: plan.status?.label ?? "草稿")
                    LabeledContent("可行性", value: plan.feasibilityLabel)
                    LabeledContent("求解器", value: plan.solver ?? "—")
                    LabeledContent("总行程", value: "\(plan.totalTravelMinutes) 分钟")
                }

                if let validation = plan.validation {
                    Section("独立验证") {
                        Label(
                            validation.feasible == true ? "验证通过" : "验证未通过",
                            systemImage: validation.feasible == true
                                ? "checkmark.circle.fill" : "xmark.octagon.fill"
                        )
                        .foregroundStyle(validation.feasible == true ? .green : .red)
                        ForEach(validation.checkList) { check in
                            HStack {
                                Image(systemName: check.glyph)
                                VStack(alignment: .leading) {
                                    Text(check.rule).font(.caption.weight(.semibold))
                                    if let detail = check.detail {
                                        Text(detail).font(.caption2).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }

                ForEach(plan.dayList) { day in
                    Section(day.displayDate) {
                        ForEach(day.stops) { stop in
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(stop.sequence). \(stop.displayName)").font(.body)
                                Text("\(stop.arriveLabel) – \(stop.departLabel) · 行程 \(stop.travelLabel)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                if !plan.unplacedList.isEmpty {
                    Section("未排入") {
                        ForEach(plan.unplacedList) { item in
                            Text("\(item.visitId.prefix(8)) · \(item.reason ?? "未知原因")")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                }

                Section {
                    Button {
                        Task { await viewModel.activate() }
                    } label: {
                        Label(
                            viewModel.activating ? "激活中…" : "激活此计划",
                            systemImage: "bolt.badge.checkmark"
                        )
                    }
                    .disabled(viewModel.activating || plan.status == .active)
                } footer: {
                    Text("激活会写入审批与审计链；若出行在生成后已被修改，服务端会拒绝并要求重新生成。")
                }
            } else if viewModel.isLoading {
                ProgressView("加载计划…")
            }
        }
        .navigationTitle("计划详情")
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
    }
}
