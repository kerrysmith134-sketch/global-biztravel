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
    var outcomeNote: String?
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

struct MarketOpportunityCreateRequest: Codable, Equatable {
    var title: String
    var description: String?
    var nextAction: String?
}

struct MarketOpportunityEvidenceCreateRequest: Codable, Equatable {
    var evidenceId: String
    var relationType: String
    var rationale: String?
}

struct MarketOpportunityTransitionRequest: Codable, Equatable {
    var targetStatus: String
    var note: String?
}

struct MarketOpportunityQualifyRequest: Codable, Equatable {
    var qualificationReason: String
}

struct MarketOpportunityOutcomeRequest: Codable, Equatable {
    var outcome: String
    var outcomeNote: String
}

struct MarketOpportunityEvidenceRead: Decodable, Equatable, Identifiable {
    var relationType: String
    var rationale: String?
    var evidence: ResearchEvidence

    var id: String { "\(evidence.id):\(relationType)" }
}

struct MarketEntityAliasRead: Decodable, Equatable, Identifiable {
    var id: String
    var aliasType: String
    var rawValue: String
    var normalizedValue: String
    var sourceEvidenceId: String?
    var createdAt: Date
}

struct MarketEntityRead: Decodable, Equatable, Identifiable {
    var id: String
    var entityKind: String
    var displayName: String
    var normalizedName: String
    var description: String?
    var canonicalDomain: String?
    var countryCode: String?
    var attributes: [String: JSONValue]
    var status: String
    var createdAt: Date
    var updatedAt: Date
    var aliases: [MarketEntityAliasRead]

    var statusLabel: String {
        switch status.uppercased() {
        case "CANDIDATE": return "候选"
        case "REVIEWED": return "已审核"
        case "VERIFIED": return "已核验"
        case "REJECTED": return "已驳回"
        case "MERGED": return "已合并"
        default: return status
        }
    }

    var locationLabel: String {
        countryCode?.uppercased() ?? "国家未注明"
    }
}

struct MarketWorkspaceEntityRead: Decodable, Equatable, Identifiable {
    var entity: MarketEntityRead
    var discoveryReason: String?
    var relevanceNote: String?
    var status: String
    var firstSeenAt: Date
    var lastSeenAt: Date
    var possibleDuplicateEntityIds: [String]

    var id: String { entity.id }

    var workspaceStatusLabel: String {
        switch status.uppercased() {
        case "DISCOVERED": return "已发现"
        case "RESEARCHING": return "研究中"
        case "INCLUDED": return "已纳入"
        case "EXCLUDED": return "已排除"
        default: return status
        }
    }
}

struct MarketWorkspaceEntityCreateRequest: Codable, Equatable {
    var existingEntityId: String?
    var entityKind: String?
    var displayName: String?
    var description: String?
    var canonicalDomain: String?
    var countryCode: String?
    var attributes: [String: JSONValue]
    var discoveryReason: String?
    var relevanceNote: String?

    init(
        entityKind: String,
        displayName: String,
        description: String? = nil,
        canonicalDomain: String? = nil,
        countryCode: String? = nil,
        attributes: [String: JSONValue] = [:],
        discoveryReason: String? = nil,
        relevanceNote: String? = nil
    ) {
        self.existingEntityId = nil
        self.entityKind = entityKind.trimmingCharacters(in: .whitespacesAndNewlines)
        self.displayName = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        self.description = description?.nilIfBlank
        self.canonicalDomain = canonicalDomain?.nilIfBlank
        self.countryCode = countryCode?.nilIfBlank?.uppercased()
        self.attributes = attributes
        self.discoveryReason = discoveryReason?.nilIfBlank
        self.relevanceNote = relevanceNote?.nilIfBlank
    }
}

struct MarketOpportunityEntitiesAttachRequest: Codable, Equatable {
    var entityIds: [String]
    var relationshipNote: String?
}

struct MarketOpportunityDetailRead: Decodable, Equatable {
    var opportunity: MarketOpportunitySummaryRead
    var evidence: [MarketOpportunityEvidenceRead]
    var entities: [MarketEntityRead]
    var assessments: [JSONValue]
}

@propertyWrapper
struct FlexibleDecimal: Decodable, Equatable {
    var wrappedValue: Decimal

    init(wrappedValue: Decimal) {
        self.wrappedValue = wrappedValue
    }

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let string = try? value.decode(String.self),
           let decimal = Decimal(string: string, locale: Locale(identifier: "en_US_POSIX")) {
            wrappedValue = decimal
            return
        }
        if let number = try? value.decode(Double.self) {
            wrappedValue = Decimal(number)
            return
        }
        throw DecodingError.dataCorruptedError(
            in: value,
            debugDescription: "金额既不是有效的十进制字符串也不是数字"
        )
    }
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
    @FlexibleDecimal var businessValue: Decimal
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
    @FlexibleDecimal var businessValue: Decimal
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
