import Foundation

/// The direct (non-outbox) write path for a visit record.
///
/// `SubmitVisitRecordUseCase` calls this only after the record is already safely in the offline
/// queue, so `submitRecord` throwing is never data loss — it just means “still queued”.
protocol VisitRepository {
    func listVisits(tripId: String) async throws -> [TripVisit]
    func submitRecord(visitId: String, request: VisitRecordRequest) async throws -> VisitRecord
    func draftMinutes(visitId: String) async throws -> MinutesDraft
    func syncToCRM(recordId: String) async throws -> CrmSyncJob
    func crmSyncJob(id: String) async throws -> CrmSyncJob
}

final class RemoteVisitRepository: VisitRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func listVisits(tripId: String) async throws -> [TripVisit] {
        try await client.send(Endpoints.listVisits(tripId: tripId), as: [TripVisit].self)
    }

    func submitRecord(visitId: String, request: VisitRecordRequest) async throws -> VisitRecord {
        try await client.send(
            try Endpoints.submitVisitRecord(visitId: visitId, body: request),
            as: VisitRecord.self
        )
    }

    func draftMinutes(visitId: String) async throws -> MinutesDraft {
        try await client.send(Endpoints.draftMinutes(visitId: visitId), as: MinutesDraft.self)
    }

    func syncToCRM(recordId: String) async throws -> CrmSyncJob {
        try await client.send(Endpoints.crmSync(recordId: recordId), as: CrmSyncJob.self)
    }

    func crmSyncJob(id: String) async throws -> CrmSyncJob {
        try await client.send(Endpoints.crmSyncJob(id: id), as: CrmSyncJob.self)
    }
}

/// Keeps the server-acknowledged record for every `client_record_id` it has seen.
///
/// `client_record_id` is the idempotency key for a visit record, so a *transport* failure on a
/// resubmit of an id that the server already accepted can be answered from this cache instead of
/// leaving a duplicate in the outbox for the next sync round. Server verdicts (including conflicts)
/// are still returned as-is and are never masked by the cache.
final class OfflineVisitRepository: VisitRepository {
    private let remote: VisitRepository
    private let recordCache: JSONFileCache<[String: VisitRecord]>
    private let listCache: JSONFileCache<[String: [TripVisit]]>
    private let indicator: OfflineIndicator

    init(
        remote: VisitRepository,
        recordCache: JSONFileCache<[String: VisitRecord]>,
        listCache: JSONFileCache<[String: [TripVisit]]>,
        indicator: OfflineIndicator
    ) {
        self.remote = remote
        self.recordCache = recordCache
        self.listCache = listCache
        self.indicator = indicator
    }

    convenience init(remote: VisitRepository, directory: URL, indicator: OfflineIndicator) {
        self.init(
            remote: remote,
            recordCache: JSONFileCache<[String: VisitRecord]>(
                directory: directory,
                fileName: "visit-records.json"
            ),
            listCache: JSONFileCache<[String: [TripVisit]]>(
                directory: directory,
                fileName: "visits-by-trip.json"
            ),
            indicator: indicator
        )
    }

    func listVisits(tripId: String) async throws -> [TripVisit] {
        do {
            let visits = try await remote.listVisits(tripId: tripId)
            var stored = listCache.load() ?? [:]
            stored[tripId] = visits
            listCache.save(stored)
            indicator.markServedFromServer()
            return visits
        } catch {
            guard let cached = listCache.load()?[tripId], !cached.isEmpty else { throw error }
            indicator.markServedFromCache("拜访列表")
            return cached
        }
    }

    func submitRecord(visitId: String, request: VisitRecordRequest) async throws -> VisitRecord {
        do {
            let record = try await remote.submitRecord(visitId: visitId, request: request)
            if record.conflict == nil {
                var stored = recordCache.load() ?? [:]
                stored[request.clientRecordId] = record
                recordCache.save(stored)
            }
            indicator.markServedFromServer()
            return record
        } catch let error as APIError where error.isRetryable {
            guard let cached = recordCache.load()?[request.clientRecordId] else { throw error }
            indicator.markServedFromCache("拜访记录（同 id 已提交）")
            return cached
        }
    }

    /// Last server-acknowledged record for a client-generated id, if any.
    func cachedRecord(clientRecordId: String) -> VisitRecord? {
        recordCache.load()?[clientRecordId]
    }

    func draftMinutes(visitId: String) async throws -> MinutesDraft {
        // Drafts are always generated server-side; there is no offline template on the client.
        try await remote.draftMinutes(visitId: visitId)
    }

    /// CRM sync is a server-initiated background job; the client only asks and polls.
    func syncToCRM(recordId: String) async throws -> CrmSyncJob {
        try await remote.syncToCRM(recordId: recordId)
    }

    func crmSyncJob(id: String) async throws -> CrmSyncJob {
        try await remote.crmSyncJob(id: id)
    }

    func cachedRecordCount() -> Int {
        (recordCache.load() ?? [:]).count
    }
}
