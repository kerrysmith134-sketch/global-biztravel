import SwiftUI

/// Editorial header for the Mission workspace.
struct MissionHomeHeader: View {
    let greeting: String
    let offlineSummary: String
    let isLocalMode: Bool

    private var todayLabel: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "yyyy年M月d日 · EEEE"
        return formatter.string(from: Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.md) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("GLOBAL BUSINESS TRAVEL")
                        .font(.system(size: 10, weight: .bold, design: .rounded))
                        .tracking(1.5)
                        .foregroundStyle(DS.ColorToken.brand)
                    Text(greeting)
                        .font(.system(size: 28, weight: .semibold, design: .rounded))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                }
                Spacer()
                Image(systemName: "globe.europe.africa")
                    .font(.system(size: 25, weight: .light))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(width: 52, height: 52)
                    .background(DS.ColorToken.brandSubtle, in: Circle())
                    .accessibilityHidden(true)
            }
            HStack(spacing: DS.Spacing.sm) {
                Text(todayLabel)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                Spacer(minLength: 2)
                Label(isLocalMode ? "设备本地" : offlineSummary,
                      systemImage: isLocalMode ? "internaldrive" : (offlineSummary == "数据来自服务端" ? "checkmark.icloud" : "wifi.slash"))
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(isLocalMode ? DS.ColorToken.info : (offlineSummary == "数据来自服务端" ? DS.ColorToken.success : DS.ColorToken.warning))
                    .lineLimit(1)
            }
        }
        .padding(.bottom, DS.Spacing.xs)
        .accessibilityElement(children: .combine)
    }
}

/// Primary mission card: status, destination, trip window and next operational cue.
struct MissionHeroCard: View {
    @EnvironmentObject private var environment: AppEnvironment

    let cockpit: TravelCockpit
    let trip: Trip
    let plan: PlanVersionDetail?

    private var countdown: String {
        cockpit.countdownText ?? "时间窗口未设置"
    }

    private var statusText: String {
        if let plan {
            return plan.status == .active ? "执行计划已激活" : "计划待确认 · \(plan.versionLabel)"
        }
        return "尚未生成行程计划"
    }

    private var destinationText: String {
        let countries = (trip.countries ?? []).filter { !$0.isEmpty }
        return countries.isEmpty ? "目的地待补充" : countries.joined(separator: "  ·  ")
    }

    var body: some View {
        NavigationLink {
            TripDetailView(tripId: trip.id, environment: environment)
        } label: {
            ZStack(alignment: .topTrailing) {
                Circle()
                    .stroke(.white.opacity(0.10), lineWidth: 1)
                    .frame(width: 190, height: 190)
                    .offset(x: 78, y: -86)
                Circle()
                    .stroke(.white.opacity(0.08), lineWidth: 1)
                    .frame(width: 132, height: 132)
                    .offset(x: 40, y: -57)

                VStack(alignment: .leading, spacing: DS.Spacing.md) {
                    HStack(alignment: .center) {
                        Label("当前 Mission", systemImage: "scope")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.82))
                        Spacer()
                        Text(trip.statusLabel)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 5)
                            .background(.white.opacity(0.14), in: Capsule())
                    }

                    VStack(alignment: .leading, spacing: 5) {
                        Text(trip.name)
                            .font(.system(size: 23, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                        Label(destinationText, systemImage: "mappin.and.ellipse")
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.white.opacity(0.78))
                            .lineLimit(1)
                    }

                    HStack(alignment: .bottom, spacing: DS.Spacing.md) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(countdown)
                                .font(.system(size: 21, weight: .semibold, design: .rounded).monospacedDigit())
                                .foregroundStyle(.white)
                                .lineLimit(1)
                                .minimumScaleFactor(0.78)
                            Text(trip.windowLabel)
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.72))
                        }
                        Spacer(minLength: 4)
                        VStack(alignment: .trailing, spacing: 5) {
                            Label(statusText, systemImage: plan?.status == .active ? "checkmark.seal.fill" : "calendar.badge.clock")
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(.white.opacity(0.82))
                                .lineLimit(2)
                                .multilineTextAlignment(.trailing)
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(.white)
                                .frame(width: 34, height: 34)
                                .background(.white.opacity(0.14), in: Circle())
                        }
                    }
                }
                .padding(DS.Spacing.xl)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(DS.ColorToken.brandPressed)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.prominentCard))
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.prominentCard)
                    .stroke(.white.opacity(0.10), lineWidth: 0.8)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("当前 Mission：\(trip.name)，\(destinationText)，\(countdown)。进入行程详情")
    }
}

/// Three defensible operational KPIs. Missing values remain an em dash, never a synthetic zero.
struct MissionMetricStrip: View {
    let trip: Trip
    let plan: PlanVersionDetail?

    private var customerValue: String {
        trip.stats?.customerCount.map(String.init) ?? "—"
    }

    private var plannedVisitValue: String {
        guard let plan else { return "—" }
        return String(plan.displayStopCount)
    }

    private var plannedDayValue: String {
        guard let plan else { return "—" }
        return String(plan.displayDayCount)
    }

    var body: some View {
        HStack(spacing: DS.Spacing.sm) {
            DSMetricCard(
                title: "客户",
                value: customerValue,
                detail: "本次行程客户",
                symbol: "building.2"
            )
            DSMetricCard(
                title: "计划拜访",
                value: plannedVisitValue,
                detail: plan == nil ? "尚无计划" : "计划停靠点",
                symbol: "mappin.and.ellipse"
            )
            DSMetricCard(
                title: "规划天数",
                value: plannedDayValue,
                detail: "计划覆盖日期",
                symbol: "calendar"
            )
        }
    }
}

/// Budget snapshot from the trip budget endpoint. No conversion or ratio is invented when
/// the backend has not supplied a comparable amount in the budget currency.
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
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.md) {
                DSSectionHeader(
                    title: "差旅预算",
                    subtitle: "预算、计划估算与实际费用"
                )
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("预算")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text(summary.budgetLabel)
                            .font(.system(size: 19, weight: .semibold, design: .rounded).monospacedDigit())
                            .foregroundStyle(DS.ColorToken.textPrimary)
                    }
                    Spacer(minLength: DS.Spacing.sm)
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("实际")
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
                }
                HStack(alignment: .top, spacing: DS.Spacing.md) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("计划估算")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Text(summary.plannedLabel)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
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
                    Text("还没有进行中的 Mission")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    Text("创建商务出行，加入目标客户与时间窗口，再由规划引擎生成可核验的行程方案。")
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                DSButton(title: "管理商务行程", systemImage: "airplane", action: onOpenTrips)
            }
        }
    }
}
