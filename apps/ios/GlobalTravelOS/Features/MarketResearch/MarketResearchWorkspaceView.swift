import SwiftUI

@MainActor
final class MarketResearchWorkspaceListViewModel: ObservableObject {
    @Published private(set) var workspaces: [MarketWorkspace] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isCreating = false
    @Published var errorMessage: String?

    let repository: ResearchRepository

    init(repository: ResearchRepository) {
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let page = try await repository.listWorkspaces(page: PageRequest(limit: 50))
            workspaces = page.items
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func create(name: String, objective: String, context: String) async -> Bool {
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanName.isEmpty else {
            errorMessage = "请填写工作空间名称"
            return false
        }
        isCreating = true
        defer { isCreating = false }
        do {
            let workspace = try await repository.createWorkspace(
                MarketWorkspaceCreateRequest(
                    name: cleanName,
                    objective: objective.nilIfBlank,
                    initialContext: context.nilIfBlank
                )
            )
            workspaces.insert(workspace, at: 0)
            errorMessage = nil
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

struct MarketResearchWorkspaceView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: MarketResearchWorkspaceListViewModel
    @State private var showsCreate = false

    init(environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: MarketResearchWorkspaceListViewModel(
                repository: environment.researchRepository
            )
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.md) {
                    intro
                    if let error = viewModel.errorMessage {
                        DSErrorState(message: error)
                    }
                    if viewModel.workspaces.isEmpty && !viewModel.isLoading {
                        emptyState
                    } else {
                        ForEach(viewModel.workspaces) { workspace in
                            NavigationLink {
                                MarketResearchWorkspaceDetailView(
                                    workspace: workspace,
                                    repository: repository
                                )
                            } label: {
                                MarketResearchWorkspaceCard(workspace: workspace)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .padding(DS.Spacing.lg)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("研究工作区")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsCreate = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("新建研究工作区")
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
            .sheet(isPresented: $showsCreate) {
                MarketResearchWorkspaceCreateSheet { name, objective, context in
                    await viewModel.create(name: name, objective: objective, context: context)
                }
            }
        }
    }

    private var repository: ResearchRepository {
        // The view model and detail screens share the same app-owned repository.
        viewModel.repository
    }

    private var intro: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(spacing: DS.Spacing.sm) {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("从问题开始，逐步建立证据")
                            .font(DS.Typography.sectionTitle)
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text("每个研究问题有自己的来源范围和审核状态。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
                HStack(spacing: DS.Spacing.sm) {
                    DSDataQualityTag(kind: .estimate, text: "搜索来源需核验")
                    DSDataQualityTag(kind: .live, text: "答案需人工采纳")
                }
            }
        }
    }

    private var emptyState: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                Image(systemName: "square.stack.3d.up")
                    .font(.title2)
                    .foregroundStyle(DS.ColorToken.brand)
                Text("还没有研究工作区")
                    .font(DS.Typography.sectionTitle)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text("创建一个工作区，整理研究目标和问题，再让系统按依赖关系推进证据与答案。")
                    .font(.subheadline)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                DSButton(title: "创建工作区", systemImage: "plus") {
                showsCreate = true
            }
            }
        }
    }
}

private struct MarketResearchWorkspaceCard: View {
    let workspace: MarketWorkspace

    var body: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(workspace.name)
                            .font(.headline)
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .lineLimit(2)
                        Text(workspace.objective ?? "尚未补充研究目标")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 8)
                    DSBadge(
                        text: workspace.statusLabel,
                        color: DS.ColorToken.status(workspace.status)
                    )
                }
                HStack(spacing: DS.Spacing.md) {
                    Label(
                        workspace.currentContextVersion.map { "上下文 v\($0)" } ?? "未采纳上下文",
                        systemImage: "doc.text"
                    )
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    Spacer()
                    Image(systemName: "arrow.up.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                }
            }
        }
    }
}

@MainActor
final class MarketResearchWorkspaceDetailViewModel: ObservableObject {
    @Published private(set) var workspace: MarketWorkspace
    @Published private(set) var questions: [MarketResearchQuestion] = []
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
            let questionPage = try await repository.listQuestions(
                workspaceId: workspace.id,
                page: PageRequest(limit: 100)
            )
            questions = questionPage.items
            graph = try await repository.graph(workspaceId: workspace.id)
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
        .task { await viewModel.load() }
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

private struct MarketResearchQuestionCard: View {
    let question: MarketResearchQuestion
    let readiness: String

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                    Text(String(format: "%02d", question.priority))
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(DS.ColorToken.brand)
                        .frame(width: 30, height: 30)
                        .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: 9))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(question.question)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        if let rationale = question.rationale, !rationale.isEmpty {
                            Text(rationale)
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                                .lineLimit(2)
                        }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                HStack(spacing: DS.Spacing.xs) {
                    DSBadge(
                        text: question.statusLabel,
                        color: DS.ColorToken.status(question.status)
                    )
                    DSBadge(
                        text: readinessLabel(readiness),
                        color: readinessColor(readiness)
                    )
                    if !question.expectedEvidence.isEmpty {
                        DSBadge(text: "预期证据 \(question.expectedEvidence.count)", color: DS.ColorToken.info)
                    }
                }
            }
        }
    }

    private func readinessLabel(_ value: String) -> String {
        switch value {
        case "READY": return "可启动"
        case "QUEUED": return "排队中"
        case "RUNNING": return "执行中"
        case "WAITING_FOR_REVIEW": return "待审核"
        case "WAITING_FOR_EVIDENCE": return "待证据"
        case "WAITING_FOR_DEPENDENCY": return "待前置问题"
        case "ANSWERED": return "已采纳"
        case "FAILED": return "失败"
        case "REJECTED": return "已驳回"
        case "BLOCKED": return "已阻塞"
        default: return "等待中"
        }
    }

    private func readinessColor(_ value: String) -> Color {
        switch value {
        case "READY", "ANSWERED": return DS.ColorToken.success
        case "FAILED", "REJECTED", "BLOCKED": return DS.ColorToken.danger
        case "WAITING_FOR_REVIEW", "WAITING_FOR_EVIDENCE": return DS.ColorToken.warning
        default: return DS.ColorToken.textSecondary
        }
    }
}

@MainActor
final class MarketResearchQuestionDetailViewModel: ObservableObject {
    @Published private(set) var plan: ResearchPlan?
    @Published private(set) var answers: [MarketQuestionAnswer] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isSubmitting = false
    @Published var queryText = ""
    @Published var reviewNote = ""
    @Published var errorMessage: String?
    @Published var informationMessage: String?

    let workspaceId: String
    let question: MarketResearchQuestion
    let repository: ResearchRepository

    init(workspaceId: String, question: MarketResearchQuestion, repository: ResearchRepository) {
        self.workspaceId = workspaceId
        self.question = question
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let plans = try await repository.listWorkspacePlans(
                workspaceId: workspaceId,
                targetQuestionId: question.id
            )
            answers = try await repository.answers(
                workspaceId: workspaceId,
                questionId: question.id
            )
            if let latest = plans.first {
                setPlan(latest)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createPlan() async {
        guard !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        errorMessage = nil
        do {
            let requestText = [
                question.question,
                question.rationale.map { "研究背景：\($0)" },
                question.acceptanceCriteria.isEmpty
                    ? nil
                    : "验收标准：\(question.acceptanceCriteria.joined(separator: "；"))",
                question.expectedEvidence.isEmpty
                    ? nil
                    : "期望证据：\(question.expectedEvidence.joined(separator: "；"))"
            ]
            .compactMap { $0 }
            .joined(separator: "\n")
            let created = try await repository.createPlan(
                ResearchPlanCreateRequest(
                    request: requestText,
                    sourceScope: .externalOnly,
                    workspaceId: workspaceId,
                    targetQuestionId: question.id
                )
            )
            setPlan(created)
            informationMessage = "研究计划已生成。检查并编辑查询后，再确认外部检索。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func executeSearch() async {
        guard let plan, !isSubmitting else { return }
        let queries = queryText.lines
        guard !queries.isEmpty else {
            errorMessage = "至少保留一条有效搜索查询"
            return
        }
        isSubmitting = true
        defer { isSubmitting = false }
        errorMessage = nil
        informationMessage = nil
        do {
            let accepted = try await repository.executePlan(id: plan.id, searchQueries: queries)
            self.plan = try await repository.plan(id: plan.id)
            informationMessage = accepted.reused
                ? "相同查询的检索任务已存在，正在复用。"
                : "检索任务已入队；当前只代表任务受理，不代表来源已采集。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshPlan() async {
        guard let current = plan else { return }
        do {
            setPlan(try await repository.plan(id: current.id), preserveQueries: true)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func draftAnswer() async {
        guard !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let accepted = try await repository.draftAnswer(
                workspaceId: workspaceId,
                questionId: question.id
            )
            informationMessage = "答案草稿任务已受理，\(accepted.evidenceCount) 条问题级证据将进入分析。"
            answers = try await repository.answers(workspaceId: workspaceId, questionId: question.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func review(_ answer: MarketQuestionAnswer, accept: Bool) async {
        guard !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let note = reviewNote.nilIfBlank
            let result = if accept {
                try await repository.acceptAnswer(
                    workspaceId: workspaceId,
                    questionId: question.id,
                    answerId: answer.id,
                    reviewNote: note
                )
            } else {
                try await repository.rejectAnswer(
                    workspaceId: workspaceId,
                    questionId: question.id,
                    answerId: answer.id,
                    reviewNote: note
                )
            }
            if let index = answers.firstIndex(where: { $0.id == result.id }) {
                answers[index] = result
            } else {
                answers.insert(result, at: 0)
            }
            reviewNote = ""
            informationMessage = accept
                ? "答案已采纳；只有此时依赖节点才会被释放。"
                : "答案已驳回；研究图不会把它视为已完成。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func setPlan(_ value: ResearchPlan, preserveQueries: Bool = false) {
        let isNewPlan = plan?.id != value.id
        plan = value
        if !preserveQueries || isNewPlan || queryText.isEmpty {
            let queries = value.reviewedSearchQueries.isEmpty
                ? value.plan.searchQueries
                : value.reviewedSearchQueries
            queryText = queries.joined(separator: "\n")
        }
    }
}

struct MarketResearchQuestionDetailView: View {
    @StateObject private var viewModel: MarketResearchQuestionDetailViewModel
    @State private var confirmsReject: MarketQuestionAnswer?

    init(
        workspaceId: String,
        question: MarketResearchQuestion,
        readiness: String,
        repository: ResearchRepository
    ) {
        _viewModel = StateObject(
            wrappedValue: MarketResearchQuestionDetailViewModel(
                workspaceId: workspaceId,
                question: question,
                repository: repository
            )
        )
        self.readiness = readiness
    }

    let readiness: String

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: DS.Spacing.lg) {
                questionHeader
                if let error = viewModel.errorMessage {
                    DSErrorState(message: error)
                }
                if let message = viewModel.informationMessage {
                    DSCard {
                        Label(message, systemImage: "info.circle")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.info)
                    }
                }
                researchPlanSection
                evidenceSection
                answersSection
            }
            .padding(DS.Spacing.lg)
        }
        .background(DS.ColorToken.canvas.ignoresSafeArea())
        .navigationTitle("问题研究")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await viewModel.load(); await viewModel.refreshPlan() }
        .task { await viewModel.load() }
        .confirmationDialog(
            "驳回这版答案？",
            item: $confirmsReject,
            titleVisibility: .visible
        ) { answer in
            Button("确认驳回", role: .destructive) {
                Task { await viewModel.review(answer, accept: false) }
            }
            Button("取消", role: .cancel) {}
        } message: { _ in
            Text("驳回不会释放下游研究问题。")
        }
    }

    private var questionHeader: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack {
                    DSBadge(
                        text: viewModel.question.statusLabel,
                        color: DS.ColorToken.status(viewModel.question.status)
                    )
                    DSBadge(text: readiness, color: readinessColor)
                    Spacer()
                    Text("优先级 \(viewModel.question.priority)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                Text(viewModel.question.question)
                    .font(DS.Typography.sectionTitle)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if let rationale = viewModel.question.rationale, !rationale.isEmpty {
                    Text(rationale)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                if !viewModel.question.acceptanceCriteria.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("验收标准")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        ForEach(viewModel.question.acceptanceCriteria, id: \.self) { item in
                            Label(item, systemImage: "checkmark.circle")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textPrimary)
                        }
                    }
                }
            }
        }
    }

    private var readinessColor: Color {
        switch readiness {
        case "READY", "ANSWERED": return DS.ColorToken.success
        case "FAILED", "REJECTED", "BLOCKED": return DS.ColorToken.danger
        case "WAITING_FOR_EVIDENCE", "WAITING_FOR_REVIEW": return DS.ColorToken.warning
        default: return DS.ColorToken.textSecondary
        }
    }

    private var researchPlanSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "研究计划",
                subtitle: "先生成查询草案；外部检索始终由你确认"
            )
            if let plan = viewModel.plan {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        HStack {
                            DSBadge(
                                text: plan.retrievalStatusLabel,
                                color: DS.ColorToken.status(plan.retrievalStatus)
                            )
                            Spacer()
                            Button("刷新") {
                                Task { await viewModel.refreshPlan() }
                            }
                            .font(.caption.weight(.medium))
                        }
                        Text(plan.plan.intentSummary)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(plan.plan.objective)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        if plan.plan.externalResearchNeeded,
                           plan.retrievalStatus == "NOT_STARTED" || plan.retrievalStatus == "FAILED" {
                            Text("搜索查询（可编辑）")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            TextEditor(text: $viewModel.queryText)
                                .font(.subheadline)
                                .frame(minHeight: 110)
                                .scrollContentBackground(.hidden)
                                .padding(DS.Spacing.xs)
                                .background(
                                    DS.ColorToken.surfaceSecondary,
                                    in: RoundedRectangle(cornerRadius: DS.Radius.control)
                                )
                            Button {
                                Task { await viewModel.executeSearch() }
                            } label: {
                                Label(
                                    viewModel.isSubmitting ? "正在提交…" : "确认查询并开始检索",
                                    systemImage: "magnifyingglass"
                                )
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(DSPrimaryButtonStyle())
                            .disabled(viewModel.isSubmitting)
                        } else if plan.plan.externalResearchNeeded {
                            Text("本次提交的查询已锁定。状态刷新后会显示采集结果。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        } else {
                            Text("本次计划不要求外部检索。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        if let error = plan.retrievalError, !error.isEmpty {
                            Text(error)
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.danger)
                        }
                    }
                }
            } else {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        Text("为当前问题生成定向研究计划")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text("Planner 只依据当前问题和 Workspace 上下文提出研究范围与查询，不会自动发起外部搜索。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        DSButton(
                            title: viewModel.isSubmitting ? "正在生成…" : "生成研究计划",
                            systemImage: "sparkles",
                            isLoading: viewModel.isSubmitting
                        ) {
                            Task { await viewModel.createPlan() }
                        }
                    }
                }
            }
        }
    }

    private var evidenceSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "来源证据",
                subtitle: "\(viewModel.plan?.evidence.count ?? 0) 条与当前问题关联的来源"
            )
            if let evidence = viewModel.plan?.evidence, !evidence.isEmpty {
                ForEach(evidence) { item in
                    ResearchEvidenceCard(evidence: item)
                }
            } else {
                DSCard {
                    HStack(spacing: DS.Spacing.sm) {
                        Image(systemName: "doc.text.magnifyingglass")
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text("还没有可用来源。完成检索后，来源会按问题范围显示在这里。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
        }
    }

    private var answersSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "答案版本",
                subtitle: "只有人工采纳的答案才会释放下游依赖"
            )
            DSButton(
                title: viewModel.isSubmitting ? "正在处理…" : "生成答案草稿",
                systemImage: "text.badge.plus",
                isLoading: viewModel.isSubmitting,
                isDisabled: viewModel.plan?.evidence.isEmpty != false
            ) {
                Task { await viewModel.draftAnswer() }
            }
            ForEach(viewModel.answers) { answer in
                MarketQuestionAnswerCard(
                    answer: answer,
                    reviewNote: $viewModel.reviewNote,
                    isSubmitting: viewModel.isSubmitting,
                    onAccept: { Task { await viewModel.review(answer, accept: true) } },
                    onReject: { confirmsReject = answer }
                )
            }
            if viewModel.answers.isEmpty {
                DSCard {
                    Text("当前问题还没有答案版本。来源证据就绪后，可以生成待审核草稿。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }
}

private struct ResearchEvidenceCard: View {
    let evidence: ResearchEvidence

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                HStack {
                    Text(evidence.title ?? evidence.kind.replacingOccurrences(of: "_", with: " "))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .lineLimit(2)
                    Spacer(minLength: 4)
                    DSEvidenceTag(
                        title: evidence.isUnverifiedSearchResult ? "尚未核验" : evidence.kind,
                        isVerified: false
                    )
                }
                if let snippet = evidence.snippet, !snippet.isEmpty {
                    Text(snippet)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .lineLimit(4)
                }
                HStack {
                    Text(evidence.source ?? evidence.engine ?? "来源")
                        .font(.caption2)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                    Spacer()
                    if let url = evidence.sourceURL {
                        Link(destination: url) {
                            Label("打开来源", systemImage: "arrow.up.right")
                                .font(.caption.weight(.medium))
                        }
                    }
                }
            }
        }
    }
}

private struct MarketQuestionAnswerCard: View {
    let answer: MarketQuestionAnswer
    @Binding var reviewNote: String
    let isSubmitting: Bool
    let onAccept: () -> Void
    let onReject: () -> Void

    var body: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack {
                    Text("版本 \(answer.version)")
                        .font(.caption.weight(.semibold).monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                    DSBadge(
                        text: answer.statusLabel,
                        color: DS.ColorToken.status(answer.status)
                    )
                    DSBadge(text: "置信度 \(answer.confidenceLevel)", color: DS.ColorToken.info)
                    Spacer()
                }
                Text(answer.answerSummary)
                    .font(.subheadline)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if !answer.assumptions.isEmpty {
                    AnswerList(title: "假设", values: answer.assumptions, symbol: "info.circle")
                }
                if !answer.unknowns.isEmpty {
                    AnswerList(title: "未知项", values: answer.unknowns, symbol: "questionmark.circle")
                }
                if let evidenceGap = answer.evidenceGap, !evidenceGap.isEmpty {
                    Text(evidenceGap)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.warning)
                }
                if !answer.evidence.isEmpty {
                    HStack(spacing: DS.Spacing.xs) {
                        DSEvidenceTag(title: "\(answer.evidence.count) 条关联证据")
                        Text("\(answer.evidence.filter { $0.relationType == "SUPPORTS" }.count) 支持 · \(answer.evidence.filter { $0.relationType == "CONTRADICTS" }.count) 冲突")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
                if answer.status == "NEEDS_REVIEW" || answer.status == "CONFLICTED" {
                    TextField("审核备注（可选）", text: $reviewNote, axis: .vertical)
                        .font(.caption)
                        .lineLimit(1...3)
                        .textFieldStyle(.roundedBorder)
                    HStack(spacing: DS.Spacing.sm) {
                        DSButton(
                            title: isSubmitting ? "正在提交…" : "采纳",
                            systemImage: "checkmark",
                            isLoading: isSubmitting,
                            isDisabled: isSubmitting,
                            action: onAccept
                        )
                        Button(role: .destructive, action: onReject) {
                            Label("驳回", systemImage: "xmark")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .disabled(isSubmitting)
                    }
                }
            }
        }
    }
}

private struct AnswerList: View {
    let title: String
    let values: [String]
    let symbol: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(DS.ColorToken.textSecondary)
            ForEach(values.prefix(4), id: \.self) { value in
                Label(value, systemImage: symbol)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .lineLimit(3)
            }
            if values.count > 4 {
                Text("另有 \(values.count - 4) 项")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
    }
}

private struct MarketResearchWorkspaceCreateSheet: View {
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

private struct MarketResearchQuestionCreateSheet: View {
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

private extension String {
    var nilIfBlank: String? {
        let clean = trimmingCharacters(in: .whitespacesAndNewlines)
        return clean.isEmpty ? nil : clean
    }

    var lines: [String] {
        var seen: Set<String> = []
        return components(separatedBy: .newlines).compactMap { raw in
            let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty else { return nil }
            let key = value.lowercased()
            guard seen.insert(key).inserted else { return nil }
            return value
        }
    }
}
