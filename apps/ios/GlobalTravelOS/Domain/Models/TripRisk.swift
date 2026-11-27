import Foundation

/// The backend keeps the source domains authoritative; this model is the trip-level projection.
struct TripRiskCase: Codable, Equatable, Identifiable {
    var id: String
    var organizationId: String
    var tripId: String
    var caseKey: String
    var riskType: String
    var status: String
    var severity: String
    var title: String
    var summary: String
    var sourceRefs: [[String: JSONValue]]?
    var evidenceRefs: [[String: JSONValue]]?
    var context: [String: JSONValue]?
    var firstSeenAt: Date
    var lastObservedAt: Date
    var nextReviewAt: Date?
    var acknowledgedAt: Date?
    var startedAt: Date?
    var waitingSince: Date?
    var escalatedAt: Date?
    var resolvedAt: Date?
    var dismissedAt: Date?
    var resolutionBasis: TripRiskResolutionBasis?
    var resolutionNote: String?
    var version: Int
    var createdAt: Date
    var updatedAt: Date

    var isTerminal: Bool {
        ["RESOLVED", "DISMISSED"].contains(status.uppercased())
    }

    var statusLabel: String {
        switch status.uppercased() {
        case "OPEN": return "待处理"
        case "ACKNOWLEDGED": return "已确认"
        case "IN_PROGRESS": return "处理中"
        case "WAITING_EXTERNAL": return "等待外部"
        case "ESCALATED": return "已升级"
        case "RESOLVED": return "已解决"
        case "DISMISSED": return "已驳回"
        default: return status
        }
    }

    var severityLabel: String {
        switch severity.uppercased() {
        case "CRITICAL": return "紧急"
        case "HIGH": return "高"
        case "MEDIUM": return "中"
        case "LOW": return "低"
        default: return severity
        }
    }

    var riskTypeLabel: String {
        switch riskType.uppercased() {
        case "FLIGHT": return "航班"
        case "WEATHER": return "天气"
        case "VISA": return "签证"
        case "FINANCIAL": return "财务"
        case "PLANNING": return "计划"
        case "APPOINTMENT": return "预约"
        case "VISIT_FOLLOW_UP": return "拜访跟进"
        default: return riskType
        }
    }

    var nextReviewLabel: String {
        guard let nextReviewAt else { return "未安排复核" }
        return "下次复核 · " + ISO8601.shortDisplay(from: nextReviewAt)
    }

    var sourceDescriptions: [String] {
        (sourceRefs ?? []).map { reference in
            let type = reference["type"]?.stringValue
                ?? reference["source_type"]?.stringValue
                ?? "来源记录"
            let label: String
            switch type.uppercased() {
            case "PROACTIVE_SUGGESTION": label = "主动建议"
            case "VISIT_FOLLOW_UP": label = "拜访跟进"
            default: label = type
            }
            guard let identifier = reference["id"]?.stringValue else { return label }
            return "\(label) · \(identifier.prefix(8))"
        }
    }

    var evidenceDescriptions: [String] {
        (evidenceRefs ?? []).map { reference in
            let kind = reference["kind"]?.stringValue
                ?? reference["type"]?.stringValue
                ?? "证据"
            let ref = reference["ref"]?.stringValue
                ?? reference["id"]?.stringValue
            return ref.map { "\(kind) · \($0)" } ?? kind
        }
    }

    /// The server's transition schema intentionally does not accept manual reopening.
    var availableTransitions: [TripRiskTransitionTarget] {
        switch status.uppercased() {
        case "OPEN":
            return [.acknowledged, .inProgress, .waitingExternal, .resolved, .dismissed]
        case "ACKNOWLEDGED":
            return [.inProgress, .waitingExternal, .resolved, .dismissed]
        case "IN_PROGRESS":
            return [.acknowledged, .waitingExternal, .resolved, .dismissed]
        case "WAITING_EXTERNAL":
            return [.acknowledged, .inProgress, .resolved, .dismissed]
        case "ESCALATED":
            return [.acknowledged, .inProgress, .waitingExternal, .resolved, .dismissed]
        default:
            return []
        }
    }
}

enum TripRiskTransitionTarget: String, Codable, CaseIterable, Identifiable {
    case acknowledged = "ACKNOWLEDGED"
    case inProgress = "IN_PROGRESS"
    case waitingExternal = "WAITING_EXTERNAL"
    case resolved = "RESOLVED"
    case dismissed = "DISMISSED"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .acknowledged: return "确认收到"
        case .inProgress: return "开始处理"
        case .waitingExternal: return "等待外部确认"
        case .resolved: return "标记已解决"
        case .dismissed: return "驳回为误报"
        }
    }

    var requiresResolutionBasis: Bool {
        self == .resolved || self == .dismissed
    }
}

enum TripRiskResolutionBasis: String, Codable, CaseIterable, Hashable, Identifiable {
    case planRevalidated = "PLAN_REVALIDATED"
    case followUpCompleted = "FOLLOW_UP_COMPLETED"
    case tripClosed = "TRIP_CLOSED"
    case humanAttestation = "HUMAN_ATTESTATION"
    case falsePositiveConfirmed = "FALSE_POSITIVE_CONFIRMED"
    case followUpDismissed = "FOLLOW_UP_DISMISSED"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .planRevalidated: return "计划已重新验证"
        case .followUpCompleted: return "来源跟进已完成"
        case .tripClosed: return "行程已关闭"
        case .humanAttestation: return "人工确认已解决"
        case .falsePositiveConfirmed: return "全部主动建议已驳回"
        case .followUpDismissed: return "全部来源跟进已驳回"
        }
    }
}

struct TripRiskCaseEvent: Codable, Equatable, Identifiable {
    /// case_version is the server's causal ordering key; created_at may collide at DB precision.
    static func orderedByCaseVersion(_ events: [TripRiskCaseEvent]) -> [TripRiskCaseEvent] {
        events.sorted { lhs, rhs in
            if lhs.caseVersion != rhs.caseVersion {
                return lhs.caseVersion < rhs.caseVersion
            }
            if lhs.createdAt != rhs.createdAt {
                return lhs.createdAt < rhs.createdAt
            }
            return lhs.id < rhs.id
        }
    }

    var id: String
    var organizationId: String
    var tripId: String
    var caseId: String
    var caseVersion: Int
    var eventType: String
    var fromStatus: String?
    var toStatus: String
    var actorType: String
    var actorUserId: String?
    var note: String?
    var detail: [String: JSONValue]?
    var createdAt: Date

    var eventLabel: String {
        switch eventType.uppercased() {
        case "OPENED": return "风险发现"
        case "SOURCE_LINKED": return "关联来源更新"
        case "ACKNOWLEDGED": return "已确认"
        case "STARTED": return "开始处理"
        case "WAITING_EXTERNAL": return "等待外部确认"
        case "ESCALATED": return "升级提醒"
        case "RESOLVED": return "已解决"
        case "DISMISSED": return "已驳回"
        case "REOPENED": return "新证据重新开启"
        default: return eventType
        }
    }

    var actorLabel: String {
        actorType.uppercased() == "USER" ? "人工操作" : "系统记录"
    }
}

struct TripRiskTransitionRequest: Codable, Equatable {
    var expectedVersion: Int
    var toStatus: TripRiskTransitionTarget
    var note: String
    var nextReviewAt: Date?
    var resolutionBasis: TripRiskResolutionBasis?

    init(
        expectedVersion: Int,
        toStatus: TripRiskTransitionTarget,
        note: String,
        nextReviewAt: Date? = nil,
        resolutionBasis: TripRiskResolutionBasis? = nil
    ) {
        self.expectedVersion = expectedVersion
        self.toStatus = toStatus
        self.note = note
        self.nextReviewAt = nextReviewAt
        self.resolutionBasis = resolutionBasis
    }
}

struct TripRiskReconciliation: Codable, Equatable {
    var tripId: String
    var casesCreated: Int
    var casesUpdated: Int
    var casesReopened: Int
    var escalated: Int
    var reviewed: Int
    var errors: [String]

    var summary: String {
        "新增 \(casesCreated) · 更新 \(casesUpdated) · 重新开启 \(casesReopened) · 升级 \(escalated)"
    }
}

struct TripRiskTransitionResponse: Codable, Equatable {
    var caseItem: TripRiskCase
    var event: TripRiskCaseEvent

    private enum CodingKeys: String, CodingKey {
        case caseItem = "case"
        case event
    }
}
