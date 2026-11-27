import Foundation

/// `severity:"LOW|MEDIUM|HIGH"` — the only suggestion field whose value set the contract pins.
enum SuggestionSeverity: String, Codable, CaseIterable, Identifiable {
    case low = "LOW"
    case medium = "MEDIUM"
    case high = "HIGH"
    case unknown = "UNKNOWN"

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = SuggestionSeverity(rawValue: raw) ?? .unknown
    }

    var id: String { rawValue }

    var label: String {
        switch self {
        case .low: return "低"
        case .medium: return "中"
        case .high: return "高"
        case .unknown: return "未知"
        }
    }

    /// Ordering key so `HIGH` sorts above `LOW` in the Today feed.
    var rank: Int {
        switch self {
        case .high: return 3
        case .medium: return 2
        case .low: return 1
        case .unknown: return 0
        }
    }

    var tintGlyph: String {
        switch self {
        case .high: return "exclamationmark.triangle.fill"
        case .medium: return "bolt.fill"
        case .low: return "info.circle"
        case .unknown: return "questionmark.circle"
        }
    }
}

/// `evidence:[{kind, ref, retrieved_at}]`.
struct SuggestionEvidence: Codable, Equatable, Identifiable {
    var kind: String?
    var ref: String?
    var retrievedAt: Date?

    init(kind: String? = nil, ref: String? = nil, retrievedAt: Date? = nil) {
        self.kind = kind
        self.ref = ref
        self.retrievedAt = retrievedAt
    }

    var id: String { "\(kind ?? "-")|\(ref ?? "-")" }

    var displayText: String {
        var parts: [String] = []
        if let kind { parts.append(kind) }
        if let ref { parts.append(ref) }
        if let retrievedAt { parts.append("取自 " + ISO8601.shortDisplay(from: retrievedAt)) }
        return parts.isEmpty ? "无证据" : parts.joined(separator: " · ")
    }

    var sourceLabel: String { ref ?? kind ?? "—" }
}

/// A recovery action derived from a verified flight-impact snapshot.
struct AppointmentRecoveryAction: Equatable, Identifiable {
    let visitId: String?
    let planStopId: String?
    let actionCode: String
    let priority: String
    let decisionStatus: String
    let rationale: String?
    let delayMinutes: Int?
    let requiredInputs: [String]

    var id: String {
        "\(visitId ?? "visit")|\(planStopId ?? "stop")|\(actionCode)"
    }

    var actionLabel: String {
        switch actionCode {
        case "REQUEST_CUSTOMER_RESCHEDULE_WINDOW": return "征求客户改期窗口"
        case "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM": return "核实替代航班并重确认"
        case "REVALIDATE_TRAVEL_CHAIN": return "重新验证交通链"
        case "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW": return "确认预约时间窗口"
        case "RECONFIRM_ARRIVAL_AND_KEEP_APPOINTMENT_PROVISIONAL":
            return "复核到达时间并暂保留预约"
        default: return "复核拜访恢复方案"
        }
    }

    var priorityLabel: String {
        switch priority.uppercased() {
        case "HIGH": return "高优先级"
        case "MEDIUM": return "需关注"
        default: return "待复核"
        }
    }

    var decisionLabel: String {
        switch decisionStatus.uppercased() {
        case "REQUIRES_HUMAN_DECISION": return "需要人工决定"
        case "DATA_REVALIDATION_REQUIRED": return "需要补齐交通证据"
        case "CUSTOMER_CONFIRMATION_REQUIRED": return "需要客户确认"
        case "AT_RISK_REVIEW": return "预约仍暂时有效"
        default: return "待复核"
        }
    }

    var delayLabel: String? {
        guard let delayMinutes, delayMinutes > 0 else { return nil }
        return "预计影响 \(delayMinutes) 分钟"
    }

    fileprivate init?(json: JSONValue) {
        guard let value = json.objectValue,
              let actionCode = value["action_code"]?.stringValue else {
            return nil
        }
        visitId = value["visit_id"]?.stringValue
        planStopId = value["plan_stop_id"]?.stringValue
        self.actionCode = actionCode
        priority = value["priority"]?.stringValue ?? "UNKNOWN"
        decisionStatus = value["decision_status"]?.stringValue ?? "UNKNOWN"
        rationale = value["rationale"]?.stringValue
        delayMinutes = value["delay_minutes"]?.integerValue
        requiredInputs = value["required_inputs"]?.arrayValue?.compactMap(\.stringValue) ?? []
    }
}

/// Deterministic appointment recovery summary embedded in a proactive suggestion.
struct AppointmentRecoveryBrief: Equatable {
    let status: String
    let actions: [AppointmentRecoveryAction]
    let dataGaps: [String]
    let humanDecisionRequired: Bool
    let planMutated: Bool
    let bookingChanged: Bool
    let customerContacted: Bool
    let candidateWindowsInferred: Bool

    var statusLabel: String {
        switch status.uppercased() {
        case "RECOVERY_REQUIRED": return "需要安排恢复"
        case "REVIEW_REQUIRED": return "需要复核"
        case "NO_RECOVERY_ACTION_REQUIRED": return "暂不需要恢复动作"
        default: return "恢复状态待确认"
        }
    }

    fileprivate init?(json: JSONValue) {
        guard let value = json.objectValue,
              let rawActions = value["actions"]?.arrayValue else {
            return nil
        }
        status = value["status"]?.stringValue ?? "UNKNOWN"
        actions = rawActions.compactMap(AppointmentRecoveryAction.init(json:))
        dataGaps = value["data_gaps"]?.arrayValue?.compactMap(\.stringValue) ?? []
        let guardrails = value["guardrails"]?.objectValue ?? [:]
        humanDecisionRequired = guardrails["human_decision_required"]?.boolValue ?? !actions.isEmpty
        planMutated = guardrails["plan_mutated"]?.boolValue ?? false
        bookingChanged = guardrails["booking_changed"]?.boolValue ?? false
        customerContacted = guardrails["customer_contacted"]?.boolValue ?? false
        candidateWindowsInferred = guardrails["candidate_windows_inferred"]?.boolValue ?? false
    }
}

/// `proposed_action:{kind, tool, params}` — what the agent would do if a human approves.
struct ProposedAction: Codable, Equatable {
    var kind: String?
    var tool: String?
    var params: [String: JSONValue]?

    init(kind: String? = nil, tool: String? = nil, params: [String: JSONValue]? = nil) {
        self.kind = kind
        self.tool = tool
        self.params = params
    }

    var title: String { kind ?? "未命名动作" }

    var toolLabel: String { tool ?? "内部能力" }

    var paramsSummary: String {
        guard let params, !params.isEmpty else { return "无参数" }
        return params.keys.sorted()
            .map { "\($0)=\(params[$0]?.displayDescription ?? "")" }
            .joined(separator: ", ")
    }

    var hasParams: Bool { !(params ?? [:]).isEmpty }

    var appointmentRecoveryBrief: AppointmentRecoveryBrief? {
        guard let value = params?["recovery_brief"] else { return nil }
        return AppointmentRecoveryBrief(json: value)
    }
}

/// `daily_cap:{used,cap}`.
struct DailyCap: Codable, Equatable {
    var used: Int?
    var cap: Int?

    init(used: Int? = nil, cap: Int? = nil) {
        self.used = used
        self.cap = cap
    }

    var usedCount: Int { used ?? 0 }
    var capCount: Int { cap ?? 0 }

    var remaining: Int { max(capCount - usedCount, 0) }

    var isExhausted: Bool { capCount > 0 && usedCount >= capCount }

    var displayText: String {
        guard capCount > 0 else { return "当日打扰 \(usedCount) 次（未设上限）" }
        return "当日打扰 \(usedCount)/\(capCount)，剩余 \(remaining) 次"
    }

    var ratio: Double {
        guard capCount > 0 else { return 0 }
        return min(Double(usedCount) / Double(capCount), 1)
    }
}

/// One proactive suggestion from `GET /v1/proactive/suggestions`.
///
/// `status` is kept as a raw string: the contract pins values for approve/dismiss responses but
/// does not enumerate the full status vocabulary of the list endpoint, and an unknown status must
/// not break decoding of the whole feed.
struct ProactiveSuggestion: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String?
    var suggestionType: String?
    var status: String?
    var title: String
    var rationale: String?
    var evidence: [SuggestionEvidence]?
    var proposedAction: ProposedAction?
    var confidence: Double?
    var requiresApproval: Bool?
    var severity: SuggestionSeverity?
    var dedupeKey: String?
    var createdAt: Date?

    init(
        id: String,
        tripId: String? = nil,
        suggestionType: String? = nil,
        status: String? = nil,
        title: String,
        rationale: String? = nil,
        evidence: [SuggestionEvidence]? = nil,
        proposedAction: ProposedAction? = nil,
        confidence: Double? = nil,
        requiresApproval: Bool? = nil,
        severity: SuggestionSeverity? = nil,
        dedupeKey: String? = nil,
        createdAt: Date? = nil
    ) {
        self.id = id
        self.tripId = tripId
        self.suggestionType = suggestionType
        self.status = status
        self.title = title
        self.rationale = rationale
        self.evidence = evidence
        self.proposedAction = proposedAction
        self.confidence = confidence
        self.requiresApproval = requiresApproval
        self.severity = severity
        self.dedupeKey = dedupeKey
        self.createdAt = createdAt
    }

    var evidenceList: [SuggestionEvidence] { evidence ?? [] }

    var resolvedSeverity: SuggestionSeverity { severity ?? .unknown }

    var statusLabel: String {
        switch (status ?? "PENDING").uppercased() {
        case "PENDING": return "待处理"
        case "APPROVED": return "已批准"
        case "DISMISSED": return "已忽略"
        case "EXECUTED": return "已执行"
        case "EXPIRED": return "已过期"
        default: return "未知状态（\(status ?? "-")）"
        }
    }

    var isActionable: Bool {
        let value = (status ?? "PENDING").uppercased()
        return value == "PENDING"
    }

    var needsHumanApproval: Bool { requiresApproval ?? false }

    var confidenceLabel: String {
        guard let confidence else { return "置信度未知" }
        return String(format: "置信度 %.0f%%", confidence * 100)
    }

    var confidenceRatio: Double {
        guard let confidence else { return 0 }
        return min(max(confidence, 0), 1)
    }

    var createdAtLabel: String {
        guard let createdAt else { return "—" }
        return ISO8601.shortDisplay(from: createdAt)
    }

    var typeLabel: String { suggestionType ?? "通用建议" }

    var actionSummary: String {
        guard let proposedAction else { return "该建议不含可执行动作" }
        return "\(proposedAction.title) · \(proposedAction.toolLabel)"
    }

    var recoveryBrief: AppointmentRecoveryBrief? {
        proposedAction?.appointmentRecoveryBrief
    }
}

/// `GET /v1/proactive/suggestions` response — a page that also carries the daily interruption cap.
struct SuggestionFeed: Codable, Equatable {
    var items: [ProactiveSuggestion]
    var dailyCap: DailyCap?
    var nextCursor: String?

    init(items: [ProactiveSuggestion] = [], dailyCap: DailyCap? = nil, nextCursor: String? = nil) {
        self.items = items
        self.dailyCap = dailyCap
        self.nextCursor = nextCursor
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.items = try container.decodeIfPresent([ProactiveSuggestion].self, forKey: .items) ?? []
        self.dailyCap = try container.decodeIfPresent(DailyCap.self, forKey: .dailyCap)
        self.nextCursor = try container.decodeIfPresent(String.self, forKey: .nextCursor)
    }

    private enum CodingKeys: String, CodingKey {
        case items
        case dailyCap
        case nextCursor
    }

    var cap: DailyCap { dailyCap ?? DailyCap() }

    var hasMore: Bool {
        guard let nextCursor else { return false }
        return !nextCursor.isEmpty
    }

    var sortedBySeverity: [ProactiveSuggestion] {
        items.sorted { lhs, rhs in
            if lhs.resolvedSeverity.rank != rhs.resolvedSeverity.rank {
                return lhs.resolvedSeverity.rank > rhs.resolvedSeverity.rank
            }
            return (lhs.createdAt ?? .distantPast) > (rhs.createdAt ?? .distantPast)
        }
    }
}

/// `approval:{approved_by, approved_at}`.
struct SuggestionApproval: Codable, Equatable {
    var approvedBy: String?
    var approvedAt: Date?

    init(approvedBy: String? = nil, approvedAt: Date? = nil) {
        self.approvedBy = approvedBy
        self.approvedAt = approvedAt
    }

    var displayText: String {
        let actor = approvedBy ?? "未知审批人"
        guard let approvedAt else { return actor }
        return "\(actor) · \(ISO8601.shortDisplay(from: approvedAt))"
    }
}

/// `execution:{status, detail}|null`.
struct SuggestionExecution: Codable, Equatable {
    var status: String?
    var detail: String?

    init(status: String? = nil, detail: String? = nil) {
        self.status = status
        self.detail = detail
    }

    var displayText: String {
        [status, detail].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

/// `POST /v1/proactive/suggestions/{id}:approve` response.
struct SuggestionApprovalResponse: Codable, Equatable, Identifiable {
    var id: String
    var status: String?
    var approval: SuggestionApproval?
    var execution: SuggestionExecution?

    init(
        id: String,
        status: String? = nil,
        approval: SuggestionApproval? = nil,
        execution: SuggestionExecution? = nil
    ) {
        self.id = id
        self.status = status
        self.approval = approval
        self.execution = execution
    }

    var isApproved: Bool { (status ?? "").uppercased() == "APPROVED" }

    var executionSummary: String {
        guard let execution else { return "已登记批准，尚未执行" }
        return execution.displayText.isEmpty ? "已登记批准" : execution.displayText
    }
}

/// `memory_feedback:{type, weight_delta}` — dismiss feeds the memory layer a negative signal.
struct MemoryFeedback: Codable, Equatable {
    var type: String?
    var weightDelta: Double?

    init(type: String? = nil, weightDelta: Double? = nil) {
        self.type = type
        self.weightDelta = weightDelta
    }

    var displayText: String {
        var parts: [String] = []
        if let type { parts.append(type) }
        if let weightDelta { parts.append(String(format: "权重 %+.2f", weightDelta)) }
        return parts.isEmpty ? "已记录反馈" : parts.joined(separator: " · ")
    }
}

/// `POST /v1/proactive/suggestions/{id}:dismiss` response.
struct SuggestionDismissResponse: Codable, Equatable, Identifiable {
    var id: String
    var status: String?
    var dismissedReason: String?
    var memoryFeedback: MemoryFeedback?

    init(
        id: String,
        status: String? = nil,
        dismissedReason: String? = nil,
        memoryFeedback: MemoryFeedback? = nil
    ) {
        self.id = id
        self.status = status
        self.dismissedReason = dismissedReason
        self.memoryFeedback = memoryFeedback
    }

    var isDismissed: Bool { (status ?? "").uppercased() == "DISMISSED" }

    var feedbackSummary: String {
        let reason = dismissedReason ?? "未填写忽略原因"
        guard let memoryFeedback else { return reason }
        return "\(reason) · 记忆层：\(memoryFeedback.displayText)"
    }
}
