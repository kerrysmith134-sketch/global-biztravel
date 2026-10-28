import Foundation

/// The trip memory layer: durable takeaways plus the raw procedure log.
///
/// Memory is exactly the kind of data worth caching aggressively — it is written by the server
/// after each trip and read by the operator between trips, often on a plane.
protocol MemoryRepository {
    func tripMemory(tripId: String) async throws -> TripMemory
}

final class RemoteMemoryRepository: MemoryRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func tripMemory(tripId: String) async throws -> TripMemory {
        try await client.send(Endpoints.tripMemory(tripId: tripId), as: TripMemory.self)
    }
}

final class OfflineMemoryRepository: MemoryRepository {
    private let remote: MemoryRepository
    private let cache: JSONFileCache<[String: TripMemory]>
    private let indicator: OfflineIndicator

    init(
        remote: MemoryRepository,
        cache: JSONFileCache<[String: TripMemory]>,
        indicator: OfflineIndicator
    ) {
        self.remote = remote
        self.cache = cache
        self.indicator = indicator
    }

    convenience init(remote: MemoryRepository, directory: URL, indicator: OfflineIndicator) {
        self.init(
            remote: remote,
            cache: JSONFileCache<[String: TripMemory]>(
                directory: directory,
                fileName: "trip-memory.json"
            ),
            indicator: indicator
        )
    }

    func tripMemory(tripId: String) async throws -> TripMemory {
        do {
            let memory = try await remote.tripMemory(tripId: tripId)
            var stored = cache.load() ?? [:]
            stored[tripId] = memory
            cache.save(stored)
            indicator.markServedFromServer()
            return memory
        } catch {
            guard let cached = cache.load()?[tripId] else { throw error }
            indicator.markServedFromCache("行程记忆")
            return cached
        }
    }
}