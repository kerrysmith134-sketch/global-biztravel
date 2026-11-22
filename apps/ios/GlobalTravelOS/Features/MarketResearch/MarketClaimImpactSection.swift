import SwiftUI

struct MarketClaimImpactSection: View {
    let report: MarketClaimImpactReport?
    let errorMessage: String?
    let onSelectImpact: (MarketClaimImpact) -> Void
    let onRetry: () -> Void

    private var visibleImpacts: [MarketClaimImpact] {
        guard let report else { return [] }
        return Array(report.impacts.prefix(8))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "业务结论影响",
                subtitle: "主张版本变化后，识别仍引用旧证据的市场判断和商业机会"
            )
            if let report {
                reportCard(report)
            } else if let errorMessage {
                errorCard(errorMessage)
            }
        }
    }

    private func reportCard(_ report: MarketClaimImpactReport) -> some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.md) {
                HStack(spacing: DS.Spacing.md) {
                    metric("受影响对象", value: report.affectedConsumerCount)
                    metric("影响关系", value: report.impactCount)
                    metric("已分析主张", value: report.analyzedLineageCount)
                }
                if report.impacts.isEmpty {
                    Label(
                        "暂未发现需要重新检查的下游结论",
                        systemImage: "checkmark.circle"
                    )
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                } else {
                    ForEach(visibleImpacts) { impact in
                        impactRow(impact)
                        if impact.id != visibleImpacts.last?.id {
                            Divider()
                        }
                    }
                }
                if report.impacts.count > 8 {
                    Text("此处展示前 8 项；当前报告共 \(report.impactCount) 条影响关系。")
                        .font(.caption2)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                if report.truncated {
                    Text(
                        "报告已按安全上限截断（分析 \(report.analyzedLineageCount)/"
                        + "\(report.totalLineageCount) 条主张谱系）。"
                    )
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.status("WARNING"))
                }
                Text(
                    "这是基于同一 Evidence ID 的影响提示，不代表下游结论必然错误；"
                    + "不会自动修改 Insight、机会阶段或已采纳决策。"
                )
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
    }

    private func impactRow(_ impact: MarketClaimImpact) -> some View {
        Button {
            onSelectImpact(impact)
        } label: {
            HStack(alignment: .top, spacing: DS.Spacing.sm) {
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text(impact.consumerTypeLabel)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Spacer()
                        Text(impact.severityLabel)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(
                                impact.severity == "HIGH"
                                    ? DS.ColorToken.status("WARNING")
                                    : DS.ColorToken.info
                            )
                    }
                    Text(impact.consumerTitle)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(
                        "\(impact.reasonLabel) · 主张 v\(impact.latestClaimVersion) · "
                        + "关联证据 \(impact.matchedEvidenceCount) 条"
                        + (impact.staleEvidenceCount > 0
                            ? " · 旧证据 \(impact.staleEvidenceCount) 条" : "")
                    )
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    Label("查看证据与版本历史", systemImage: "arrow.up.right.circle")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                }
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .padding(.top, 2)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 7)
        }
        .buttonStyle(.plain)
        .accessibilityHint("打开受影响对象、引用证据和主张版本历史")
    }

    private func errorCard(_ error: String) -> some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                Text("暂时无法读取业务结论影响报告")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text(error)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                Button("重试", action: onRetry)
                    .font(.caption.weight(.semibold))
            }
        }
    }

    private func metric(_ title: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
            Text("\(value)")
                .font(.title3.weight(.semibold).monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
