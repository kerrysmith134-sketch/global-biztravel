import Foundation

extension Endpoints {
    static func reassessVisitFollowUp(
        id: String,
        request: AppointmentRecoveryReassessmentRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/visit-follow-ups/\(id):reassess",
            body: try DateCoding.encode(request),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }
}
