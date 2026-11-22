import Foundation

struct MarketWorkspace: Decodable, Equatable, Identifiable {
    var id: String
    var name: String
    var objective: String?
    var status: String
    var ownerUserId: String?
    var currentContextVersion: Int?
    var createdAt: Date?
    var updatedAt: Date?

    var statusLabel: String {
        switch status.uppercased() {
        case "DRAFT": return "草稿"
        case "ACTIVE": return "进行中"
        case "PAUSED": return "已暂停"
        case "COMPLETED": return "已完成"
        case "ARCHIVED": return "已归档"
        default: return status
        }
    }
}

struct MarketWorkspaceCreateRequest: Codable, Equatable {
    var name: String
    var objective: String?
    var initialContext: String?
}

struct MarketResearchQuestion: Decodable, Equatable, Identifiable {
    var id: String
    var workspaceId: String
    var parentQuestionId: String?
    var question: String
    var rationale: String?
    var source: String
    var priority: Int
    var status: String
    var acceptanceCriteria: [String]
    var answerSummary: String?
    var blockedReason: String?
    var createdAt: Date?
    var updatedAt: Date?
    var expectedEvidence: [String]
    var generatedByRunId: String?
    var plannerQuestionKey: String?

    private enum CodingKeys: String, CodingKey {
        case id, workspaceId, parentQuestionId, question, rationale, source, priority, status
        case acceptanceCriteria, answerSummary, blockedReason, createdAt, updatedAt
        case expectedEvidence, generatedByRunId, plannerQuestionKey
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        workspaceId = try values.decode(String.self, forKey: .workspaceId)
        parentQuestionId = try values.decodeIfPresent(String.self, forKey: .parentQuestionId)
        question = try values.decode(String.self, forKey: .question)
        rationale = try values.decodeIfPresent(String.self, forKey: .rationale)
        source = try values.decode(String.self, forKey: .source)
        priority = try values.decode(Int.self, forKey: .priority)
        status = try values.decode(String.self, forKey: .status)
        acceptanceCriteria = try values.decodeIfPresent([String].self, forKey: .acceptanceCriteria) ?? []
        answerSummary = try values.decodeIfPresent(String.self, forKey: .answerSummary)
        blockedReason = try values.decodeIfPresent(String.self, forKey: .blockedReason)
        createdAt = try values.decodeIfPresent(Date.self, forKey: .createdAt)
        updatedAt = try values.decodeIfPresent(Date.self, forKey: .updatedAt)
        expectedEvidence = try values.decodeIfPresent([String].self, forKey: .expectedEvidence) ?? []
        generatedByRunId = try values.decodeIfPresent(String.self, forKey: .generatedByRunId)
        plannerQuestionKey = try values.decodeIfPresent(String.self, forKey: .plannerQuestionKey)
    }

    var statusLabel: String {
        switch status.uppercased() {
        case "OPEN": return "待研究"
        case "IN_PROGRESS": return "研究中"
        case "ANSWERED": return "已采纳答案"
        case "BLOCKED": return "已阻塞"
        case "CANCELLED": return "已取消"
        default: return status
        }
    }
}

struct MarketResearchQuestionCreateRequest: Codable, Equatable {
    var question: String
    var rationale: String?
    var priority: Int
    var acceptanceCriteria: [String]
    var parentQuestionId: String?

    init(
        question: String,
        rationale: String? = nil,
        priority: Int = 3,
        acceptanceCriteria: [String] = [],
        parentQuestionId: String? = nil
    ) {
        self.question = question
        self.rationale = rationale
        self.priority = priority
        self.acceptanceCriteria = acceptanceCriteria
        self.parentQuestionId = parentQuestionId
    }
}

struct MarketResearchGraphNode: Decodable, Equatable, Identifiable {
    var id: String
    var question: String
    var rationale: String?
    var priority: Int
    var status: String
    var source: String
    var parentQuestionId: String?
    var acceptanceCriteria: [String]
    var expectedEvidence: [String]
    var dependsOnQuestionIds: [String]
    var waitingOnQuestionIds: [String]
    var readiness: String
}

struct MarketResearchGraphEdge: Decodable, Equatable {
    var questionId: String
    var dependsOnQuestionId: String
}

struct MarketResearchGraph: Decodable, Equatable {
    var workspaceId: String
    var nodes: [MarketResearchGraphNode]
    var edges: [MarketResearchGraphEdge]
    var topologicalOrder: [String]
    var readyQuestionIds: [String]
    var cycleDetected: Bool
}

struct MarketResearchGraphExecutionCreate: Codable, Equatable {
    var maxConcurrency: Int?
    var maxQuestions: Int?
    var maxDurationMinutes: Int?
    var maxCost: String?

    init(
        maxConcurrency: Int = 2,
        maxQuestions: Int = 12,
        maxDurationMinutes: Int = 60,
        maxCost: String? = nil
    ) {
        self.maxConcurrency = maxConcurrency
        self.maxQuestions = maxQuestions
        self.maxDurationMinutes = maxDurationMinutes
        self.maxCost = maxCost
    }
}

struct MarketResearchGraphExecutionAccepted: Decodable, Equatable {
    var executionId: String
    var jobId: String
    var workflowStatus: String
    var reused: Bool
}

struct MarketResearchGraphExecutionNode: Decodable, Equatable, Identifiable {
    var questionId: String
    var state: String
    var latestAnswerId: String?
    var latestAnswerStatus: String?
    var jobId: String?
    var error: String?

    var id: String { questionId }
}

struct MarketResearchGraphExecution: Decodable, Equatable, Identifiable {
    var executionId: String
    var workspaceId: String
    var workflowStatus: String
    var executionState: String
    var graphDigest: String
    var maxConcurrency: Int
    var maxQuestions: Int
    var maxDurationMinutes: Int
    var maxCost: String?
    var costEstimate: String?
    var costMeteringAvailable: Bool
    var dispatchedCount: Int
    var completedCount: Int
    var activeCount: Int
    var readyCount: Int
    var waitingReviewCount: Int
    var waitingEvidenceCount: Int
    var failedCount: Int
    var nodeStates: [MarketResearchGraphExecutionNode]
    var stopReason: String?
    var createdAt: Date
    var startedAt: Date?
    var deadlineAt: Date
    var finishedAt: Date?

    var id: String { executionId }

    var stateLabel: String {
        switch executionState.uppercased() {
        case "QUEUED": return "排队中"
        case "READY": return "等待执行"
        case "RUNNING": return "执行中"
        case "WAITING_FOR_REVIEW": return "等待人工审核"
        case "WAITING_FOR_EVIDENCE": return "等待证据"
        case "WAITING_FOR_DEPENDENCY": return "等待前置问题"
        case "COMPLETED": return "全部完成"
        case "PARTIAL": return "部分完成"
        case "FAILED": return "执行失败"
        case "CANCELLED": return "已取消"
        case "BUDGET_EXHAUSTED": return "达到预算上限"
        case "TIMED_OUT": return "执行超时"
        default: return executionState
        }
    }
}

struct MarketQuestionAnswerEvidenceRead: Decodable, Equatable {
    var relationType: String
    var rationale: String?
    var evidence: ResearchEvidence
}

struct MarketQuestionAnswer: Decodable, Equatable, Identifiable {
    var id: String
    var workspaceId: String
    var questionId: String
    var version: Int
    var answerSummary: String
    var assumptions: [String]
    var unknowns: [String]
    var confidenceLevel: String
    var status: String
    var evidenceGap: String?
    var reviewedAt: Date?
    var reviewNote: String?
    var createdAt: Date
    var evidence: [MarketQuestionAnswerEvidenceRead]

    var statusLabel: String {
        switch status.uppercased() {
        case "DRAFT": return "草稿"
        case "NEEDS_REVIEW": return "待审核"
        case "CONFLICTED": return "存在冲突"
        case "ACCEPTED": return "已采纳"
        case "REJECTED": return "已驳回"
        case "SUPERSEDED": return "已被替代"
        default: return status
        }
    }
}

struct MarketQuestionAnswerGenerationAccepted: Decodable, Equatable {
    var jobId: String
    var workflowId: String
    var status: String
    var evidenceCount: Int
    var reused: Bool
}

struct MarketQuestionAnswerReviewRequest: Codable, Equatable {
    var reviewNote: String?
}

struct MarketQuestionAnswerGenerationCreate: Codable, Equatable {
    var evidenceIds: [String]?

    init(evidenceIds: [String]? = nil) {
        self.evidenceIds = evidenceIds
    }
}

struct MarketContextFieldSummary: Equatable, Identifiable {
    var key: String
    var title: String
    var value: String

    var id: String { key }
}

struct MarketContextSnapshotRead: Decodable, Equatable, Identifiable {
    var id: String
    var workspaceId: String
    var version: Int
    var rawInput: String
    var extractedContext: [String: JSONValue]
    var userOverrides: [String: JSONValue]
    var unknowns: [String]
    var provenance: [String: JSONValue]
    var extractionStatus: String
    var extractionPromptId: String?
    var extractionPromptVersion: String?
    var isCurrent: Bool
    var createdByUserId: String?
    var createdAt: Date

    var statusLabel: String {
        switch extractionStatus.uppercased() {
        case "NOT_STARTED": return "待提取"
        case "PENDING": return "提取中"
        case "COMPLETED": return "已提取"
        case "FAILED": return "提取失败"
        default: return extractionStatus
        }
    }

    var extractionError: String? {
        provenance["extraction"]?.objectValue?["error"]?.stringValue
    }

    var fieldSummaries: [MarketContextFieldSummary] {
        extractedContext.keys.sorted().compactMap { key in
            guard let value = extractedContext[key] else { return nil }
            let entries = value.arrayValue?.compactMap { item -> String? in
                if let label = item.objectValue?["label"]?.stringValue { return label }
                return item.stringValue
            } ?? []
            let rendered = entries.isEmpty ? value.displayDescription : entries.joined(separator: " · ")
            guard !rendered.isEmpty, rendered != "[]" else { return nil }
            return MarketContextFieldSummary(
                key: key,
                title: key.replacingOccurrences(of: "_", with: " ").capitalized,
                value: rendered
            )
        }
    }
}

struct MarketEvidenceIntelligenceItem: Decodable, Equatable, Identifiable {
    var evidenceId: String
    var kind: String
    var source: String?
    var canonicalRef: String?
    var sourceHost: String?
    var contentFingerprint: String?
    var fingerprintSource: String
    var verificationStatus: String
    var publishedAt: Date?
    var retrievedAt: Date?
    var ageDays: Int?
    var originAssessment: String
    var duplicateContentEvidenceIds: [String]
    var sameHostEvidenceIds: [String]
    var sameSourceVersionEvidenceIds: [String]
    var derivedPageEvidenceIds: [String]
    var issues: [String]

    var id: String { evidenceId }

    var originLabel: String {
        switch originAssessment {
        case "DUPLICATE_CONTENT": return "内容重复"
        case "SAME_HOST": return "同一主机"
        case "HOST_IDENTIFIED": return "主机已识别"
        default: return "主机未识别"
        }
    }

    var issueLabels: [String] {
        issues.map { issue in
            switch issue {
            case "UNVERIFIED_SOURCE": return "尚未核验"
            case "PARTIALLY_VERIFIED_SOURCE": return "部分核验"
            case "DISPUTED_SOURCE": return "存在争议"
            case "REJECTED_SOURCE": return "已驳回"
            case "MISSING_PUBLICATION_DATE": return "缺少发布日期"
            case "MISSING_STORED_CONTENT_HASH": return "缺少存储内容哈希"
            case "SOURCE_HOST_UNRESOLVED": return "无法识别来源主机"
            case "DUPLICATE_CONTENT": return "与其他来源内容重复"
            case "SAME_SOURCE_MULTIPLE_CONTENT_VERSIONS": return "同一地址存在不同内容版本"
            case "SEARCH_RESULT_NOT_PAGE_EXTRACTED": return "搜索摘要尚未提取网页正文"
            case "PUBLICATION_DATE_IN_FUTURE": return "发布日期晚于当前时间"
            default: return issue
            }
        }
    }
}

struct MarketEvidenceIntelligenceReport: Decodable, Equatable {
    var workspaceId: String
    var generatedAt: Date
    var totalEvidenceCount: Int
    var analyzedEvidenceCount: Int
    var isTruncated: Bool
    var distinctHostCount: Int
    var duplicateContentGroupCount: Int
    var duplicateContentEvidenceCount: Int
    var multiVersionSourceCount: Int
    var multiVersionEvidenceCount: Int
    var unverifiedCount: Int
    var partiallyVerifiedCount: Int
    var verifiedCount: Int
    var disputedCount: Int
    var rejectedCount: Int
    var missingPublicationDateCount: Int
    var missingContentHashCount: Int
    var unextractedSearchResultCount: Int
    var items: [MarketEvidenceIntelligenceItem]
    var caveats: [String]
}



struct MarketSourceVerificationEvidence: Decodable, Equatable, Identifiable {
    var evidenceId: String
    var relationType: String
    var sourceHost: String?
    var contentFingerprint: String?
    var fingerprintSource: String
    var contentChangedSinceAssessment: Bool
    var statusChangedSinceAssessment: Bool
    var sourceVersionChanged: Bool
    var latestSourceEvidenceId: String?
    var unextracted: Bool
    var issues: [String]

    var id: String { evidenceId }
}

struct MarketSourceVerificationClaim: Decodable, Equatable, Identifiable {
    var claimId: String
    var claimLineageId: String?
    var claimVersion: Int?
    var statement: String
    var claimType: String
    var claimStatus: String
    var verificationVerdict: String
    var supportingEvidenceCount: Int
    var contradictingEvidenceCount: Int
    var distinctHostCount: Int
    var distinctContentCount: Int
    var sourceOriginState: String
    var changedSourceCount: Int
    var statusChangedSourceCount: Int
    var unextractedSourceCount: Int
    var evidence: [MarketSourceVerificationEvidence]
    var issues: [String]

    var id: String { claimId }

    var originLabel: String {
        switch sourceOriginState {
        case "NO_SUPPORTING_EVIDENCE": return "缺少支持证据"
        case "SINGLE_SOURCE_ONLY": return "单一来源"
        case "SAME_HOST_ONLY": return "同一主机"
        case "POSSIBLE_SYNDICATION": return "疑似转载"
        case "MULTIPLE_HOSTS_UNCONFIRMED": return "多主机未确认独立"
        case "CONFLICTING_SIGNALS": return "存在冲突"
        default: return sourceOriginState
        }
    }
}

struct MarketClaimLineageVersion: Decodable, Equatable, Identifiable {
    var id: String
    var answerId: String
    var verificationRunId: String
    var claimLineageId: String
    var version: Int
    var statement: String
    var claimType: String
    var verificationVerdict: String
    var confidenceLevel: String
    var status: String
    var supportingEvidenceCount: Int
    var contradictingEvidenceCount: Int
    var reviewedAt: Date?
    var reviewNote: String?
    var createdAt: Date

    var statusLabel: String {
        switch status {
        case "ACCEPTED": return "当前已采纳"
        case "SUPERSEDED": return "已被后续版本替代"
        case "REJECTED": return "已驳回"
        case "CONFLICTED": return "证据冲突"
        case "INSUFFICIENT": return "证据不足"
        default: return "待人工审核"
        }
    }
}

struct MarketClaimLineageHistory: Decodable, Equatable {
    var workspaceId: String
    var claimLineageId: String
    var claimKey: String
    var latestVersion: Int
    var currentAcceptedClaimId: String?
    var versions: [MarketClaimLineageVersion]
}

struct MarketClaimImpact: Decodable, Equatable, Identifiable {
    var claimLineageId: String
    var latestClaimId: String
    var latestClaimVersion: Int
    var latestClaimStatus: String
    var latestVerificationVerdict: String
    var currentAcceptedClaimId: String?
    var consumerType: String
    var consumerId: String
    var consumerTitle: String
    var consumerStatus: String
    var relationTypes: [String]
    var matchedEvidenceCount: Int
    var staleEvidenceCount: Int
    var referencedClaimVersions: [Int]
    var severity: String
    var reasonCode: String

    var id: String { "\(consumerType):\(consumerId):\(claimLineageId)" }

    var consumerTypeLabel: String {
        consumerType == "INSIGHT" ? "市场判断" : "商业机会"
    }

    var severityLabel: String {
        severity == "HIGH" ? "高优先级" : "需要复核"
    }

    var reasonLabel: String {
        switch reasonCode {
        case "LATEST_VERSION_CONTRADICTORY": return "最新主张出现反驳或冲突"
        case "LATEST_VERSION_INSUFFICIENT": return "最新版本证据不足"
        case "LATEST_VERSION_PENDING_REVIEW": return "最新主张版本等待人工审核"
        case "CONSUMER_USES_OLDER_EVIDENCE_SNAPSHOT": return "仍引用旧版本的来源证据"
        case "NO_ACCEPTED_CLAIM_VERSION": return "当前没有已采纳的主张版本"
        default: return reasonCode
        }
    }
}

struct MarketClaimImpactReport: Decodable, Equatable {
    var workspaceId: String
    var generatedAt: Date
    var analyzedLineageCount: Int
    var totalLineageCount: Int
    var affectedConsumerCount: Int
    var impactCount: Int
    var truncated: Bool
    var impacts: [MarketClaimImpact]
}


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


struct MarketSourceVerificationAction: Decodable, Equatable, Identifiable {
    var actionId: String
    var actionType: String
    var priority: String
    var targetType: String
    var targetId: String
    var evidenceIds: [String]
    var claimId: String?
    var title: String
    var rationale: String
    var researchQuestionId: String?
    var researchQuestionStatus: String?

    var id: String { actionId }

    var priorityLabel: String {
        switch priority {
        case "HIGH": return "高优先级"
        case "MEDIUM": return "中优先级"
        default: return "低优先级"
        }
    }
}

struct MarketSourceVerificationPlan: Decodable, Equatable {
    var workspaceId: String
    var generatedAt: Date
    var totalEvidenceCount: Int
    var analyzedEvidenceCount: Int
    var evidenceTruncated: Bool
    var totalClaimCount: Int
    var analyzedClaimCount: Int
    var claimsTruncated: Bool
    var claimsNeedingCorroborationCount: Int
    var claimsWithContradictionsCount: Int
    var claimsWithChangedSourcesCount: Int
    var actionCount: Int
    var actionsTruncated: Bool
    var claims: [MarketSourceVerificationClaim]
    var actions: [MarketSourceVerificationAction]
    var limitations: [String]

    var actionsNeedingResearchTasks: Bool {
        actions.contains {
            $0.researchQuestionId == nil || $0.researchQuestionStatus == "CANCELLED"
        }
    }
}

struct MarketSourceVerificationTask: Decodable, Equatable, Identifiable {
    var actionId: String
    var actionType: String
    var questionId: String
    var question: String
    var questionStatus: String
    var priority: String
    var reused: Bool
    var dependsOnQuestionIds: [String]

    var id: String { actionId }
}

struct MarketSourceVerificationPlanApplication: Decodable, Equatable {
    var workspaceId: String
    var planDigest: String
    var appliedAt: Date
    var actionCount: Int
    var createdQuestionCount: Int
    var reusedQuestionCount: Int
    var dependencyCount: Int
    var tasks: [MarketSourceVerificationTask]
    var reusedApplication: Bool
}
