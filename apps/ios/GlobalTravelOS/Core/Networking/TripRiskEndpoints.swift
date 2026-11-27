import Foundation

extension Endpoints {
    static func listTripRiskCases(
        tripId: String,
        status: String? = nil,
        limit: Int = 100
    ) -> APIRequest {
        var query = [URLQueryItem(name: "limit", value: String(min(max(limit, 1), 200)))]
        if let status, !status.isEmpty {
            query.append(URLQueryItem(name: "status", value: status))
        }
        return APIRequest(
            method: .get,
            path: "/v1/trips/\(tripId)/risk-cases",
            queryItems: query
        )
    }

    static func listTripRiskEvents(
        tripId: String,
        caseId: String,
        limit: Int = 100
    ) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/trips/\(tripId)/risk-cases/\(caseId)/events",
            queryItems: [URLQueryItem(name: "limit", value: String(min(max(limit, 1), 500)))]
        )
    }

    static func reconcileTripRiskCases(tripId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/risk-cases:reconcile",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func transitionTripRiskCase(
        tripId: String,
        caseId: String,
        request: TripRiskTransitionRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/risk-cases/\(caseId):transition",
            body: try DateCoding.encode(request),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }
}
