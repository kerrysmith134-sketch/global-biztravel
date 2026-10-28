import Foundation

/// The sync seam.
///
/// Kept narrower than `VisitRepository` on purpose: `SyncPushUseCase` needs exactly one call, and
/// a one-method protocol is trivial to stub in tests without dragging `URLSession` along.
protocol SyncTransport {
    func push(_ request: SyncPushRequest) async throws -> SyncPushResult
}

/// `POST /v1/sync:push` over the shared `APIClient`.
final class RemoteSyncTransport: SyncTransport {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func push(_ request: SyncPushRequest) async throws -> SyncPushResult {
        let apiRequest = try Endpoints.syncPush(body: request)
        return try await client.send(apiRequest, as: SyncPushResult.self)
    }
}