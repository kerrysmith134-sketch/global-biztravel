import Foundation

/// `POST /v1/visits/{visit_id}/minutes:draft` request body.
struct MinutesDraftRequest: Codable, Equatable {
    var tone: String

    init(tone: String = "PROFESSIONAL") {
        self.tone = tone
    }
}

/// `POST /v1/visits/{visit_id}/minutes:draft` response.
///
/// `degraded=true` means the LLM was unavailable and the backend filled the draft from a
/// template. The screen shows that explicitly instead of passing template text off as model output.
struct MinutesDraft: Codable, Equatable, Identifiable {
    var draftId: String
    var llmInvocationId: String?
    var title: String
    var summary: String
    var decisions: [String]?
    var followUpTasks: [FollowUpTask]?
    var degraded: Bool?

    init(
        draftId: String,
        llmInvocationId: String? = nil,
        title: String,
        summary: String,
        decisions: [String]? = nil,
        followUpTasks: [FollowUpTask]? = nil,
        degraded: Bool? = nil
    ) {
        self.draftId = draftId
        self.llmInvocationId = llmInvocationId
        self.title = title
        self.summary = summary
        self.decisions = decisions
        self.followUpTasks = followUpTasks
        self.degraded = degraded
    }

    var id: String { draftId }

    var decisionList: [String] { decisions ?? [] }
    var taskList: [FollowUpTask] { followUpTasks ?? [] }

    var sourceLabel: String {
        (degraded ?? false) ? "模板兜底（LLM 不可用）" : "模型草稿"
    }
}
