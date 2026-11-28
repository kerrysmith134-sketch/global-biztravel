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
    // Read-side value stays open-ended: the server may add system-only/future audit bases.
    var resolutionBasis: String?
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

    var resolutionBasisLabel: String? {
        guard let resolutionBasis else { return nil }
        if let known = TripRiskResolutionBasis(rawValue: resolutionBasis) {
            return known.label
        }
        switch resolutionBasis {
        case "SOURCE_SUGGESTION_DISMISSED":
            return "来源主动建议已驳回"
        default:
            return "其他关闭依据 · " + resolutionBasis
        }
    }

    var impactAssessmentDescriptions: [String] {
        let assessments = context?["external_signal_assessments"]?.arrayValue ?? []
        return assessments.compactMap { item in
            guard
                let record = item.objectValue,
                let assessment = record["impact_assessment"]?.objectValue,
                let status = assessment["status"]?.stringValue
            else {
                return nil
            }

            let signalType = record["signal_type"]?.stringValue ?? "EXTERNAL"
            let signalLabel: String
            switch signalType {
            case "WEATHER_DISRUPTION": signalLabel = "天气"
            case "FLIGHT_STATUS": signalLabel = "航班"
            case "VISA_POLICY_CHANGE": signalLabel = "签证"
            case "FX_VOLATILITY": signalLabel = "汇率"
            default: signalLabel = signalType
            }

            let statusLabel: String
            switch status {
            case "PLACE_AND_TIME_OVERLAP": statusLabel = "地点与拜访时段精确重叠"
            case "PLACE_MATCH_NO_TIME_OVERLAP": statusLabel = "地点匹配但时段未重叠"
            case "OPERATIONAL_DELAY_ASSESSED": statusLabel = "航班运营影响已计算"
            case "FLIGHT_CANCELLED": statusLabel = "航班取消"
            case "FLIGHT_DIVERTED": statusLabel = "航班备降"
            case "ARRIVAL_ESTIMATE_MISSING": statusLabel = "缺少到达时间估计"
            case "NO_SCHEDULE_IMPACT_REPORTED": statusLabel = "未报告时刻影响"
            case "SOURCE_ONLY_REQUIRES_HUMAN_REVIEW": statusLabel = "仅有外部来源，需人工核验"
            case "OPERATIONAL_UPDATE_EVIDENCE_MISMATCH",
                 "OPERATIONAL_UPDATE_ENVELOPE_MISMATCH":
                statusLabel = "运营事件证据不一致"
            case "NO_ACTIVE_PLAN": statusLabel = "缺少有效计划，影响未评估"
            case "STALE_ACTIVE_PLAN_INPUT":
                statusLabel = "生效计划基于过期行程输入，需要重新规划"
            default: statusLabel = status
            }

            let affectedCount = assessment["affected_visit_ids"]?.arrayValue?.count ?? 0
            let missedCount = assessment["missed_visit_ids"]?.arrayValue?.count ?? 0
            let impactCount = affectedCount > 0 ? " · 受影响拜访 \(affectedCount) 项" : ""
            let missedSuffix = missedCount > 0 ? " · 超出预约窗口 \(missedCount) 项" : ""
            let planVersionValue = assessment["plan_version_number"]?.integerValue
            let planVersion = planVersionValue.map { " · 计划 v\($0)" } ?? ""
            let explanation = assessment["explanation"]?.stringValue ?? ""

            return "\(signalLabel) · \(statusLabel)\(impactCount)\(missedSuffix)\(planVersion)\n\(explanation)"
        }
    }


    var recoveryReadinessDescriptions: [String] {
        guard let readiness = context?["recovery_readiness"]?.objectValue else { return [] }
        let rawStatus = readiness["status"]?.stringValue ?? "UNKNOWN"
        let statusLabel: String
        switch rawStatus {
        case "NOT_AVAILABLE": statusLabel = "尚未生成恢复方案"
        case "INTEGRITY_FAILURE": statusLabel = "恢复证据完整性校验失败"
        case "STALE_BRIEF": statusLabel = "恢复方案已过期，需要重新评估"
        case "AWAITING_HUMAN_DECISION": statusLabel = "等待人工确认恢复动作"
        case "AWAITING_HUMAN_ACTION": statusLabel = "等待执行已确认的恢复动作"
        case "PARTIALLY_ACCEPTED": statusLabel = "部分恢复动作尚未确认"
        case "IN_PROGRESS": statusLabel = "恢复动作处理中"
        case "REASSESSMENT_REQUIRED": statusLabel = "恢复动作已完成，等待重新评估"
        case "REASSESSMENT_REVIEW_REQUIRED": statusLabel = "重评仍有数据缺口，需要复核"
        case "REASSESSMENT_STALE": statusLabel = "重评依据已变化，需要重新评估"
        case "PLAN_CHANGE_HUMAN_SUBMISSION_REQUIRED": statusLabel = "恢复建议已形成，等待人工提交计划变更"
        case "REASSESSMENT_CLEARED_PENDING_HUMAN_RESOLUTION":
            statusLabel = "重评通过，仍需人工确认风险关闭"
        case "HUMAN_REVIEW_REQUIRED": statusLabel = "恢复动作已驳回，需要人工复核"
        case "NO_ACTION_REQUIRED_VERIFIED": statusLabel = "完整影响评估确认无需恢复动作"
        default: statusLabel = rawStatus
        }

        let integrity = readiness["integrity_status"]?.stringValue ?? "UNKNOWN"
        let integrityLabel = integrity == "VERIFIED" ? "证据链已验证" : "证据链未通过验证"
        let expected = readiness["expected_action_count"]?.integerValue ?? 0
        let accepted = readiness["accepted_action_count"]?.integerValue ?? 0
        let completed = readiness["completed_action_count"]?.integerValue ?? 0
        let summary = "\(statusLabel) · \(integrityLabel) · 已确认 \(accepted)/\(expected) · 已完成 \(completed)"

        let actions = readiness["actions"]?.arrayValue ?? []
        let details = actions.compactMap { item -> String? in
            guard let action = item.objectValue else { return nil }
            let code = action["action_code"]?.stringValue ?? "恢复动作"
            let actionLabel: String
            switch code {
            case "REQUEST_CUSTOMER_RESCHEDULE_WINDOW": actionLabel = "征求客户改期窗口"
            case "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM": actionLabel = "核实替代航班"
            case "REVALIDATE_TRAVEL_CHAIN": actionLabel = "重新核验交通链"
            case "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW": actionLabel = "确认客户预约窗口"
            case "RECONFIRM_ARRIVAL_AND_KEEP_APPOINTMENT_PROVISIONAL": actionLabel = "复核到达时间并暂留预约"
            default: actionLabel = code
            }

            let followUpStatus = action["follow_up_status"]?.stringValue ?? "AWAITING_HUMAN_DECISION"
            let followUpLabel: String
            switch followUpStatus {
            case "AWAITING_HUMAN_DECISION": followUpLabel = "待人工确认"
            case "OPEN": followUpLabel = "待执行"
            case "IN_PROGRESS": followUpLabel = "处理中"
            case "COMPLETED": followUpLabel = "已完成"
            case "DISMISSED": followUpLabel = "已驳回"
            default: followUpLabel = followUpStatus
            }

            let reassessment = action["reassessment"]?.objectValue
            let reassessmentStatus = reassessment?["status"]?.stringValue
            let reassessmentLabel: String
            switch reassessmentStatus {
            case "PLAN_CHANGE_HUMAN_SUBMISSION_REQUIRED":
                reassessmentLabel = " · 重评待人工提交计划"
            case "REASSESSMENT_CLEARED_PENDING_HUMAN_RESOLUTION":
                reassessmentLabel = " · 重评通过，待人工关闭"
            case "REASSESSMENT_REVIEW_REQUIRED":
                reassessmentLabel = " · 重评仍需复核"
            case "REASSESSMENT_STALE":
                reassessmentLabel = " · 重评依据已变化"
            case "INTEGRITY_FAILURE":
                reassessmentLabel = " · 重评证据异常"
            case "NOT_REASSESSED":
                reassessmentLabel = " · 尚未重评"
            default:
                reassessmentLabel = ""
            }
            return "\(actionLabel) · \(followUpLabel)\(reassessmentLabel)"
        }
        return [summary] + details
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
            case "EXTERNAL_SIGNAL": label = "外部信号"
            default: label = type
            }
            guard let identifier = reference["id"]?.stringValue else { return label }
            return "\(label) · \(identifier.prefix(8))"
        }
    }

    var evidenceDescriptions: [String] {
        (evidenceRefs ?? []).map { reference in
            let rawKind = reference["kind"]?.stringValue
                ?? reference["type"]?.stringValue
                ?? "证据"
            let kind: String
            switch rawKind {
            case "EXTERNAL_SIGNAL_PAYLOAD": kind = "外部信号内容指纹"
            case "IMPACT_ASSESSMENT": kind = "影响评估指纹"
            case "PLAN_VERSION": kind = "计划版本"
            case "PLAN_STOP": kind = "计划停靠点"
            case "PLAN_STOP_IMPACT": kind = "拜访时刻影响"
            case "PLAN_TRAVEL_LEG": kind = "计划交通段"
            case "FLIGHT_OPERATIONAL_UPDATE": kind = "航班运营事件"
            case "FLIGHT_SEGMENT": kind = "机票段"
            case "FLIGHT_SEGMENT_PLAN_BINDING": kind = "机票段计划绑定"
            default: kind = rawKind
            }
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
