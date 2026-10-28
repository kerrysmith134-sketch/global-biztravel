import Foundation

enum VisitOutcome: String, Codable, CaseIterable, Identifiable {
    case met = "MET"
    case notMet = "NOT_MET"
    case rescheduled = "RESCHEDULED"
    case cancelled = "CANCELLED"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .met: return "已会面"
        case .notMet: return "未会面"
        case .rescheduled: return "改期"
        case .cancelled: return "取消"
        }
    }

    var glyph: String {
        switch self {
        case .met: return "checkmark.seal.fill"
        case .notMet: return "person.crop.circle.badge.xmark"
        case .rescheduled: return "calendar.badge.clock"
        case .cancelled: return "xmark.circle.fill"
        }
    }

    /// Whether a follow-up task is expected to be created for this outcome.
    var requiresFollowUp: Bool {
        self == .notMet || self == .rescheduled
    }
}

struct FollowUpTask: Codable, Equatable {
    var title: String
    /// `due_date` — kept as a raw string, the backend contract does not pin the format.
    var dueDate: String?

    init(title: String, dueDate: String? = nil) {
        self.title = title
        self.dueDate = dueDate
    }

    var displayText: String {
        guard let dueDate, !dueDate.isEmpty else { return title }
        return "\(title) · 截止 \(dueDate)"
    }
}

/// `POST /v1/visits/{visit_id}/records` request body.
struct VisitRecordRequest: Codable, Equatable {
    var clientRecordId: String
    var outcome: VisitOutcome
    var notes: String
    var clientUpdatedAt: Date
    var photos: [String]
    var followUpTasks: [FollowUpTask]

    init(
        clientRecordId: String,
        outcome: VisitOutcome,
        notes: String,
        clientUpdatedAt: Date = Date(),
        photos: [String] = [],
        followUpTasks: [FollowUpTask] = []
    ) {
        self.clientRecordId = clientRecordId
        self.outcome = outcome
        self.notes = notes
        self.clientUpdatedAt = clientUpdatedAt
        self.photos = photos
        self.followUpTasks = followUpTasks
    }

    static func newClientRecordId() -> String {
        UUID().uuidString.lowercased()
    }

    var isEmptyNotes: Bool {
        notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

/// Conflict snapshot shared by visit records and sync push results.
///
/// `server` / `client` are the two versions of the record; the client shows them side by side so a
/// human, not the client, resolves a `reject-and-conflict-record` case (design doc §10.5).
struct ConflictRecord: Codable, Equatable {
    var reason: String?
    var server: [String: JSONValue]?
    var client: [String: JSONValue]?

    init(
        reason: String? = nil,
        server: [String: JSONValue]? = nil,
        client: [String: JSONValue]? = nil
    ) {
        self.reason = reason
        self.server = server
        self.client = client
    }

    var reasonLabel: String { reason ?? "服务端判定冲突" }

    var hasSnapshots: Bool {
        (server?.isEmpty == false) || (client?.isEmpty == false)
    }
}

/// `POST /v1/visits/{visit_id}/records` response.
struct VisitRecord: Codable, Equatable, Identifiable {
    var id: String
    var visitId: String?
    var outcome: VisitOutcome?
    var serverVersion: Int?
    var conflict: ConflictRecord?

    init(
        id: String,
        visitId: String? = nil,
        outcome: VisitOutcome? = nil,
        serverVersion: Int? = nil,
        conflict: ConflictRecord? = nil
    ) {
        self.id = id
        self.visitId = visitId
        self.outcome = outcome
        self.serverVersion = serverVersion
        self.conflict = conflict
    }

    /// A record produced locally because the device is offline — it has no server version yet and
    /// is sitting in the outbox waiting for the next `sync:push`.
    var isLocalOnly: Bool { serverVersion == nil }

    var outcomeLabel: String { outcome?.label ?? "未知" }
}

/// A visit record the operator is still editing, before it becomes an outbox item.
struct VisitDraft: Codable, Equatable, Identifiable {
    var clientRecordId: String
    var visitId: String
    var outcome: VisitOutcome
    var notes: String
    var photos: [String]
    var followUpTasks: [FollowUpTask]
    var updatedAt: Date

    init(
        clientRecordId: String = VisitRecordRequest.newClientRecordId(),
        visitId: String,
        outcome: VisitOutcome = .met,
        notes: String = "",
        photos: [String] = [],
        followUpTasks: [FollowUpTask] = [],
        updatedAt: Date = Date()
    ) {
        self.clientRecordId = clientRecordId
        self.visitId = visitId
        self.outcome = outcome
        self.notes = notes
        self.photos = photos
        self.followUpTasks = followUpTasks
        self.updatedAt = updatedAt
    }

    var id: String { clientRecordId }

    func makeRequest(clientUpdatedAt: Date = Date()) -> VisitRecordRequest {
        VisitRecordRequest(
            clientRecordId: clientRecordId,
            outcome: outcome,
            notes: notes,
            clientUpdatedAt: clientUpdatedAt,
            photos: photos,
            followUpTasks: followUpTasks
        )
    }

    var isReadyToSubmit: Bool {
        outcome.requiresFollowUp ? !followUpTasks.isEmpty : true
    }
}