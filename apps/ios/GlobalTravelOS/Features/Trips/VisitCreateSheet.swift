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
    @State private var saving = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("拜访") {
                    TextField("拜访标题（如 大众总部年度复审）", text: $title)
                    TextField("客户/地点名称", text: $customerName)
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
    }
}
