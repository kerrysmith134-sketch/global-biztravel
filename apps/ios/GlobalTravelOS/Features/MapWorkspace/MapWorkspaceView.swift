import SwiftUI

private struct CustomerFetchResult {
    var customerId: String
    var customer: Customer?
    var errorMessage: String?
}

private struct CustomerPlacesFetchResult {
    var customerId: String
    var places: [CustomerPlace]
    var errorMessage: String?
}

@MainActor
final class MapWorkspaceViewModel: ObservableObject {
    @Published private(set) var trips: [Trip] = []
    @Published private(set) var plan: PlanVersionDetail?
    @Published private(set) var visits: [TripVisit] = []
    @Published private(set) var customers: [Customer] = []
    @Published private(set) var customerLocations: [CustomerMapLocation] = []
    @Published private(set) var customerPlacesMessage: String?
    @Published private(set) var isLoadingCustomerPlaces = false
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
                async let contextLoad = loadMissionContext(tripId: selectedTripID)
                await loadSelectedPlan()
                await contextLoad
            } else {
                await loadSelectedPlan()
            }
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func selectTrip(_ id: String) {
        guard id != selectedTripID else { return }
        errorMessage = nil
        planChangeMessage = nil
        selectedTripID = id
        plan = nil
        planChangeMessage = nil
        visits = []
        customers = []
        customerLocations = []
        customerPlacesMessage = nil
        Task {
            async let contextLoad = loadMissionContext(tripId: id)
            await loadSelectedPlan()
            await contextLoad
        }
    }

    /// Load visit and customer context independently from the plan payload.
    /// The map remains usable if CRM enrichment is unavailable; missing customer metadata is omitted.
    private func loadMissionContext(tripId: String) async {
        async let visitResult = try? await environment.visitRepository.listVisits(tripId: tripId)
        let visitResponse = await visitResult

        guard selectedTripID == tripId else { return }
        guard let loadedVisits = visitResponse else {
            visits = []
            customers = []
            customerLocations = []
            customerPlacesMessage = "读取当前 Mission 的拜访列表失败，客户地址状态未知。"
            return
        }

        visits = loadedVisits
        customerLocations = []
        customerPlacesMessage = nil
        let referencedCustomerIDs = Array(Set(loadedVisits.compactMap(\\.customerId))).sorted()
        guard !referencedCustomerIDs.isEmpty else {
            customers = []
            customerPlacesMessage = loadedVisits.isEmpty
                ? "当前 Mission 尚无拜访；新增拜访并关联 CRM 客户后，客户地址会出现在地图上。"
                : "当前 Mission 的拜访尚未关联 CRM 客户；可在新增拜访时选择客户。"
            return
        }

        isLoadingCustomerPlaces = true
        defer { isLoadingCustomerPlaces = false }
        let customerRepository = environment.customerRepository
        var customerIterator = referencedCustomerIDs.makeIterator()
        var loadedCustomers: [Customer] = []
        var customerFailures = 0

        await withTaskGroup(of: CustomerFetchResult.self) { group in
            for _ in 0..<min(4, referencedCustomerIDs.count) {
                guard let customerID = customerIterator.next() else { break }
                group.addTask {
                    do {
                        return CustomerFetchResult(
                            customerId: customerID,
                            customer: try await customerRepository.customer(id: customerID),
                            errorMessage: nil
                        )
                    } catch {
                        return CustomerFetchResult(customerId: customerID, customer: nil, errorMessage: error.localizedDescription)
                    }
                }
            }

            while let result = await group.next() {
                if let customer = result.customer { loadedCustomers.append(customer) }
                if result.errorMessage != nil { customerFailures += 1 }
                if let nextID = customerIterator.next() {
                    group.addTask {
                        do {
                            return CustomerFetchResult(
                                customerId: nextID,
                                customer: try await customerRepository.customer(id: nextID),
                                errorMessage: nil
                            )
                        } catch {
                            return CustomerFetchResult(customerId: nextID, customer: nil, errorMessage: error.localizedDescription)
                        }
                    }
                }
            }
        }

        guard selectedTripID == tripId else { return }
        customers = loadedCustomers.sorted { $0.name < $1.name }
        await loadCustomerPlaces(
            for: customers,
            tripId: tripId,
            missingCustomerCount: customerFailures
        )
    }

    /// Fetch linked addresses only for customers referenced by this Mission.
    /// Four concurrent requests cap API pressure without serialising every customer lookup.
    private func loadCustomerPlaces(
        for missionCustomers: [Customer],
        tripId: String,
        missingCustomerCount: Int
    ) async {
        customerLocations = []
        customerPlacesMessage = nil
        guard !missionCustomers.isEmpty else {
            customerPlacesMessage = "\(missingCustomerCount) 个 CRM 客户档案读取失败，客户地址状态未知。"
            return
        }
        guard !environment.isLocalMode else {
            customerPlacesMessage = "本地模式尚未建立客户—地址关联表；当前仅显示行程拜访点。"
            return
        }

        let repository = environment.customerRepository
        var iterator = missionCustomers.makeIterator()
        var locations: [CustomerMapLocation] = []
        var failures = 0

        await withTaskGroup(of: CustomerPlacesFetchResult.self) { group in
            for _ in 0..<min(4, missionCustomers.count) {
                guard let customer = iterator.next() else { break }
                group.addTask {
                    do {
                        return CustomerPlacesFetchResult(
                            customerId: customer.id,
                            places: try await repository.customerPlaces(customerId: customer.id),
                            errorMessage: nil
                        )
                    } catch {
                        return CustomerPlacesFetchResult(
                            customerId: customer.id,
                            places: [],
                            errorMessage: error.localizedDescription
                        )
                    }
                }
            }

            while let result = await group.next() {
                if let customer = missionCustomers.first(where: { $0.id == result.customerId }) {
                    locations.append(contentsOf: result.places.map {
                        CustomerMapLocation(customer: customer, place: $0)
                    })
                }
                if result.errorMessage != nil { failures += 1 }
                if let nextCustomer = iterator.next() {
                    group.addTask {
                        do {
                            return CustomerPlacesFetchResult(
                                customerId: nextCustomer.id,
                                places: try await repository.customerPlaces(customerId: nextCustomer.id),
                                errorMessage: nil
                            )
                        } catch {
                            return CustomerPlacesFetchResult(
                                customerId: nextCustomer.id,
                                places: [],
                                errorMessage: error.localizedDescription
                            )
                        }
                    }
                }
            }
        }

        guard selectedTripID == tripId else { return }
        customerLocations = locations.sorted {
            if $0.customer.name != $1.customer.name { return $0.customer.name < $1.customer.name }
            if $0.place.isPrimary != $1.place.isPrimary { return $0.place.isPrimary }
            return $0.place.name < $1.place.name
        }
        var warnings: [String] = []
        if failures > 0 {
            warnings.append("\(failures) 个客户的地址读取失败，相关地址状态未知")
        } else if locations.isEmpty {
            warnings.append("当前 Mission 的客户没有返回已关联地址；请在客户档案中维护总部、工厂或拜访地址")
        }
        if missingCustomerCount > 0 {
            warnings.append("\(missingCustomerCount) 个被拜访客户档案读取失败")
        }
        customerPlacesMessage = warnings.isEmpty ? nil : warnings.joined(separator: "；")
    }

    /// The server is the only authority that mutates visit inputs and produces a new plan version.
    func cancelVisit(_ stop: PlanStop) async {
        guard let trip = selectedTrip, let currentPlan = self.plan, currentPlan.status == .active else {
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
                    basePlanVersionId: currentPlan.id,
                    baseTripVersion: trip.version,
                    operationType: .cancelVisit,
                    payload: ["visit_id": stop.visitId],
                    source: "MAP",
                    reason: "地图工作区取消拜访并重新优化路线"
                )
            )
            planChangeMessage = "变更已受理 · 正在排队求解 · 任务 \(accepted.optimizationJobId.prefix(8))"

            // Follow the same server-owned job lifecycle as the Generate Plan flow.
            // The old active plan stays visible until a new server version is returned.
            for attempt in 0..<30 {
                guard selectedTripID == trip.id else { return }
                let job = try await environment.planRepository.job(id: accepted.optimizationJobId)
                if job.resolvedStatus.isTerminal {
                    guard job.resolvedStatus.isSuccess else {
                        let detail = job.displayError.map { "：\($0)" } ?? ""
                        errorMessage = "计划优化\(job.statusLabel)\(detail)"
                        planChangeMessage = nil
                        return
                    }
                    guard let newPlanId = job.producedPlanVersionId else {
                        errorMessage = "优化任务已完成，但服务端没有返回计划版本编号。请打开行程详情核查。"
                        planChangeMessage = nil
                        return
                    }
                    let newPlan = try await environment.planRepository.planDetail(
                        tripId: trip.id,
                        planId: newPlanId
                    )
                    guard selectedTripID == trip.id else { return }
                    plan = newPlan
                    planChangeMessage = "新计划 \(newPlan.versionLabel) 已生成草稿。旧激活计划仍保留，请检查新方案后再激活。"
                    return
                }

                planChangeMessage = "变更已受理 · \(job.statusLabel) · 尝试 \(job.attempts ?? 0) 次"
                if attempt < 29 {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                }
            }
            planChangeMessage = "优化仍在后台运行 · 任务 \(accepted.optimizationJobId.prefix(8))。当前计划未被客户端修改，请稍后刷新查看新版本。"
        } catch {
            errorMessage = environment.errorMessage(error)
            if planChangeMessage != nil {
                planChangeMessage = "变更请求已受理，但暂时无法读取优化任务状态。请稍后刷新计划版本。"
            }
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
                        .foregroundStyle(message.contains("已生成草稿") ? DS.ColorToken.success : DS.ColorToken.warning)
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
                        text: viewModel.plan?.status == .active
                            ? "已激活计划" : (viewModel.plan == nil ? "加载计划" : "计划预览"),
                        color: viewModel.plan?.status == .active
                            ? DS.ColorToken.success : DS.ColorToken.warning
                    )
                }
                .padding(.horizontal, 2)

                if let plan = viewModel.plan {
                    PlanMapView(
                        customerLocations: viewModel.customerLocations,
                        customerPlacesMessage: viewModel.customerPlacesMessage,
                        isLoadingCustomerPlaces: viewModel.isLoadingCustomerPlaces,
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
