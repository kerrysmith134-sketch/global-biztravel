import SwiftUI

/// A focused inspection drawer for a selected stop on the route map.
struct MapVisitDetailDrawer: View {
    let stop: PlanStop
    let visit: TripVisit?
    let customer: Customer?
    let dayDate: String?
    let timeZone: TimeZone
    let isPlanActive: Bool
    let isSubmittingChange: Bool
    let onCancelVisit: ((PlanStop) -> Void)?
    let onClose: () -> Void

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                header
                customerContext
                visitContext
                timingContext
                travelContext
                cancellationAction
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .accessibilityElement(children: .contain)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: DS.Spacing.sm) {
            Text(String(format: "%02d", stop.sequence))
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(DS.ColorToken.brand, in: RoundedRectangle(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 3) {
                Text(stop.displayName)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .lineLimit(2)
                Text([stop.city, stop.countryCode].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
            Spacer(minLength: 2)
            Button {
                onClose()
            } label: {
                Image(systemName: "xmark")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .frame(width: 32, height: 32)
                    .background(DS.ColorToken.surfaceSecondary, in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("关闭拜访详情")
        }
    }

    private var customerContext: some View {
        Group {
            if let customer {
                VStack(alignment: .leading, spacing: 3) {
                    Label(customer.name, systemImage: "building.2")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    if let industry = customer.industry, !industry.isEmpty {
                        Text(industry)
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var visitContext: some View {
        if let visit {
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                HStack(spacing: DS.Spacing.xs) {
                    DSBadge(text: visit.statusLabel, color: DS.ColorToken.status(visit.status ?? ""))
                    if let priority = visit.priority {
                        DSBadge(text: "优先级 \(priority)", color: DS.ColorToken.priority("P\(priority)"))
                    }
                    if let customer {
                        DSBadge(text: customer.tierLabel, color: DS.ColorToken.tier(customer.tier))
                    }
                }
                if let objective = visit.objective, !objective.isEmpty {
                    Text(objective)
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if visit.timeWindowStart != nil || visit.timeWindowEnd != nil {
                    Label(visit.timeWindowLabel, systemImage: "clock")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private var timingContext: some View {
        HStack(spacing: DS.Spacing.md) {
            if let dayDate { Text(dayDate) }
            Label(stop.arrive.map { time($0) } ?? "到达时间未知", systemImage: "clock")
            if let depart = stop.depart {
                Text("离开 \(time(depart))")
            }
            Spacer(minLength: 0)
            if let minutes = stop.serviceMinutes {
                Text("停留 \(duration(minutes))")
            }
        }
        .font(.caption)
        .foregroundStyle(DS.ColorToken.textSecondary)
    }

    private var travelContext: some View {
        HStack(alignment: .center, spacing: DS.Spacing.sm) {
            if let travel = stop.travelMinutes {
                Label("路程 \(duration(travel))", systemImage: "car")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
            if stop.distanceKm != nil {
                Text(stop.distanceLabel)
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
            if stop.travelIsStale {
                DSDataQualityTag(kind: .stale, text: "交通数据需复核")
            } else if let source = stop.travelSource {
                DSDataQualityTag(
                    kind: source == "osrm-road" ? .live : .estimate,
                    text: source == "osrm-road" ? "路网路线" : "路线估算"
                )
            }
            Spacer(minLength: 0)
            if let cost = stop.costAmount {
                Text("\(stop.costCurrency ?? "") \(String(format: "%.2f", cost))")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(DS.ColorToken.textPrimary)
            }
        }
    }

    @ViewBuilder
    private var cancellationAction: some View {
        if isPlanActive, let onCancelVisit, visit?.isOpen ?? true {
            Divider()
            Button(role: .destructive) {
                onCancelVisit(stop)
            } label: {
                Label(
                    isSubmittingChange ? "正在处理计划变更…" : "取消此拜访并重新优化路线",
                    systemImage: "arrow.clockwise"
                )
                .font(.caption.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 42)
            }
            .disabled(isSubmittingChange)
            .accessibilityHint("提交前会要求再次确认，服务端将生成新的计划版本")
        }
    }

    private func time(_ date: Date) -> String {
        ISO8601.timeDisplay(from: date, timeZone: timeZone)
    }

    private func duration(_ minutes: Int) -> String {
        Distance.formatDuration(minutes: minutes)
    }
}
