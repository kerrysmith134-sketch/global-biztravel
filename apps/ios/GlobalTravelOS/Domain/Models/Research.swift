import Foundation

enum ResearchSourceScope: String, Codable, CaseIterable, Identifiable {
    case automatic = "AUTO"
    case internalOnly = "INTERNAL_ONLY"
    case externalOnly = "EXTERNAL_ONLY"
    case internalAndExternal = "INTERNAL_AND_EXTERNAL"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .automatic: return "自动判断"
        case .internalOnly: return "仅使用内部资料"
        case .externalOnly: return "仅使用外部资料"
        case .internalAndExternal: return "内部 + 外部资料"
        }
    }

    var explanation: String {
        switch self {
        case .automatic: return "根据本次请求判断是否需要外部资料"
        case .internalOnly: return "不生成外部网页检索查询"
        case .externalOnly: return "不读取已选客户或行程记录"
        case .internalAndExternal: return "允许结合已选记录与外部资料"
        }
    }
}

struct ResearchPlanCreateRequest: Codable, Equatable {
    var request: String
    var sourceScope: ResearchSourceScope
    var tripId: String?
    var customerId: String?
    var contextText: String?
    var locale: String

    init(
        request: String,
        sourceScope: ResearchSourceScope = .automatic,
        tripId: String? = nil,
        customerId: String? = nil,
        contextText: String? = nil,
        locale: String = Locale.current.identifier
    ) {
        self.request = request
        self.sourceScope = sourceScope
        self.tripId = tripId
        self.customerId = customerId
        self.contextText = contextText
        self.locale = locale
    }
}

struct ResearchPlanIntent: Decodable, Equatable {
    var intentSummary: String
    var objective: String
    var subject: String
    var domain: String?
    var geographies: [String]
    var entities: [String]
    var timeScope: String?
    var researchQuestions: [String]
    var searchQueries: [String]
    var sourceTypes: [String]
    var deliverables: [String]
    var missingInformation: [String]
    var externalResearchNeeded: Bool

    private enum CodingKeys: String, CodingKey {
        case intentSummary, objective, subject, domain, geographies, entities
        case timeScope, researchQuestions, searchQueries, sourceTypes, deliverables
        case missingInformation, externalResearchNeeded
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        intentSummary = try values.decodeIfPresent(String.self, forKey: .intentSummary) ?? ""
        objective = try values.decodeIfPresent(String.self, forKey: .objective) ?? ""
        subject = try values.decodeIfPresent(String.self, forKey: .subject) ?? ""
        domain = try values.decodeIfPresent(String.self, forKey: .domain)
        geographies = try values.decodeIfPresent([String].self, forKey: .geographies) ?? []
        entities = try values.decodeIfPresent([String].self, forKey: .entities) ?? []
        timeScope = try values.decodeIfPresent(String.self, forKey: .timeScope)
        researchQuestions = try values.decodeIfPresent([String].self, forKey: .researchQuestions) ?? []
        searchQueries = try values.decodeIfPresent([String].self, forKey: .searchQueries) ?? []
        sourceTypes = try values.decodeIfPresent([String].self, forKey: .sourceTypes) ?? []
        deliverables = try values.decodeIfPresent([String].self, forKey: .deliverables) ?? []
        missingInformation = try values.decodeIfPresent([String].self, forKey: .missingInformation) ?? []
        externalResearchNeeded = try values.decodeIfPresent(
            Bool.self, forKey: .externalResearchNeeded
        ) ?? false
    }
}

struct ResearchPlannerAudit: Decodable, Equatable {
    var agentRunId: String
    var llmInvocationId: String?
    var model: String
    var degraded: Bool
    var error: String?
}

struct ResearchEvidence: Decodable, Equatable, Identifiable {
    var id: String
    var kind: String
    var ref: String
    var source: String?
    var retrievedAt: Date?
    var createdAt: Date?
}

struct ResearchPlan: Decodable, Equatable, Identifiable {
    var id: String
    var tripId: String?
    var customerId: String?
    var jobId: String?
    var retrievalError: String?
    var resultsCount: Int
    var request: String
    var requestedScope: String
    var recommendedScope: String
    var status: String
    var retrievalStatus: String
    var plan: ResearchPlanIntent
    var planner: ResearchPlannerAudit
    var evidence: [ResearchEvidence]
    var createdAt: Date?

    var retrievalStatusLabel: String {
        switch retrievalStatus.uppercased() {
        case "NOT_STARTED":
            return plan.externalResearchNeeded ? "尚未采集外部证据" : "本次不需要外部检索"
        case "QUEUED": return "检索任务排队中"
        case "RUNNING": return "正在采集证据"
        case "COMPLETED":
            return resultsCount > 0 ? "已采集 \(resultsCount) 条来源（尚未核验）" : "检索完成，未找到来源"
        case "FAILED": return "证据采集失败"
        default: return retrievalStatus
        }
    }

    var sourceScopeLabel: String {
        switch recommendedScope {
        case "INTERNAL_ONLY": return "仅内部资料"
        case "EXTERNAL_ONLY": return "仅外部资料"
        case "INTERNAL_AND_EXTERNAL": return "内部 + 外部资料"
        default: return recommendedScope
        }
    }
}

struct ResearchExecutionAccepted: Decodable, Equatable {
    var planId: String
    var jobId: String
    var status: String
    var reused: Bool
}
