import SwiftUI

struct CockpitStopRow: View {
    let stop: PlanStop
    let timeZone: TimeZone?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(arriveText)
                    .font(.caption.monospacedDigit().weight(.semibold))
                Text(departText)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            .frame(width: 46, alignment: .leading)

            VStack(alignment: .leading, spacing: 2) {
                Text(stop.displayName).font(.subheadline)
                HStack(spacing: 6) {
                    if let city = stop.city, !city.isEmpty { Text(city) }
                    if let travel = stop.travelMinutes, travel > 0 {
                        Text("路程 \(CockpitFormat.duration(travel))")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.vertical, 9)
        .padding(.horizontal, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(arriveText) \(stop.displayName)")
    }

    private var arriveText: String {
        guard let arrive = stop.arrive else { return "—" }
        return CockpitFormat.time(arrive, timeZone: timeZone)
    }

    private var departText: String {
        guard let depart = stop.depart else { return "" }
        return CockpitFormat.time(depart, timeZone: timeZone)
    }
}

struct CockpitRiskRow: View {
    @EnvironmentObject private var environment: AppEnvironment
    let risk: TravelCockpit.Risk
    let trip: Trip?

    var body: some View {
        NavigationLink {
            if let trip {
                TripDetailView(tripId: trip.id, environment: environment)
            }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                DSBadge(text: severityLabel, color: DS.ColorToken.severity(risk.severity))
                VStack(alignment: .leading, spacing: 2) {
                    Text(risk.title)
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                    if let detail = risk.detail, !detail.isEmpty {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .padding(.top, 2)
            }
            .padding(.vertical, 9)
            .padding(.horizontal, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }

    private var severityLabel: String {
        switch risk.severity {
        case "HIGH": return "高"
        case "MEDIUM": return "中"
        default: return "低"
        }
    }
}

/// 驾驶舱的时刻/时长格式化（统一按行程出发地时区）。
enum CockpitFormat {
    static func time(_ date: Date, timeZone: TimeZone?) -> String {
        ISO8601.timeDisplay(from: date, timeZone: timeZone ?? .current)
    }

    static func day(_ date: Date, timeZone: TimeZone?) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.timeZone = timeZone ?? .current
        formatter.dateFormat = "M月d日 EEE"
        return formatter.string(from: date)
    }

    static func duration(_ minutes: Int) -> String {
        if minutes >= 60 {
            let hours = minutes / 60
            let rest = minutes % 60
            return rest == 0 ? "\(hours) 小时" : "\(hours) 小时 \(rest) 分"
        }
        return "\(minutes) 分钟"
    }
}

struct SuggestionCard: View {
    let suggestion: ProactiveSuggestion
    let isBusy: Bool
    let approve: () async -> Void
    let dismiss: () async -> Void

    var body: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.md) {
                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                    Image(systemName: suggestion.resolvedSeverity.tintGlyph)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(tint)
                        .frame(width: 34, height: 34)
                        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(suggestion.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(suggestion.typeLabel) · \(suggestion.statusLabel)")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Spacer(minLength: 2)
                    DSBadge(text: suggestion.resolvedSeverity.label, color: tint)
                }

                if let rationale = suggestion.rationale, !rationale.isEmpty {
                    Text(rationale)
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if !suggestion.evidenceList.isEmpty {
                    VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                        Text("依据与来源")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        ForEach(suggestion.evidenceList) { evidence in
                            DSEvidenceTag(title: evidence.displayText)
                        }
                    }
                }

                if let action = suggestion.proposedAction {
                    Label(action.actionSummary, systemImage: "wand.and.stars")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(alignment: .center, spacing: DS.Spacing.md) {
                    Text(suggestion.confidenceLabel)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                    Spacer()
                    if suggestion.isActionable {
                        Button {
                            Task { await approve() }
                        } label: {
                            Label("批准并准备", systemImage: "checkmark.circle.fill")
                        }
                        .font(.caption.weight(.semibold))
                        .buttonStyle(.borderedProminent)
                        .tint(DS.ColorToken.brand)
                        .disabled(isBusy)

                        Button(role: .destructive) {
                            Task { await dismiss() }
                        } label: {
                            Label("忽略", systemImage: "xmark")
                        }
                        .font(.caption.weight(.medium))
                        .buttonStyle(.bordered)
                        .disabled(isBusy)
                    }
                }
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var tint: Color {
        switch suggestion.resolvedSeverity {
        case .high: return DS.ColorToken.warning
        case .medium: return DS.ColorToken.info
        case .low: return DS.ColorToken.textSecondary
        case .unknown: return DS.ColorToken.textSecondary
        }
    }
}

private extension ProposedAction {
    var actionSummary: String {
        "\(title) · \(toolLabel)"
    }
}

struct ErrorBanner: View {
    let message: String

    var body: some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote)
            .foregroundStyle(.red)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }
}
