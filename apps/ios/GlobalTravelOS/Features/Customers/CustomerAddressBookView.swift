import SwiftUI

/// Customer-level address book. The customer-place relation is managed separately from
/// organization-level places: unlinking never deletes a shared place.
struct CustomerAddressBookSection: View {
    @EnvironmentObject private var environment: AppEnvironment
    let customerId: String

    @State private var places: [CustomerPlace] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showingEditor = false
    @State private var selectedPlaceToUnlink: CustomerPlace?
    @State private var showingUnlinkConfirmation = false

    private var linkedPlaceIDs: Set<String> { Set(places.map(\.placeId)) }

    var body: some View {
        Section {
            if isLoading && places.isEmpty {
                HStack {
                    ProgressView()
                    Text("正在读取企业地址…")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } else if places.isEmpty {
                ContentUnavailableView(
                    "尚未维护企业地址",
                    systemImage: "mappin.and.ellipse",
                    description: Text("添加总部、工厂或分支机构后，这些地址可用于地图和后续拜访规划。")
                )
                .frame(minHeight: 120)
            } else {
                ForEach(places) { place in
                    HStack(alignment: .top, spacing: DS.Spacing.sm) {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack(spacing: 7) {
                                Text(place.name)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                                if place.isPrimary {
                                    DSBadge(text: "主地址", color: DS.ColorToken.success)
                                }
                            }
                            if let label = place.label, !label.isEmpty {
                                Text(label)
                                    .font(.caption.weight(.medium))
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if !place.displayAddress.isEmpty {
                                Text(place.displayAddress)
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            HStack(spacing: 8) {
                                Label(
                                    place.hasValidCoordinates ? "坐标可用" : "缺少坐标",
                                    systemImage: place.hasValidCoordinates ? "location.fill" : "mappin.slash"
                                )
                                .foregroundStyle(
                                    place.hasValidCoordinates ? DS.ColorToken.success : DS.ColorToken.warning
                                )
                                if let source = place.geocodeSource, !source.isEmpty {
                                    Text("来源：\(source)")
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                                if let confidence = place.confidenceLabel {
                                    Text("置信度 \(confidence)")
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                            .font(.caption2)
                            .accessibilityElement(children: .combine)
                        }
                        Spacer(minLength: 4)
                        Menu {
                            if !place.isPrimary {
                                Button {
                                    Task { await makePrimary(place) }
                                } label: {
                                    Label("设为主地址", systemImage: "star")
                                }
                            }
                            Button(role: .destructive) {
                                selectedPlaceToUnlink = place
                                showingUnlinkConfirmation = true
                            } label: {
                                Label("解除关联", systemImage: "link")
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                                .font(.title3)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                                .frame(width: 36, height: 36)
                                .contentShape(Rectangle())
                        }
                        .accessibilityLabel("管理地址 \(place.name)")
                    }
                    .padding(.vertical, 4)
                }
            }

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Button {
                showingEditor = true
            } label: {
                Label("添加企业地址", systemImage: "plus.circle.fill")
                    .font(.subheadline.weight(.semibold))
            }
            .accessibilityIdentifier("customer-add-address")
        } header: {
            HStack {
                Text("企业地址")
                Spacer()
                Text("\(places.count) 个地点")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
        .task(id: customerId) { await load() }
        .sheet(isPresented: $showingEditor) {
            CustomerAddressEditorSheet(
                customerId: customerId,
                linkedPlaceIDs: linkedPlaceIDs,
                shouldDefaultPrimary: places.isEmpty,
                onSaved: { await load() }
            )
            .environmentObject(environment)
        }
        .confirmationDialog(
            "解除这个客户与地点的关联？",
            isPresented: $showingUnlinkConfirmation,
            titleVisibility: .visible
        ) {
            Button("解除关联", role: .destructive) {
                guard let place = selectedPlaceToUnlink else { return }
                Task { await unlink(place) }
            }
            Button("取消", role: .cancel) {
                selectedPlaceToUnlink = nil
            }
        } message: {
            Text("只会解除该客户与地点的关系。地点本身不会删除，其他客户和历史拜访不受影响。")
        }
    }

    @MainActor
    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            places = try await environment.customerRepository.customerPlaces(customerId: customerId)
                .sorted {
                    if $0.isPrimary != $1.isPrimary { return $0.isPrimary }
                    return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
                }
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    @MainActor
    private func makePrimary(_ place: CustomerPlace) async {
        do {
            _ = try await environment.customerRepository.linkCustomerPlace(
                customerId: customerId,
                request: CustomerPlaceLinkRequest(
                    placeId: place.placeId,
                    label: place.label,
                    isPrimary: true
                )
            )
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    @MainActor
    private func unlink(_ place: CustomerPlace) async {
        do {
            try await environment.customerRepository.unlinkCustomerPlace(
                customerId: customerId,
                placeId: place.placeId
            )
            selectedPlaceToUnlink = nil
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct CustomerAddressEditorSheet: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    let customerId: String
    let linkedPlaceIDs: Set<String>
    let onSaved: () async -> Void

    @State private var mode = "catalog"
    @State private var catalogPlaces: [PlaceCatalogItem] = []
    @State private var isLoadingCatalog = false
    @State private var query = ""
    @State private var catalogError: String?
    @State private var saveError: String?
    @State private var isSaving = false

    @State private var name = ""
    @State private var addressLine = ""
    @State private var city = ""
    @State private var region = ""
    @State private var countryCode = ""
    @State private var timezone = ""
    @State private var latitude = ""
    @State private var longitude = ""
    @State private var label: String
    @State private var isPrimary: Bool

    private let labelChoices = ["总部", "销售办公室", "工厂", "仓库", "服务中心", "分支机构", "其他"]

    init(
        customerId: String,
        linkedPlaceIDs: Set<String>,
        shouldDefaultPrimary: Bool,
        onSaved: @escaping () async -> Void
    ) {
        self.customerId = customerId
        self.linkedPlaceIDs = linkedPlaceIDs
        self.onSaved = onSaved
        _label = State(initialValue: shouldDefaultPrimary ? "总部" : "分支机构")
        _isPrimary = State(initialValue: shouldDefaultPrimary)
    }

    private var availablePlaces: [PlaceCatalogItem] {
        catalogPlaces.filter { !linkedPlaceIDs.contains($0.id) }
    }

    private var parsedLatitude: Double? {
        let value = latitude.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : Double(value)
    }

    private var parsedLongitude: Double? {
        let value = longitude.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : Double(value)
    }

    private var coordinateError: String? {
        let hasLatitude = !latitude.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let hasLongitude = !longitude.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if hasLatitude != hasLongitude {
            return "纬度和经度必须同时填写，或同时留空。"
        }
        if hasLatitude && parsedLatitude == nil { return "纬度格式无效。" }
        if hasLongitude && parsedLongitude == nil { return "经度格式无效。" }
        if let parsedLatitude, !(-90.0...90.0).contains(parsedLatitude) {
            return "纬度必须在 -90 到 90 之间。"
        }
        if let parsedLongitude, !(-180.0...180.0).contains(parsedLongitude) {
            return "经度必须在 -180 到 180 之间。"
        }
        return nil
    }

    private var placeRequest: PlaceCreateRequest {
        PlaceCreateRequest(
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            addressLine: cleaned(addressLine),
            city: cleaned(city),
            region: cleaned(region),
            countryCode: cleaned(countryCode),
            timezone: cleaned(timezone),
            lat: parsedLatitude,
            lon: parsedLongitude
        )
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: DS.Spacing.md) {
                Picker("添加方式", selection: $mode) {
                    Text("从地点库关联").tag("catalog")
                    Text("新建地点").tag("new")
                }
                .pickerStyle(.segmented)
                .accessibilityLabel("选择地址添加方式")

                if mode == "catalog" {
                    catalogContent
                } else {
                    newPlaceForm
                }
            }
            .padding(.horizontal, DS.Spacing.md)
            .padding(.top, DS.Spacing.sm)
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("添加客户地址")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .task { await loadCatalog() }
        }
    }

    private var catalogContent: some View {
        VStack(spacing: DS.Spacing.sm) {
            HStack(spacing: DS.Spacing.sm) {
                TextField("搜索地点名称或城市", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.search)
                    .onSubmit { Task { await loadCatalog() } }
                Button {
                    Task { await loadCatalog() }
                } label: {
                    Image(systemName: "magnifyingglass")
                        .frame(width: 38, height: 38)
                }
                .buttonStyle(.bordered)
                .accessibilityLabel("搜索地点库")
            }

            if isLoadingCatalog && catalogPlaces.isEmpty {
                DSLoadingState(message: "正在读取组织地点库…")
                    .frame(maxHeight: .infinity)
            } else if availablePlaces.isEmpty {
                DSEmptyState(
                    title: linkedPlaceIDs.isEmpty ? "地点库暂无可关联地址" : "没有更多可关联地点",
                    message: "可以切换到“新建地点”，录入总部、工厂或其他分支机构。",
                    systemImage: "mappin.and.ellipse"
                )
                .frame(maxHeight: .infinity)
            } else {
                List(availablePlaces) { place in
                    Button {
                        Task { await linkExisting(place) }
                    } label: {
                        HStack(spacing: DS.Spacing.sm) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(place.name)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                                if !place.displayAddress.isEmpty {
                                    Text(place.displayAddress)
                                        .font(.caption)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                                HStack(spacing: 7) {
                                    Text(place.hasValidCoordinates ? "坐标可用" : "缺少坐标")
                                    if let source = place.geocodeSource { Text("来源：\(source)") }
                                    if let confidence = place.confidenceLabel { Text(confidence) }
                                }
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            Spacer()
                            Image(systemName: "plus.circle.fill")
                                .foregroundStyle(DS.ColorToken.brand)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(isSaving)
                }
                .listStyle(.plain)
                .refreshable { await loadCatalog() }
            }

            if let catalogError {
                Text(catalogError)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.danger)
            }
            if let saveError {
                Text(saveError)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.danger)
            }
        }
    }

    private var newPlaceForm: some View {
        Form {
            Section("地点信息") {
                TextField("地点名称（如 Berlin HQ）", text: $name)
                TextField("街道地址", text: $addressLine)
                TextField("城市", text: $city)
                TextField("州 / 省 / 区域", text: $region)
                TextField("国家代码（如 DE）", text: $countryCode)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                TextField("时区（如 Europe/Berlin）", text: $timezone)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
            Section("地图坐标") {
                TextField("纬度（可留空）", text: $latitude)
                    .keyboardType(.decimalPad)
                TextField("经度（可留空）", text: $longitude)
                    .keyboardType(.decimalPad)
                Text("不填写坐标时，地点仍会保存，但不会被当作可定位地址参与路线计算。系统不会猜测坐标。")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                if let coordinateError {
                    Text(coordinateError)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.danger)
                }
            }
            Section("客户关联") {
                Picker("地址标签", selection: $label) {
                    ForEach(labelChoices, id: \.self) { choice in
                        Text(choice).tag(choice)
                    }
                }
                Toggle("设为主地址", isOn: $isPrimary)
            }
            if let saveError {
                Section { Text(saveError).font(.caption).foregroundStyle(DS.ColorToken.danger) }
            }
            Section {
                Button {
                    Task { await createAndLink() }
                } label: {
                    HStack {
                        Spacer()
                        if isSaving {
                            ProgressView()
                        } else {
                            Label("创建并关联", systemImage: "plus.circle.fill")
                        }
                        Spacer()
                    }
                }
                .disabled(isSaving || placeRequest.validationMessage != nil || coordinateError != nil)
            }
        }
    }

    @MainActor
    private func loadCatalog() async {
        isLoadingCatalog = true
        catalogError = nil
        defer { isLoadingCatalog = false }
        do {
            let page = try await environment.customerRepository.listPlaces(
                query: cleaned(query),
                countryCode: nil,
                page: PageRequest(limit: 100)
            )
            catalogPlaces = page.items
        } catch {
            catalogError = environment.errorMessage(error)
        }
    }

    @MainActor
    private func linkExisting(_ place: PlaceCatalogItem) async {
        guard !isSaving else { return }
        isSaving = true
        saveError = nil
        defer { isSaving = false }
        do {
            _ = try await environment.customerRepository.linkCustomerPlace(
                customerId: customerId,
                request: CustomerPlaceLinkRequest(
                    placeId: place.id,
                    label: label,
                    isPrimary: isPrimary
                )
            )
            await onSaved()
            dismiss()
        } catch {
            saveError = environment.errorMessage(error)
        }
    }

    @MainActor
    private func createAndLink() async {
        guard !isSaving, placeRequest.validationMessage == nil, coordinateError == nil else { return }
        isSaving = true
        saveError = nil
        defer { isSaving = false }
        do {
            _ = try await environment.customerRepository.createAndLinkCustomerPlace(
                customerId: customerId,
                place: placeRequest,
                label: label,
                isPrimary: isPrimary
            )
            await onSaved()
            dismiss()
        } catch {
            saveError = environment.errorMessage(error)
        }
    }

    private func cleaned(_ value: String) -> String? {
        let value = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}
