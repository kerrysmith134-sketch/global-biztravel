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
/// 一条被识别的信息（PRD 页面 07 §8.2）。`requiresConfirmation` 由服务端强制：
/// 除 SUMMARY 外一律 true，客户端只展示、不改写。
struct MinutesInsight: Codable, Equatable, Identifiable {
    var kind: String
    var text: String
    var requiresConfirmation: Bool

    var id: String { "\(kind):\(text.prefix(24))" }

    var kindLabel: String {
        switch kind.uppercased() {
        case "SUMMARY": return "会议摘要"
        case "CUSTOMER_NEED": return "客户需求"
        case "OPPORTUNITY_SIGNAL": return "商机信号"
        case "DEAL_TERM": return "交易条件"
        case "COMMITMENT": return "商业承诺"
        case "STAGE_CHANGE": return "商机阶段变化"
        default: return kind
        }
    }

    var confirmLabel: String { requiresConfirmation ? "需人工确认" : "可作草稿" }
}

struct MinutesDraft: Codable, Equatable, Identifiable {
    var draftId: String
    var llmInvocationId: String?
    var title: String
    var summary: String
    var decisions: [String]?
    var followUpTasks: [FollowUpTask]?
    var insights: [MinutesInsight]?
    var degraded: Bool?

    init(
        draftId: String,
        llmInvocationId: String? = nil,
        title: String,
        summary: String,
        decisions: [String]? = nil,
        followUpTasks: [FollowUpTask]? = nil,
        insights: [MinutesInsight]? = nil,
        degraded: Bool? = nil
    ) {
        self.draftId = draftId
        self.llmInvocationId = llmInvocationId
        self.title = title
        self.summary = summary
        self.decisions = decisions
        self.followUpTasks = followUpTasks
        self.insights = insights
        self.degraded = degraded
    }

    var id: String { draftId }

    var decisionList: [String] { decisions ?? [] }
    var taskList: [FollowUpTask] { followUpTasks ?? [] }
    var insightList: [MinutesInsight] { insights ?? [] }

    /// 待人工确认的条目数——PRD：不得把 AI 推断当已确认事实。
    var pendingConfirmationCount: Int {
        insightList.filter(\.requiresConfirmation).count
    }

    var sourceLabel: String {
        (degraded ?? false) ? "模板兜底（LLM 不可用）" : "模型草稿"
    }
}
