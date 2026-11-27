import SwiftUI

/// Prioritised risk list. The count and severity are visible before opening details.
struct MissionRiskSummaryCard: View {
    let cockpit: TravelCockpit

    private var highCount: Int { cockpit.risks.filter { $0.severity == "HIGH" }.count }
    private var mediumCount: Int { cockpit.risks.filter { $0.severity == "MEDIUM" }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "风险与准备度",
                subtitle: "\(cockpit.risks.count) 项需关注"
            )
            DSCard(padding: DS.Spacing.md) {
                VStack(alignment: .leading, spacing: DS.Spacing.md) {
                    HStack(spacing: DS.Spacing.sm) {
                        if highCount > 0 {
                            DSBadge(text: "\(highCount) 高风险", color: DS.ColorToken.danger)
                        }
                        if mediumCount > 0 {
                            DSBadge(text: "\(mediumCount) 中风险", color: DS.ColorToken.warning)
                        }
                        if highCount == 0 && mediumCount == 0 {
                            DSBadge(text: "低风险 / 提醒", color: DS.ColorToken.success)
                        }
                    }
                    ForEach(Array(cockpit.risks.prefix(4).enumerated()), id: \.element.id) { index, item in
                        CockpitRiskRow(risk: item, trip: cockpit.trip)
                        if index < min(cockpit.risks.count, 4) - 1 {
                            Divider()
                        }
                    }
                    if cockpit.risks.count > 4 {
                        Text("另有 \(cockpit.risks.count - 4) 项，请打开 Mission 查看")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
        }
    }
}

/// Compact approval queue preview. Deep evidence and full workflow stay in Action Center.
struct MissionDecisionCard: View {
    let suggestion: ProactiveSuggestion
    let isBusy: Bool
    let approve: () async -> Void
    let dismiss: () async -> Void

    private var tint: Color {
        switch suggestion.resolvedSeverity {
        case .high: return DS.ColorToken.danger
        case .medium: return DS.ColorToken.warning
        case .low, .unknown: return DS.ColorToken.textSecondary
        }
    }

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                    Image(systemName: suggestion.resolvedSeverity.tintGlyph)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(tint)
                        .frame(width: 34, height: 34)
                        .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 3) {
                        Text(suggestion.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(suggestion.typeLabel) · \(suggestion.statusLabel)")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Spacer(minLength: 2)
                    DSBadge(text: suggestion.resolvedSeverity.label, color: tint)
                }

                if let rationale = suggestion.rationale, !rationale.isEmpty {
                    Text(rationale)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if let recovery = suggestion.recoveryBrief, !recovery.actions.isEmpty {
                    MissionRecoveryBriefSection(brief: recovery)
                }

                HStack(spacing: DS.Spacing.sm) {
                    Text(suggestion.confidenceLabel)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                    if !suggestion.evidenceList.isEmpty {
                        DSEvidenceTag(title: "\(suggestion.evidenceList.count) 项依据")
                    }
                    Spacer(minLength: 2)
                    Button {
                        Task { await dismiss() }
                    } label: {
                        Image(systemName: "xmark")
                            .font(.caption.weight(.semibold))
                            .frame(width: 36, height: 36)
                    }
                    .buttonStyle(.bordered)
                    .tint(DS.ColorToken.textSecondary)
                    .accessibilityLabel("忽略建议")
                    .disabled(isBusy)

                    Button {
                        Task { await approve() }
                    } label: {
                        Label("批准", systemImage: "checkmark")
                            .font(.caption.weight(.semibold))
                            .frame(minHeight: 36)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(DS.ColorToken.brand)
                    .disabled(isBusy || !suggestion.isActionable)
                }
            }
        }
    }
}


/// A compact, read-only preview of the server-generated appointment recovery plan.
private struct MissionRecoveryBriefSection: View {
    let brief: AppointmentRecoveryBrief

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
            HStack {
                Text("预约恢复")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Spacer()
                Text(brief.statusLabel)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }

            ForEach(Array(brief.actions.prefix(3).enumerated()), id: \.element.id) { index, action in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: DS.Spacing.xs) {
                        Text(action.actionLabel)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Spacer(minLength: 4)
                        Text(action.priorityLabel)
                            .font(.caption2)
                            .foregroundStyle(action.priority.uppercased() == "HIGH"
                                ? DS.ColorToken.danger
                                : DS.ColorToken.textSecondary)
                    }
                    if let delay = action.delayLabel {
                        Text(delay)
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if let rationale = action.rationale, !rationale.isEmpty {
                        Text(rationale)
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .lineLimit(2)
                    }
                }
                if index < min(brief.actions.count, 3) - 1 {
                    Divider()
                }
            }

            if brief.actions.count > 3 {
                Text("另有 \(brief.actions.count - 3) 项恢复动作")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
            if !brief.dataGaps.isEmpty {
                Text("仍需补充 \(brief.dataGaps.count) 项信息，系统不会推断客户可用时段")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
            if brief.humanDecisionRequired {
                Label("待人工确认 · 不自动改期或联系客户", systemImage: "hand.raised")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
        .padding(.vertical, DS.Spacing.xs)
        .accessibilityElement(children: .combine)
    }
}
