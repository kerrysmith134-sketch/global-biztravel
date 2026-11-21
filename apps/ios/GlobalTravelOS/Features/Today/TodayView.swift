import SwiftUI

@MainActor
final class TodayViewModel: ObservableObject {
    @Published private(set) var items: [ProactiveSuggestion] = []
    @Published private(set) var dailyCap: DailyCap?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published private(set) var busyIds: Set<String> = []
    /// 差旅驾驶舱（审计 5.1）：今天时间轴、下一站、风险与 7 天窗口。
    @Published private(set) var cockpit: TravelCockpit?
    @Published private(set) var activePlan: PlanVersionDetail?
    @Published private(set) var budgetSummary: BudgetSummary?
    @Published private(set) var budgetErrorMessage: String?

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let feed = try await environment.proactiveRepository.suggestions(
                tripId: nil,
                status: nil,
                limit: 30,
                cursor: nil
            )
            items = feed.items
            dailyCap = feed.dailyCap
        } catch {
            // Mission data must stay usable when the proactive suggestion service is unavailable.
            errorMessage = environment.errorMessage(error)
        }
        await loadCockpit()
        environment.refreshOfflineSummary()
    }

    /// Load one selected mission and its verified operational context.
    /// Financial values come from the budget endpoint; no client-side estimate is fabricated.
    private func loadCockpit() async {
        do {
            let page = try await environment.tripRepository.listTrips(page: PageRequest(limit: 50))
            let trips = page.items
            guard let selected = TravelCockpitBuilder.selectTrip(from: trips, now: Date()) else {
                cockpit = nil
                activePlan = nil
                budgetSummary = nil
                budgetErrorMessage = nil
                return
            }

            // List responses can omit detail-only fields such as customer stats and active plan.
            let detailedTrip = (try? await environment.tripRepository.trip(id: selected.id)) ?? selected
            var enrichedTrips = trips
            if let index = enrichedTrips.firstIndex(where: { $0.id == detailedTrip.id }) {
                enrichedTrips[index] = detailedTrip
            }

            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = detailedTrip.originPlace?.timeZone() ?? .current

            var planId = detailedTrip.activePlanVersionId
            var planDetail = detailedTrip.activePlan
            if planDetail == nil, planId == nil {
                let plans = try? await environment.planRepository.listPlans(
                    tripId: detailedTrip.id, page: PageRequest(limit: 1)
                )
                planId = plans?.items.first?.id
            }
            if planDetail == nil, let planId {
                planDetail = try? await environment.planRepository.planDetail(
                    tripId: detailedTrip.id, planId: planId
                )
            }

            let visaCases = (try? await environment.tripRepository.visaCases(tripId: detailedTrip.id)) ?? []
            activePlan = planDetail
            cockpit = TravelCockpitBuilder.build(
                trips: enrichedTrips,
                plan: planDetail,
                visaCases: visaCases,
                now: Date(),
                calendar: calendar
            )

            do {
                budgetSummary = try await environment.tripRepository.budgetSummary(
                    tripId: detailedTrip.id,
                    baseCurrency: detailedTrip.currency
                )
                budgetErrorMessage = nil
            } catch {
                budgetSummary = nil
                budgetErrorMessage = environment.errorMessage(error)
            }
        } catch {
            cockpit = nil
            activePlan = nil
            budgetSummary = nil
            budgetErrorMessage = environment.errorMessage(error)
        }
    }

    func approve(_ suggestion: ProactiveSuggestion) async {
        busyIds.insert(suggestion.id)
        defer { busyIds.remove(suggestion.id) }
        do {
            let response = try await environment.proactiveRepository.approve(
                suggestionId: suggestion.id
            )
            if let index = items.firstIndex(where: { $0.id == suggestion.id }) {
                items[index].status = response.status ?? "EXECUTED"
            }
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func dismiss(_ suggestion: ProactiveSuggestion) async {
        busyIds.insert(suggestion.id)
        defer { busyIds.remove(suggestion.id) }
        do {
            let response = try await environment.proactiveRepository.dismiss(
                suggestionId: suggestion.id
            )
            if let index = items.firstIndex(where: { $0.id == suggestion.id }) {
                items[index].status = response.status ?? "DISMISSED"
            }
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct TodayView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TodayViewModel
        @State private var showsActionCenter = false
    @State private var showsNotifications = false
    @State private var showsAssistant = false
    @State private var showsTrips = false

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: TodayViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.lg) {
                    MissionHomeHeader(
                        greeting: greeting,
                        offlineSummary: environment.offlineSummary,
                        isLocalMode: environment.isLocalMode
                    )

                    if let error = viewModel.errorMessage {
                        DSErrorState(message: error)
                    }

                    if let cockpit = viewModel.cockpit, let trip = cockpit.trip {
                        MissionHeroCard(cockpit: cockpit, trip: trip, plan: viewModel.activePlan)
                        MissionMetricStrip(trip: trip, plan: viewModel.activePlan)
                        missionOperationsSection(cockpit)

                        if let budget = viewModel.budgetSummary {
                            NavigationLink {
                                TripDetailView(tripId: trip.id, environment: environment)
                            } label: {
                                MissionBudgetCard(summary: budget)
                            }
                            .buttonStyle(.plain)
                        } else if let budgetError = viewModel.budgetErrorMessage {
                            DSCard {
                                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                                    DSSectionHeader(title: "差旅预算", subtitle: "预算数据暂时不可用")
                                    Text(budgetError)
                                        .font(.caption)
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                        }

                        if !cockpit.risks.isEmpty {
                            cockpitRiskSection(cockpit)
                        }
                        decisionSection
                        if !cockpit.upcoming.isEmpty {
                            upcomingSection(cockpit)
                        }
                    } else if viewModel.isLoading {
                        DSLoadingState(message: "正在汇总 Mission、路线与预算…")
                    } else {
                        MissionEmptyState {
                            showsTrips = true
                        }
                        decisionSection
                    }
                }
                .padding(.horizontal, DS.Spacing.lg)
                .padding(.top, DS.Spacing.md)
                .padding(.bottom, DS.Spacing.xxxl)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("任务")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsAssistant = true
                    } label: {
                        Image(systemName: DSIconCatalog.assistant)
                    }
                    .accessibilityLabel("AI 助手")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsNotifications = true
                    } label: {
                        Image(systemName: DSIconCatalog.notifications)
                    }
                    .accessibilityLabel("通知")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsTrips = true
                    } label: {
                        Image(systemName: DSIconCatalog.trip)
                    }
                    .accessibilityLabel("出行")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsActionCenter = true
                    } label: {
                        Image(systemName: DSIconCatalog.actions)
                    }
                    .accessibilityLabel("行动中心")
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
            .sheet(isPresented: $showsNotifications) {
                NotificationsView(environment: environment)
            }
            .sheet(isPresented: $showsAssistant) {
                AssistantView(environment: environment)
            }
            .sheet(isPresented: $showsActionCenter) {
                ActionCenterView(environment: environment)
            }
            .sheet(isPresented: $showsTrips) {
                TripsView(environment: environment)
            }
        }
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<11: return "早上好"
        case 11..<13: return "中午好"
        case 13..<18: return "下午好"
        default: return "晚上好"
        }
    }

    private var decisionSection: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.md) {
            DSSectionHeader(
                title: "AI 洞察与待决策",
                subtitle: "\(pendingSuggestions.count) 项待你确认",
                actionTitle: "全部查看"
            ) {
                showsActionCenter = true
            }

            if pendingSuggestions.isEmpty && !viewModel.isLoading {
                DSCard {
                    HStack(spacing: DS.Spacing.md) {
                        Image(systemName: "checkmark.seal")
                            .font(.title2)
                            .foregroundStyle(DS.ColorToken.success)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("暂时没有待决策事项")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Text("系统会继续监控计划风险和待处理建议。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                    }
                }
            } else {
                ForEach(Array(pendingSuggestions.prefix(3))) { suggestion in
                    SuggestionCard(
                        suggestion: suggestion,
                        isBusy: viewModel.busyIds.contains(suggestion.id),
                        approve: { await viewModel.approve(suggestion) },
                        dismiss: { await viewModel.dismiss(suggestion) }
                    )
                }
                if pendingSuggestions.count > 3 {
                    Button("查看其余 \(pendingSuggestions.count - 3) 项建议") {
                        showsActionCenter = true
                    }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(maxWidth: .infinity, minHeight: 44)
                }
            }

            if let cap = viewModel.dailyCap {
                Text(cap.displayText)
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
    }

    private var pendingSuggestions: [ProactiveSuggestion] {
        viewModel.items
            .filter(\.isActionable)
            .sorted {
                if $0.resolvedSeverity.rank != $1.resolvedSeverity.rank {
                    return $0.resolvedSeverity.rank > $1.resolvedSeverity.rank
                }
                return ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast)
            }
    }

    @ViewBuilder
    private func missionOperationsSection(_ cockpit: TravelCockpit) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.md) {
            DSSectionHeader(
                title: "今日执行",
                subtitle: cockpit.timeZone.map { "计划基准时区 · " + zoneName($0) } ?? "计划时区未配置"
            )
            DSCard {
                VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                    HStack {
                        Label("今日拜访", systemImage: "calendar.day.timeline.left")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Spacer()
                        Text("\(cockpit.todayStops.count) 站")
                            .font(.caption.weight(.semibold).monospacedDigit())
                            .foregroundStyle(DS.ColorToken.brand)
                    }
                    if cockpit.todayStops.isEmpty {
                        Text(emptyTodayText(cockpit))
                            .font(.footnote)
                            .foregroundStyle(DS.ColorToken.textSecondary)
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

            if let next = cockpit.nextStop {
                DSSectionHeader(title: "下一站", subtitle: cockpit.nextDayLabel ?? "下一项待执行拜访")
                DSCard {
                    HStack(alignment: .top, spacing: DS.Spacing.md) {
                        Image(systemName: "arrow.turn.down.right")
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundStyle(DS.ColorToken.brand)
                            .frame(width: 38, height: 38)
                            .background(DS.ColorToken.brandSubtle, in: RoundedRectangle(cornerRadius: 12))
                        VStack(alignment: .leading, spacing: 4) {
                            Text(next.displayName)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            if let city = next.city, !city.isEmpty {
                                Text(city)
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if let arrive = next.arrive {
                                Text("预计到达 \(CockpitFormat.time(arrive, timeZone: cockpit.timeZone))")
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            if let leaveBy = cockpit.leaveBy {
                                Text("建议 \(CockpitFormat.time(leaveBy, timeZone: cockpit.timeZone)) 出发 · 路程 \(CockpitFormat.duration(next.travelMinutes ?? 0))")
                                    .font(.caption.weight(.medium))
                                    .foregroundStyle(DS.ColorToken.brand)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        }
    }

    private func emptyTodayText(_ cockpit: TravelCockpit) -> String {
        if let countdown = cockpit.countdownText, countdown.hasPrefix("D-"),
           let start = cockpit.trip?.window?.start {
            return "今天没有排期；\(CockpitFormat.day(start, timeZone: cockpit.timeZone)) 出发。"
        }
        return "今天没有排定拜访。"
    }

    private func zoneName(_ timeZone: TimeZone) -> String {
        if timeZone.identifier == TimeZone.current.identifier { return "本地时区" }
        return timeZone.localizedName(for: .generic, locale: Locale(identifier: "zh_CN"))
            ?? timeZone.identifier
    }

    @ViewBuilder
    private func cockpitRiskSection(_ cockpit: TravelCockpit) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("待处理").font(.headline)
                Spacer()
                Text("\(cockpit.risks.count) 项")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            VStack(spacing: 0) {
                ForEach(Array(cockpit.risks.enumerated()), id: \.element.id) { index, risk in
                    CockpitRiskRow(risk: risk, trip: cockpit.trip)
                    if index < cockpit.risks.count - 1 {
                        Divider().padding(.leading, 26)
                    }
                }
            }
            .background(DS.ColorToken.surface, in: RoundedRectangle(cornerRadius: DS.Radius.card))
        }
    }

    @ViewBuilder
    private func upcomingSection(_ cockpit: TravelCockpit) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("未来 7 天").font(.headline)
            VStack(spacing: 0) {
                ForEach(Array(cockpit.upcoming.enumerated()), id: \.element.id) { index, trip in
                    NavigationLink {
                        TripDetailView(tripId: trip.id, environment: environment)
                    } label: {
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(trip.name).font(.subheadline)
                                if let start = trip.window?.start {
                                    Text(CockpitFormat.day(start, timeZone: cockpit.timeZone))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 8)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if index < cockpit.upcoming.count - 1 {
                        Divider().padding(.leading, 2)
                    }
                }
            }
            .padding(.horizontal, 12)
            .background(.background, in: RoundedRectangle(cornerRadius: DS.Radius.card))
        }
    }


}

/// 驾驶舱英雄卡：当前/下一行程、位置、倒计时与今日概览。
private struct CockpitHeroCard: View {
    @EnvironmentObject private var environment: AppEnvironment
    let cockpit: TravelCockpit
    let trip: Trip

    var body: some View {
        NavigationLink {
            TripDetailView(tripId: trip.id, environment: environment)
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Label(labelText, systemImage: labelGlyph)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(labelColor)
                    Spacer()
                    if let countdown = cockpit.countdownText {
                        Text(countdown)
                            .font(.caption.monospacedDigit().weight(.semibold))
                            .foregroundStyle(labelColor)
                    }
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                Text(trip.name)
                    .font(.headline)
                if let location = cockpit.locationText {
                    Text(location)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 12) {
                    Text(trip.windowLabel)
                    if !cockpit.todayStops.isEmpty {
                        Text("·")
                        Text("今天 \(cockpit.todayStops.count) 站")
                    }
                    if let next = cockpit.nextStop, let arrive = next.arrive {
                        Text("·")
                        Text("下一站 \(CockpitFormat.time(arrive, timeZone: cockpit.timeZone))")
                    }
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: DS.Radius.card))
        }
        .buttonStyle(.plain)
    }

    private var labelText: String {
        if cockpit.planActive { return "进行中" }
        if let countdown = cockpit.countdownText, countdown.hasPrefix("第 ") { return "行程进行中" }
        return "即将出发"
    }

    private var labelGlyph: String {
        cockpit.planActive ? "airplane.departure" : "calendar.badge.clock"
    }

    private var labelColor: Color {
        if cockpit.planActive { return .green }
        if let countdown = cockpit.countdownText,
           let days = Int(countdown.dropFirst(2).prefix(while: { $0.isNumber })), days <= 2 {
            return .orange
        }
        return .secondary
    }
}

/// 今日时间轴的一站：时刻、名称、城市与路程。
private struct CockpitStopRow: View {
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

/// 下一站 + 建议离场时刻（到达 − 路程）。
private struct CockpitNextStopCard: View {
    let stop: PlanStop
    let dayLabel: String?
    let leaveBy: Date?
    let timeZone: TimeZone?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "arrow.turn.down.right")
                .font(.caption)
                .foregroundStyle(DS.ColorToken.brand)
                .padding(.top, 3)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                if let leaveBy {
                    Text("建议 \(CockpitFormat.time(leaveBy, timeZone: timeZone)) 出发 · 路程 \(CockpitFormat.duration(stop.travelMinutes ?? 0))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if let window = stop.timeWindow?.displayText {
                    Text("时间窗 \(window)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding(12)
        .background(
            DS.ColorToken.brand.opacity(0.08),
            in: RoundedRectangle(cornerRadius: DS.Radius.card)
        )
        .accessibilityElement(children: .combine)
    }

    private var title: String {
        var parts: [String] = ["下一站"]
        if let dayLabel { parts.append(dayLabel) }
        if let arrive = stop.arrive {
            parts.append(CockpitFormat.time(arrive, timeZone: timeZone))
        }
        parts.append(stop.displayName)
        return parts.joined(separator: " · ")
    }
}

/// 风险/待办行：严重度徽标 + 标题 + 依据，点进行程处理。
private struct CockpitRiskRow: View {
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
private enum CockpitFormat {
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

private struct SuggestionCard: View {
    let suggestion: ProactiveSuggestion
    let isBusy: Bool
    let approve: () async -> Void
    let dismiss: () async -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Image(systemName: suggestion.resolvedSeverity.tintGlyph)
                    .foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text(suggestion.title)
                        .font(.headline)
                    Text("\(suggestion.typeLabel) · \(statusLabel)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(suggestion.confidenceLabel)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            if let rationale = suggestion.rationale, !rationale.isEmpty {
                Text(rationale)
                    .font(.subheadline)
            }

            if !suggestion.evidenceList.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    Text("证据")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(suggestion.evidenceList) { evidence in
                        Text("• " + evidence.displayText)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if let action = suggestion.proposedAction {
                Label(action.actionSummary, systemImage: "wand.and.stars")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if suggestion.isActionable {
                HStack(spacing: 10) {
                    Button {
                        Task { await approve() }
                    } label: {
                        Label("批准并准备", systemImage: "checkmark.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isBusy)

                    Button(role: .destructive) {
                        Task { await dismiss() }
                    } label: {
                        Label("驳回", systemImage: "hand.raised")
                    }
                    .buttonStyle(.bordered)
                    .disabled(isBusy)
                }
            }
        }
        .padding()
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var tint: Color {
        switch suggestion.resolvedSeverity {
        case .high: return .orange
        case .medium: return .blue
        case .low: return .secondary
        case .unknown: return .gray
        }
    }

    private var statusLabel: String {
        switch (suggestion.status ?? "").uppercased() {
        case "PENDING": return "待处理"
        case "APPROVED": return "已批准"
        case "EXECUTED": return "已执行"
        case "DISMISSED": return "已驳回"
        case "EXPIRED": return "已过期"
        default: return suggestion.status ?? "未知"
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
