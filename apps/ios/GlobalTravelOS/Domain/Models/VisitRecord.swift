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


// MARK: - Human-confirmed visit follow-ups

struct VisitFollowUpEvent: Codable, Equatable, Identifiable {
    var id: String
    var eventType: String
    var fromStatus: String?
    var toStatus: String
    var actorUserId: String?
    var note: String?
    var createdAt: Date

    var label: String {
        switch eventType {
        case "ACCEPTED": return "已确认"
        case "DISMISSED": return "已驳回"
        case "CREATED_MANUAL": return "手工创建"
        case "STARTED": return "开始处理"
        case "COMPLETED": return "已完成"
        case "REOPENED": return "重新打开"
        case "UPDATED": return "已修改"
        default: return eventType
        }
    }
}

struct VisitFollowUpActionContext: Codable, Equatable {
    var integrityStatus: String?
    var actionCode: String?
    var priority: String?
    var decisionStatus: String?
    var appointmentStatus: String?
    var rationale: String?
    var requiredInputs: [String]?
    var recommendedSteps: [String]?
}

struct VisitFollowUp: Codable, Equatable, Identifiable {
    var id: String
    var visitId: String
    var tripId: String
    var sourceType: String
    var sourceRecordId: String?
    var sourceAgentRunId: String?
    var sourceIndex: Int?
    var sourceTitle: String?
    var sourceDueDate: String?
    var sourceActionContext: VisitFollowUpActionContext?
    var title: String
    var dueDate: String?
    var status: String
    var createdByUserId: String?
    var confirmedByUserId: String?
    var confirmedAt: Date?
    var completedByUserId: String?
    var completedAt: Date?
    var createdAt: Date
    var updatedAt: Date
    var events: [VisitFollowUpEvent]

    var statusLabel: String {
        switch status {
        case "OPEN": return "待开始"
        case "IN_PROGRESS": return "进行中"
        case "COMPLETED": return "已完成"
        case "DISMISSED": return "已驳回"
        default: return status
        }
    }

    var sourceLabel: String {
        switch sourceType {
        case "RECORD": return "现场记录"
        case "MINUTES_DRAFT": return "AI 会议纪要"
        case "APPOINTMENT_RECOVERY": return "航班预约恢复"
        case "MANUAL": return "手工创建"
        default: return sourceType
        }
    }
}

struct VisitFollowUpSuggestion: Codable, Equatable, Identifiable {
    var sourceType: String
    var sourceRecordId: String?
    var sourceAgentRunId: String?
    var sourceIndex: Int
    var title: String
    var dueDate: String?
    var sourceCreatedAt: Date
    var actionCode: String?
    var priority: String?
    var decisionStatus: String?
    var appointmentStatus: String?
    var rationale: String?
    var requiredInputs: [String]?
    var recommendedSteps: [String]?

    var id: String {
        "\(sourceType):\(sourceRecordId ?? sourceAgentRunId ?? "unknown"):\(sourceIndex)"
    }

    var sourceLabel: String {
        switch sourceType {
        case "RECORD": return "来自现场记录"
        case "MINUTES_DRAFT": return "来自 AI 会议纪要"
        case "APPOINTMENT_RECOVERY": return "来自航班预约恢复 Agent"
        default: return sourceType
        }
    }
}

struct VisitFollowUpFeed: Codable, Equatable {
    var visitId: String
    var tripId: String
    var items: [VisitFollowUp]
    var suggestions: [VisitFollowUpSuggestion]
}

struct VisitFollowUpDecisionRequest: Encodable, Equatable {
    var sourceType: String
    var decision: String
    var sourceRecordId: String?
    var sourceAgentRunId: String?
    var sourceIndex: Int?
    var title: String?
    var dueDate: String?
    var note: String?

    static func accept(_ item: VisitFollowUpSuggestion) -> VisitFollowUpDecisionRequest {
        VisitFollowUpDecisionRequest(
            sourceType: item.sourceType,
            decision: "ACCEPT",
            sourceRecordId: item.sourceRecordId,
            sourceAgentRunId: item.sourceAgentRunId,
            sourceIndex: item.sourceIndex,
            title: nil,
            dueDate: nil,
            note: nil
        )
    }

    static func dismiss(_ item: VisitFollowUpSuggestion) -> VisitFollowUpDecisionRequest {
        VisitFollowUpDecisionRequest(
            sourceType: item.sourceType,
            decision: "DISMISS",
            sourceRecordId: item.sourceRecordId,
            sourceAgentRunId: item.sourceAgentRunId,
            sourceIndex: item.sourceIndex,
            title: nil,
            dueDate: nil,
            note: nil
        )
    }
}

struct VisitFollowUpUpdateRequest: Encodable, Equatable {
    var status: String?
    var title: String?
    var dueDate: String?
    var note: String?

    init(
        status: String? = nil,
        title: String? = nil,
        dueDate: String? = nil,
        note: String? = nil
    ) {
        self.status = status
        self.title = title
        self.dueDate = dueDate
        self.note = note
    }
}
