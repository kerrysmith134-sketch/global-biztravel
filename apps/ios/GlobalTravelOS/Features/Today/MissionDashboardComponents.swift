import SwiftUI

/// Compact editorial masthead. Device time is labelled as device time, not destination time.
struct MissionHomeHeader: View {
    let greeting: String
    let offlineSummary: String
    let isLocalMode: Bool

    private var statusTint: Color {
        if isLocalMode { return DS.ColorToken.info }
        return offlineSummary == "数据来自服务端"
            ? DS.ColorToken.success
            : DS.ColorToken.warning
    }

    private var todayLabel: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.timeZone = .current
        formatter.dateFormat = "M月d日 EEEE"
        return formatter.string(from: Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("GLOBAL BUSINESS TRAVEL")
                        .font(.system(size: 10, weight: .bold, design: .rounded))
                        .tracking(1.6)
                        .foregroundStyle(DS.ColorToken.brand)
                    Text(greeting)
                        .font(.largeTitle.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                }
                Spacer()
                Image(systemName: DSIconCatalog.mission)
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(width: 46, height: 46)
                    .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: 15))
                    .accessibilityHidden(true)
            }
            HStack(spacing: DS.Spacing.sm) {
                Text(todayLabel + " · 设备时区")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                Spacer(minLength: 2)
                Label(
                    isLocalMode ? "本地模式" : offlineSummary,
                    systemImage: isLocalMode
                        ? "internaldrive"
                        : (offlineSummary == "数据来自服务端" ? "checkmark.icloud" : "wifi.slash")
                )
                .font(.caption2.weight(.medium))
                .foregroundStyle(statusTint)
                .lineLimit(1)
            }
        }
        .padding(.bottom, DS.Spacing.xs)
        .accessibilityElement(children: .contain)
    }
}

/// Primary mission identity card. A single strong surface replaces decorative map-like ornament.
struct MissionHeroCard: View {
    @EnvironmentObject private var environment: AppEnvironment

    let cockpit: TravelCockpit
    let trip: Trip
    let plan: PlanVersionDetail?

    private var destinationText: String {
        let countries = (trip.countries ?? []).filter { !$0.isEmpty }
        if !countries.isEmpty { return countries.joined(separator: "  ·  ") }
        return "目的地待补充"
    }

    private var planStatus: String {
        guard let plan else { return "尚未生成计划" }
        return plan.status == .active ? "执行计划已激活" : "计划待确认 · \(plan.versionLabel)"
    }

    private var planTint: Color {
        plan?.status == .active ? .white : Color.white.opacity(0.78)
    }

    var body: some View {
        NavigationLink {
            TripDetailView(tripId: trip.id, environment: environment)
        } label: {
            VStack(alignment: .leading, spacing: DS.Spacing.lg) {
                HStack(alignment: .center) {
                    Label("CURRENT MISSION", systemImage: DSIconCatalog.mission)
                        .font(.caption2.weight(.bold))
                        .tracking(1)
                        .foregroundStyle(.white.opacity(0.76))
                    Spacer()
                    Text(trip.statusLabel.uppercased())
                        .font(.system(size: 9, weight: .bold))
                        .tracking(0.8)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 6)
                        .background(.white.opacity(0.14), in: Capsule())
                }

                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    Text(trip.name)
                        .font(.title2.weight(.semibold))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    Label(destinationText, systemImage: "mappin.and.ellipse")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white.opacity(0.78))
                        .lineLimit(2)
                }

                Rectangle()
                    .fill(.white.opacity(0.18))
                    .frame(height: 1)

                HStack(alignment: .bottom, spacing: DS.Spacing.md) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("MISSION WINDOW")
                            .font(.system(size: 9, weight: .bold))
                            .tracking(1)
                            .foregroundStyle(.white.opacity(0.62))
                        Text(trip.windowLabel)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.white)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 4)
                    VStack(alignment: .trailing, spacing: 5) {
                        Text("COUNTDOWN")
                            .font(.system(size: 9, weight: .bold))
                            .tracking(1)
                            .foregroundStyle(.white.opacity(0.62))
                        Text(cockpit.countdownText ?? "—")
                            .font(.title3.weight(.semibold).monospacedDigit())
                            .foregroundStyle(.white)
                            .lineLimit(1)
                            .minimumScaleFactor(0.78)
                    }
                }

                HStack(spacing: 6) {
                    Image(systemName: plan?.status == .active ? "checkmark.seal.fill" : "calendar.badge.clock")
                    Text(planStatus)
                    Spacer()
                    Image(systemName: "arrow.up.right")
                }
                .font(.caption.weight(.medium))
                .foregroundStyle(planTint)
            }
            .padding(DS.Spacing.xl)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.ColorToken.heroSurface)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.prominentCard))
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.prominentCard)
                    .stroke(.white.opacity(0.10), lineWidth: 0.8)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("当前 Mission：\(trip.name)，\(destinationText)，\(cockpit.countdownText ?? "时间窗口未设置")。进入行程详情")
    }
}

/// Three source-backed operational measures. Unknown values remain an em dash.
struct MissionMetricStrip: View {
    let trip: Trip
    let plan: PlanVersionDetail?

    private var customerValue: String {
        guard let count = trip.stats?.customerCount else { return "—" }
        return String(count)
    }

    private var visitValue: String {
        plan.map { String($0.displayStopCount) } ?? "—"
    }

    private var dayValue: String {
        plan.map { String($0.displayDayCount) } ?? "—"
    }

    var body: some View {
        HStack(spacing: DS.Spacing.sm) {
            metric("客户", value: customerValue, detail: "本次 Mission", symbol: DSIconCatalog.customers)
            metric("计划拜访", value: visitValue, detail: plan == nil ? "尚无计划" : "计划停靠点", symbol: "mappin.and.ellipse")
            metric("行程天数", value: dayValue, detail: "计划覆盖日期", symbol: DSIconCatalog.calendar)
        }
    }

    private func metric(_ title: String, value: String, detail: String, symbol: String) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: symbol)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.brand)
                Text(value)
                    .font(.title2.weight(.semibold).monospacedDigit())
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text(detail)
                    .font(.system(size: 9))
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A compact operational pulse, built only from the loaded cockpit and actionable suggestions.
struct MissionPulseStrip: View {
    let riskCount: Int
    let decisionCount: Int
    let upcomingCount: Int

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            HStack(spacing: 0) {
                pulse(
                    title: "待处理风险",
                    value: riskCount,
                    symbol: DSIconCatalog.warning,
                    tint: riskCount > 0 ? DS.ColorToken.warning : DS.ColorToken.success
                )
                divider
                pulse(
                    title: "待你决策",
                    value: decisionCount,
                    symbol: DSIconCatalog.actions,
                    tint: decisionCount > 0 ? DS.ColorToken.info : DS.ColorToken.success
                )
                divider
                pulse(
                    title: "未来 7 天",
                    value: upcomingCount,
                    symbol: DSIconCatalog.calendar,
                    tint: DS.ColorToken.brand
                )
            }
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(DS.ColorToken.border)
            .frame(width: 1, height: 34)
            .padding(.horizontal, DS.Spacing.sm)
    }

    private func pulse(title: String, value: Int, symbol: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Image(systemName: symbol)
                .font(.caption)
                .foregroundStyle(tint)
            Text(String(value))
                .font(.title3.weight(.semibold).monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
            Text(title)
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(DS.ColorToken.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// Next stop is the most immediate field action; it opens the real plan/map context.
struct MissionNextStopCard: View {
    let cockpit: TravelCockpit
    let trip: Trip
    let plan: PlanVersionDetail

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "下一站",
                subtitle: cockpit.nextDayLabel ?? "下一项待执行拜访"
            )
            NavigationLink {
                PlanMapView(plan: plan, origin: trip.originPlace, title: "地图")
            } label: {
                DSCard {
                    VStack(alignment: .leading, spacing: DS.Spacing.md) {
                        HStack(alignment: .top, spacing: DS.Spacing.md) {
                            Image(systemName: "arrow.turn.down.right")
                                .font(.system(size: 18, weight: .semibold))
                                .foregroundStyle(DS.ColorToken.brand)
                                .frame(width: 42, height: 42)
                                .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: 13))
                            VStack(alignment: .leading, spacing: 4) {
                                Text(cockpit.nextStop?.displayName ?? "下一站")
                                    .font(.headline)
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                                if let city = cockpit.nextStop?.city, !city.isEmpty {
                                    Text(city)
                                        .font(.caption)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                            Spacer(minLength: 4)
                            Image(systemName: "arrow.up.right")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.brand)
                        }

                        if let next = cockpit.nextStop {
                            HStack(spacing: DS.Spacing.md) {
                                timeItem("预计到达", value: next.arrive.map {
                                    CockpitFormat.time($0, timeZone: cockpit.timeZone)
                                } ?? "时间未确认")
                                if let leaveBy = cockpit.leaveBy {
                                    timeItem("建议出发", value: CockpitFormat.time(leaveBy, timeZone: cockpit.timeZone))
                                }
                            }
                            if let minutes = next.travelMinutes, minutes > 0 {
                                Label("计划路程 · \(CockpitFormat.duration(minutes))", systemImage: DSIconCatalog.trip)
                                    .font(.caption.weight(.medium))
                                    .foregroundStyle(DS.ColorToken.brand)
                            }
                        }
                    }
                }
            }
            .buttonStyle(.plain)
        }
    }

    private func timeItem(_ title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
            Text(value)
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Today's agenda remains a true schedule: times and timezone are visible, never inferred from device locale.
struct MissionTodayAgendaCard: View {
    let cockpit: TravelCockpit
    let emptyText: String

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "今日安排",
                subtitle: cockpit.timeZone.map { $0.identifier } ?? "计划时区未配置"
            )
            DSCard(padding: DS.Spacing.md) {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    HStack {
                        Label("拜访时间轴", systemImage: "calendar.day.timeline.left")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Spacer()
                        Text("\(cockpit.todayStops.count) 站")
                            .font(.caption.weight(.semibold).monospacedDigit())
                            .foregroundStyle(DS.ColorToken.brand)
                    }
                    if cockpit.todayStops.isEmpty {
                        Text(emptyText)
                            .font(.footnote)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .padding(.vertical, DS.Spacing.sm)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(cockpit.todayStops.enumerated()), id: \.element.id) { index, stop in
                                CockpitStopRow(stop: stop, timeZone: cockpit.timeZone)
                                if index < cockpit.todayStops.count - 1 {
                                    Divider().padding(.leading, 58)
                                }
                            }
                        }
                    }
                    if !cockpit.todayStops.isEmpty {
                        HStack {
                            Text("客户服务时长")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            Spacer()
                            Text(CockpitFormat.duration(cockpit.todayServiceMinutes))
                                .font(.caption.weight(.semibold).monospacedDigit())
                                .foregroundStyle(DS.ColorToken.textPrimary)
                        }
                        .padding(.top, DS.Spacing.xs)
                    }
                }
            }
        }
    }
}

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

/// Budget snapshot from the trip budget endpoint. No FX conversion or ratio is fabricated.
struct MissionBudgetCard: View {
    let summary: BudgetSummary

    private var spendRatio: Double? {
        guard
            let budget = summary.budgetAmount,
            budget > 0,
            let actual = summary.actualTotalInBudgetCurrency
        else { return nil }
        let denominator = NSDecimalNumber(decimal: budget).doubleValue
        guard denominator > 0 else { return nil }
        let numerator = NSDecimalNumber(decimal: actual).doubleValue
        return min(max(numerator / denominator, 0), 1)
    }

    private var actualDisplay: String {
        if let actual = summary.actualTotalInBudgetCurrency {
            return "\(summary.budgetCurrency) \(NSDecimalNumber(decimal: actual).stringValue)"
        }
        return summary.actualLabel
    }

    var body: some View {
        DSCard(padding: DS.Spacing.lg) {
            VStack(alignment: .leading, spacing: DS.Spacing.md) {
                DSSectionHeader(title: "差旅预算", subtitle: "计划与实际 · \(summary.budgetCurrency)")
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("预算上限")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text(summary.budgetLabel)
                            .font(.title3.weight(.semibold).monospacedDigit())
                            .foregroundStyle(DS.ColorToken.textPrimary)
                    }
                    Spacer(minLength: DS.Spacing.sm)
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("实际发生")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text(actualDisplay)
                            .font(.subheadline.weight(.semibold).monospacedDigit())
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .multilineTextAlignment(.trailing)
                    }
                }
                if let spendRatio {
                    ProgressView(value: spendRatio)
                        .tint(spendRatio >= 1 ? DS.ColorToken.danger : DS.ColorToken.brand)
                        .accessibilityLabel("预算使用率")
                        .accessibilityValue("\(Int(spendRatio * 100))%")
                    Text("已使用 \(Int(spendRatio * 100))%")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(spendRatio >= 1 ? DS.ColorToken.danger : DS.ColorToken.textSecondary)
                } else {
                    DSDataQualityTag(kind: .gap, text: "缺少可比币种金额，暂不计算预算使用率")
                }
                HStack(alignment: .top, spacing: DS.Spacing.md) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("计划估算")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text(summary.plannedLabel)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                    }
                    Spacer(minLength: DS.Spacing.sm)
                    if let note = summary.conversionNote {
                        Text(note)
                            .font(.caption2)
                            .foregroundStyle(summary.conversionComplete == true ? DS.ColorToken.success : DS.ColorToken.warning)
                            .multilineTextAlignment(.trailing)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

struct MissionEmptyState: View {
    let onOpenTrips: () -> Void

    var body: some View {
        DSCard(padding: DS.Spacing.xl) {
            VStack(alignment: .leading, spacing: DS.Spacing.md) {
                Image(systemName: "globe.europe.africa")
                    .font(.system(size: 30, weight: .light))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(width: 58, height: 58)
                    .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: 18))
                VStack(alignment: .leading, spacing: 5) {
                    Text("没有进行中的商务 Mission")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    Text("创建商务任务、加入目标客户与出行窗口，再由规划引擎生成可核验的方案。这里会持续汇总路线、风险、预算和待决策事项。")
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                DSButton(title: "创建或管理 Mission", systemImage: DSIconCatalog.trip, action: onOpenTrips)
            }
        }
    }
}
