import SwiftUI

struct MarketOpportunityExecutionSection: View {
    let execution: MarketOpportunityExecutionRead?
    let isLoading: Bool
    let isSubmitting: Bool
    @Binding var visitLinkNote: String
    let onLink: (MarketOpportunityVisitCandidateRead) -> Void
    let onUnlink: (MarketOpportunityVisitLinkRead) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "出差执行",
                subtitle: "将已制定行动的商业机会落实到具体 Trip 和拜访，不自动创建或改期"
            )
            if let execution {
                if let blockedReason = execution.blockedReason {
                    DSCard {
                        Label(blockedReason, systemImage: "info.circle")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                linkedVisits(execution.links)
                if execution.canLinkVisits {
                    candidateVisits(execution.candidates)
                }
                if execution.candidates.count > 8 {
                    Text("当前仅展示前 8 项可关联拜访，请先缩小 Trip 范围或整理行程。")
                        .font(.caption2)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } else if isLoading {
                ProgressView("正在读取 Trip 执行上下文…")
                    .frame(maxWidth: .infinity)
            } else {
                DSCard {
                    Text("暂时无法读取出差执行上下文，请刷新重试。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
            Text("关联操作不会改变机会阶段、拜访状态或 Trip 计划版本；解除关联会保留审核历史。")
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
        }
    }

    @ViewBuilder
    private func linkedVisits(_ links: [MarketOpportunityVisitLinkRead]) -> some View {
        if !links.isEmpty {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                Text("已关联 / 历史拜访")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                ForEach(links) { link in
                    DSCard(padding: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                            HStack {
                                DSBadge(
                                    text: link.statusLabel,
                                    color: link.isLinked
                                        ? DS.ColorToken.success
                                        : DS.ColorToken.textSecondary
                                )
                                DSBadge(
                                    text: visitStatusLabel(link.visitStatus),
                                    color: DS.ColorToken.status(link.visitStatus)
                                )
                                Spacer()
                                if link.isLinked {
                                    Button("解除") {
                                        onUnlink(link)
                                    }
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.danger)
                                    .disabled(isSubmitting)
                                }
                            }
                            Text(link.visitTitle ?? link.placeName)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Text("\(link.tripName) · \(link.dateLabel)")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            Text(
                                [link.customerName, link.placeName, link.city, link.countryCode]
                                    .compactMap { $0 }
                                    .filter { !$0.isEmpty }
                                    .joined(separator: " · ")
                            )
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            if let note = link.linkNote, !note.isEmpty {
                                Text("关联说明：\(note)")
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if let note = link.unlinkNote, !note.isEmpty {
                                Text("解除说明：\(note)")
                                    .font(.caption2)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func candidateVisits(_ candidates: [MarketOpportunityVisitCandidateRead]) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            Text("可关联的 Trip 拜访")
                .font(.caption.weight(.semibold))
                .foregroundStyle(DS.ColorToken.textSecondary)
            TextField("关联说明（可选）", text: $visitLinkNote, axis: .vertical)
                .lineLimit(2...3)
                .textFieldStyle(.roundedBorder)
            if candidates.isEmpty {
                DSCard {
                    Text("当前没有可关联的待排期拜访。请先在某个 Trip 中创建拜访，再回到这里完成关联。")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
            ForEach(candidates.prefix(8)) { candidate in
                DSCard(padding: DS.Spacing.md) {
                    VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                        Text(candidate.visitTitle ?? candidate.placeName)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text("\(candidate.tripName) · \(candidate.dateLabel)")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text(
                            [candidate.customerName, candidate.placeName, candidate.locationLabel]
                                .compactMap { $0 }
                                .filter { !$0.isEmpty }
                                .joined(separator: " · ")
                        )
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        HStack {
                            Label(candidate.visitStatusLabel, systemImage: "calendar")
                            Spacer()
                            Button {
                                onLink(candidate)
                            } label: {
                                Label("关联到机会", systemImage: "link")
                                    .font(.caption.weight(.semibold))
                            }
                            .disabled(isSubmitting)
                        }
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.brand)
                    }
                }
            }
        }
    }

    private func visitStatusLabel(_ status: String) -> String {
        switch status.uppercased() {
        case "PLANNED": return "待排期"
        case "SCHEDULED": return "已排期"
        case "DONE": return "已完成"
        case "MISSED": return "未会面"
        case "CANCELLED": return "已取消"
        default: return status
        }
    }
}
