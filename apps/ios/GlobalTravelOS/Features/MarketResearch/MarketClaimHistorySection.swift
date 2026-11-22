import SwiftUI

struct MarketClaimHistorySection: View {
    let history: MarketClaimLineageHistory?
    let isLoading: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(title: "主张版本历史", subtitle: "核对本次变化发生在哪个版本")
            if let history {
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
                                Text(
                                    "支持 \(version.supportingEvidenceCount) · "
                                    + "反驳 \(version.contradictingEvidenceCount)"
                                )
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
            } else if isLoading {
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
}
