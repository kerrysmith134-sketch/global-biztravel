import Foundation

/// `summaries:[{id, kind, summary, decisions, follow_ups, lessons, valid_from, confidence}]`.
///
/// The memory layer is what makes the agent improve trip over trip: summaries are the durable
/// takeaways, `procedure` is the raw record of what was attempted and how it turned out.
struct TripMemorySummary: Codable, Equatable, Identifiable {
    var id: String
    var kind: String?
    var summary: String?
    var decisions: [String]?
    var followUps: [String]?
    var lessons: [String]?
    var validFrom: Date?
    var confidence: Double?

    init(
        id: String,
        kind: String? = nil,
        summary: String? = nil,
        decisions: [String]? = nil,
        followUps: [String]? = nil,
        lessons: [String]? = nil,
        validFrom: Date? = nil,
        confidence: Double? = nil
    ) {
        self.id = id
        self.kind = kind
        self.summary = summary
        self.decisions = decisions
        self.followUps = followUps
        self.lessons = lessons
        self.validFrom = validFrom
        self.confidence = confidence
    }

    var kindLabel: String { kind ?? "通用记忆" }

    var summaryText: String { summary ?? "（无摘要正文）" }

    var decisionList: [String] { decisions ?? [] }
    var followUpList: [String] { followUps ?? [] }
    var lessonList: [String] { lessons ?? [] }

    var isEmpty: Bool {
        summaryText.isEmpty
            && decisionList.isEmpty
            && followUpList.isEmpty
            && lessonList.isEmpty
    }

    var confidenceLabel: String {
        guard let confidence else { return "置信度未知" }
        return String(format: "置信度 %.0f%%", confidence * 100)
    }

    var validFromLabel: String {
        guard let validFrom else { return "—" }
        return ISO8601.shortDisplay(from: validFrom)
    }

    var totalItems: Int {
        decisionList.count + followUpList.count + lessonList.count
    }
}

/// `procedure:[{attempt_id, outcome, reason, created_at}]` — the audit trail behind the summaries.
struct MemoryProcedureEntry: Codable, Equatable, Identifiable {
    var attemptId: String
    var outcome: String?
    var reason: String?
    var createdAt: Date?

    init(
        attemptId: String,
        outcome: String? = nil,
        reason: String? = nil,
        createdAt: Date? = nil
    ) {
        self.attemptId = attemptId
        self.outcome = outcome
        self.reason = reason
        self.createdAt = createdAt
    }

    var id: String { attemptId }

    var outcomeLabel: String { outcome ?? "结果未记录" }

    var reasonLabel: String { reason ?? "未给出原因" }

    var createdAtLabel: String {
        guard let createdAt else { return "—" }
        return ISO8601.shortDisplay(from: createdAt)
    }

    /// Outcomes that count as a lesson worth carrying into the next trip.
    var isFailureRecord: Bool {
        let value = (outcome ?? "").uppercased()
        return value.contains("FAIL") || value.contains("REJECT") || value.contains("ERROR")
    }
}

/// `GET /v1/trips/{id}/memory` response.
struct TripMemory: Codable, Equatable {
    var tripId: String
    var summaries: [TripMemorySummary]?
    var procedure: [MemoryProcedureEntry]?

    init(
        tripId: String,
        summaries: [TripMemorySummary]? = nil,
        procedure: [MemoryProcedureEntry]? = nil
    ) {
        self.tripId = tripId
        self.summaries = summaries
        self.procedure = procedure
    }

    var summaryList: [TripMemorySummary] { summaries ?? [] }

    var procedureList: [MemoryProcedureEntry] { procedure ?? [] }

    var isEmpty: Bool { summaryList.isEmpty && procedureList.isEmpty }

    var countsLine: String {
        "记忆摘要 \(summaryList.count) 条 · 过程记录 \(procedureList.count) 条"
    }

    var lessonCount: Int {
        summaryList.reduce(0) { $0 + $1.lessonList.count }
    }

    var followUpCount: Int {
        summaryList.reduce(0) { $0 + $1.followUpList.count }
    }

    /// Failed attempts first — those are the ones a planner should read before planning again.
    var procedureByRecency: [MemoryProcedureEntry] {
        procedureList.sorted { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }
    }

    var failedAttempts: [MemoryProcedureEntry] {
        procedureList.filter(\.isFailureRecord)
    }
}