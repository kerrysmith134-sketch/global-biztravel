import SwiftUI

@MainActor
final class MapWorkspaceViewModel: ObservableObject {
    @Published private(set) var trips: [Trip] = []
    @Published private(set) var plan: PlanVersionDetail?
    @Published private(set) var visits: [TripVisit] = []
    @Published private(set) var customers: [Customer] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isLoadingPlan = false
    @Published private(set) var isSubmittingChange = false
    @Published var planChangeMessage: String?
    @Published var errorMessage: String?
    @Published var selectedTripID: String?

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    var selectedTrip: Trip? {
        trips.first { $0.id == selectedTripID }
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let page = try await environment.tripRepository.listTrips(page: PageRequest(limit: 50))
            trips = page.items
            if selectedTripID == nil || !trips.contains(where: { $0.id == selectedTripID }) {
                selectedTripID = trips.first(where: { $0.status?.uppercased() == "ACTIVE" })?.id
                    ?? trips.first?.id
            }
            if let selectedTripID {
                await loadMissionContext(tripId: selectedTripID)
            }
            await loadSelectedPlan()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func selectTrip(_ id: String) {
        guard id != selectedTripID else { return }
        errorMessage = nil
        selectedTripID = id
        visits = []
        customers = []
        Task {
            await loadMissionContext(tripId: id)
            guard selectedTripID == id else { return }
            await loadSelectedPlan()
        }
    }

    /// Load visit and customer context independently from the plan payload.
    /// The map remains usable if CRM enrichment is unavailable; missing customer metadata is omitted.
    private func loadMissionContext(tripId: String) async {
        async let visitResult = try? await environment.visitRepository.listVisits(tripId: tripId)
        async let customerResult = try? await environment.customerRepository.listCustomers(
            query: nil,
            page: PageRequest(limit: 200)
        )
        let visitResponse = await visitResult
        let customerResponse = await customerResult
        let loadedVisits = visitResponse ?? []
        let loadedCustomers = customerResponse?.items ?? []

        guard selectedTripID == tripId else { return }
        visits = loadedVisits
        let referencedCustomerIDs = Set(loadedVisits.compactMap(\.customerId))
        customers = loadedCustomers.filter { referencedCustomerIDs.contains($0.id) }
    }


    /// The server is the only authority that mutates visit inputs and produces a new plan version.
    func cancelVisit(_ stop: PlanStop) async {
        guard let trip = selectedTrip, let plan, plan.status == .active else {
            errorMessage = "只能从当前已激活的计划发起拜访调整"
            return
        }
        guard !isSubmittingChange else { return }

        isSubmittingChange = true
        errorMessage = nil
        planChangeMessage = nil
        defer { isSubmittingChange = false }

        do {
            let accepted = try await environment.planRepository.createPlanChange(
                tripId: trip.id,
                request: PlanChangeCreate(
                    basePlanVersionId: plan.id,
                    baseTripVersion: trip.version,
                    operationType: .cancelVisit,
                    payload: ["visit_id": stop.visitId],
                    source: "MAP",
                    reason: "地图工作区取消拜访并重新优化路线"
                )
            )
            planChangeMessage = "变更已受理 · \(accepted.status) · 优化任务 \(accepted.optimizationJobId.prefix(8))"
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func loadSelectedPlan() async {
        guard let trip = selectedTrip else {
            plan = nil
            visits = []
            customers = []
            return
        }
        isLoadingPlan = true
        plan = nil
        defer { isLoadingPlan = false }

        if let embedded = trip.activePlan {
            plan = embedded
            return
        }

        do {
            if let activeID = trip.activePlanVersionId {
                plan = try await environment.planRepository.planDetail(
                    tripId: trip.id, planId: activeID
                )
                return
            }
            let versions = try await environment.planRepository.listPlans(
                tripId: trip.id, page: PageRequest(limit: 1)
            )
            guard let latest = versions.items.first else { return }
            plan = try await environment.planRepository.planDetail(
                tripId: trip.id, planId: latest.id
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

/// Map-first workspace for the currently selected business mission.
/// Camera gestures are display-only; plan changes remain explicit workflow actions.
struct MapWorkspaceView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: MapWorkspaceViewModel
    @State private var pendingCancelStop: PlanStop?
    @State private var showsCancelConfirmation = false

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: MapWorkspaceViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.trips.isEmpty {
                    DSLoadingState(message: "正在载入行程与地图…")
                } else if let error = viewModel.errorMessage, viewModel.trips.isEmpty {
                    DSErrorState(message: error) {
                        Task { await viewModel.load() }
                    }
                } else if viewModel.trips.isEmpty {
                    DSEmptyState(
                        title: "还没有可展示的行程",
                        message: "先建立一次商务 Mission，导入客户并生成计划，地图会在这里汇总所有拜访点与路线。",
                        systemImage: DSIconCatalog.map
                    )
                } else {
                    workspace
                }
            }
            .padding(.horizontal, DS.Spacing.lg)
            .padding(.top, DS.Spacing.sm)
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("地图")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await viewModel.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("刷新地图数据")
                }
            }
            .task { await viewModel.load() }
            .refreshable { await viewModel.load() }
            .confirmationDialog(
                "取消这次拜访并重新优化计划？",
                isPresented: $showsCancelConfirmation,
                titleVisibility: .visible
            ) {
                Button("取消拜访并重新求解", role: .destructive) {
                    guard let stop = pendingCancelStop else { return }
                    Task {
                        await viewModel.cancelVisit(stop)
                        pendingCancelStop = nil
                    }
                }
                Button("保留拜访", role: .cancel) {
                    pendingCancelStop = nil
                }
            } message: {
                Text("服务器会修改拜访输入并排队生成新计划版本。当前计划不会被客户端直接修改；新版本生成后仍需审核与激活。")
            }
        }
    }

    private var workspace: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.md) {
            tripSelector
            if let error = viewModel.errorMessage {
                DSErrorState(message: error) {
                    Task { await viewModel.load() }
                }
            }
            if let message = viewModel.planChangeMessage {
                DSCard(padding: DS.Spacing.md) {
                    Label(message, systemImage: "clock.badge.checkmark")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(DS.ColorToken.success)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let trip = viewModel.selectedTrip {
                HStack(spacing: DS.Spacing.sm) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(trip.name)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text(destinationLine(trip))
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Spacer(minLength: 4)
                    DSBadge(
                        text: (trip.activePlan != nil || trip.activePlanVersionId != nil)
                            ? "已激活计划" : "计划预览",
                        color: (trip.activePlan != nil || trip.activePlanVersionId != nil)
                            ? DS.ColorToken.success : DS.ColorToken.warning
                    )
                }
                .padding(.horizontal, 2)

                if let plan = viewModel.plan {
                    PlanMapView(
                        plan: plan,
                        origin: trip.originPlace,
                        title: "地图",
                        visits: viewModel.visits,
                        customers: viewModel.customers,
                        isPlanActive: plan.status == .active,
                        isSubmittingChange: viewModel.isSubmittingChange,
                        onCancelVisit: { stop in
                            pendingCancelStop = stop
                            showsCancelConfirmation = true
                        }
                    )
                        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.card))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.Radius.card)
                                .stroke(DS.ColorToken.border, lineWidth: 0.8)
                        )
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    NavigationLink {
                        TripDetailView(tripId: trip.id, environment: environment)
                    } label: {
                        Label("打开 Mission 与计划详情", systemImage: "arrow.up.right")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.brand)
                            .frame(maxWidth: .infinity, minHeight: 46)
                            .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: DS.Radius.control))
                    }
                    .buttonStyle(.plain)
                } else if viewModel.isLoadingPlan {
                    DSLoadingState(message: "正在载入计划路线…")
                        .frame(maxHeight: .infinity)
                } else {
                    ContentUnavailableView(
                        "该行程还没有计划",
                        systemImage: DSIconCatalog.map,
                        description: Text("地图不会用直线或虚构路线填充空白。先打开 Mission 添加客户并生成计划。")
                    )
                    .frame(maxHeight: .infinity)
                    NavigationLink {
                        TripDetailView(tripId: trip.id, environment: environment)
                    } label: {
                        Label("打开行程并生成计划", systemImage: "arrow.right")
                            .font(.subheadline.weight(.semibold))
                    }
                }
            }
        }
    }

    private var tripSelector: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: 6) {
                Text("当前 Mission")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                Picker("选择行程", selection: Binding(
                    get: { viewModel.selectedTripID ?? "" },
                    set: { viewModel.selectTrip($0) }
                )) {
                    ForEach(viewModel.trips) { trip in
                        Text(trip.name).tag(trip.id)
                    }
                }
                .pickerStyle(.menu)
                .tint(DS.ColorToken.brand)
                .accessibilityLabel("选择地图对应的商务行程")
            }
        }
    }

    private func destinationLine(_ trip: Trip) -> String {
        let countries = trip.countries ?? []
        return countries.isEmpty ? trip.windowLabel : "\(countries.joined(separator: " · "))  ·  \(trip.windowLabel)"
    }
}
