import SwiftUI

private struct OpportunityStatusFilter: Identifiable {
    let label: String
    let value: String
    var id: String { value }
}

struct MarketOpportunityWorkspaceView: View {
    @StateObject private var viewModel: MarketOpportunityWorkspaceViewModel
    @State private var activeSheet: SheetRoute?

    init(workspaceId: String, repository: ResearchRepository) {
        _viewModel = StateObject(
            wrappedValue: MarketOpportunityWorkspaceViewModel(
                workspaceId: workspaceId,
                repository: repository
            )
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "商业机会",
                subtitle: "把经研究的市场信号转成可审核、可执行的业务机会",
                actionTitle: "新建",
                action: { activeSheet = .create }
            )
            HStack {
                Menu {
                    Button("全部机会") {
                        Task { await viewModel.setStatusFilter(nil) }
                    }
                    ForEach(Self.filters) { filter in
                        Button(filter.label) {
                            Task { await viewModel.setStatusFilter(filter.value) }
                        }
                    }
                } label: {
                    Label(viewModel.statusFilterLabel, systemImage: "line.3.horizontal.decrease.circle")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(DS.ColorToken.brand)
                }
                Spacer()
                Text("\(viewModel.opportunities.count) 项")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }

            if let error = viewModel.errorMessage {
                DSErrorState(message: error)
            }

            if viewModel.isLoading && viewModel.opportunities.isEmpty {
                ProgressView("正在读取商业机会…")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, DS.Spacing.lg)
            } else if viewModel.opportunities.isEmpty {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                        Label("尚无商业机会", systemImage: "scope")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text("可以从研究结论中发现机会，也可以先创建一个候选，再逐步补充证据和资格判断。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Button("创建第一条机会") {
                            activeSheet = .create
                        }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                    }
                }
            } else {
                ForEach(viewModel.opportunities) { opportunity in
                    Button {
                        activeSheet = .detail(opportunity)
                    } label: {
                        opportunityCard(opportunity)
                    }
                    .buttonStyle(.plain)
                }
            }

            if viewModel.nextCursor != nil {
                Button {
                    Task { await viewModel.load(reset: false) }
                } label: {
                    HStack {
                        if viewModel.isLoadingMore {
                            ProgressView()
                        }
                        Text(viewModel.isLoadingMore ? "正在加载…" : "加载更多机会")
                    }
                    .font(.caption.weight(.medium))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, DS.Spacing.xs)
                }
                .disabled(viewModel.isLoadingMore)
            }
        }
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        .sheet(item: $activeSheet) { route in
            switch route {
            case .create:
                MarketOpportunityCreateSheet { title, description, nextAction in
                    await viewModel.createOpportunity(
                        title: title,
                        description: description,
                        nextAction: nextAction
                    )
                }
            case .detail(let opportunity):
                MarketOpportunityDetailSheet(
                    workspaceId: viewModel.workspaceId,
                    opportunityId: opportunity.id,
                    repository: viewModel.repository,
                    onChanged: { await viewModel.load() }
                )
            }
        }
    }

    private func opportunityCard(_ opportunity: MarketOpportunitySummaryRead) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                    Text(opportunity.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    DSBadge(
                        text: opportunity.statusLabel,
                        color: DS.ColorToken.status(opportunity.status)
                    )
                }
                if let description = opportunity.description, !description.isEmpty {
                    Text(description)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                HStack(alignment: .firstTextBaseline) {
                    if let nextAction = opportunity.nextAction, !nextAction.isEmpty {
                        Label(nextAction, systemImage: "arrow.turn.down.right")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .lineLimit(1)
                    } else {
                        Text("置信度：\(opportunity.assessmentConfidence)")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Spacer()
                    Text(opportunity.updatedAt.formatted(date: .abbreviated, time: .omitted))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private static let filters: [OpportunityStatusFilter] = [
        OpportunityStatusFilter(label: "已发现", value: "DISCOVERED"),
        OpportunityStatusFilter(label: "研究中", value: "RESEARCHING"),
        OpportunityStatusFilter(label: "证据就绪", value: "EVIDENCE_READY"),
        OpportunityStatusFilter(label: "待资格确认", value: "QUALIFICATION_PENDING"),
        OpportunityStatusFilter(label: "已确认资格", value: "QUALIFIED"),
        OpportunityStatusFilter(label: "已制定行动", value: "ACTION_PLANNED"),
        OpportunityStatusFilter(label: "暂缓", value: "ON_HOLD"),
        OpportunityStatusFilter(label: "已赢单", value: "WON"),
        OpportunityStatusFilter(label: "已失单", value: "LOST"),
        OpportunityStatusFilter(label: "已淘汰", value: "DISQUALIFIED")
    ]

    private enum SheetRoute: Identifiable {
        case create
        case detail(MarketOpportunitySummaryRead)

        var id: String {
            switch self {
            case .create: return "create"
            case .detail(let item): return item.id
            }
        }
    }
}

struct MarketOpportunityCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var description = ""
    @State private var nextAction = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    let onCreate: (String, String, String) async -> Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("机会信息") {
                    TextField("机会名称", text: $title)
                    TextField("业务背景（可选）", text: $description, axis: .vertical)
                        .lineLimit(3...6)
                    TextField("建议下一步（可选）", text: $nextAction, axis: .vertical)
                        .lineLimit(2...4)
                }
                Section {
                    Text("新建后状态为“已发现”。阶段、资格和结果都必须经过明确操作；创建不会自动认定机会已成立。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.danger)
                    }
                }
            }
            .navigationTitle("新建商业机会")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "保存中…" : "创建") {
                        Task { await create() }
                    }
                    .disabled(isSaving || title.nilIfBlank == nil)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func create() async {
        isSaving = true
        defer { isSaving = false }
        if await onCreate(title, description, nextAction) {
            dismiss()
        } else {
            errorMessage = "机会未能创建，请检查输入或权限后重试。"
        }
    }
}

struct MarketOpportunityDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: MarketOpportunityDetailViewModel
    @State private var qualificationReason = ""
    @State private var outcomeNote = ""
    @State private var evidenceRationale = ""
    @State private var visitLinkNote = ""
    @State private var entityRelationshipNote = ""
    @State private var showsCreateEntitySheet = false

    let onChanged: () async -> Void

    init(
        workspaceId: String,
        opportunityId: String,
        repository: ResearchRepository,
        onChanged: @escaping () async -> Void
    ) {
        _viewModel = StateObject(
            wrappedValue: MarketOpportunityDetailViewModel(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                repository: repository
            )
        )
        self.onChanged = onChanged
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.lg) {
                    if let error = viewModel.errorMessage {
                        DSErrorState(message: error)
                    }
                    if let detail = viewModel.detail {
                        opportunitySummary(detail.opportunity)
                        MarketOpportunityEntitySection(
                            entities: detail.entities,
                            candidates: viewModel.availableWorkspaceEntities,
                            relationshipNote: $entityRelationshipNote,
                            isSubmitting: viewModel.isSubmitting,
                            onCreate: { showsCreateEntitySheet = true },
                            onAttach: { candidate, note in
                                Task {
                                    if await viewModel.attachEntity(candidate, note: note) {
                                        entityRelationshipNote = ""
                                        await onChanged()
                                    }
                                }
                            }
                        )
                        evidenceSection(detail)
                        statusActions(detail.opportunity)
                        if let execution = viewModel.execution,
                           execution.canLinkVisits || !execution.links.isEmpty {
                            MarketOpportunityExecutionSection(
                                execution: execution,
                                isLoading: viewModel.isLoading,
                                isSubmitting: viewModel.isSubmitting,
                                visitLinkNote: $visitLinkNote,
                                onLink: { candidate in
                                    Task {
                                        if await viewModel.linkVisit(candidate, note: visitLinkNote) {
                                            visitLinkNote = ""
                                            await onChanged()
                                        }
                                    }
                                },
                                onUnlink: { link in
                                    Task {
                                        if await viewModel.unlinkVisit(link) {
                                            await onChanged()
                                        }
                                    }
                                }
                            )
                        }
                    } else if viewModel.isLoading {
                        ProgressView("正在读取机会和证据…")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, DS.Spacing.xl)
                    }
                }
                .padding(DS.Spacing.lg)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("机会详情")
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
                    .accessibilityLabel("刷新机会")
                    .disabled(viewModel.isLoading || viewModel.isSubmitting)
                }
            }
        }
        .presentationDetents([.large])
        .sheet(isPresented: $showsCreateEntitySheet) {
            MarketWorkspaceEntityCreateSheet { request in
                let created = await viewModel.createWorkspaceCandidate(request)
                if created { await onChanged() }
                return created
            }
        }
        .task { await viewModel.load() }
    }

    private func opportunitySummary(_ opportunity: MarketOpportunitySummaryRead) -> some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack {
                    DSBadge(
                        text: opportunity.statusLabel,
                        color: DS.ColorToken.status(opportunity.status)
                    )
                    DSBadge(
                        text: opportunity.assessmentConfidence,
                        color: DS.ColorToken.info
                    )
                    Spacer()
                }
                Text(opportunity.title)
                    .font(DS.Typography.sectionTitle)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if let description = opportunity.description, !description.isEmpty {
                    Text(description)
                        .font(.subheadline)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let nextAction = opportunity.nextAction, !nextAction.isEmpty {
                    Label(nextAction, systemImage: "arrow.turn.down.right")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.brand)
                }
                if let qualificationReason = opportunity.qualificationReason,
                   !qualificationReason.isEmpty {
                    Text("资格确认：\(qualificationReason)")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                if let outcome = opportunity.outcomeNote, !outcome.isEmpty {
                    Text("业务结果：\(outcome)")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private func evidenceSection(_ detail: MarketOpportunityDetailRead) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "机会证据",
                subtitle: "支持、反驳和上下文均引用 Workspace 原始 Evidence"
            )
            if detail.evidence.isEmpty {
                DSCard {
                    Text("尚未关联证据。没有支持证据，服务端不会允许机会进入“证据就绪”或通过资格确认。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } else {
                ForEach(detail.evidence) { item in
                    DSCard(padding: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                            HStack {
                                DSBadge(
                                    text: relationLabel(item.relationType),
                                    color: relationColor(item.relationType)
                                )
                                DSBadge(
                                    text: item.evidence.verificationStatusLabel,
                                    color: DS.ColorToken.status(item.evidence.verificationStatus ?? "UNKNOWN")
                                )
                            }
                            Text(item.evidence.title ?? item.evidence.source ?? item.evidence.kind)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            if let snippet = item.evidence.snippet, !snippet.isEmpty {
                                Text(snippet)
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    .lineLimit(3)
                            }
                            if let rationale = item.rationale, !rationale.isEmpty {
                                Text("关联理由：\(rationale)")
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                        }
                    }
                }
            }

            if !viewModel.availableEvidence.isEmpty {
                Text("可加入的 Workspace 来源")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                TextField("关联理由（可选）", text: $evidenceRationale, axis: .vertical)
                    .lineLimit(2...3)
                    .textFieldStyle(.roundedBorder)
                ForEach(viewModel.availableEvidence.prefix(8)) { evidence in
                    DSCard(padding: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                            Text(evidence.title ?? evidence.source ?? evidence.kind)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Text(evidence.verificationStatusLabel)
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            if let snippet = evidence.snippet, !snippet.isEmpty {
                                Text(snippet)
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    .lineLimit(2)
                            }
                            HStack {
                                Spacer()
                                Button("反驳") {
                                    Task {
                                        if await viewModel.addEvidence(
                                            evidence,
                                            relationType: "CONTRADICTS",
                                            rationale: evidenceRationale
                                        ) {
                                            evidenceRationale = ""
                                            await onChanged()
                                        }
                                    }
                                }
                                .font(.caption.weight(.medium))
                                .foregroundStyle(DS.ColorToken.warning)
                                .disabled(viewModel.isSubmitting)
                                Button("支持") {
                                    Task {
                                        if await viewModel.addEvidence(
                                            evidence,
                                            relationType: "SUPPORTS",
                                            rationale: evidenceRationale
                                        ) {
                                            evidenceRationale = ""
                                            await onChanged()
                                        }
                                    }
                                }
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.brand)
                                .disabled(viewModel.isSubmitting)
                            }
                        }
                    }
                }
                if viewModel.availableEvidence.count > 8 {
                    Text("目前展示前 8 项来源。")
                        .font(.caption2)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } else {
                Text("当前没有可关联的新来源。请先在研究问题中添加或提取 Evidence。")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
    }

    @ViewBuilder
    private func statusActions(_ opportunity: MarketOpportunitySummaryRead) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: "阶段与决策", subtitle: "每次状态变化都由用户明确触发并写入审计事件")
            DSCard {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    switch opportunity.status.uppercased() {
                    case "DISCOVERED":
                        actionButton("开始研究", target: "RESEARCHING")
                    case "RESEARCHING":
                        if viewModel.hasSupportingEvidence {
                            actionButton("标记证据就绪", target: "EVIDENCE_READY")
                        } else {
                            Label(
                                "先关联至少一条 SUPPORTS 证据，才能标记证据就绪。",
                                systemImage: "info.circle"
                            )
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                    case "EVIDENCE_READY":
                        actionButton("提交资格审核", target: "QUALIFICATION_PENDING")
                    case "QUALIFICATION_PENDING":
                        Text("资格确认必须由具备管理员权限的成员完成，并提供人工审核理由。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        TextField("资格确认理由（必填）", text: $qualificationReason, axis: .vertical)
                            .lineLimit(2...4)
                            .textFieldStyle(.roundedBorder)
                        if viewModel.hasContradictingEvidence {
                            Label(
                                "存在反驳证据，必须先处理冲突，当前不可确认资格。",
                                systemImage: "exclamationmark.triangle"
                            )
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.warning)
                        }
                        Button {
                            Task {
                                if await viewModel.qualify(reason: qualificationReason) {
                                    qualificationReason = ""
                                    await onChanged()
                                }
                            }
                        } label: {
                            Label("确认机会资格", systemImage: "checkmark.seal")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(DS.ColorToken.brand)
                        .disabled(
                            viewModel.isSubmitting
                            || qualificationReason.nilIfBlank == nil
                            || !viewModel.hasSupportingEvidence
                            || viewModel.hasContradictingEvidence
                        )
                    case "QUALIFIED":
                        actionButton("制定行动计划", target: "ACTION_PLANNED")
                    case "ACTION_PLANNED":
                        TextField("记录实际业务结果（必填）", text: $outcomeNote, axis: .vertical)
                            .lineLimit(2...4)
                            .textFieldStyle(.roundedBorder)
                        HStack {
                            Button("赢单") {
                                Task {
                                    if await viewModel.recordOutcome("WON", note: outcomeNote) {
                                        outcomeNote = ""
                                        await onChanged()
                                    }
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(DS.ColorToken.success)
                            .disabled(viewModel.isSubmitting || outcomeNote.nilIfBlank == nil)
                            Button("失单") {
                                Task {
                                    if await viewModel.recordOutcome("LOST", note: outcomeNote) {
                                        outcomeNote = ""
                                        await onChanged()
                                    }
                                }
                            }
                            .buttonStyle(.bordered)
                            .tint(DS.ColorToken.danger)
                            .disabled(viewModel.isSubmitting || outcomeNote.nilIfBlank == nil)
                        }
                        Button {
                            Task {
                                if await viewModel.transition(to: "ON_HOLD", note: "用户暂缓机会推进") {
                                    await onChanged()
                                }
                            }
                        } label: {
                            Label("暂缓推进", systemImage: "pause.circle")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .disabled(viewModel.isSubmitting)
                    case "ON_HOLD":
                        HStack {
                            actionButton("恢复研究", target: "RESEARCHING")
                            actionButton("恢复行动", target: "ACTION_PLANNED")
                        }
                    default:
                        Text("该机会已进入终态或当前没有可执行的阶段动作。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
        }
    }

    private func actionButton(_ title: String, target: String) -> some View {
        Button {
            Task {
                if await viewModel.transition(to: target) {
                    await onChanged()
                }
            }
        } label: {
            Label(title, systemImage: "arrow.right.circle")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .tint(DS.ColorToken.brand)
        .disabled(viewModel.isSubmitting)
    }

    private func relationLabel(_ relation: String) -> String {
        switch relation.uppercased() {
        case "SUPPORTS": return "支持"
        case "CONTRADICTS": return "反驳"
        case "CONTEXT": return "背景"
        default: return relation
        }
    }

    private func relationColor(_ relation: String) -> Color {
        switch relation.uppercased() {
        case "SUPPORTS": return DS.ColorToken.success
        case "CONTRADICTS": return DS.ColorToken.warning
        default: return DS.ColorToken.info
        }
    }
}
