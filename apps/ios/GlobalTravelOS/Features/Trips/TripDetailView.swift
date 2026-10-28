import SwiftUI

@MainActor
final class TripDetailViewModel: ObservableObject {
    @Published private(set) var trip: Trip?
    @Published private(set) var visits: [TripVisit] = []
    @Published private(set) var plans: [PlanVersion] = []
    @Published private(set) var isLoading = false
    @Published private(set) var generating = false
    @Published var errorMessage: String?
    @Published var statusMessage: String?

    let tripId: String
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
            let loaded = try await environment.tripRepository.trip(id: tripId)
            trip = loaded
            environment.rememberTrip(tripId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
        visits = (try? await environment.visitRepository.listVisits(tripId: tripId)) ?? visits
        plans = (try? await environment.planRepository.listPlans(
            tripId: tripId,
            page: PageRequest(limit: 50, cursor: nil)
        ).items) ?? plans
        environment.refreshOfflineSummary()
    }

    func hold() async {
        await transition { try await environment.tripRepository.holdTrip(id: tripId, version: $0) }
    }

    func resume() async {
        await transition { try await environment.tripRepository.resumeTrip(id: tripId, version: $0) }
    }

    func complete() async {
        await transition { try await environment.tripRepository.completeTrip(id: tripId, version: $0) }
    }

    private func transition(
        _ action: (Int) async throws -> Trip
    ) async {
        guard let current = trip else { return }
        errorMessage = nil
        do {
            _ = try await action(current.version)
            statusMessage = "状态已更新"
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func generatePlan() async {
        generating = true
        errorMessage = nil
        statusMessage = "正在排队求解…"
        defer { generating = false }
        do {
            let outcome = try await environment.generatePlanUseCase.generate(
                tripId: tripId,
                onProgress: { job in
                    Task { @MainActor in
                        self.statusMessage = "求解中 · \(job.statusLabel) · 尝试 \(job.attempts ?? 0) 次"
                    }
                }
            )
            statusMessage = outcome.message
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
            statusMessage = nil
        }
    }
}

struct TripDetailView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TripDetailViewModel
    @State private var selectedVisit: TripVisit?
    @State private var showsImport = false

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: TripDetailViewModel(tripId: tripId, environment: environment)
        )
    }

    var body: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if let status = viewModel.statusMessage {
                Section {
                    Label(status, systemImage: "gearshape.2")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if let trip = viewModel.trip {
                Section("概览") {
                    LabeledContent("状态", value: trip.statusLabel)
                    LabeledContent("时间窗口", value: trip.windowLabel)
                    LabeledContent("出发地", value: trip.originLabel)
                    LabeledContent("版本", value: "v\(trip.version)")
                }
            }

            if let trip = viewModel.trip {
                Section("出行状态") {
                    let status = (trip.status ?? "").uppercased()
                    if ["DRAFT", "PLANNING", "READY", "ACTIVE"].contains(status) {
                        Button(role: .destructive) {
                            Task { await viewModel.hold() }
                        } label: {
                            Label("暂停出行", systemImage: "pause.circle")
                        }
                    }
                    if status == "HOLD" {
                        Button {
                            Task { await viewModel.resume() }
                        } label: {
                            Label("恢复出行", systemImage: "play.circle")
                        }
                    }
                    if status != "COMPLETED" && status != "CANCELLED" {
                        Button {
                            Task { await viewModel.complete() }
                        } label: {
                            Label("标记完成", systemImage: "checkmark.circle")
                        }
                    }
                }
            }

            Section("客户导入") {
                Button {
                    showsImport = true
                } label: {
                    Label("导入 Excel 客户名单", systemImage: "doc.badge.plus")
                }
            }

            Section("拜访") {
                if viewModel.visits.isEmpty {
                    Text("还没有拜访。导入客户或新增拜访后再生成计划。")
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.visits) { visit in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(visit.displayName).font(.body)
                            Text("\(visit.statusLabel) · \(visit.timeWindowLabel)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("记录") { selectedVisit = visit }
                            .buttonStyle(.bordered)
                    }
                }
            }

            Section("计划版本") {
                if viewModel.plans.isEmpty {
                    Text("还没有计划版本。").foregroundStyle(.secondary)
                }
                ForEach(viewModel.plans) { plan in
                    NavigationLink {
                        PlanDetailView(
                            tripId: viewModel.tripId,
                            planId: plan.id,
                            tripVersion: viewModel.trip?.version ?? 1,
                            environment: environment
                        )
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(plan.versionLabel).font(.headline)
                                Spacer()
                                Text(plan.status?.label ?? "草稿")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Text("\(plan.feasibilityLabel) · \(plan.stopCountLabel)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Button {
                    Task { await viewModel.generatePlan() }
                } label: {
                    Label(
                        viewModel.generating ? "求解中…" : "生成新计划",
                        systemImage: "wand.and.stars"
                    )
                }
                .disabled(viewModel.generating)
            }

            Section("记忆") {
                NavigationLink {
                    TripMemoryView(tripId: viewModel.tripId, environment: environment)
                } label: {
                    Label("查看复盘记忆", systemImage: "brain.head.profile")
                }
            }
        }
        .navigationTitle(viewModel.trip?.name ?? "出行详情")
        .refreshable { await viewModel.load() }
        .task { await viewModel.load() }
        .sheet(item: $selectedVisit) { visit in
            VisitRecordSheet(visit: visit)
        }
        .sheet(isPresented: $showsImport) {
            ImportSheet(tripId: viewModel.tripId) {
                await viewModel.load()
            }
        }
    }
}
