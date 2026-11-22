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

struct MarketOpportunityVisitCandidateRead: Decodable, Equatable, Identifiable {
    var tripId: String
    var tripName: String
    var tripStatus: String
    var tripWindowStart: Date
    var tripWindowEnd: Date
    var visitId: String
    var visitTitle: String?
    var visitObjective: String?
    var visitStatus: String
    var customerId: String?
    var customerName: String?
    var placeId: String
    var placeName: String
    var city: String?
    var countryCode: String?
    var latitude: Double?
    var longitude: Double?
    var timezone: String?
    var timeWindowStart: Date?
    var timeWindowEnd: Date?
    var serviceMinutes: Int
    var priority: Int
    var businessValue: Decimal
    var movementMode: String

    var id: String { visitId }

    var dateLabel: String {
        ISO8601.dayString(from: tripWindowStart) + " – " + ISO8601.dayString(from: tripWindowEnd)
    }

    var visitStatusLabel: String {
        switch visitStatus.uppercased() {
        case "PLANNED": return "待排期"
        case "SCHEDULED": return "已排期"
        case "DONE": return "已完成"
        case "MISSED": return "未会面"
        case "CANCELLED": return "已取消"
        default: return visitStatus
        }
    }

    var locationLabel: String {
        [city, countryCode].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

struct MarketOpportunityVisitLinkRead: Decodable, Equatable, Identifiable {
    var id: String
    var workspaceId: String
    var opportunityId: String
    var linkStatus: String
    var linkNote: String?
    var linkedByUserId: String?
    var linkedAt: Date
    var unlinkedByUserId: String?
    var unlinkedAt: Date?
    var unlinkNote: String?
    var tripId: String
    var tripName: String
    var tripStatus: String
    var tripWindowStart: Date
    var tripWindowEnd: Date
    var visitId: String
    var visitTitle: String?
    var visitObjective: String?
    var visitStatus: String
    var customerId: String?
    var customerName: String?
    var placeId: String
    var placeName: String
    var city: String?
    var countryCode: String?
    var latitude: Double?
    var longitude: Double?
    var timezone: String?
    var timeWindowStart: Date?
    var timeWindowEnd: Date?
    var serviceMinutes: Int
    var priority: Int
    var businessValue: Decimal
    var movementMode: String

    var isLinked: Bool { linkStatus.uppercased() == "LINKED" }

    var statusLabel: String {
        isLinked ? "已关联" : "已解除"
    }

    var dateLabel: String {
        ISO8601.dayString(from: tripWindowStart) + " – " + ISO8601.dayString(from: tripWindowEnd)
    }
}

struct MarketOpportunityVisitLinkCreate: Codable, Equatable {
    var tripId: String
    var visitId: String
    var linkNote: String?
}

struct MarketOpportunityVisitUnlinkRequest: Codable, Equatable {
    var unlinkNote: String?
}

struct MarketOpportunityExecutionRead: Decodable, Equatable {
    var workspaceId: String
    var opportunityId: String
    var opportunityStatus: String
    var canLinkVisits: Bool
    var blockedReason: String?
    var links: [MarketOpportunityVisitLinkRead]
    var candidates: [MarketOpportunityVisitCandidateRead]
}
