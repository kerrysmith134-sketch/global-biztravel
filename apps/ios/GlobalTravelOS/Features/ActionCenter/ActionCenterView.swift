import SwiftUI

@MainActor
final class ActionCenterViewModel: ObservableObject {
    @Published private(set) var center: ActionCenter?
    @Published private(set) var isLoading = false
    @Published private(set) var busyIds: Set<String> = []
    @Published var errorMessage: String?

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            center = try await environment.actionCenterRepository.actionCenter(tripId: nil)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func approve(_ item: ActionItem) async {
        busyIds.insert(item.id)
        defer { busyIds.remove(item.id) }
        do {
            _ = try await environment.proactiveRepository.approve(suggestionId: item.entityId)
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func dismiss(_ item: ActionItem) async {
        busyIds.insert(item.id)
        defer { busyIds.remove(item.id) }
        do {
            _ = try await environment.proactiveRepository.dismiss(suggestionId: item.entityId)
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct ActionCenterView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: ActionCenterViewModel
    @Environment(\.dismiss) private var dismiss

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: ActionCenterViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            List {
                if let error = viewModel.errorMessage {
                    Section { ErrorBanner(message: error) }
                }

                if let center = viewModel.center {
                    if center.total == 0 {
                        ContentUnavailableView(
                            "没有待处理事项",
                            systemImage: "checkmark.seal",
                            description: Text("建议、报销单与后台任务都处理完了。")
                        )
                    }
                    ForEach(center.sectionList.filter { !$0.items.isEmpty }) { section in
                        Section {
                            ForEach(section.items) { item in
                                ActionItemRow(
                                    item: item,
                                    isBusy: viewModel.busyIds.contains(item.id),
                                    environment: environment,
                                    approve: { await viewModel.approve(item) },
                                    dismiss: { await viewModel.dismiss(item) }
                                )
                            }
                        } header: {
                            HStack {
                                Text(section.title)
                                Spacer()
                                Text("\(section.count)")
                                    .font(.caption.monospacedDigit())
                            }
                        }
                    }
                } else if viewModel.isLoading {
                    ProgressView("加载中…")
                }
            }
            .navigationTitle("行动中心")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await viewModel.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(viewModel.isLoading)
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
        }
    }
}

private struct ActionItemRow: View {
    let item: ActionItem
    let isBusy: Bool
    let environment: AppEnvironment
    let approve: () async -> Void
    let dismiss: () async -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top) {
                Image(systemName: item.glyph)
                    .foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title)
                        .font(.headline)
                    Text("\(item.kindLabel) · \(item.categoryLabel)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if let trip = item.tripLabel {
                    Text(trip)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Text(item.detail)
                .font(.subheadline)
            Text(item.actionHint)
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack(spacing: 10) {
                if item.canApproveSuggestion {
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
                if let tripId = item.tripId {
                    NavigationLink("查看行程") {
                        TripDetailView(tripId: tripId, environment: environment)
                    }
                    .font(.footnote)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var tint: Color {
        switch item.tint {
        case "blue": return .blue
        case "indigo": return .indigo
        case "teal": return .teal
        case "red": return .red
        case "orange": return .orange
        case "green": return .green
        default: return .secondary
        }
    }
}
