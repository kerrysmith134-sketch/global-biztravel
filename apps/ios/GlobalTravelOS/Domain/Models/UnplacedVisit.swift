import Foundation

/// `unplaced:[{visit_id, place_name, reason}]` — visits the solver could not fit.
struct UnplacedVisit: Codable, Equatable, Identifiable {
    var visitId: String
    var placeName: String?
    var reason: String?

    init(visitId: String, placeName: String? = nil, reason: String? = nil) {
        self.visitId = visitId
        self.placeName = placeName
        self.reason = reason
    }

    var id: String { visitId }

    var displayName: String { placeName ?? visitId }

    var reasonLabel: String { reason ?? "求解器未给出原因" }
}
