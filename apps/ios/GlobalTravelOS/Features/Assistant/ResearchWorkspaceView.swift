import SwiftUI

@MainActor
final class ResearchWorkspaceViewModel: ObservableObject {
    @Published var request = ""
    @Published var sourceScope: ResearchSourceScope = .automatic
    @Published var contextText = ""
    @Published private(set) var plan: ResearchPlan?
    @Published private(set) var isSubmitting = false
    @Published var errorMessage: String?

    private let repository: ResearchRepository
    private let tripId: String?
    private let customerId: String?

    init(
        repository: ResearchRepository,
        tripId: String? = nil,
        customerId: String? = nil
    ) {
        self.repository = repository
        self.tripId = tripId
        self.customerId = customerId
    }

    var canSubmit: Bool {
        request.trimmingCharacters(in: .whitespacesAndNewlines).count >= 3 && !isSubmitting
    }

    func submit() async {
        let trimmedRequest = request.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmedRequest.count >= 3 else {
            errorMessage = "请先描述你要研究的问题或目标。"
            return
        }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }

        do {
            let context = contextText.trimmingCharacters(in: .whitespacesAndNewlines)
            plan = try await repository.createPlan(
                ResearchPlanCreateRequest(
                    request: trimmedRequest,
                    sourceScope: sourceScope,
                    tripId: tripId,
                    customerId: customerId,
                    contextText: context.isEmpty ? nil : context
                )
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct ResearchWorkspaceView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var model: ResearchWorkspaceViewModel

    init(
        environment: AppEnvironment,
        tripId: String? = nil,
        customerId: String? = nil
    ) {
        _model = StateObject(
            wrappedValue: ResearchWorkspaceViewModel(
                repository: environment.researchRepository,
                tripId: tripId,
                customerId: customerId
            )
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: DS.Spacing.lg) {
                    introduction
                    requestSection
                    sourceSection
                    contextSection
                    submitButton
                    if let error = model.errorMessage {
                        DSErrorState(message: error)
                    }
                    if let plan = model.plan {
                        planResult(plan)
                    }
                }
                .padding(.horizontal, DS.Spacing.lg)
                .padding(.vertical, DS.Spacing.md)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("研究工作区")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }

    private var introduction: some View {
        DSCard(padding: DS.Spacing.md) {
            HStack(alignment: .top, spacing: DS.Spacing.md) {
                Image(systemName: "sparkle.magnifyingglass")
                    .font(.title2)
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(width: 42, height: 42)
                    .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 5) {
                    Text("从你的问题开始")
                        .font(.headline)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    Text("研究对象、领域、地区和检索问题由本次输入动态提取，不预设行业或市场模板。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private var requestSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "你要研究什么？",
                subtitle: "描述目标、对象、时间范围，以及最终希望做出的决定"
            )
            DSCard(padding: DS.Spacing.sm) {
                TextEditor(text: $model.request)
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 120)
                    .font(.body)
                    .accessibilityIdentifier("research-request-input")
            }
            Text("示例：研究这家公司的背景，为下周的拜访准备问题。")
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
        }
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: "资料范围", subtitle: "你可以指定边界，也可以交给系统按问题判断")
            DSCard(padding: DS.Spacing.md) {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    Picker("资料范围", selection: $model.sourceScope) {
                        ForEach(ResearchSourceScope.allCases) { scope in
                            Text(scope.title).tag(scope)
                        }
                    }
                    .pickerStyle(.menu)
                    Text(model.sourceScope.explanation)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private var contextSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "补充材料",
                subtitle: "可粘贴客户介绍、邮件内容、会议记录或其他文本"
            )
            DSCard(padding: DS.Spacing.sm) {
                TextEditor(text: $model.contextText)
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 110)
                    .font(.body)
                    .accessibilityIdentifier("research-context-input")
            }
            Text("文本会发送给当前配置的模型用于本次规划；服务器保存内容摘要与字符数，不把全文复制到通用运行日志。当前版本尚未接入文件上传。")
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
        }
    }

    private var submitButton: some View {
        Button {
            Task { await model.submit() }
        } label: {
            HStack(spacing: DS.Spacing.sm) {
                if model.isSubmitting {
                    ProgressView().tint(.white)
                } else {
                    Image(systemName: "sparkles")
                }
                Text(model.isSubmitting ? "正在生成研究计划…" : "生成研究计划")
            }
            .font(.subheadline.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: 48)
            .foregroundStyle(.white)
            .background(
                model.canSubmit ? DS.ColorToken.brand : DS.ColorToken.textSecondary,
                in: RoundedRectangle(cornerRadius: DS.Radius.control)
            )
        }
        .buttonStyle(.plain)
        .disabled(!model.canSubmit)
        .accessibilityIdentifier("research-create-plan")
    }

    private func planResult(_ plan: ResearchPlan) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.md) {
            DSSectionHeader(title: "研究计划", subtitle: "由本次请求提取的目标与研究范围")
            statusCard(plan)
            DSCard {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    Text(plan.plan.objective)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    Text("研究对象：\(plan.plan.subject)")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                    if let domain = plan.plan.domain {
                        detailRow("领域", domain)
                    }
                    if let time = plan.plan.timeScope {
                        detailRow("时间范围", time)
                    }
                    if !plan.plan.geographies.isEmpty {
                        detailRow("地区", plan.plan.geographies.joined(separator: "、"))
                    }
                    if !plan.plan.entities.isEmpty {
                        detailRow("涉及对象", plan.plan.entities.joined(separator: "、"))
                    }
                }
            }
            if !plan.plan.researchQuestions.isEmpty {
                listCard(
                    title: "研究问题",
                    subtitle: "研究执行阶段需要回答的问题",
                    items: plan.plan.researchQuestions,
                    symbol: "questionmark.circle"
                )
            }
            if !plan.plan.searchQueries.isEmpty {
                listCard(
                    title: "检索查询草案",
                    subtitle: "这是待执行的查询，不代表已经检索",
                    items: plan.plan.searchQueries,
                    symbol: "magnifyingglass"
                )
            }
            if !plan.plan.sourceTypes.isEmpty {
                listCard(
                    title: "建议资料类型",
                    subtitle: "由本次问题动态选择",
                    items: plan.plan.sourceTypes,
                    symbol: "doc.text"
                )
            }
            if !plan.plan.deliverables.isEmpty {
                listCard(
                    title: "预期交付",
                    subtitle: nil,
                    items: plan.plan.deliverables,
                    symbol: "checkmark.seal"
                )
            }
            if !plan.plan.missingInformation.isEmpty {
                listCard(
                    title: "待补充信息",
                    subtitle: "这些信息缺失可能影响研究质量",
                    items: plan.plan.missingInformation,
                    symbol: "exclamationmark.circle"
                )
            }
            evidenceCard(plan)
            auditCard(plan)
        }
        .accessibilityIdentifier("research-plan-result")
    }

    private func statusCard(_ plan: ResearchPlan) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack {
                    DSBadge(text: plan.sourceScopeLabel, color: DS.ColorToken.brand)
                    Spacer()
                    DSDataQualityTag(kind: .stale, text: plan.retrievalStatusLabel)
                }
                Text("计划已生成，外部证据尚未采集。")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text("规划器：\(plan.planner.model)\(plan.planner.degraded ? " · 已降级" : "")")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
    }

    private func listCard(
        title: String,
        subtitle: String?,
        items: [String],
        symbol: String
    ) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                DSSectionHeader(title: title, subtitle: subtitle)
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .top, spacing: DS.Spacing.sm) {
                        Image(systemName: symbol)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.brand)
                            .frame(width: 18)
                        Text(item)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Spacer(minLength: 0)
                    }
                    .accessibilityIdentifier("\(title)-item-\(index)")
                }
            }
        }
    }

    private func evidenceCard(_ plan: ResearchPlan) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                DSSectionHeader(
                    title: "已有来源",
                    subtitle: "仅列出本次输入和显式选定的内部记录"
                )
                if plan.evidence.isEmpty {
                    Text("当前没有来源记录。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                } else {
                    ForEach(plan.evidence) { item in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.kind.replacingOccurrences(of: "_", with: " "))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Text(item.ref)
                                .font(.caption2.monospaced())
                                .foregroundStyle(DS.ColorToken.textSecondary)
                                .textSelection(.enabled)
                        }
                    }
                }
            }
        }
    }

    private func auditCard(_ plan: ResearchPlan) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                DSSectionHeader(title: "运行审计", subtitle: "用于回溯本次规划调用")
                detailRow("Workflow", plan.id)
                detailRow("Agent Run", plan.planner.agentRunId)
                if let invocation = plan.planner.llmInvocationId {
                    detailRow("LLM Invocation", invocation)
                }
                if let error = plan.planner.error, !error.isEmpty {
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(DS.ColorToken.warning)
                }
            }
        }
    }

    private func detailRow(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
            Text(value)
                .font(.caption)
                .foregroundStyle(DS.ColorToken.textPrimary)
                .textSelection(.enabled)
        }
    }
}
