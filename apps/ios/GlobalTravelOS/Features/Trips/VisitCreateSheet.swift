import SwiftUI

/// 新增拜访：明确关联 CRM 客户和客户真实地址，不把客户总部自动当成拜访地点。
struct VisitCreateSheet: View {
    @Environment(\.dismiss) private var dismiss

    let tripId: String
    let environment: AppEnvironment
    let onCreated: () async -> Void

    @State private var title = ""
    @State private var customerName = ""
    @State private var city = ""
    @State private var countryCode = ""
    @State private var latitude = ""
    @State private var longitude = ""
    @State private var serviceMinutes = "60"
    @State private var movementMode = "GROUND"
    @State private var customers: [Customer] = []
    @State private var selectedCustomerID = ""
    @State private var customerPlaces: [CustomerPlace] = []
    @State private var selectedCustomerPlaceID = ""
    @State private var addsNewPlaceToCustomer = true
    @State private var didCreateVisit = false
    @State private var isLoadingCustomers = false
    @State private var isLoadingCustomerPlaces = false
    @State private var customerPlacesMessage: String?
    @State private var saving = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("拜访") {
                    TextField("拜访标题（如 客户年度复审）", text: $title)
                    TextField("实际拜访地点名称", text: $customerName)
                }

                Section("CRM 客户") {
                    Picker("关联客户", selection: $selectedCustomerID) {
                        Text("不关联客户").tag("")
                        ForEach(customers) { customer in
                            Text("\(customer.name) · \(customer.tier)")
                                .tag(customer.id)
                        }
                    }
                    .disabled(isLoadingCustomers || customers.isEmpty)

                    if isLoadingCustomers {
                        ProgressView("正在读取客户…")
                    } else if customers.isEmpty {
                        Text("客户库暂无可选客户，或客户列表读取失败。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }

                    if !selectedCustomerID.isEmpty {
                        if isLoadingCustomerPlaces {
                            ProgressView("正在读取客户已登记地址…")
                        } else if !customerPlaces.isEmpty {
                            Picker("使用客户已登记地址", selection: $selectedCustomerPlaceID) {
                                Text("新建地点").tag("")
                                ForEach(customerPlaces) { place in
                                    Text("\(place.label ?? place.name)\(place.isPrimary ? " · 主地址" : "")")
                                        .tag(place.placeId)
                                }
                            }
                            .onChange(of: selectedCustomerPlaceID) { _, placeID in
                                applyCustomerPlace(placeID)
                            }
                        }

                        if let customerPlacesMessage {
                            Text(customerPlacesMessage)
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }

                        if selectedCustomerPlaceID.isEmpty {
                            Toggle("将新地点加入客户地址簿", isOn: $addsNewPlaceToCustomer)
                            Text("仅关联为普通地址，不会自动设为客户主地址。")
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                    }
                }

                Section("实际地点") {
                    TextField("城市", text: $city)
                    TextField("国家代码（如 DE）", text: $countryCode)
                        .textInputAutocapitalization(.characters)
                    TextField("纬度", text: $latitude)
                        .keyboardType(.decimalPad)
                    TextField("经度", text: $longitude)
                        .keyboardType(.decimalPad)
                }

                Section("时长与交通") {
                    TextField("服务分钟", text: $serviceMinutes)
                        .keyboardType(.numberPad)
                    Picker("交通方式", selection: $movementMode) {
                        Text("航空（跨洲/长途）").tag("AIR")
                        Text("铁路（城市间）").tag("RAIL")
                        Text("驾车（市内）").tag("GROUND")
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.danger)
                    }
                }
            }
            .navigationTitle("新增拜访")
            .task { await loadCustomers() }
            .onChange(of: selectedCustomerID) { _, customerID in
                Task { await loadCustomerPlaces(customerId: customerID) }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(didCreateVisit ? "已创建" : "保存") {
                        Task { await saveVisit() }
                    }
                    .disabled(
                        saving || didCreateVisit ||
                            title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
            }
        }
    }

    private func loadCustomers() async {
        isLoadingCustomers = true
        defer { isLoadingCustomers = false }
        do {
            let page = try await environment.customerRepository.listCustomers(
                query: nil,
                page: PageRequest(limit: 200)
            )
            customers = page.items.filter(\.isActive)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func loadCustomerPlaces(customerId: String) async {
        customerPlaces = []
        selectedCustomerPlaceID = ""
        customerPlacesMessage = nil
        guard !customerId.isEmpty else { return }

        isLoadingCustomerPlaces = true
        defer { isLoadingCustomerPlaces = false }
        do {
            let places = try await environment.customerRepository.customerPlaces(customerId: customerId)
            guard selectedCustomerID == customerId else { return }
            customerPlaces = places
            if places.isEmpty {
                customerPlacesMessage = "该客户暂无已登记地址，可填写新的实际拜访地点。"
            }
        } catch {
            guard selectedCustomerID == customerId else { return }
            customerPlacesMessage = "读取客户地址失败：\(environment.errorMessage(error))。仍可填写新地点。"
        }
    }

    private func applyCustomerPlace(_ placeID: String) {
        guard !placeID.isEmpty,
              let place = customerPlaces.first(where: { $0.placeId == placeID }) else { return }
        customerName = place.name
        city = place.city ?? ""
        countryCode = place.countryCode ?? countryCode
        if place.hasValidCoordinates, let lat = place.lat, let lon = place.lon {
            latitude = String(lat)
            longitude = String(lon)
        } else {
            latitude = ""
            longitude = ""
        }
    }

    private func saveVisit() async {
        guard !saving else { return }
        errorMessage = nil

        let latitudeValue = Double(latitude)
        let longitudeValue = Double(longitude)
        if (latitudeValue == nil) != (longitudeValue == nil) {
            errorMessage = "纬度和经度必须同时填写，或同时留空。"
            return
        }

        if selectedCustomerPlaceID.isEmpty && (latitudeValue == nil || longitudeValue == nil) {
            errorMessage = "新建地点必须填写真实经纬度；也可选择客户已经登记的地址。"
            return
        }
        if let latitudeValue, let longitudeValue {
            guard (-90.0...90.0).contains(latitudeValue),
                  (-180.0...180.0).contains(longitudeValue) else {
                errorMessage = "经纬度超出有效范围。"
                return
            }
        }

        let serviceValue = Int(serviceMinutes) ?? 60
        guard (1...1440).contains(serviceValue) else {
            errorMessage = "服务时长必须在 1–1440 分钟之间。"
            return
        }

        saving = true
        defer { saving = false }
        do {
            let createdVisit = try await environment.visitRepository.createVisit(
                tripId: tripId,
                request: VisitCreateRequest(
                    placeId: selectedCustomerPlaceID.isEmpty ? nil : selectedCustomerPlaceID,
                    customerId: selectedCustomerID.isEmpty ? nil : selectedCustomerID,
                    placeName: customerName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        ? title : customerName,
                    city: city.isEmpty ? nil : city,
                    countryCode: countryCode.isEmpty ? nil : countryCode.uppercased(),
                    latitude: latitudeValue,
                    longitude: longitudeValue,
                    timezone: nil,
                    title: title,
                    serviceMinutes: serviceValue,
                    movementMode: movementMode
                )
            )

            if addsNewPlaceToCustomer,
               !selectedCustomerID.isEmpty,
               selectedCustomerPlaceID.isEmpty {
                let customerID = selectedCustomerID
                guard let createdPlaceID = createdVisit.placeId else {
                    didCreateVisit = true
                    await onCreated()
                    errorMessage = "拜访已创建，但服务端没有返回地点编号；请在客户档案中检查地址，勿重复保存。"
                    return
                }
                do {
                    _ = try await environment.customerRepository.linkCustomerPlace(
                        customerId: customerID,
                        request: CustomerPlaceLinkRequest(
                            placeId: createdPlaceID,
                            label: "拜访地址",
                            isPrimary: false
                        )
                    )
                } catch {
                    didCreateVisit = true
                    await onCreated()
                    errorMessage = "拜访已创建，但新地点加入客户地址簿失败：\(environment.errorMessage(error))。请稍后在客户档案补关联，勿重复保存。"
                    return
                }
            }

            await onCreated()
            dismiss()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}
