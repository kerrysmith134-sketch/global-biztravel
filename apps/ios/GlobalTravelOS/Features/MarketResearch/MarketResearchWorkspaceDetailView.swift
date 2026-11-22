import SwiftUI

@MainActor
final class MarketResearchWorkspaceDetailViewModel: ObservableObject {
    @Published private(set) var workspace: MarketWorkspace
    @Published private(set) var questions: [MarketResearchQuestion] = []
    @Published private(set) var contexts: [MarketContextSnapshotRead] = []
    @Published private(set) var isExtractingContext = false
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
