import SwiftUI

private struct ResolutionBasisOption: Identifiable {
    let value: String
    let label: String

    var id: String { value }
}

struct TripRiskTransitionSheet: View {
    @Environment(\.dismiss) private var dismiss
    let riskCase: TripRiskCase
    let target: TripRiskTransitionTarget
    let onSubmit: (TripRiskTransitionRequest) async throws -> Void

    @State private var note = ""
    @State private var nextReviewAt = Date().addingTimeInterval(2 * 60 * 60)
    @State private var resolutionBasis: String
    @State private var isSubmitting = false
    @State private var errorMessage: String?

    init(
        riskCase: TripRiskCase,
        target: TripRiskTransitionTarget,
        onSubmit: @escaping (TripRiskTransitionRequest) async throws -> Void
    ) {
        self.riskCase = riskCase
        self.target = target
        self.onSubmit = onSubmit
        _resolutionBasis = State(
            initialValue: target == .resolved ? "HUMAN_ATTESTATION" : "FALSE_POSITIVE_CONFIRMED"
        )
    }

    private var basisOptions: [ResolutionBasisOption] {
        if target == .resolved {
            return [
                .init(value: "HUMAN_ATTESTATION", label: "人工确认已解决"),
                .init(value: "PLAN_REVALIDATED", label: "计划已重新验证"),
                .init(value: "FOLLOW_UP_COMPLETED", label: "来源跟进已完成"),
                .init(value: "TRIP_CLOSED", label: "行程已关闭")
            ]
        }
        if target == .dismissed {
            return [
                .init(value: "FALSE_POSITIVE_CONFIRMED", label: "全部主动建议已驳回"),
                .init(value: "FOLLOW_UP_DISMISSED", label: "全部来源跟进已驳回")
            ]
        }
        return []
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("处置动作") {
                    LabeledContent("目标状态", value: target.label)
                    LabeledContent("当前版本", value: "v\(riskCase.version)")
                }

                if target == .waitingExternal {
                    Section("复核安排") {
                        DatePicker(
                            "下次复核时间",
                            selection: $nextReviewAt,
                            in: Date().addingTimeInterval(60)...,
                            displayedComponents: [.date, .hourAndMinute]
                        )
                        Text("等待外部确认必须安排未来复核时间。到期后由服务端风险扫描判断是否升级。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                if target.requiresResolutionBasis {
                    Section("关闭依据") {
                        Picker("依据", selection: $resolutionBasis) {
                            ForEach(basisOptions) { option in
                                Text(option.label).tag(option.value)
                            }
                        }
                        Text(target == .resolved
                             ? "服务端会核验所选依据；人工确认不会替代来源数据的事实校验。"
                             : "服务端会核验关联建议或跟进项是否已全部驳回，未满足时不会关闭风险。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("处置说明") {
                    TextEditor(text: $note)
                        .frame(minHeight: 110)
                    Text("至少填写 3 个字符。说明会写入不可变审计事件。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("更新风险状态")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                        .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await submit() }
                    } label: {
                        if isSubmitting {
                            ProgressView()
                        } else {
                            Text("确认")
                        }
                    }
                    .disabled(isSubmitting || note.trimmingCharacters(in: .whitespacesAndNewlines).count < 3)
                }
            }
        }
    }

    @MainActor
    private func submit() async {
        let cleanNote = note.trimmingCharacters(in: .whitespacesAndNewlines)
        guard cleanNote.count >= 3 else { return }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }

        let request = TripRiskTransitionRequest(
            expectedVersion: riskCase.version,
            toStatus: target,
            note: cleanNote,
            nextReviewAt: target == .waitingExternal ? nextReviewAt : nil,
            resolutionBasis: target.requiresResolutionBasis ? resolutionBasis : nil
        )
        do {
            try await onSubmit(request)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
