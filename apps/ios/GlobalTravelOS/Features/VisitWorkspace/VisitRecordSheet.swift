import SwiftUI

struct VisitRecordSheet: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    let visit: TripVisit

    @State private var outcome: VisitOutcome = .met
    @State private var notes = ""
    @State private var followUpTitle = ""
    @State private var followUpDue = Date().addingTimeInterval(86_400 * 7)
    @State private var wantsFollowUp = false
    @State private var isSubmitting = false
    @State private var submission: VisitSubmissionResult?
    @State private var minutes: MinutesDraft?
    @State private var isLoadingMinutes = false
    @State private var crmJob: CrmSyncJob?
    @State private var isSyncingCRM = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("拜访") {
                    LabeledContent("拜访", value: visit.displayName)
                    LabeledContent("当前状态", value: visit.statusLabel)
                }

                Section("现场结果") {
                    Picker("结果", selection: $outcome) {
                        ForEach(VisitOutcome.allCases) { item in
                            Label(item.label, systemImage: item.glyph).tag(item)
                        }
                    }
                    TextField("现场记录", text: $notes, axis: .vertical)
                        .lineLimit(3 ... 8)
                }

                Section("跟进任务") {
                    Toggle("需要跟进", isOn: $wantsFollowUp)
                    if wantsFollowUp {
                        TextField("任务标题", text: $followUpTitle)
                        DatePicker("截止日期", selection: $followUpDue, displayedComponents: .date)
                    }
                }

                if let submission {
                    Section("提交结果") {
                        Label(submission.headline, systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                        Text(submission.detail)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                if let uploadedRecordId {
                    Section {
                        if let crmJob {
                            LabeledContent("状态", value: crmJob.status.label)
                            if let externalId = crmJob.externalId {
                                Text(externalId)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                            if let error = crmJob.error {
                                Text(error).font(.footnote).foregroundStyle(.red)
                            }
                        } else {
                            Text("尚未同步这条现场记录。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        Button {
                            Task { await syncToCRM(recordId: uploadedRecordId) }
                        } label: {
                            Label(isSyncingCRM ? "同步中…" : "同步到 CRM", systemImage: "arrow.triangle.2.circlepath")
                        }
                        .disabled(isSyncingCRM)
                        if let crmJob, !crmJob.status.isTerminal {
                            Button {
                                Task { await refreshCRM(id: crmJob.id) }
                            } label: {
                                Label("刷新状态", systemImage: "arrow.clockwise")
                            }
                            .disabled(isSyncingCRM)
                        }
                    } header: {
                        Text("CRM 同步")
                    } footer: {
                        Text("服务端异步处理；文件适配器只生成待导入文件（PREPARED），不会声称已写入外部 CRM。")
                    }
                }

                if let minutes {
                    Section("会议纪要草稿") {
                        Text(minutes.title).font(.headline)
                        Text(minutes.summary).font(.body)
                        ForEach(minutes.taskList, id: \.title) { task in
                            Label(task.displayText, systemImage: "checklist")
                                .font(.footnote)
                        }
                        Text("来源：\(minutes.sourceLabel)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        if let id = minutes.llmInvocationId {
                            Text("调用记录：\(id)")
                                .font(.caption2.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                if let errorMessage {
                    Section { ErrorBanner(message: errorMessage) }
                }

                Section {
                    Button {
                        Task { await submit() }
                    } label: {
                        Label(
                            isSubmitting ? "提交中…" : "保存现场记录",
                            systemImage: "square.and.arrow.up"
                        )
                    }
                    .disabled(isSubmitting || !isReady)

                    Button {
                        Task { await loadMinutes() }
                    } label: {
                        Label(
                            isLoadingMinutes ? "生成中…" : "生成会议纪要草稿",
                            systemImage: "doc.text"
                        )
                    }
                    .disabled(isLoadingMinutes)
                } footer: {
                    Text("记录先写入本地离线队列，再尝试上传；失败不会丢笔记，可在“同步”页重试。")
                }
            }
            .navigationTitle("现场记录")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }

    private var isReady: Bool {
        guard outcome.requiresFollowUp else { return true }
        return wantsFollowUp && !followUpTitle.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func submit() async {
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        var tasks: [FollowUpTask] = []
        if wantsFollowUp {
            let title = followUpTitle.trimmingCharacters(in: .whitespacesAndNewlines)
            if !title.isEmpty {
                tasks.append(
                    FollowUpTask(title: title, dueDate: ISO8601.dayString(from: followUpDue))
                )
            }
        }
        let draft = VisitDraft(
            visitId: visit.id,
            outcome: outcome,
            notes: notes,
            photos: [],
            followUpTasks: tasks
        )
        do {
            submission = try await environment.submitVisitRecordUseCase.submit(
                draft,
                networkAvailable: true
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private var uploadedRecordId: String? {
        if case let .uploaded(record) = submission { return record.id }
        return nil
    }

    private func syncToCRM(recordId: String) async {
        isSyncingCRM = true
        errorMessage = nil
        defer { isSyncingCRM = false }
        do {
            crmJob = try await environment.visitRepository.syncToCRM(recordId: recordId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func refreshCRM(id: String) async {
        isSyncingCRM = true
        errorMessage = nil
        defer { isSyncingCRM = false }
        do {
            crmJob = try await environment.visitRepository.crmSyncJob(id: id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func loadMinutes() async {
        isLoadingMinutes = true
        errorMessage = nil
        defer { isLoadingMinutes = false }
        do {
            minutes = try await environment.visitRepository.draftMinutes(visitId: visit.id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}
