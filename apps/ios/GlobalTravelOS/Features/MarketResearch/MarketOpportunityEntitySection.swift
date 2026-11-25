import SwiftUI

struct MarketOpportunityEntitySection: View {
    let entities: [MarketEntityRead]
    let candidates: [MarketWorkspaceEntityRead]
    @Binding var relationshipNote: String
    let isSubmitting: Bool
    @State private var showsAllCandidates = false

    private var visibleCandidates: [MarketWorkspaceEntityRead] {
        showsAllCandidates ? candidates : Array(candidates.prefix(8))
    }
    let onCreate: () -> Void
    let onAttach: (MarketWorkspaceEntityRead, String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "关联企业",
                subtitle: "先在 Workspace 建立候选实体，再明确关联到当前商业机会",
                actionTitle: "新建候选",
                action: onCreate
            )

            if entities.isEmpty {
                DSCard {
                    Text("当前机会尚未关联企业。实体候选与正式 CRM 客户是不同记录，不会因关联而自动创建或修改客户。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } else {
                ForEach(entities) { entity in
                    DSCard(padding: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                            HStack {
                                Text(entity.displayName)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                                Spacer()
                                DSBadge(
                                    text: entity.statusLabel,
                                    color: DS.ColorToken.status(entity.status)
                                )
                            }
                            Text([entity.entityKind, entity.locationLabel]
                                .filter { !$0.isEmpty }
                                .joined(separator: " · "))
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            if let domain = entity.canonicalDomain, !domain.isEmpty {
                                Text(domain)
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if let description = entity.description, !description.isEmpty {
                                Text(description)
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                    .lineLimit(3)
                            }
                        }
                    }
                }
            }

            if !candidates.isEmpty {
                Text("Workspace 候选实体")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                TextField("关联说明（可选）", text: $relationshipNote, axis: .vertical)
                    .lineLimit(2...3)
                    .textFieldStyle(.roundedBorder)
                ForEach(visibleCandidates) { candidate in
                    DSCard(padding: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                            HStack {
                                Text(candidate.entity.displayName)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                                Spacer()
                                DSBadge(
                                    text: candidate.entity.statusLabel,
                                    color: DS.ColorToken.status(candidate.entity.status)
                                )
                                DSBadge(
                                    text: candidate.workspaceStatusLabel,
                                    color: DS.ColorToken.info
                                )
                            }
                            Text([candidate.entity.entityKind, candidate.entity.locationLabel]
                                .filter { !$0.isEmpty }
                                .joined(separator: " · "))
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            if let reason = candidate.discoveryReason, !reason.isEmpty {
                                Text(reason)
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if !candidate.possibleDuplicateEntityIds.isEmpty {
                                Label(
                                    "存在 \(candidate.possibleDuplicateEntityIds.count) 条可能重复实体记录，请先核对名称与域名",
                                    systemImage: "exclamationmark.triangle"
                                )
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.warning)
                            }
                            HStack {
                                Spacer()
                                Button {
                                    onAttach(candidate, relationshipNote)
                                } label: {
                                    Label("关联到机会", systemImage: "link")
                                        .font(.caption.weight(.semibold))
                                }
                                .disabled(isSubmitting)
                            }
                        }
                    }
                }
                if candidates.count > 8 {
                    Button(showsAllCandidates ? "收起候选企业" : "查看全部 \(candidates.count) 个候选") {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            showsAllCandidates.toggle()
                        }
                    }
                    .font(.caption.weight(.medium))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
            } else {
                Text("没有可关联的 Workspace 候选实体。可以新建一个候选，或先在该工作空间中整理候选企业。")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
    }
}

struct MarketWorkspaceEntityCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var displayName = ""
    @State private var entityKind = ""
    @State private var canonicalDomain = ""
    @State private var countryCode = ""
    @State private var description = ""
    @State private var discoveryReason = ""
    @State private var relevanceNote = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    let onCreate: (MarketWorkspaceEntityCreateRequest) async -> Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("企业信息") {
                    TextField("企业名称", text: $displayName)
                    TextField("实体类型（如经销商、运营商、制造商）", text: $entityKind)
                    TextField("官方网站域名（可选）", text: $canonicalDomain)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("国家代码（ISO alpha-2，可选）", text: $countryCode)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .onChange(of: countryCode) { _, value in
                            countryCode = String(value.uppercased().prefix(2))
                        }
                    TextField("企业背景（可选）", text: $description, axis: .vertical)
                        .lineLimit(2...4)
                }
                Section("发现依据") {
                    TextField("发现来源或原因（可选）", text: $discoveryReason, axis: .vertical)
                        .lineLimit(2...3)
                    TextField("与本工作区的相关性（可选）", text: $relevanceNote, axis: .vertical)
                        .lineLimit(2...3)
                }
                Section {
                    Text("新建记录只作为候选实体。系统会提示可能重复的记录，但不会自动合并，也不会创建或修改 CRM 客户。")
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
            .navigationTitle("新建企业候选")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "保存中…" : "创建") {
                        Task { await create() }
                    }
                    .disabled(
                        isSaving
                        || displayName.nilIfBlank == nil
                        || entityKind.nilIfBlank == nil
                        || (!countryCode.isEmpty && countryCode.count != 2)
                    )
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func create() async {
        isSaving = true
        defer { isSaving = false }
        let request = MarketWorkspaceEntityCreateRequest(
            entityKind: entityKind,
            displayName: displayName,
            description: description,
            canonicalDomain: canonicalDomain,
            countryCode: countryCode,
            discoveryReason: discoveryReason,
            relevanceNote: relevanceNote
        )
        if await onCreate(request) {
            dismiss()
        } else {
            errorMessage = "候选实体未能创建，请检查名称、国家代码或权限后重试。"
        }
    }
}
