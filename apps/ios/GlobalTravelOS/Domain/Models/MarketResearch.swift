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
