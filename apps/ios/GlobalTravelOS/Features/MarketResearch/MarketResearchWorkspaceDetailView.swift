import SwiftUI

@MainActor
final class MarketResearchWorkspaceDetailViewModel: ObservableObject {
    @Published private(set) var workspace: MarketWorkspace
    @Published private(set) var questions: [MarketResearchQuestion] = []
    @Published private(set) var contexts: [MarketContextSnapshotRead] = []
    @Published private(set) var isExtractingContext = false
    @Published private(set) var evidenceReport: MarketEvidenceIntelligenceReport?
    @Published private(set) var evidenceReportError: String?
    @Published private(set) var sourceVerificationPlan: MarketSourceVerificationPlan?
    @Published private(set) var sourceVerificationPlanError: String?
    @Published private(set) var isApplyingSourceVerificationPlan = false
    @Published private(set) var sourceVerificationApplication: MarketSourceVerificationPlanApplication?
    @Published private(set) var graph: MarketResearchGraph?
    @Published private(set) var execution: MarketResearchGraphExecution?
    @Published private(set) var isLoading = false
    @Published private(set) var isSubmitting = false
    @Published var errorMessage: String?

    let repository: ResearchRepository

    init(workspace: MarketWorkspace, repository: ResearchRepository) {
        self.workspace = workspace
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            workspace = try await repository.workspace(id: workspace.id)
            contexts = try await repository.listContexts(workspaceId: workspace.id)
            let questionPage = try await repository.listQuestions(
                workspaceId: workspace.id,
                page: PageRequest(limit: 100)
            )
            questions = questionPage.items
            graph = try await repository.graph(workspaceId: workspace.id)
        } catch {
            errorMessage = error.localizedDescription
        }
        do {
            evidenceReport = try await repository.evidenceIntelligence(workspaceId: workspace.id)
            evidenceReportError = nil
        } catch {
            evidenceReportError = error.localizedDescription
        }
        do {
            sourceVerificationPlan = try await repository.sourceVerificationPlan(workspaceId: workspace.id)
            sourceVerificationPlanError = nil
        } catch {
            sourceVerificationPlanError = error.localizedDescription
        }
    }

    func applySourceVerificationPlan() async {
        guard !isApplyingSourceVerificationPlan else { return }
        isApplyingSourceVerificationPlan = true
        defer { isApplyingSourceVerificationPlan = false }
        do {
            sourceVerificationApplication = try await repository.applySourceVerificationPlan(
                workspaceId: workspace.id
            )
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func extractContext(contextId: String) async {
        guard !isExtractingContext else { return }
        isExtractingContext = true
        defer { isExtractingContext = false }
        errorMessage = nil
        do {
            let updated = try await repository.extractContext(
                workspaceId: workspace.id,
                contextId: contextId
            )
            if let index = contexts.firstIndex(where: { $0.id == updated.id }) {
                contexts[index] = updated
            } else {
                contexts.insert(updated, at: 0)
            }
            workspace = try await repository.workspace(id: workspace.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createQuestion(
        question: String,
        rationale: String,
        priority: Int,
        acceptanceCriteria: String
    ) async -> Bool {
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            _ = try await repository.createQuestion(
                workspaceId: workspace.id,
                request: MarketResearchQuestionCreateRequest(
                    question: question,
                    rationale: rationale.nilIfBlank,
                    priority: priority,
                    acceptanceCriteria: acceptanceCriteria.lines
                )
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func startExecution() async {
        guard !(graph?.readyQuestionIds.isEmpty ?? true), !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let accepted = try await repository.startExecution(
                workspaceId: workspace.id,
                request: MarketResearchGraphExecutionCreate()
            )
            execution = try await repository.execution(
                workspaceId: workspace.id,
                executionId: accepted.executionId
            )
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshExecution() async {
        guard let execution else { return }
        do {
            self.execution = try await repository.execution(
                workspaceId: workspace.id,
                executionId: execution.executionId
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func cancelExecution() async {
        guard let execution else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            try await repository.cancelExecution(
                workspaceId: workspace.id,
                executionId: execution.executionId
            )
            await refreshExecution()
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func readiness(for questionId: String) -> String {
        graph?.nodes.first(where: { $0.id == questionId })?.readiness ?? "WAITING"
    }
}

struct MarketResearchWorkspaceDetailView: View {
    @StateObject private var viewModel: MarketResearchWorkspaceDetailViewModel
    @State private var showsCreateQuestion = false
    @State private var confirmsCancelExecution = false
    @State private var historyClaimID: String?

    init(workspace: MarketWorkspace, repository: ResearchRepository) {
        _viewModel = StateObject(
            wrappedValue: MarketResearchWorkspaceDetailViewModel(
                workspace: workspace,
                repository: repository
            )
        )
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: DS.Spacing.lg) {
                workspaceHeader
                contextSection
                evidenceIntelligenceSection
                sourceVerificationPlanSection
                if let error = viewModel.errorMessage {
                    DSErrorState(message: error)
                }
                executionSection
                questionsSection
            }
            .padding(DS.Spacing.lg)
        }
        .background(DS.ColorToken.canvas.ignoresSafeArea())
        .navigationTitle(viewModel.workspace.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showsCreateQuestion = true
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("新增研究问题")
            }
        }
        .refreshable {
            await viewModel.load()
            await viewModel.refreshExecution()
        }
        .onAppear {
            // Returning from a question after accepting/rejecting an answer must refresh
            // the server-owned DAG readiness and release state.
            Task { await viewModel.load() }
        }
        .sheet(isPresented: $showsCreateQuestion) {
            MarketResearchQuestionCreateSheet { question, rationale, priority, criteria in
                await viewModel.createQuestion(
                    question: question,
                    rationale: rationale,
                    priority: priority,
                    acceptanceCriteria: criteria
                )
            }
        }
        .sheet(
            isPresented: Binding(
                get: { historyClaimID != nil },
                set: { if !$0 { historyClaimID = nil } }
            )
        ) {
            if let claimID = historyClaimID {
                MarketClaimHistorySheet(
                    workspaceID: viewModel.workspace.id,
                    claimID: claimID,
                    repository: viewModel.repository
                )
            }
        }
        .confirmationDialog(
            "取消本次研究图执行？",
            isPresented: $confirmsCancelExecution,
            titleVisibility: .visible
        ) {
            Button("取消执行", role: .destructive) {
                Task { await viewModel.cancelExecution() }
            }
            Button("继续执行", role: .cancel) {}
        } message: {
            Text("尚未启动的节点将停止排队；已经开始的外部模型调用无法强制中断。")
        }
    }

    private var workspaceHeader: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack {
                    DSBadge(
                        text: viewModel.workspace.statusLabel,
                        color: DS.ColorToken.status(viewModel.workspace.status)
                    )
                    if let version = viewModel.workspace.currentContextVersion {
                        DSBadge(text: "上下文 v\(version)", color: DS.ColorToken.info)
                    }
                    Spacer()
                    if viewModel.isLoading {
                        ProgressView().tint(DS.ColorToken.brand)
                    }
                }
                Text(viewModel.workspace.objective ?? "为本工作区整理问题、证据与判断。")
                    .font(.subheadline)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: DS.Spacing.sm) {
                    metric("问题", value: viewModel.questions.count, symbol: "point.3.connected.trianglepath.dotted")
                    metric("可启动", value: viewModel.graph?.readyQuestionIds.count ?? 0, symbol: "play.circle")
                    metric(
                        "已回答",
                        value: viewModel.graph?.nodes.filter { $0.readiness == "ANSWERED" }.count ?? 0,
                        symbol: "checkmark.seal"
                    )
                }
            }
        }
    }

    private func metric(_ title: String, value: Int, symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Label(title, systemImage: symbol)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
            Text("\(value)")
                .font(.title3.weight(.semibold).monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var contextSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "业务上下文",
                subtitle: "只整理本工作区的用户输入，不预设行业或市场"
            )
            if let context = viewModel.contexts.first(where: { $0.isCurrent }) ?? viewModel.contexts.first {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        HStack {
                            DSBadge(
                                text: "版本 v\(context.version)",
                                color: context.isCurrent ? DS.ColorToken.info : DS.ColorToken.textSecondary
                            )
                            DSBadge(
                                text: context.statusLabel,
                                color: DS.ColorToken.status(context.extractionStatus)
                            )
                            Spacer()
                        }
                        Text(context.rawInput)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .lineLimit(5)
                            .fixedSize(horizontal: false, vertical: true)

                        if context.extractionStatus == "COMPLETED" {
                            if context.fieldSummaries.isEmpty {
                                Text("未从原文中提取到可引用的结构化条目。")
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            } else {
                                ForEach(context.fieldSummaries) { field in
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(field.title)
                                            .font(.caption2.weight(.semibold))
                                            .foregroundStyle(DS.ColorToken.textSecondary)
                                        Text(field.value)
                                            .font(.caption)
                                            .foregroundStyle(DS.ColorToken.textPrimary)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                        }

                        if !context.unknowns.isEmpty {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("待确认 / 信息缺口")
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                ForEach(context.unknowns, id: \.self) { item in
                                    Text("• \(item)")
                                        .font(.caption)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                        }
                        if let error = context.extractionError, !error.isEmpty {
                            Text("提取记录：\(error)")
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.status("FAILED"))
                        }
                        if context.extractionStatus != "COMPLETED" {
                            DSButton(
                                title: viewModel.isExtractingContext
                                    ? "正在提取…"
                                    : context.extractionStatus == "FAILED"
                                        ? "重试结构化提取"
                                        : "提取结构化上下文",
                                systemImage: "sparkles",
                                isLoading: viewModel.isExtractingContext,
                                isDisabled: viewModel.isExtractingContext || context.extractionStatus == "PENDING"
                            ) {
                                Task { await viewModel.extractContext(contextId: context.id) }
                            }
                        }
                    }
                }
            } else {
                DSCard {
                    Text("当前工作区尚未添加原始业务背景。创建工作区时可以填写背景，再进行结构化提取。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }


    private var sourceVerificationPlanSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "来源核验计划",
                subtitle: "根据主张快照、来源版本变化和冲突自动整理下一步研究任务"
            )
            if let plan = viewModel.sourceVerificationPlan {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.md) {
                        HStack(spacing: DS.Spacing.md) {
                            evidenceMetric("待处理任务", value: plan.actionCount)
                            evidenceMetric("来源变更主张", value: plan.claimsWithChangedSourcesCount)
                            evidenceMetric("待交叉验证", value: plan.claimsNeedingCorroborationCount)
                        }
                        if plan.claimsWithContradictionsCount > 0 {
                            Label(
                                "\(plan.claimsWithContradictionsCount) 条主张存在支持与反驳信号",
                                systemImage: "exclamationmark.triangle"
                            )
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.status("WARNING"))
                        }
                        if plan.actionsNeedingResearchTasks {
                            DSButton(
                                title: viewModel.isApplyingSourceVerificationPlan
                                    ? "正在生成研究任务…" : "将核验动作加入研究图",
                                systemImage: "point.3.connected.trianglepath.dotted",
                                isLoading: viewModel.isApplyingSourceVerificationPlan,
                                isDisabled: viewModel.isApplyingSourceVerificationPlan
                            ) {
                                Task { await viewModel.applySourceVerificationPlan() }
                            }
                            Text("只创建有审计记录的研究问题与依赖，不会立即调用模型。确认后可在下方研究图中手动启动。")
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        } else if !plan.actions.isEmpty {
                            Label("当前核验动作均已关联研究问题", systemImage: "checkmark.circle")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        if let application = viewModel.sourceVerificationApplication {
                            Text("已同步 \(application.actionCount) 项核验动作：新建 \(application.createdQuestionCount)，复用 \(application.reusedQuestionCount)，建立依赖 \(application.dependencyCount)。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        if plan.actions.isEmpty {
                            Text("当前没有发现需要优先处理的来源核验任务。新证据加入或来源版本变化后，计划会重新计算。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        } else {
                            ForEach(plan.actions.prefix(8)) { action in
                                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(action.title)
                                            .font(.subheadline.weight(.semibold))
                                            .foregroundStyle(DS.ColorToken.textPrimary)
                                        Text(action.rationale)
                                            .font(.caption)
                                            .foregroundStyle(DS.ColorToken.textSecondary)
                                            .fixedSize(horizontal: false, vertical: true)
                                        if let status = action.researchQuestionStatus {
                                            Text(sourceQuestionStatusLabel(status))
                                                .font(.caption2.weight(.medium))
                                                .foregroundStyle(DS.ColorToken.info)
                                        }
                                    }
                                    Spacer(minLength: 4)
                                    Text(action.priorityLabel)
                                        .font(.caption2.weight(.medium))
                                        .foregroundStyle(action.priority == "HIGH"
                                            ? DS.ColorToken.status("WARNING")
                                            : DS.ColorToken.textSecondary)
                                }
                                .padding(.vertical, 4)
                            }
                        }
                        if plan.actionsTruncated || plan.evidenceTruncated || plan.claimsTruncated {
                            Text("结果已按安全上限截断；来源 \(plan.analyzedEvidenceCount)/\(plan.totalEvidenceCount)，主张 \(plan.analyzedClaimCount)/\(plan.totalClaimCount)。")
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.status("WARNING"))
                        }
                        if let claim = plan.claims.first(where: { !$0.issues.isEmpty }) {
                            Divider()
                            Text("需要重点复核的主张")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            Text(claim.statement)
                                .font(.subheadline)
                                .foregroundStyle(DS.ColorToken.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text("\(claim.originLabel) · 支持 \(claim.supportingEvidenceCount) · 反驳 \(claim.contradictingEvidenceCount)")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            Button {
                                historyClaimID = claim.claimId
                            } label: {
                                Label("查看版本历史", systemImage: "clock.arrow.circlepath")
                                    .font(.caption.weight(.semibold))
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(DS.ColorToken.brand)
                        }
                        Text("此计划仅生成待办建议，不会自动抓取网页、修改证据或批准主张；不同域名不代表来源独立。")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            } else if let error = viewModel.sourceVerificationPlanError {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        Text("暂时无法读取来源核验计划")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Button("重试") {
                            Task { await viewModel.load() }
                        }
                        .font(.caption.weight(.semibold))
                    }
                }
            }
        }
    }

    private func sourceQuestionStatusLabel(_ status: String) -> String {
        switch status {
        case "OPEN": return "已加入研究图 · 待执行"
        case "IN_PROGRESS": return "研究执行中"
        case "ANSWERED": return "研究答案已采纳 · 待重新核验"
        case "BLOCKED": return "研究任务受阻"
        case "CANCELLED": return "任务已取消"
        default: return "已关联研究任务"
        }
    }

    private var evidenceIntelligenceSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "证据质量",
                subtitle: "内容重复、来源版本和证据完整性"
            )
            if let report = viewModel.evidenceReport {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.md) {
                        HStack(spacing: DS.Spacing.md) {
                            evidenceMetric("证据", value: report.totalEvidenceCount)
                            evidenceMetric("不同主机", value: report.distinctHostCount)
                            evidenceMetric("重复内容组", value: report.duplicateContentGroupCount)
                            evidenceMetric("多版本来源", value: report.multiVersionSourceCount)
                        }
                        HStack(spacing: DS.Spacing.sm) {
                            DSDataQualityTag(kind: .estimate, text: "不同主机不等于独立来源")
                            DSDataQualityTag(kind: .live, text: "核验状态独立统计")
                        }
                        if report.unextractedSearchResultCount > 0 {
                            Label(
                                "\(report.unextractedSearchResultCount) 条搜索结果尚未提取网页正文",
                                systemImage: "doc.text.magnifyingglass"
                            )
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        if report.missingPublicationDateCount > 0 {
                            Label(
                                "\(report.missingPublicationDateCount) 条证据缺少发布日期",
                                systemImage: "calendar.badge.exclamationmark"
                            )
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        if report.isTruncated {
                            Text("当前报告只分析最近 \(report.analyzedEvidenceCount) 条证据，共 \(report.totalEvidenceCount) 条。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.status("WARNING"))
                        }
                        if !report.items.isEmpty {
                            Divider()
                            Text("需要关注的来源")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            ForEach(report.items.filter { !$0.issues.isEmpty }.prefix(8)) { item in
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack {
                                        Text(item.sourceHost ?? item.source ?? item.kind)
                                            .font(.subheadline.weight(.semibold))
                                            .foregroundStyle(DS.ColorToken.textPrimary)
                                            .lineLimit(1)
                                        Spacer(minLength: 4)
                                        DSBadge(
                                            text: item.originLabel,
                                            color: item.originAssessment == "DUPLICATE_CONTENT"
                                                ? DS.ColorToken.status("WARNING")
                                                : DS.ColorToken.textSecondary
                                        )
                                    }
                                    if let canonicalRef = item.canonicalRef {
                                        Text(canonicalRef)
                                            .font(.caption2)
                                            .foregroundStyle(DS.ColorToken.textSecondary)
                                            .lineLimit(1)
                                    }
                                    Text(item.issueLabels.joined(separator: " · "))
                                        .font(.caption)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                .padding(.vertical, 4)
                            }
                        }
                        ForEach(report.caveats.prefix(3), id: \.self) { caveat in
                            Text("• \(caveat)")
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Text("报告生成于 \(report.generatedAt.formatted(date: .abbreviated, time: .shortened))")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            } else if let error = viewModel.evidenceReportError {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        Text("暂时无法读取证据分析")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Button("重试") {
                            Task { await viewModel.load() }
                        }
                        .font(.caption.weight(.medium))
                    }
                }
            } else {
                DSCard {
                    Text("工作区尚无可分析的证据。完成搜索或添加来源后，这里会展示重复内容、版本差异和元数据缺口。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private func evidenceMetric(_ title: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.headline.monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
            Text(title)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var executionSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "研究图执行",
                subtitle: "只调度已满足前置依赖且具有可用证据的问题"
            )
            if let execution = viewModel.execution {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        HStack {
                            DSBadge(
                                text: execution.stateLabel,
                                color: DS.ColorToken.status(execution.executionState)
                            )
                            Spacer()
                            Button("刷新") {
                                Task { await viewModel.refreshExecution() }
                            }
                            .font(.caption.weight(.medium))
                        }
                        HStack(spacing: DS.Spacing.md) {
                            executionMetric("已采纳", value: execution.completedCount)
                            executionMetric("执行中", value: execution.activeCount)
                            executionMetric("待证据", value: execution.waitingEvidenceCount)
                            executionMetric("待审核", value: execution.waitingReviewCount)
                        }
                        if let reason = execution.stopReason, !reason.isEmpty {
                            Text(reason)
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        if execution.activeCount > 0 || execution.workflowStatus == "RUNNING" {
                            Button(role: .destructive) {
                                confirmsCancelExecution = true
                            } label: {
                                Label("取消执行", systemImage: "stop.circle")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                }
            } else {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        Text("按依赖顺序推进研究")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text("每个节点只使用与该问题关联的证据。下游问题必须等待前置答案通过人工采纳。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        DSButton(
                            title: viewModel.isSubmitting ? "正在启动…" : "执行研究图",
                            systemImage: "play.fill",
                            isLoading: viewModel.isSubmitting,
                            isDisabled: viewModel.graph?.readyQuestionIds.isEmpty != false
                        ) {
                            Task { await viewModel.startExecution() }
                        }
                    }
                }
            }
        }
    }

    private func executionMetric(_ title: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.headline.monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
            Text(title)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var questionsSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "研究问题",
                subtitle: "问题之间的依赖和当前状态由服务端计算",
                actionTitle: "新增",
                action: { showsCreateQuestion = true }
            )
            if viewModel.questions.isEmpty && !viewModel.isLoading {
                DSCard {
                    Text("尚未添加研究问题。先把目标拆分成可独立核验的问题。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
            ForEach(viewModel.questions) { question in
                NavigationLink {
                    MarketResearchQuestionDetailView(
                        workspaceId: viewModel.workspace.id,
                        question: question,
                        readiness: viewModel.readiness(for: question.id),
                        repository: viewModel.repository
                    )
                } label: {
                    MarketResearchQuestionCard(
                        question: question,
                        readiness: viewModel.readiness(for: question.id)
                    )
                }
                .buttonStyle(.plain)
            }
        }
    }
}



private struct MarketClaimHistorySheet: View {
    @Environment(\.dismiss) private var dismiss
    let workspaceID: String
    let claimID: String
    let repository: ResearchRepository

    @State private var history: MarketClaimLineageHistory?
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.md) {
                    if isLoading {
                        ProgressView("正在读取主张历史…")
                            .frame(maxWidth: .infinity)
                            .padding(.top, DS.Spacing.lg)
                    } else if let errorMessage {
                        DSErrorState(message: errorMessage)
                    } else if let history {
                        DSCard {
                            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                                HStack {
                                    Text("事实谱系")
                                        .font(.headline)
                                        .foregroundStyle(DS.ColorToken.textPrimary)
                                    Spacer()
                                    DSBadge(
                                        text: "共 \(history.latestVersion) 个版本",
                                        color: DS.ColorToken.info
                                    )
                                }
                                Text("同一精确主张指纹下的跨研究答案历史。新版本不会自动撤销当前采纳结论。")
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                if let acceptedID = history.currentAcceptedClaimId,
                                   let accepted = history.versions.first(where: { $0.id == acceptedID }) {
                                    Text("当前有效采纳：v\(accepted.version)")
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(DS.ColorToken.status("SUCCESS"))
                                } else {
                                    Text("当前没有已采纳版本")
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                        }
                        ForEach(history.versions) { version in
                            DSCard {
                                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                                    HStack {
                                        Text("版本 v\(version.version)")
                                            .font(.subheadline.weight(.semibold))
                                            .foregroundStyle(DS.ColorToken.textPrimary)
                                        Spacer()
                                        DSBadge(
                                            text: version.statusLabel,
                                            color: statusColor(version.status)
                                        )
                                    }
                                    Text(version.statement)
                                        .font(.subheadline)
                                        .foregroundStyle(DS.ColorToken.textPrimary)
                                        .fixedSize(horizontal: false, vertical: true)
                                    Text(
                                        "支持 \(version.supportingEvidenceCount) · " +
                                        "反驳 \(version.contradictingEvidenceCount) · " +
                                        "\(version.verificationVerdict)"
                                    )
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    Text(
                                        "答案版本 \(version.answerId) · " +
                                        version.createdAt.formatted(date: .abbreviated, time: .shortened)
                                    )
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    if let reviewNote = version.reviewNote, !reviewNote.isEmpty {
                                        Text("审核说明：\(reviewNote)")
                                            .font(.caption)
                                            .foregroundStyle(DS.ColorToken.textSecondary)
                                    }
                                }
                            }
                        }
                    }
                }
                .padding(DS.Spacing.lg)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("主张版本历史")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .task {
                isLoading = true
                defer { isLoading = false }
                do {
                    history = try await repository.claimHistory(
                        workspaceId: workspaceID,
                        claimId: claimID
                    )
                } catch {
                    errorMessage = error.localizedDescription
                }
            }
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "ACCEPTED": return DS.ColorToken.status("SUCCESS")
        case "CONFLICTED", "REJECTED": return DS.ColorToken.status("WARNING")
        default: return DS.ColorToken.textSecondary
        }
    }
}
