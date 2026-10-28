import SwiftUI

@MainActor
final class SyncCenterViewModel: ObservableObject {
    @Published private(set) var items: [OutboxItem] = []
    @Published private(set) var lastSyncAt: Date?
    @Published private(set) var isSyncing = false
    @Published private(set) var outcome: SyncRunOutcome?
    @Published var errorMessage: String?

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    var pendingCount: Int { items.count }

    var needsAttention: Bool {
        items.contains { $0.needsManualAttention }
    }

    func load() {
        items = environment.offlineStore.currentItems()
        lastSyncAt = environment.offlineStore.lastSyncAt()
        environment.refreshOfflineSummary()
    }

    func syncNow() async {
        isSyncing = true
        errorMessage = nil
        defer { isSyncing = false }
        let result = await environment.syncPushUseCase.pushPending(
            deviceId: environment.settings.deviceId
        )
        outcome = result
        if let message = result.errorMessage {
            errorMessage = message
        }
        load()
    }

    func discard(_ item: OutboxItem) {
        do {
            _ = try environment.syncPushUseCase.discard(itemId: item.itemId)
            load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct SyncCenterView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: SyncCenterViewModel

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: SyncCenterViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("待同步 \(viewModel.pendingCount) 条").font(.headline)
                            Text(lastSyncLabel)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button {
                            Task { await viewModel.syncNow() }
                        } label: {
                            Label(
                                viewModel.isSyncing ? "同步中…" : "立即同步",
                                systemImage: "arrow.triangle.2.circlepath"
                            )
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(viewModel.isSyncing || viewModel.pendingCount == 0)
                    }
                } footer: {
                    Text("同步按逐项结果处理：applied 清出队列，conflict / rejected 留给人工确认。")
                }

                if let error = viewModel.errorMessage {
                    Section { ErrorBanner(message: error) }
                }

                if let outcome = viewModel.outcome {
                    Section("上次同步") {
                        Text(outcome.summaryLine).font(.subheadline)
                        if outcome.hasItemsNeedingAttention {
                            Text("有 \(outcome.conflict + outcome.rejected) 条需要人工处理。")
                                .font(.footnote)
                                .foregroundStyle(.orange)
                        }
                    }
                }

                Section("离线队列") {
                    if viewModel.items.isEmpty {
                        ContentUnavailableView(
                            "队列为空",
                            systemImage: "tray",
                            description: Text("现场记录会先在这里排队，再按网络情况同步。")
                        )
                    }
                    ForEach(viewModel.items) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(item.entity.label).font(.headline)
                                Spacer()
                                Text(item.displayStatus)
                                    .font(.caption)
                                    .foregroundStyle(item.needsManualAttention ? .orange : .secondary)
                            }
                            Text(item.item.payloadSummary)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let conflict = item.conflictDetail {
                                Text(conflict).font(.caption2).foregroundStyle(.orange)
                            }
                            HStack {
                                Text(item.retryLabel).font(.caption2).foregroundStyle(.tertiary)
                                Spacer()
                                Button("丢弃", role: .destructive) {
                                    viewModel.discard(item)
                                }
                                .font(.caption)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
            .navigationTitle("离线同步")
            .refreshable { viewModel.load() }
            .task { viewModel.load() }
        }
    }

    private var lastSyncLabel: String {
        guard let last = viewModel.lastSyncAt else { return "尚未同步过" }
        return "上次同步：\(ISO8601.shortDisplay(from: last))"
    }
}
