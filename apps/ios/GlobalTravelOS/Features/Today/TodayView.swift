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
    @State private var showsMarketResearch = false

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

                        MissionPulseStrip(
                            riskCount: cockpit.risks.count,
                            decisionCount: pendingSuggestions.count,
                            upcomingCount: cockpit.upcoming.count
                        )

                        if cockpit.nextStop != nil, let plan = viewModel.activePlan {
                            MissionNextStopCard(cockpit: cockpit, trip: trip, plan: plan)
                        }

                        MissionTodayAgendaCard(
                            cockpit: cockpit,
                            emptyText: emptyTodayText(cockpit)
                        )

                        if !cockpit.risks.isEmpty {
                            MissionRiskSummaryCard(cockpit: cockpit)
                        }

                        decisionSection

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
                    Menu {
                        Button {
                            showsNotifications = true
                        } label: {
                            Label("通知中心", systemImage: DSIconCatalog.notifications)
                        }
                        Button {
                            showsTrips = true
                        } label: {
                            Label("行程管理", systemImage: DSIconCatalog.trip)
                        }
                        Button {
                            showsActionCenter = true
                        } label: {
                            Label("行动中心", systemImage: DSIconCatalog.actions)
                        }
                        Button {
                            showsMarketResearch = true
                        } label: {
                            Label("研究工作区", systemImage: "point.3.connected.trianglepath.dotted")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .accessibilityLabel("更多操作")
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
            .sheet(isPresented: $showsMarketResearch) {
                MarketResearchWorkspaceView(environment: environment)
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
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "AI 洞察与待决策",
                subtitle: "\(pendingSuggestions.count) 项待你确认",
                actionTitle: "全部查看"
            ) {
                showsActionCenter = true
            }

            if pendingSuggestions.isEmpty && !viewModel.isLoading {
                DSCard(padding: DS.Spacing.md) {
                    HStack(spacing: DS.Spacing.md) {
                        Image(systemName: "checkmark.seal")
                            .font(.title3)
                            .foregroundStyle(DS.ColorToken.success)
                            .frame(width: 36, height: 36)
                            .background(DS.ColorToken.success.opacity(0.10), in: Circle())
                        VStack(alignment: .leading, spacing: 3) {
                            Text("当前没有待你确认的建议")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Text("系统会继续监控行程准备度与执行风险。")
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                    }
                }
            } else {
                ForEach(Array(pendingSuggestions.prefix(2))) { suggestion in
                    MissionDecisionCard(
                        suggestion: suggestion,
                        isBusy: viewModel.busyIds.contains(suggestion.id),
                        approve: { await viewModel.approve(suggestion) },
                        dismiss: { await viewModel.dismiss(suggestion) }
                    )
                }
                if pendingSuggestions.count > 2 {
                    Button {
                        showsActionCenter = true
                    } label: {
                        HStack {
                            Text("查看其余 \(pendingSuggestions.count - 2) 项")
                            Spacer()
                            Image(systemName: "arrow.right")
                        }
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                        .frame(minHeight: 44)
                    }
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

    private func upcomingSection(_ cockpit: TravelCockpit) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            DSSectionHeader(
                title: "接下来的 Mission",
                subtitle: "未来 7 天内的出行安排"
            )
            DSCard(padding: DS.Spacing.md) {
                VStack(spacing: 0) {
                    ForEach(Array(cockpit.upcoming.enumerated()), id: \.element.id) { index, trip in
                        NavigationLink {
                            TripDetailView(tripId: trip.id, environment: environment)
                        } label: {
                            HStack(alignment: .center, spacing: DS.Spacing.md) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(trip.name)
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(DS.ColorToken.textPrimary)
                                        .lineLimit(2)
                                    HStack(spacing: 6) {
                                        Text(trip.countries?.joined(separator: " · ") ?? "目的地待补充")
                                        if let start = trip.window?.start {
                                            Text("·")
                                            Text(CockpitFormat.day(start, timeZone: cockpit.timeZone))
                                        }
                                    }
                                    .font(.caption)
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                                Spacer(minLength: 4)
                                DSBadge(text: trip.statusLabel, color: DS.ColorToken.status(trip.status ?? ""))
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textSecondary)
                            }
                            .padding(.vertical, DS.Spacing.md)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        if index < cockpit.upcoming.count - 1 {
                            Divider()
                        }
                    }
                }
            }
        }
    }

    private func emptyTodayText(_ cockpit: TravelCockpit) -> String {
        if let start = cockpit.trip?.window?.start,
           let countdown = cockpit.countdownText,
           countdown.hasPrefix("D-") || countdown == "明天出发" {
            return "今天没有排定拜访；\(CockpitFormat.day(start, timeZone: cockpit.timeZone)) 出发。"
        }
        return "今天没有排定拜访。下一站和后续计划会在这里显示。"
    }
}
