import SwiftUI

struct MarketResearchQuestionCard: View {
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
