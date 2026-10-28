import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    @State private var baseURLString = ""
    @State private var orgId = ""
    @State private var userId = ""
    @State private var operatorName = ""
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            Form {
                Section("服务端") {
                    TextField("Base URL", text: $baseURLString)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section("身份（本地开发）") {
                    TextField("组织 ID (X-Org-Id)", text: $orgId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("用户 ID (X-User-Id)", text: $userId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("称呼（可选）", text: $operatorName)
                }
                Section {
                    Button("恢复开发默认") {
                        baseURLString = APIConfiguration.defaultBaseURLString
                        orgId = APIConfiguration.defaultOrgId
                        userId = APIConfiguration.defaultUserId
                        operatorName = ""
                    }
                } footer: {
                    Text("默认身份对应本地种子数据：安纳瑞能源 / kerry。生产环境由网关注入身份，不应在客户端保存凭证。")
                }
                Section("当前状态") {
                    LabeledContent("离线显示", value: environment.offlineSummary)
                    LabeledContent("设备 ID", value: environment.settings.deviceId)
                    LabeledContent("已排队写入", value: "\(environment.offlineStore.pendingCount()) 条")
                }
            }
            .navigationTitle("设置")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        environment.updateSettings { settings in
                            settings.baseURLString = baseURLString.trimmingCharacters(in: .whitespaces)
                            settings.orgId = orgId.trimmingCharacters(in: .whitespaces)
                            settings.userId = userId.trimmingCharacters(in: .whitespaces)
                            let name = operatorName.trimmingCharacters(in: .whitespaces)
                            settings.operatorName = name.isEmpty ? nil : name
                            settings.isAuthenticated = true
                        }
                        dismiss()
                    }
                }
            }
            .task {
                guard !loaded else { return }
                loaded = true
                baseURLString = environment.settings.baseURLString
                orgId = environment.settings.orgId
                userId = environment.settings.userId
                operatorName = environment.settings.operatorName ?? ""
            }
        }
    }
}
