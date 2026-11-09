import SwiftUI

@MainActor
final class PlanDetailViewModel: ObservableObject {
    @Published private(set) var plan: PlanVersionDetail?
    @Published private(set) var isLoading = false
    @Published private(set) var activating = false
    @Published private(set) var submittingChange = false
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

    /// AT-007：从**当前生效计划**发起一次"取消拜访"变更（改拜访输入 → 重新求解）。
    func cancelVisit(_ plan: PlanVersionDetail, stop: PlanStop) async {
        submittingChange = true
        errorMessage = nil
        defer { submittingChange = false }
        do {
            let accepted = try await environment.planRepository.createPlanChange(
                tripId: tripId,
                request: PlanChangeCreate(
                    basePlanVersionId: plan.id,
                    baseTripVersion: tripVersion,
                    operationType: .cancelVisit,
                    payload: ["visit_id": stop.visitId],
                    reason: "客户端取消拜访"
                )
            )
            statusMessage = accepted.summary
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
    @State private var pendingCancelStop: PlanStop?
    @State private var showCancelConfirm = false

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
                Section("视图") {
                    NavigationLink {
                        PlanMapView(plan: plan)
                    } label: {
                        Label("行程地图", systemImage: "map")
                    }
                    NavigationLink {
                        PlanCalendarView(plan: plan)
                    } label: {
                        Label("行程日历", systemImage: "calendar")
                    }
                }

                if let violations = plan.policyViolations, !violations.isEmpty {
                    Section("差旅政策") {
                        ForEach(violations) { violation in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(violation.code).font(.caption.weight(.semibold))
                                Text(violation.message).font(.caption)
                            }
                            .foregroundStyle(.orange)
                        }
                    }
                }

                Section("计划") {
                    LabeledContent("版本", value: plan.versionLabel)
                    LabeledContent("状态", value: plan.status?.label ?? "草稿")
                    LabeledContent("可行性", value: plan.feasibilityLabel)
                    LabeledContent("求解状态", value: plan.solutionStatusLabel)
                    LabeledContent("求解器", value: plan.solver ?? "—")
                    LabeledContent("总行程", value: "\(plan.totalTravelMinutes) 分钟")
                    LabeledContent("费用", value: plan.costLabel)
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

                if let legs = plan.legs, !legs.isEmpty {
                    Section("交通段") {
                        ForEach(legs) { leg in
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(leg.sourceLabel) · \(leg.mode)")
                                    .font(.caption.weight(.semibold))
                                Text("\(leg.travelMinutes) 分钟 · \(leg.distanceLabel) · \(leg.costLabel)")
                                    .font(.caption)
                                    .foregroundStyle(leg.stale ? .orange : .secondary)
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

                if plan.status == .active {
                    Section {
                        ForEach(plan.orderedStops, id: \.stop.visitId) { item in
                            Button(role: .destructive) {
                                pendingCancelStop = item.stop
                                showCancelConfirm = true
                            } label: {
                                Label(
                                    "取消拜访：\(item.stop.displayName)",
                                    systemImage: "xmark.circle"
                                )
                                .font(.caption)
                            }
                            .disabled(viewModel.submittingChange)
                        }
                    } header: {
                        Text("调整拜访")
                    } footer: {
                        Text("取消会修改拜访输入并重新入队求解，生成新计划版本；旧版本保留，可与新版本比较。")
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
        .confirmationDialog(
            "确认取消该拜访？",
            isPresented: $showCancelConfirm,
            titleVisibility: .visible
        ) {
            Button("取消该拜访并重新生成计划", role: .destructive) {
                guard let plan = viewModel.plan, let stop = pendingCancelStop else { return }
                Task {
                    await viewModel.cancelVisit(plan, stop: stop)
                    pendingCancelStop = nil
                }
            }
            Button("不取消", role: .cancel) { pendingCancelStop = nil }
        } message: {
            Text("这会改拜访状态为已取消，并由 Worker 重新生成计划；可在版本比较里看差异。")
        }
    }
}
