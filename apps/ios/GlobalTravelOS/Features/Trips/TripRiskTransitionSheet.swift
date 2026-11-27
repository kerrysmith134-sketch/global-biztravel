import SwiftUI

struct TripRiskTransitionSheet: View {
    @Environment(\.dismiss) private var dismiss
    let riskCase: TripRiskCase
    let target: TripRiskTransitionTarget
    let onSubmit: (TripRiskTransitionRequest) async throws -> Void

    @State private var note = ""
    @State private var nextReviewAt = Date().addingTimeInterval(2 * 60 * 60)
    @State private var resolutionBasis: TripRiskResolutionBasis
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
            initialValue: target == .resolved ? .humanAttestation : .falsePositiveConfirmed
        )
    }

    private var basisOptions: [TripRiskResolutionBasis] {
        switch target {
        case .resolved:
            return [.humanAttestation, .planRevalidated, .followUpCompleted, .tripClosed]
        case .dismissed:
            return [.falsePositiveConfirmed, .followUpDismissed]
        default:
            return []
        }
    }

    private var cleanedNote: String {
        note.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Python/Pydantic string length counts Unicode code points; UnicodeScalarView matches it.
    private var noteLength: Int {
        cleanedNote.unicodeScalars.count
    }

    private var isNoteValid: Bool {
        (3 ... 2_000).contains(noteLength)
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
                                Text(option.label).tag(option)
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
                    HStack {
                        Text("填写 3–2,000 个字符。说明会写入不可变审计事件。")
                        Spacer()
                        Text("\(noteLength)/2,000")
                            .monospacedDigit()
                    }
                    .font(.footnote)
                    .foregroundStyle(noteLength > 2_000 ? DS.ColorToken.danger : DS.ColorToken.textSecondary)
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
                    .disabled(isSubmitting || !isNoteValid)
                }
            }
        }
    }

    @MainActor
    private func submit() async {
        let cleanNote = cleanedNote
        guard (3 ... 2_000).contains(cleanNote.unicodeScalars.count) else { return }
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
