import SwiftUI

@MainActor
final class NotificationsViewModel: ObservableObject {
    @Published private(set) var feed: NotificationFeed?
    @Published private(set) var isLoading = false
    @Published private(set) var busyIds: Set<String> = []
    @Published var errorMessage: String?
    @Published var filter: String?

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            feed = try await environment.notificationRepository.feed(
                unreadOnly: false,
                priority: filter
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func markRead(_ item: AppNotification) async {
        busyIds.insert(item.id)
        defer { busyIds.remove(item.id) }
        do {
            let updated = try await environment.notificationRepository.markRead(id: item.id)
            guard var items = feed?.items,
                  let index = items.firstIndex(where: { $0.id == item.id }) else { return }
            items[index] = updated
            var next = feed
            next?.items = items
            if let unread = next?.unread {
                next?.unread = max(unread - 1, 0)
            }
            feed = next
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

/// 通知中心（PRD S8-08）：按 P0–P3 分级展示未读，逐条标记已读。
struct NotificationsView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: NotificationsViewModel
    @Environment(\.dismiss) private var dismiss

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: NotificationsViewModel(environment: environment))
    }

    private let priorityOrder = ["P0", "P1", "P2", "P3"]

    var body: some View {
        NavigationStack {
            List {
                if let error = viewModel.errorMessage {
                    Section { ErrorBanner(message: error) }
                }

                if let feed = viewModel.feed {
                    Section {
                        Text(feed.unreadBreakdown)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Picker("只看等级", selection: Binding(
                            get: { viewModel.filter ?? "" },
                            set: { viewModel.filter = $0.isEmpty ? nil : $0 }
                        )) {
                            Text("全部").tag("")
                            ForEach(priorityOrder, id: \.self) { key in
                                Text(key).tag(key)
                            }
                        }
                        .pickerStyle(.segmented)
                    }

                    if feed.items.isEmpty {
                        ContentUnavailableView(
                            "没有通知",
                            systemImage: "bell.slash",
                            description: Text("建议被推送后会按等级出现在这里。")
                        )
                    }

                    ForEach(priorityOrder, id: \.self) { key in
                        let group = feed.items.filter {
                            ($0.priority ?? "P2").uppercased() == key
                        }
                        if !group.isEmpty {
                            Section {
                                ForEach(group) { item in
                                    NotificationRow(
                                        item: item,
                                        isBusy: viewModel.busyIds.contains(item.id),
                                        markRead: { await viewModel.markRead(item) }
                                    )
                                }
                            } header: {
                                Text("\(key) \(label(for: key)) · \(group.count)")
                            }
                        }
                    }
                } else if viewModel.isLoading {
                    ProgressView("加载通知…")
                }
            }
            .navigationTitle("通知")
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
            .onChange(of: viewModel.filter) {
                Task { await viewModel.load() }
            }
        }
    }

    private func label(for key: String) -> String {
        switch key {
        case "P0": return "紧急"
        case "P1": return "高优先级"
        case "P2": return "一般"
        case "P3": return "信息性"
        default: return key
        }
    }
}

private struct NotificationRow: View {
    let item: AppNotification
    let isBusy: Bool
    let markRead: () async -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(item.priorityLabel)
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(tint.opacity(0.15), in: Capsule())
                    .foregroundStyle(tint)
                if let created = item.createdAt {
                    Text(ISO8601.shortDisplay(from: created))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if item.isUnread {
                    Circle().fill(tint).frame(width: 8, height: 8)
                }
            }
            Text(item.title)
                .font(item.isUnread ? .subheadline.weight(.semibold) : .subheadline)
            if !item.body.isEmpty {
                Text(item.body)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            if item.isUnread {
                Button {
                    Task { await markRead() }
                } label: {
                    Label("标记已读", systemImage: "checkmark")
                        .font(.caption)
                }
                .disabled(isBusy)
            }
        }
        .padding(.vertical, 2)
    }

    private var tint: Color {
        switch item.tintName {
        case "red": return .red
        case "orange": return .orange
        case "blue": return .blue
        default: return .secondary
        }
    }
}
