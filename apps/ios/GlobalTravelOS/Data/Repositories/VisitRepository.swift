import Foundation

/// The direct (non-outbox) write path for a visit record.
///
/// `SubmitVisitRecordUseCase` calls this only after the record is already safely in the offline
/// queue, so `submitRecord` throwing is never data loss — it just means “still queued”.
protocol VisitRepository {
    func listVisits(tripId: String) async throws -> [TripVisit]
    /// 新增拜访（P0 审计 5.5：客户端此前没有创建入口）。
    func createVisit(tripId: String, request: VisitCreateRequest) async throws -> TripVisit
    func submitRecord(visitId: String, request: VisitRecordRequest) async throws -> VisitRecord
    func draftMinutes(visitId: String) async throws -> MinutesDraft
    func syncToCRM(recordId: String) async throws -> CrmSyncJob
    func crmSyncJob(id: String) async throws -> CrmSyncJob
    func briefing(visitId: String) async throws -> VisitBriefing
    func updateObjective(visitId: String, objective: String?) async throws -> TripVisit
    func followUps(visitId: String) async throws -> VisitFollowUpFeed
    func decideFollowUp(visitId: String, request: VisitFollowUpDecisionRequest) async throws -> VisitFollowUp
    func updateFollowUp(id: String, request: VisitFollowUpUpdateRequest) async throws -> VisitFollowUp
    func reassessFollowUp(
        id: String,
        request: AppointmentRecoveryReassessmentRequest
    ) async throws -> AppointmentRecoveryReassessmentResponse
}

final class RemoteVisitRepository: VisitRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func listVisits(tripId: String) async throws -> [TripVisit] {
        try await client.send(Endpoints.listVisits(tripId: tripId), as: [TripVisit].self)
    }

    func createVisit(tripId: String, request: VisitCreateRequest) async throws -> TripVisit {
        var placeId = request.placeId
        if placeId == nil {
            guard let latitude = request.latitude, let longitude = request.longitude,
                  (-90.0...90.0).contains(latitude),
                  (-180.0...180.0).contains(longitude) else {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR",
                    message: "新建拜访地点必须提供有效的纬度和经度",
                    traceId: nil,
                    details: [:]
                )
            }
            // Location coordinates are explicit input; never substitute a city center or 0,0.
            let placeBody = BackendPlaceCreate(
                name: request.placeName ?? request.title,
                city: request.city,
                countryCode: (request.countryCode ?? "").uppercased(),
                timezone: request.timezone,
                lat: latitude,
                lon: longitude
            )
            let place: PlaceCreated = try await client.send(
                try Endpoints.createPlace(placeBody), as: PlaceCreated.self
            )
            placeId = place.id
        }
        var body: [String: Any] = [
            "place_id": placeId ?? "",
            "title": request.title,
            "service_minutes": request.serviceMinutes,
            "priority": request.priority,
            "movement_mode": request.movementMode,
        ]
        if let customerId = request.customerId { body["customer_id"] = customerId }
        if let value = request.businessValue { body["business_value"] = value }
        return try await client.send(
            try Endpoints.createVisit(tripId: tripId, body: body), as: TripVisit.self
        )
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

    func briefing(visitId: String) async throws -> VisitBriefing {
        try await client.send(Endpoints.visitBriefing(visitId: visitId), as: VisitBriefing.self)
    }


    func followUps(visitId: String) async throws -> VisitFollowUpFeed {
        try await client.send(Endpoints.visitFollowUps(visitId: visitId), as: VisitFollowUpFeed.self)
    }

    func decideFollowUp(
        visitId: String,
        request: VisitFollowUpDecisionRequest
    ) async throws -> VisitFollowUp {
        try await client.send(
            try Endpoints.decideVisitFollowUp(visitId: visitId, request: request),
            as: VisitFollowUp.self
        )
    }

    func updateFollowUp(
        id: String,
        request: VisitFollowUpUpdateRequest
    ) async throws -> VisitFollowUp {
        try await client.send(
            try Endpoints.updateVisitFollowUp(id: id, request: request),
            as: VisitFollowUp.self
        )
    }

    func reassessFollowUp(
        id: String,
        request: AppointmentRecoveryReassessmentRequest
    ) async throws -> AppointmentRecoveryReassessmentResponse {
        try await client.send(
            try Endpoints.reassessVisitFollowUp(id: id, request: request),
            as: AppointmentRecoveryReassessmentResponse.self
        )
    }

    func updateObjective(visitId: String, objective: String?) async throws -> TripVisit {
        try await client.send(
            try Endpoints.updateVisitObjective(visitId: visitId, objective: objective),
            as: TripVisit.self
        )
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

    /// 新增拜访不做离线：写操作需要服务端确认（与 createTrip 同策略）。
    func createVisit(tripId: String, request: VisitCreateRequest) async throws -> TripVisit {
        try await remote.createVisit(tripId: tripId, request: request)
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

    func briefing(visitId: String) async throws -> VisitBriefing {
        try await remote.briefing(visitId: visitId)
    }


    func followUps(visitId: String) async throws -> VisitFollowUpFeed {
        try await remote.followUps(visitId: visitId)
    }

    func decideFollowUp(
        visitId: String,
        request: VisitFollowUpDecisionRequest
    ) async throws -> VisitFollowUp {
        try await remote.decideFollowUp(visitId: visitId, request: request)
    }

    func updateFollowUp(
        id: String,
        request: VisitFollowUpUpdateRequest
    ) async throws -> VisitFollowUp {
        try await remote.updateFollowUp(id: id, request: request)
    }

    func reassessFollowUp(
        id: String,
        request: AppointmentRecoveryReassessmentRequest
    ) async throws -> AppointmentRecoveryReassessmentResponse {
        try await remote.reassessFollowUp(id: id, request: request)
    }

    func updateObjective(visitId: String, objective: String?) async throws -> TripVisit {
        let visit = try await remote.updateObjective(visitId: visitId, objective: objective)
        // 列表缓存里是旧目标；清空让下一次读取重新取服务端版本。
        listCache.save([:])
        indicator.markServedFromServer()
        return visit
    }

    func cachedRecordCount() -> Int {
        (recordCache.load() ?? [:]).count
    }
}
