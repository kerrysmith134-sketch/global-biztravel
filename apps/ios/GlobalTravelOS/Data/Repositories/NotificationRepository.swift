import Foundation

protocol NotificationRepository {
    func feed(unreadOnly: Bool, priority: String?) async throws -> NotificationFeed
    func markRead(id: String) async throws -> AppNotification
}

/// 通知只依赖服务端（未读与投递状态都是服务端事实），不做离线读缓存。
final class RemoteNotificationRepository: NotificationRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func feed(unreadOnly: Bool, priority: String?) async throws -> NotificationFeed {
        try await client.send(
            Endpoints.notifications(unreadOnly: unreadOnly, priority: priority),
            as: NotificationFeed.self
        )
    }

    func markRead(id: String) async throws -> AppNotification {
        try await client.send(Endpoints.markNotificationRead(id: id), as: AppNotification.self)
    }
}
