import SwiftUI

/// 新增拜访表单（P0 审计 5.5：客户端此前只能通过 Excel 导入建拜访）。
struct VisitCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    let tripId: String
    let environment: AppEnvironment
    let onCreated: () async -> Void

    @State private var title = ""
    @State private var customerName = ""
    @State private var city = ""
    @State private var countryCode = "DE"
    @State private var latitude = "52.5200"
    @State private var longitude = "13.4050"
    @State private var serviceMinutes = "60"
    @State private var movementMode = "AIR"
    @State private var customers: [Customer] = []
    @State private var selectedCustomerID = ""
    @State private var customerPlaces: [CustomerPlace] = []
    @State private var selectedCustomerPlaceID = ""
    @State private var isLoadingCustomers = false
    @State private var isLoadingCustomerPlaces = false
    @State private var customerPlacesMessage: String?
    @State private var saving = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("拜访") {
                    TextField("拜访标题（如 大众总部年度复审）", text: $title)
                    TextField("客户/地点名称", text: $customerName)
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
                        } else {
                            Text(customerPlacesMessage ?? "该客户暂无可选的已登记地址，可继续填写新地点。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                    }
                }
                Section("地点") {
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
                    Section { Text(errorMessage).font(.caption).foregroundStyle(.red) }
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
                    Button("保存") {
                        saving = true
                        Task {
                            do {
                                _ = try await environment.visitRepository.createVisit(
                                    tripId: tripId,
                                    request: VisitCreateRequest(
                                        placeId: selectedCustomerPlaceID.isEmpty ? nil : selectedCustomerPlaceID,
                                        customerId: selectedCustomerID.isEmpty ? nil : selectedCustomerID,
                                        placeName: customerName.trimmingCharacters(in: .whitespaces).isEmpty
                                            ? title : customerName,
                                        city: city.isEmpty ? nil : city,
                                        countryCode: countryCode.uppercased(),
                                        latitude: Double(latitude),
                                        longitude: Double(longitude),
                                        timezone: nil,
                                        title: title,
                                        serviceMinutes: Int(serviceMinutes) ?? 60,
                                        movementMode: movementMode
                                    )
                                )
                                await onCreated()
                                dismiss()
                            } catch {
                                errorMessage = environment.errorMessage(error)
                            }
                            saving = false
                        }
                    }
                    .disabled(
                        saving || title.trimmingCharacters(in: .whitespaces).isEmpty
                    )
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
            customerPlaces = try await environment.customerRepository.customerPlaces(customerId: customerId)
            if customerPlaces.isEmpty {
                customerPlacesMessage = "该客户暂无已登记地址，可填写新的实际拜访地点。"
            }
        } catch {
            // Local mode has not implemented customer_places yet; preserve the create-new-place flow.
            customerPlacesMessage = environment.isLocalMode
                ? "本地模式尚未支持客户地址关联；仍可关联客户并填写新地点。"
                : "读取客户地址失败：\\(environment.errorMessage(error))。仍可填写新地点。"
        }
    }

    private func applyCustomerPlace(_ placeID: String) {
        guard !placeID.isEmpty,
              let place = customerPlaces.first(where: { $0.placeId == placeID }) else { return }
        customerName = place.name
        city = place.city ?? ""
        countryCode = place.countryCode ?? countryCode
        latitude = place.lat.map(String.init) ?? ""
        longitude = place.lon.map(String.init) ?? ""
    }
    }
}
