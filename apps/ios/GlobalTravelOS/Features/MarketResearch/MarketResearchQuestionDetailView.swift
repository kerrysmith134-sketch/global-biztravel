import SwiftUI

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
                            DSButton(
                                title: viewModel.isSubmitting ? "正在提交…" : "确认查询并开始检索",
                                systemImage: "magnifyingglass",
                                isLoading: viewModel.isSubmitting,
                                isDisabled: viewModel.isSubmitting
                            ) {
                                Task { await viewModel.executeSearch() }
                            }
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
