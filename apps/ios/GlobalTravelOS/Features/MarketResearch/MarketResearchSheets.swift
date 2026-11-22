import SwiftUI

struct MarketResearchWorkspaceCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var objective = ""
    @State private var context = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    let onCreate: (String, String, String) async -> Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("工作空间") {
                    TextField("名称", text: $name)
                    TextField("研究目标（可选）", text: $objective, axis: .vertical)
                        .lineLimit(2...4)
                }
                Section("初始上下文") {
                    TextField("粘贴用户提供的背景信息（可选）", text: $context, axis: .vertical)
                        .lineLimit(4...8)
                }
                if let errorMessage {
                    Text(errorMessage).foregroundStyle(DS.ColorToken.danger)
                }
            }
            .navigationTitle("新建研究工作区")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "创建中…" : "创建") {
                        Task { await create() }
                    }
                    .disabled(isSaving || name.nilIfBlank == nil)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func create() async {
        isSaving = true
        defer { isSaving = false }
        if await onCreate(name, objective, context) {
            dismiss()
        } else {
            errorMessage = "工作空间创建失败，请检查网络或输入。"
        }
    }
}

struct MarketResearchQuestionCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var question = ""
    @State private var rationale = ""
    @State private var priority = 3.0
    @State private var criteria = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    let onCreate: (String, String, Int, String) async -> Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("研究问题") {
                    TextField("描述一个可独立核验的问题", text: $question, axis: .vertical)
                        .lineLimit(2...5)
                    TextField("为什么需要回答（可选）", text: $rationale, axis: .vertical)
                        .lineLimit(2...4)
                    VStack(alignment: .leading) {
                        HStack {
                            Text("优先级")
                            Spacer()
                            Text("\(Int(priority))")
                                .monospacedDigit()
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        Slider(value: $priority, in: 1...5, step: 1)
                            .tint(DS.ColorToken.brand)
                    }
                }
                Section("验收标准（每行一项）") {
                    TextField("可选", text: $criteria, axis: .vertical)
                        .lineLimit(3...6)
                }
                if let errorMessage {
                    Text(errorMessage).foregroundStyle(DS.ColorToken.danger)
                }
            }
            .navigationTitle("新增研究问题")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "保存中…" : "添加") {
                        Task { await create() }
                    }
                    .disabled(isSaving || question.nilIfBlank == nil)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func create() async {
        isSaving = true
        defer { isSaving = false }
        if await onCreate(question, rationale, Int(priority), criteria) {
            dismiss()
        } else {
            errorMessage = "问题未能保存，请检查网络后重试。"
        }
    }
}
