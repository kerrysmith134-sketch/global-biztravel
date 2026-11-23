import SwiftUI

/// Turns a global customer-location selection into a visit in an existing Mission.
struct CustomerToMissionSheet: View {
    @Environment(\.dismiss) private var dismiss

    let location: GlobalCustomerMapLocation
    let environment: AppEnvironment
    let onCreated: () async -> Void

    @State private var trips: [Trip] = []
    @State private var selectedTripID = ""
    @State private var title: String
    @State private var serviceMinutes = "60"
    @State private var priority = 2
    @State private var movementMode = "GROUND"
    @State private var isLoading = false
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var showingSuccess = false
    @State private var successMessage = ""

    init(
        location: GlobalCustomerMapLocation,
        environment: AppEnvironment,
        onCreated: @escaping () async -> Void
    ) {
        self.location = location
        self.environment = environment
        self.onCreated = onCreated
        _title = State(initialValue: "拜访：\(location.customer.name)")
    }

    private var selectedTrip: Trip? {
        trips.first { $0.id == selectedTripID }
    }

    private var availableTrips: [Trip] {
        trips.filter {
            !["HOLD", "COMPLETED", "CANCELLED"].contains(($0.status ?? "").uppercased())
        }
    }

    private var parsedServiceMinutes: Int? {
        guard let value = Int(serviceMinutes), (1...1440).contains(value) else { return nil }
        return value
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("目标客户") {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(location.customer.name)
                                .font(.subheadline.weight(.semibold))
                            Text("\(location.customer.tier) 级 · \(location.customer.industry ?? "行业未登记")")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        Spacer()
                        DSBadge(
                            text: location.customer.tier,
                            color: DS.ColorToken.tier(location.customer.tier)
                        )
                    }
                    Text([location.place.label, location.place.name].compactMap { $0 }.joined(separator: " · "))
                        .font(.subheadline.weight(.medium))
                    if !location.place.displayAddress.isEmpty {
                        Text(location.place.displayAddress)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if location.place.hasValidCoordinates {
                        Label("地点坐标可用于路线规划", systemImage: "location.fill")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.success)
                    } else {
                        Label("地点缺少有效坐标；拜访可以加入，但规划时可能无法安排", systemImage: "mappin.slash")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.warning)
                    }
                }

                Section("加入 Mission") {
                    if isLoading {
                        ProgressView("正在读取可用 Mission…")
                    } else if availableTrips.isEmpty {
                        ContentUnavailableView(
                            "没有可加入的 Mission",
                            systemImage: "airplane",
                            description: Text("请先创建一个未完成的商务 Mission，再从全球客户地图添加拜访。")
                        )
                    } else {
                        Picker("目标 Mission", selection: $selectedTripID) {
                            ForEach(availableTrips) { trip in
                                Text(trip.name).tag(trip.id)
                            }
                        }
                        if let trip = selectedTrip {
                            LabeledContent("时间窗口", value: trip.window?.displayRange ?? "未设置")
                            LabeledContent("当前状态", value: trip.status ?? "未知")
                        }
                    }
                }

                Section("拜访安排") {
                    TextField("拜访标题", text: $title)
                    TextField("服务时长（分钟）", text: $serviceMinutes)
                        .keyboardType(.numberPad)
                    Picker("优先级", selection: $priority) {
                        Text("高").tag(1)
                        Text("正常").tag(2)
                        Text("低").tag(3)
                    }
                    Picker("交通方式", selection: $movementMode) {
                        Text("市内驾车").tag("GROUND")
                        Text("铁路").tag("RAIL")
                        Text("航空 / 长途").tag("AIR")
                    }
                }

                Section {
                    Label(
                        "新增拜访会更新 Mission 输入版本。已有计划将不再代表最新输入，需要重新生成并审核。",
                        systemImage: "arrow.triangle.2.circlepath"
                    )
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.warning)
                    .fixedSize(horizontal: false, vertical: true)
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.danger)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .navigationTitle("加入 Mission")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                        .disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("加入") {
                        Task { await addVisit() }
                    }
                    .disabled(
                        isLoading || isSaving || availableTrips.isEmpty ||
                            selectedTripID.isEmpty ||
                            title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                            parsedServiceMinutes == nil
                    )
                }
            }
            .task { await loadTrips() }
            .alert("已加入 Mission", isPresented: $showingSuccess) {
                Button("完成", role: .cancel) { dismiss() }
            } message: {
                Text(successMessage)
            }
        }
    }

    @MainActor
    private func loadTrips() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            var cursor: String?
            var all: [Trip] = []
            var pageCount = 0
            repeat {
                let page = try await environment.tripRepository.listTrips(
                    page: PageRequest(limit: 100, cursor: cursor)
                )
                all.append(contentsOf: page.items)
                cursor = page.nextCursor
                pageCount += 1
            } while cursor != nil && pageCount < 50

            trips = all
            if let lastTripID = environment.settings.lastTripId,
               availableTrips.contains(where: { $0.id == lastTripID }) {
                selectedTripID = lastTripID
            } else {
                selectedTripID = availableTrips.first?.id ?? ""
            }
            if cursor != nil {
                errorMessage = "Mission 数量较多，仅加载最近 5,000 个。"
            }
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    @MainActor
    private func addVisit() async {
        guard !isSaving, let trip = selectedTrip, let serviceMinutes = parsedServiceMinutes else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let existing = try await environment.visitRepository.listVisits(tripId: trip.id)
            if let duplicate = existing.first(where: {
                $0.customerId == location.customer.id && $0.placeId == location.place.id && $0.isOpen
            }) {
                errorMessage = "该客户地址已在此 Mission 中有未完成拜访（\(duplicate.displayName)），没有重复创建。"
                return
            }

            _ = try await environment.visitRepository.createVisit(
                tripId: trip.id,
                request: VisitCreateRequest(
                    placeId: location.place.id,
                    customerId: location.customer.id,
                    title: title.trimmingCharacters(in: .whitespacesAndNewlines),
                    serviceMinutes: serviceMinutes,
                    priority: priority,
                    movementMode: movementMode
                )
            )
            environment.rememberTrip(trip.id)
            successMessage = "已将 \(location.customer.name) · \(location.place.name) 加入 \(trip.name)。请重新生成并审核最新计划。"
            await onCreated()
            showingSuccess = true
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}
