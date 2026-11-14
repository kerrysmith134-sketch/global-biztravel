import SwiftUI

@MainActor
final class TodayViewModel: ObservableObject {
    @Published private(set) var items: [ProactiveSuggestion] = []
    @Published private(set) var dailyCap: DailyCap?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published private(set) var busyIds: Set<String> = []
    @Published private(set) var ongoingTrip: Trip?
    @Published private(set) var upcomingTrips: [Trip] = []

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
            environment.refreshOfflineSummary()
            await loadTrips()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    /// 进行中/即将出发的行程，供"今日"仪表盘顶部卡片使用。
    private func loadTrips() async {
        do {
            let page = try await environment.tripRepository.listTrips(page: PageRequest(limit: 50))
            let trips = page.items
            ongoingTrip = trips.first { $0.status == "ACTIVE" }
            upcomingTrips = trips
                .filter { $0.status == "DRAFT" || $0.status == "READY" || $0.status == "PLANNING" }
                .sorted { ($0.window?.start ?? .distantFuture) < ($1.window?.start ?? .distantFuture) }
        } catch {
            // 行程卡加载失败不打断建议流：首页主体仍是决策列表。
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
    @State private var showsSettings = false
    @State private var showsActionCenter = false
    @State private var showsNotifications = false
    @State private var showsAssistant = false

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: TodayViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    greetingHeader
                    offlineBanner
                    if let trip = viewModel.ongoingTrip ?? viewModel.upcomingTrips.first {
                        OngoingTripCard(trip: trip)
                        if viewModel.ongoingTrip == nil, viewModel.upcomingTrips.count > 1 {
                            Text("另有 \(viewModel.upcomingTrips.count - 1) 个待出发行程，见「出行」。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let error = viewModel.errorMessage {
                        ErrorBanner(message: error)
                    }
                    decisionSection
                }
                .padding()
            }
            .navigationTitle("今日")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsAssistant = true
                    } label: {
                        Image(systemName: "bubble.left.and.text.bubble.right")
                    }
                    .accessibilityLabel("AI 助手")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsNotifications = true
                    } label: {
                        Image(systemName: "bell")
                    }
                    .accessibilityLabel("通知")
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
        }
    }

    private var greetingHeader: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(greeting)
                .font(.title3.weight(.semibold))
            if let cap = viewModel.dailyCap {
                Text(cap.displayText)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var decisionSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            if viewModel.items.isEmpty && !viewModel.isLoading {
                ContentUnavailableView(
                    "今天没有需要处理的建议",
                    systemImage: "checkmark.seal",
                    description: Text(
                        viewModel.ongoingTrip != nil || !viewModel.upcomingTrips.isEmpty
                            ? "系统会持续盯签证时效、缺坐标与未排项；有变化才会打扰你。当前行程与拜访见「出行」。"
                            : "系统会持续盯签证时效、缺坐标与未排项；有变化才会打扰你。"
                    )
                )
                .padding(.top, 40)
            } else if !viewModel.items.isEmpty {
                Text("需要你决策")
                    .font(.headline)
                ForEach(viewModel.items) { suggestion in
                    SuggestionCard(
                        suggestion: suggestion,
                        isBusy: viewModel.busyIds.contains(suggestion.id),
                        approve: { await viewModel.approve(suggestion) },
                        dismiss: { await viewModel.dismiss(suggestion) }
                    )
                }
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

    private var offlineBanner: some View {
        HStack(spacing: 8) {
            Image(systemName: bannerIcon)
            Text(environment.offlineSummary)
                .font(.caption)
            Spacer()
        }
        .foregroundStyle(.secondary)
    }

    private var bannerIcon: String {
        if environment.isLocalMode { return "internaldrive" }
        return environment.offlineSummary == "数据来自服务端" ? "wifi" : "wifi.slash"
    }
}

private struct OngoingTripCard: View {
    @EnvironmentObject private var environment: AppEnvironment
    let trip: Trip

    var body: some View {
        NavigationLink {
            TripDetailView(tripId: trip.id, environment: environment)
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label(
                        trip.status == "ACTIVE" ? "进行中" : "即将出发",
                        systemImage: trip.status == "ACTIVE" ? "airplane.departure" : "clock.badge.exclamationmark"
                    )
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(trip.status == "ACTIVE" ? .green : .orange)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                Text(trip.name)
                    .font(.headline)
                if let window = trip.window {
                    Text(windowDisplay(window))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
    }

    private func windowDisplay(_ window: TripWindow) -> String {
        let formatter = DateFormatter()
        // 差旅窗口按出发地时区展示（与规划引擎 timezoneHome 口径一致，避免设备时区把 08:00 显示成 04:00）。
        formatter.timeZone = trip.originPlace?.timeZone() ?? .current
        formatter.dateFormat = "M月d日 HH:mm"
        let start = formatter.string(from: window.start)
        formatter.dateFormat = "M月d日"
        let end = formatter.string(from: window.end)
        return "\(start) – \(end)"
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
