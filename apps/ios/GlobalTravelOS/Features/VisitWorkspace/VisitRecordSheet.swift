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
    @State private var followUpFeed: VisitFollowUpFeed?
    @State private var isLoadingFollowUps = false
    @State private var busyFollowUpIds: Set<String> = []
    @State private var reopenTarget: VisitFollowUp?
    @State private var reassessmentTarget: VisitFollowUp?
    @State private var reopenReason = ""
    @State private var showReopenPrompt = false
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
                        if !minutes.insightList.isEmpty {
                            let pending = minutes.pendingConfirmationCount
                            Label(
                                pending > 0
                                    ? "识别出 \(minutes.insightList.count) 条信息，其中 \(pending) 条需人工确认后才能采信"
                                    : "识别出 \(minutes.insightList.count) 条信息，均可作草稿",
                                systemImage: pending > 0 ? "exclamationmark.circle" : "checkmark.circle"
                            )
                            .font(.caption)
                            .foregroundStyle(pending > 0 ? .orange : .secondary)
                            ForEach(minutes.insightList) { insight in
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack {
                                        Text(insight.kindLabel)
                                            .font(.caption.weight(.semibold))
                                        Spacer()
                                        Text(insight.confirmLabel)
                                            .font(.caption2)
                                            .foregroundStyle(
                                                insight.requiresConfirmation ? .orange : .secondary
                                            )
                                    }
                                    Text(insight.text)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                .padding(.vertical, 2)
                            }
                        }
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


                Section {
                    if isLoadingFollowUps && followUpFeed == nil {
                        ProgressView("读取跟进事项…")
                    } else if let followUpFeed {
                        if !followUpFeed.suggestions.isEmpty {
                            Text("以下事项来自现场记录、AI 会议纪要或航班恢复分析，必须逐条确认后才会进入行动中心。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            ForEach(followUpFeed.suggestions) { suggestion in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(suggestion.title)
                                        .font(.subheadline.weight(.medium))
                                    HStack {
                                        Text(suggestion.sourceLabel)
                                        if let dueDate = suggestion.dueDate, !dueDate.isEmpty {
                                            Text("· 截止 \(dueDate)")
                                        }
                                        if let priority = suggestion.priority {
                                            Text("· \(priority == "HIGH" ? "高优先级" : priority == "MEDIUM" ? "中优先级" : "普通")")
                                        }
                                    }
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    if let rationale = suggestion.rationale, !rationale.isEmpty {
                                        Text(rationale)
                                            .font(.footnote)
                                            .foregroundStyle(.secondary)
                                    }
                                    if let inputs = suggestion.requiredInputs, !inputs.isEmpty {
                                        Text("待补充：\(inputs.joined(separator: "、"))")
                                            .font(.caption2)
                                            .foregroundStyle(.orange)
                                    }
                                    HStack(spacing: 10) {
                                        Button {
                                            Task { await decideFollowUp(suggestion, decision: "ACCEPT") }
                                        } label: {
                                            Label("确认纳入", systemImage: "checkmark.circle")
                                        }
                                        .buttonStyle(.borderedProminent)
                                        .disabled(busyFollowUpIds.contains(suggestion.id))
                                        Button(role: .destructive) {
                                            Task { await decideFollowUp(suggestion, decision: "DISMISS") }
                                        } label: {
                                            Label("不采纳", systemImage: "xmark.circle")
                                        }
                                        .buttonStyle(.bordered)
                                        .disabled(busyFollowUpIds.contains(suggestion.id))
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                        }

                        if !followUpFeed.items.isEmpty {
                            if !followUpFeed.suggestions.isEmpty {
                                Divider()
                            }
                            ForEach(followUpFeed.items) { item in
                                VStack(alignment: .leading, spacing: 8) {
                                    HStack(alignment: .firstTextBaseline) {
                                        Text(item.title)
                                            .font(.subheadline.weight(.medium))
                                        Spacer()
                                        Text(item.statusLabel)
                                            .font(.caption.weight(.semibold))
                                            .foregroundStyle(item.status == "COMPLETED" ? .green : .secondary)
                                    }
                                    HStack {
                                        Text(item.sourceLabel)
                                        if let dueDate = item.dueDate, !dueDate.isEmpty {
                                            Text("· 截止 \(dueDate)")
                                        }
                                    }
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    if let context = item.sourceActionContext {
                                        if let rationale = context.rationale, !rationale.isEmpty {
                                            Text(rationale)
                                                .font(.footnote)
                                                .foregroundStyle(.secondary)
                                        }
                                        if let inputs = context.requiredInputs, !inputs.isEmpty {
                                            Text("待补充：\(inputs.joined(separator: "、"))")
                                                .font(.caption2)
                                                .foregroundStyle(.orange)
                                        }
                                    }
                                    if let latest = item.latestReassessment {
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text("最近重评：\(latest.decision)")
                                                .font(.caption.weight(.semibold))
                                            if let arrival = latest.projectedArrivalAt {
                                                Text("预计到达：\(arrival.formatted(date: .abbreviated, time: .shortened))")
                                                    .font(.caption)
                                                    .foregroundStyle(.secondary)
                                            }
                                            if latest.customerWindowVerificationStatus == "PARTIALLY_VERIFIED" {
                                                Text("客户预约窗口由出差人提供，尚未获得外部独立验证")
                                                    .font(.caption2)
                                                    .foregroundStyle(.orange)
                                            }
                                            if let proposal = latest.planChangeRecommendation {
                                                Text(
                                                    "建议窗口：\(proposal.newWindowStart.formatted(date: .abbreviated, time: .shortened)) – \(proposal.newWindowEnd.formatted(date: .abbreviated, time: .shortened))"
                                                )
                                                .font(.caption)
                                                Text("计划变更仍需单独提交并经人工激活")
                                                    .font(.caption2)
                                                    .foregroundStyle(.secondary)
                                            }
                                        }
                                        .padding(.vertical, 3)
                                    }
                                    HStack(spacing: 10) {
                                        if item.sourceType == "APPOINTMENT_RECOVERY"
                                            && (item.status == "OPEN" || item.status == "IN_PROGRESS") {
                                            Button {
                                                reassessmentTarget = item
                                            } label: {
                                                Label("重新评估", systemImage: "arrow.triangle.2.circlepath")
                                            }
                                            .buttonStyle(.borderedProminent)
                                            .disabled(busyFollowUpIds.contains(item.id))
                                        }
                                        if item.status == "OPEN" {
                                            followUpActionButton(
                                                "开始处理",
                                                icon: "play.circle",
                                                item: item,
                                                status: "IN_PROGRESS"
                                            )
                                        }
                                        if item.status == "OPEN" || item.status == "IN_PROGRESS" {
                                            followUpActionButton(
                                                "标记完成",
                                                icon: "checkmark.circle",
                                                item: item,
                                                status: "COMPLETED"
                                            )
                                        }
                                        if item.status == "COMPLETED" || item.status == "DISMISSED" {
                                            followUpActionButton(
                                                "重新打开",
                                                icon: "arrow.uturn.backward.circle",
                                                item: item,
                                                status: "OPEN"
                                            )
                                        }
                                    }
                                    if !item.events.isEmpty {
                                        DisclosureGroup("操作记录（\(item.events.count)）") {
                                            ForEach(item.events) { event in
                                                VStack(alignment: .leading, spacing: 2) {
                                                    Text(event.label)
                                                        .font(.caption.weight(.medium))
                                                    if let note = event.note, !note.isEmpty {
                                                        Text(note)
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                    }
                                                    Text(event.createdAt.formatted(date: .abbreviated, time: .shortened))
                                                        .font(.caption2)
                                                        .foregroundStyle(.tertiary)
                                                }
                                                .padding(.vertical, 2)
                                            }
                                        }
                                        .font(.caption)
                                    }
                                }
                                .padding(.vertical, 5)
                            }
                        }

                        if followUpFeed.suggestions.isEmpty && followUpFeed.items.isEmpty {
                            ContentUnavailableView(
                                "暂无跟进事项",
                                systemImage: "checklist",
                                description: Text("现场记录或会议纪要中的后续事项会显示在这里，确认后才进入行动中心。")
                            )
                        }
                    }
                } header: {
                    HStack {
                        Text("跟进闭环")
                        Spacer()
                        if let followUpFeed {
                            Text("\(followUpFeed.items.filter { $0.status == "OPEN" || $0.status == "IN_PROGRESS" }.count) 项待处理")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                } footer: {
                    Text("确认只创建内部跟进记录，不会自动更改行程计划、拜访状态、客户档案或发送客户消息。")
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
        .alert("重新打开跟进事项", isPresented: $showReopenPrompt) {
            TextField("填写重新打开原因", text: $reopenReason)
            Button("取消", role: .cancel) {
                reopenTarget = nil
                reopenReason = ""
            }
            Button("确认重新打开") {
                guard let target = reopenTarget else { return }
                Task {
                    await updateFollowUp(
                        target,
                        status: "OPEN",
                        note: reopenReason.trimmingCharacters(in: .whitespacesAndNewlines)
                    )
                    reopenTarget = nil
                    reopenReason = ""
                }
            }
            .disabled(reopenReason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        } message: {
            Text("请说明该事项为何需要重新打开，原因会记录在操作历史中。")
        }
        .sheet(item: $reassessmentTarget) { item in
            AppointmentRecoveryReassessmentSheet(item: item) { request in
                Task { await reassessFollowUp(item, request: request) }
            }
        }
        .task { await loadFollowUps() }
    }

    @ViewBuilder
    private func followUpActionButton(
        _ title: String,
        icon: String,
        item: VisitFollowUp,
        status: String
    ) -> some View {
        Button {
            if status == "OPEN" {
                reopenTarget = item
                reopenReason = ""
                showReopenPrompt = true
            } else {
                Task { await updateFollowUp(item, status: status) }
            }
        } label: {
            Label(title, systemImage: icon)
        }
        .buttonStyle(.bordered)
        .disabled(busyFollowUpIds.contains(item.id))
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
            await loadFollowUps()
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


    private func loadFollowUps() async {
        isLoadingFollowUps = true
        defer { isLoadingFollowUps = false }
        do {
            followUpFeed = try await environment.visitRepository.followUps(visitId: visit.id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func decideFollowUp(
        _ suggestion: VisitFollowUpSuggestion,
        decision: String
    ) async {
        busyFollowUpIds.insert(suggestion.id)
        defer { busyFollowUpIds.remove(suggestion.id) }
        errorMessage = nil
        do {
            let request = decision == "ACCEPT"
                ? VisitFollowUpDecisionRequest.accept(suggestion)
                : VisitFollowUpDecisionRequest.dismiss(suggestion)
            _ = try await environment.visitRepository.decideFollowUp(
                visitId: visit.id,
                request: request
            )
            await loadFollowUps()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func reassessFollowUp(
        _ item: VisitFollowUp,
        request: AppointmentRecoveryReassessmentRequest
    ) async {
        busyFollowUpIds.insert(item.id)
        defer { busyFollowUpIds.remove(item.id) }
        errorMessage = nil
        do {
            _ = try await environment.visitRepository.reassessFollowUp(
                id: item.id,
                request: request
            )
            await loadFollowUps()
            reassessmentTarget = nil
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func updateFollowUp(_ item: VisitFollowUp, status: String, note: String? = nil) async {
        busyFollowUpIds.insert(item.id)
        defer { busyFollowUpIds.remove(item.id) }
        errorMessage = nil
        do {
            _ = try await environment.visitRepository.updateFollowUp(
                id: item.id,
                request: VisitFollowUpUpdateRequest(
                    status: status,
                    title: nil,
                    dueDate: nil,
                    note: note
                )
            )
            await loadFollowUps()
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
            await loadFollowUps()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}
