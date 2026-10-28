import SwiftUI

@MainActor
final class TodayViewModel: ObservableObject {
    @Published private(set) var items: [ProactiveSuggestion] = []
    @Published private(set) var dailyCap: DailyCap?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published private(set) var busyIds: Set<String> = []

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
        } catch {
            errorMessage = environment.errorMessage(error)
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

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: TodayViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    offlineBanner
                    if let cap = viewModel.dailyCap {
                        Text(cap.displayText)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    if let error = viewModel.errorMessage {
                        ErrorBanner(message: error)
                    }
                    if viewModel.items.isEmpty && !viewModel.isLoading {
                        ContentUnavailableView(
                            "今天没有需要处理的建议",
                            systemImage: "checkmark.seal",
                            description: Text("系统会持续盯签证时效、缺坐标与未排项；有变化才会打扰你。")
                        )
                        .padding(.top, 40)
                    }
                    ForEach(viewModel.items) { suggestion in
                        SuggestionCard(
                            suggestion: suggestion,
                            isBusy: viewModel.busyIds.contains(suggestion.id),
                            approve: { await viewModel.approve(suggestion) },
                            dismiss: { await viewModel.dismiss(suggestion) }
                        )
                    }
                }
                .padding()
            }
            .navigationTitle("主动建议")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsActionCenter = true
                    } label: {
                        Image(systemName: "tray.full")
                    }
                    .accessibilityLabel("行动中心")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsSettings = true
                    } label: {
                        Image(systemName: "person.crop.circle")
                    }
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
            .sheet(isPresented: $showsSettings) {
                SettingsView()
            }
            .sheet(isPresented: $showsActionCenter) {
                ActionCenterView(environment: environment)
            }
        }
    }

    private var offlineBanner: some View {
        HStack(spacing: 8) {
            Image(systemName: environment.offlineSummary == "数据来自服务端" ? "wifi" : "wifi.slash")
            Text(environment.offlineSummary)
                .font(.caption)
            Spacer()
        }
        .foregroundStyle(.secondary)
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
