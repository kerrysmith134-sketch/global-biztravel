import Foundation

struct MarketInsightEvidenceRead: Decodable, Equatable, Identifiable {
    var relationType: String
    var rationale: String?
    var evidence: ResearchEvidence

    var id: String { "\(evidence.id):\(relationType)" }
}

struct MarketInsightRead: Decodable, Equatable, Identifiable {
    var id: String
    var workspaceId: String
    var version: Int
    var insightKey: String
    var insightType: String
    var statement: String
    var assumptions: [JSONValue]
    var unknowns: [JSONValue]
    var implications: [JSONValue]
    var confidenceLevel: String
    var status: String
    var insufficientEvidenceReason: String?
    var reviewedAt: Date?
    var reviewNote: String?
    var createdAt: Date
    var updatedAt: Date
    var evidence: [MarketInsightEvidenceRead]

    var statusLabel: String {
        switch status.uppercased() {
        case "NEEDS_REVIEW": return "待审核"
        case "CONFLICTED": return "存在冲突"
        case "INSUFFICIENT": return "证据不足"
        case "ACCEPTED": return "已采纳"
        case "REJECTED": return "已驳回"
        case "SUPERSEDED": return "已被替代"
        default: return status
        }
    }

    var canReview: Bool {
        !["ACCEPTED", "REJECTED", "SUPERSEDED"].contains(status.uppercased())
    }
}

struct MarketOpportunitySummaryRead: Decodable, Equatable, Identifiable {
    var id: String
    var title: String
    var description: String?
    var status: String
    var nextAction: String?
    var qualificationReason: String?
    var disqualificationReason: String?
    var assessmentSummary: String?
    var assessmentConfidence: String
    var createdAt: Date
    var updatedAt: Date

    var statusLabel: String {
        switch status.uppercased() {
        case "DISCOVERED": return "已发现"
        case "RESEARCHING": return "研究中"
        case "EVIDENCE_READY": return "证据就绪"
        case "QUALIFICATION_PENDING": return "待资格确认"
        case "QUALIFIED": return "已确认资格"
        case "ACTION_PLANNED": return "已制定行动"
        case "ON_HOLD": return "暂缓"
        case "WON": return "已赢单"
        case "LOST": return "已失单"
        case "DISQUALIFIED": return "已淘汰"
        default: return status
        }
    }
}

struct MarketOpportunityEvidenceRead: Decodable, Equatable, Identifiable {
    var relationType: String
    var rationale: String?
    var evidence: ResearchEvidence

    var id: String { "\(evidence.id):\(relationType)" }
}

struct MarketOpportunityDetailRead: Decodable, Equatable {
    var opportunity: MarketOpportunitySummaryRead
    var evidence: [MarketOpportunityEvidenceRead]
    var entities: [JSONValue]
    var assessments: [JSONValue]
}
