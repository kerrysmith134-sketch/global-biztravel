import SwiftUI

@MainActor
final class MarketImpactReviewViewModel: ObservableObject {
    @Published private(set) var insight: MarketInsightRead?
    @Published private(set) var opportunity: MarketOpportunityDetailRead?
    @Published private(set) var claimHistory: MarketClaimLineageHistory?
    @Published private(set) var isLoading = false
    @Published private(set) var isSubmitting = false
    @Published private(set) var successMessage: String?
    @Published var errorMessage: String?
    @Published var reviewNote = ""

    let impact: MarketClaimImpact
    let workspaceId: String
    let repository: ResearchRepository

    init(
        workspaceId: String,
        impact: MarketClaimImpact,
        repository: ResearchRepository
    ) {
        self.workspaceId = workspaceId
        self.impact = impact
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        if impact.consumerType == "INSIGHT" {
            async let historyRequest = repository.claimHistory(
                workspaceId: workspaceId,
                claimId: impact.latestClaimId
            )
            async let insightRequest = repository.marketInsight(
                workspaceId: workspaceId,
                insightId: impact.consumerId
            )
            do {
                let (history, insight) = try await (historyRequest, insightRequest)
                claimHistory = history
                self.insight = insight
                opportunity = nil
            } catch {
                errorMessage = error.localizedDescription
            }
        } else {
            async let historyRequest = repository.claimHistory(
                workspaceId: workspaceId,
                claimId: impact.latestClaimId
            )
            async let opportunityRequest = repository.marketOpportunity(
                workspaceId: workspaceId,
                opportunityId: impact.consumerId
            )
            do {
                let (history, opportunity) = try await (historyRequest, opportunityRequest)
                claimHistory = history
                self.opportunity = opportunity
                insight = nil
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func reviewInsight(accept: Bool) async -> Bool {
        guard let insight, insight.canReview, !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        successMessage = nil
        defer { isSubmitting = false }

        do {
            let note = reviewNote.trimmingCharacters(in: .whitespacesAndNewlines)
            let updated: MarketInsightRead
            if accept {
                updated = try await repository.acceptMarketInsight(
                    workspaceId: workspaceId,
                    insightId: insight.id,
                    reviewNote: note.isEmpty ? nil : note
                )
                successMessage = "市场判断已由你采纳。"
            } else {
                updated = try await repository.rejectMarketInsight(
                    workspaceId: workspaceId,
                    insightId: insight.id,
                    reviewNote: note.isEmpty ? nil : note
                )
                successMessage = "市场判断已由你驳回。"
            }
            self.insight = updated
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

struct MarketImpactReviewSheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: MarketImpactReviewViewModel
    @State private var showsReviewConfirmation = false

    let onReviewed: () async -> Void

    init(
        workspaceId: String,
        impact: MarketClaimImpact,
        repository: ResearchRepository,
        onReviewed: @escaping () async -> Void
    ) {
        _viewModel = StateObject(
            wrappedValue: MarketImpactReviewViewModel(
                workspaceId: workspaceId,
                impact: impact,
                repository: repository
            )
        )
        self.onReviewed = onReviewed
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.lg) {
                    impactSummary
                    if let error = viewModel.errorMessage {
                        DSErrorState(message: error)
                    }
                    if viewModel.isLoading && viewModel.insight == nil && viewModel.opportunity == nil {
                        ProgressView("正在读取判断、证据和版本历史…")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, DS.Spacing.xl)
                    } else if let insight = viewModel.insight {
                        insightSection(insight)
                        insightReviewSection(insight)
                    } else if let opportunity = viewModel.opportunity {
                        opportunitySection(opportunity)
                    }
                    claimHistorySection
                    if let message = viewModel.successMessage {
                        DSCard {
                            Label(message, systemImage: "checkmark.circle.fill")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(DS.ColorToken.success)
                        }
                    }
                }
                .padding(DS.Spacing.lg)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("影响复核")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await viewModel.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("刷新影响详情")
                    .disabled(viewModel.isLoading || viewModel.isSubmitting)
                }
            }
        }
        .presentationDetents([.large])
        .task { await viewModel.load() }
        .confirmationDialog(
            "确认复核这项市场判断？",
            isPresented: $showsReviewConfirmation,
            titleVisibility: .visible
        ) {
            Button("采纳市场判断") {
                Task {
                    if await viewModel.reviewInsight(accept: true) {
                        await onReviewed()
                    }
                }
            }
            .disabled(viewModel.isSubmitting)
            Button("驳回市场判断", role: .destructive) {
                Task {
                    if await viewModel.reviewInsight(accept: false) {
                        await onReviewed()
                    }
                }
            }
            .disabled(viewModel.isSubmitting)
            Button("暂不处理", role: .cancel) {}
        } message: {
            Text("操作会写入审核记录。采纳前服务端仍会检查是否存在反驳证据；影响报告本身不会自动修改任何判断或商机。")
        }
    }

    private var impactSummary: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack {
                    DSBadge(
                        text: viewModel.impact.severityLabel,
                        color: viewModel.impact.severity == "HIGH"
                            ? DS.ColorToken.status("WARNING")
                            : DS.ColorToken.info
                    )
                    DSBadge(
                        text: viewModel.impact.consumerTypeLabel,
                        color: DS.ColorToken.brand
                    )
                    Spacer()
                    Text("主张 v\(viewModel.impact.latestClaimVersion)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                Text(viewModel.impact.consumerTitle)
                    .font(DS.Typography.sectionTitle)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(viewModel.impact.reasonLabel)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(DS.ColorToken.warning)
                Text(
                    "关联证据 \(viewModel.impact.matchedEvidenceCount) 条"
                    + " · 旧证据 \(viewModel.impact.staleEvidenceCount) 条"
                    + " · 已引用版本 \(viewModel.impact.referencedClaimVersions.map(String.init).joined(separator: ", "))"
                )
                .font(.caption)
                .foregroundStyle(DS.ColorToken.textSecondary)
                Text("该提示来自明确的 Evidence ID 关联，不代表下游结论必然错误。请检查下面的最新主张、证据和业务上下文后再作决定。")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func insightSection(_ insight: MarketInsightRead) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: "受影响的市场判断", subtitle: "判断内容、置信度和直接关联的来源证据")
            DSCard {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    HStack {
                        DSBadge(text: insight.statusLabel, color: DS.ColorToken.status(insight.status))
                        DSBadge(text: "版本 \(insight.version)", color: DS.ColorToken.info)
                        Spacer()
                        Text(insight.confidenceLevel)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Text(insight.statement)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    detailList(title: "假设", values: insight.assumptions)
                    detailList(title: "未知项", values: insight.unknowns)
                    detailList(title: "业务影响", values: insight.implications)
                    if let reason = insight.insufficientEvidenceReason, !reason.isEmpty {
                        Text("证据缺口：\(reason)")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.warning)
                    }
                    if let note = insight.reviewNote, !note.isEmpty {
                        Text("上次审核意见：\(note)")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
            evidenceSection(
                title: "判断引用的证据",
                evidence: insight.evidence.map {
                    (relation: $0.relationType, rationale: $0.rationale, item: $0.evidence)
                }
            )
        }
    }

    private func opportunitySection(_ detail: MarketOpportunityDetailRead) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: "受影响的商业机会", subtitle: "只读取当前机会状态，不自动改变资格、阶段或结果")
            DSCard {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    HStack {
                        DSBadge(
                            text: detail.opportunity.statusLabel,
                            color: DS.ColorToken.status(detail.opportunity.status)
                        )
                        Spacer()
                        Text(detail.opportunity.assessmentConfidence)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Text(detail.opportunity.title)
                        .font(.headline)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    if let description = detail.opportunity.description, !description.isEmpty {
                        Text(description)
                            .font(.subheadline)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if let nextAction = detail.opportunity.nextAction, !nextAction.isEmpty {
                        Label(nextAction, systemImage: "arrow.turn.down.right")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.info)
                    }
                    if let summary = detail.opportunity.assessmentSummary, !summary.isEmpty {
                        Text("最近评估：\(summary)")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if let reason = detail.opportunity.qualificationReason, !reason.isEmpty {
                        Text("资格依据：\(reason)")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
            evidenceSection(
                title: "机会引用的证据",
                evidence: detail.evidence.map {
                    (relation: $0.relationType, rationale: $0.rationale, item: $0.evidence)
                }
            )
        }
    }

    private func evidenceSection(
        title: String,
        evidence: [(relation: String, rationale: String?, item: ResearchEvidence)]
    ) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: title, subtitle: "\(evidence.count) 条直接关联")
            if evidence.isEmpty {
                DSCard {
                    Text("此对象未返回关联证据。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } else {
                ForEach(Array(evidence.enumerated()), id: \.offset) { _, row in
                    DSCard(padding: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                            HStack {
                                DSBadge(text: relationLabel(row.relation), color: relationColor(row.relation))
                                Spacer()
                                Text(row.item.kind.replacingOccurrences(of: "_", with: " "))
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            Text(row.item.title ?? row.item.source ?? row.item.ref)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                                .lineLimit(3)
                            if let snippet = row.item.snippet, !snippet.isEmpty {
                                Text(snippet)
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    .lineLimit(4)
                            }
                            if let rationale = row.rationale, !rationale.isEmpty {
                                Text("关联理由：\(rationale)")
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if let url = row.item.sourceURL {
                                Link(destination: url) {
                                    Label("打开来源", systemImage: "arrow.up.right")
                                        .font(.caption.weight(.semibold))
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private var claimHistorySection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: "主张版本历史", subtitle: "核对本次变化发生在哪个版本")
            if let history = viewModel.claimHistory {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.md) {
                        HStack {
                            Text("最新版本 v\(history.latestVersion)")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Spacer()
                            if history.currentAcceptedClaimId != nil {
                                DSBadge(text: "存在已采纳版本", color: DS.ColorToken.success)
                            } else {
                                DSBadge(text: "暂无已采纳版本", color: DS.ColorToken.warning)
                            }
                        }
                        ForEach(history.versions) { version in
                            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                                HStack {
                                    DSBadge(
                                        text: "v\(version.version) · \(version.statusLabel)",
                                        color: DS.ColorToken.status(version.status)
                                    )
                                    Spacer()
                                    Text(version.confidenceLevel)
                                        .font(.caption2)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                                Text(version.statement)
                                    .font(.subheadline)
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text("支持 \(version.supportingEvidenceCount) · 反驳 \(version.contradictingEvidenceCount)")
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                if let note = version.reviewNote, !note.isEmpty {
                                    Text("审核意见：\(note)")
                                        .font(.caption2)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                            if version.id != history.versions.last?.id {
                                Divider()
                            }
                        }
                    }
                }
            } else if viewModel.isLoading {
                ProgressView().frame(maxWidth: .infinity)
            } else {
                DSCard {
                    Text("暂时无法读取主张版本历史。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private func detailList(title: String, values: [JSONValue]) -> some View {
        let rendered = values.map(\.displayDescription).filter { !$0.isEmpty && $0 != "null" }
        guard !rendered.isEmpty else { return AnyView(EmptyView()) }
        return AnyView(
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                ForEach(Array(rendered.enumerated()), id: \.offset) { _, item in
                    Text("• " + item)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        )
    }

    private func relationLabel(_ relation: String) -> String {
        switch relation {
        case "SUPPORTS": return "支持"
        case "CONTRADICTS": return "反驳"
        case "CONTEXT": return "背景"
        default: return relation
        }
    }

    private func relationColor(_ relation: String) -> Color {
        switch relation {
        case "SUPPORTS": return DS.ColorToken.success
        case "CONTRADICTS": return DS.ColorToken.danger
        default: return DS.ColorToken.info
        }
    }

    private var insightReviewSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "人工审核",
                subtitle: "只在完整检查判断、引用证据和主张历史后提交决定"
            )
            if let insight = viewModel.insight, insight.canReview {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        TextField("审核意见（可选）", text: $viewModel.reviewNote, axis: .vertical)
                            .lineLimit(2...4)
                            .textFieldStyle(.roundedBorder)
                        HStack {
                            Button {
                                showsReviewConfirmation = true
                            } label: {
                                Label("审核判断", systemImage: "checkmark.seal")
                                    .font(.subheadline.weight(.semibold))
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(DS.ColorToken.brand)
                            .disabled(viewModel.isSubmitting)
                            Spacer()
                            if viewModel.isSubmitting { ProgressView() }
                        }
                        Text("点击后仍会弹出最终确认；采纳/驳回通过服务端现有审核状态机完成。")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            } else {
                DSCard {
                    Text("该判断版本已完成审核，不能重复采纳或驳回。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }
}
