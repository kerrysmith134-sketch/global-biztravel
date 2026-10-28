import Foundation

/// Declarative catalog of the `/v1` surface. Paths and field names mirror the backend contract
/// one-for-one; nothing here invents a route.
enum Endpoints {

    // MARK: - Health

    static func health() -> APIRequest {
        APIRequest(method: .get, path: "/v1/health")
    }

    // MARK: - Trips

    static func createTrip(_ body: CreateTripRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    /// Backend-shaped create body (`window_start` / `origin_place_id`). The domain request keeps
    /// the nested origin/window; the repository creates the origin place first and calls this.
    static func createTrip(_ body: BackendTripCreate) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func createPlace(_ body: BackendPlaceCreate) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/places",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listTrips(page: PageRequest) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips", queryItems: page.queryItems)
    }

    static func tripDetail(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(id)")
    }

    // MARK: - Imports

    static func parseImport(
        tripId: String,
        fileData: Data,
        filename: String,
        mimeType: String = "text/csv"
    ) -> APIRequest {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.append(utf8("--\(boundary)\r\n"))
        body.append(utf8("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n"))
        body.append(utf8("Content-Type: \(mimeType)\r\n\r\n"))
        body.append(fileData)
        body.append(utf8("\r\n--\(boundary)--\r\n"))
        return APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/imports:parse",
            body: body,
            contentType: "multipart/form-data; boundary=\(boundary)",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func confirmImport(tripId: String, body: ConfirmImportRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/imports:confirm",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Planning

    static func generatePlan(tripId: String, body: GeneratePlanRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/plans:generate",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func job(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/jobs/\(id)")
    }

    static func listPlans(tripId: String, page: PageRequest) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/plans", queryItems: page.queryItems)
    }

    static func planDetail(tripId: String, planId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/plans/\(planId)")
    }

    static func planDiff(tripId: String, planId: String, against: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/trips/\(tripId)/plans/\(planId):diff",
            queryItems: [URLQueryItem(name: "against", value: against)]
        )
    }

    static func activatePlan(tripId: String, planId: String, tripVersion: Int) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/plans/\(planId):activate",
            idempotencyKey: APIRequest.newIdempotencyKey(),
            ifMatch: String(tripVersion)
        )
    }

    // MARK: - Visits

    static func submitVisitRecord(visitId: String, body: VisitRecordRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/visits/\(visitId)/records",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listVisits(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/visits")
    }

    static func draftMinutes(visitId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/visits/\(visitId)/minutes:draft",
            body: try? DateCoding.encode(MinutesDraftRequest()),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Offline sync

    static func syncPush(body: SyncPushRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/sync:push",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Proactive suggestions

    static func proactiveSuggestions(
        tripId: String?,
        status: String?,
        limit: Int,
        cursor: String?
    ) -> APIRequest {
        var items: [URLQueryItem] = []
        if let tripId, !tripId.isEmpty {
            items.append(URLQueryItem(name: "trip_id", value: tripId))
        }
        if let status, !status.isEmpty {
            items.append(URLQueryItem(name: "status", value: status))
        }
        items.append(URLQueryItem(name: "limit", value: String(limit)))
        if let cursor, !cursor.isEmpty {
            items.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return APIRequest(method: .get, path: "/v1/proactive/suggestions", queryItems: items)
    }

    static func approveSuggestion(id: String) -> APIRequest {
        // No request body: the approval actor is taken from `X-User-Id`.
        APIRequest(
            method: .post,
            path: "/v1/proactive/suggestions/\(id):approve",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func dismissSuggestion(id: String) -> APIRequest {
        // No request body: the contract does not define one for dismiss.
        APIRequest(
            method: .post,
            path: "/v1/proactive/suggestions/\(id):dismiss",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Memory

    static func tripMemory(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/memory")
    }

    // MARK: - Helpers

    private static func utf8(_ string: String) -> Data {
        Data(string.utf8)
    }
}