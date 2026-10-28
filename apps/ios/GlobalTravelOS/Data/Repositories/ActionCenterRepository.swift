import Foundation

protocol ActionCenterRepository {
    func actionCenter(tripId: String?) async throws -> ActionCenter
}

final class RemoteActionCenterRepository: ActionCenterRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func actionCenter(tripId: String?) async throws -> ActionCenter {
        try await client.send(Endpoints.actionCenter(tripId: tripId), as: ActionCenter.self)
    }
}
