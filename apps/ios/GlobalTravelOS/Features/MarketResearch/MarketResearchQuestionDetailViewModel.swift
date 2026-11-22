import SwiftUI

@MainActor
final class MarketResearchQuestionDetailViewModel: ObservableObject {
    @Published private(set) var plan: ResearchPlan?
    @Published private(set) var answers: [MarketQuestionAnswer] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isSubmitting = false
    @Published var queryText = ""
    @Published var reviewNote = ""
    @Published var errorMessage: String?
    @Published var informationMessage: String?

    let workspaceId: String
    let question: MarketResearchQuestion
    let repository: ResearchRepository

    init(workspaceId: String, question: MarketResearchQuestion, repository: ResearchRepository) {
        self.workspaceId = workspaceId
        self.question = question
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let plans = try await repository.listWorkspacePlans(
                workspaceId: workspaceId,
                targetQuestionId: question.id
            )
            answers = try await repository.answers(
                workspaceId: workspaceId,
                questionId: question.id
            )
            if let latest = plans.first {
                setPlan(latest)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createPlan() async {
        guard !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        errorMessage = nil
        do {
            let requestText = [
                question.question,
                question.rationale.map { "研究背景：\($0)" },
                question.acceptanceCriteria.isEmpty
                    ? nil
                    : "验收标准：\(question.acceptanceCriteria.joined(separator: "；"))",
                question.expectedEvidence.isEmpty
                    ? nil
                    : "期望证据：\(question.expectedEvidence.joined(separator: "；"))"
            ]
            .compactMap { $0 }
            .joined(separator: "\n")
            let created = try await repository.createPlan(
                ResearchPlanCreateRequest(
                    request: requestText,
                    sourceScope: .externalOnly,
                    workspaceId: workspaceId,
                    targetQuestionId: question.id
                )
            )
            setPlan(created)
            informationMessage = "研究计划已生成。检查并编辑查询后，再确认外部检索。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func executeSearch() async {
        guard let plan, !isSubmitting else { return }
        let queries = queryText.lines
        guard !queries.isEmpty else {
            errorMessage = "至少保留一条有效搜索查询"
            return
        }
        isSubmitting = true
        defer { isSubmitting = false }
        errorMessage = nil
        informationMessage = nil
        do {
            let accepted = try await repository.executePlan(id: plan.id, searchQueries: queries)
            self.plan = try await repository.plan(id: plan.id)
            informationMessage = accepted.reused
                ? "相同查询的检索任务已存在，正在复用。"
                : "检索任务已入队；当前只代表任务受理，不代表来源已采集。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshPlan() async {
        guard let current = plan else { return }
        do {
            setPlan(try await repository.plan(id: current.id), preserveQueries: true)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func draftAnswer() async {
        guard !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let accepted = try await repository.draftAnswer(
                workspaceId: workspaceId,
                questionId: question.id
            )
            informationMessage = "答案草稿任务已受理，\(accepted.evidenceCount) 条问题级证据将进入分析。"
            answers = try await repository.answers(workspaceId: workspaceId, questionId: question.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func review(_ answer: MarketQuestionAnswer, accept: Bool) async {
        guard !isSubmitting else { return }
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let note = reviewNote.nilIfBlank
            let result = if accept {
                try await repository.acceptAnswer(
                    workspaceId: workspaceId,
                    questionId: question.id,
                    answerId: answer.id,
                    reviewNote: note
                )
            } else {
                try await repository.rejectAnswer(
                    workspaceId: workspaceId,
                    questionId: question.id,
                    answerId: answer.id,
                    reviewNote: note
                )
            }
            if let index = answers.firstIndex(where: { $0.id == result.id }) {
                answers[index] = result
            } else {
                answers.insert(result, at: 0)
            }
            reviewNote = ""
            informationMessage = accept
                ? "答案已采纳；只有此时依赖节点才会被释放。"
                : "答案已驳回；研究图不会把它视为已完成。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func setPlan(_ value: ResearchPlan, preserveQueries: Bool = false) {
        let isNewPlan = plan?.id != value.id
        plan = value
        if !preserveQueries || isNewPlan || queryText.isEmpty {
            let queries = value.reviewedSearchQueries.isEmpty
                ? value.plan.searchQueries
                : value.reviewedSearchQueries
            queryText = queries.joined(separator: "\n")
        }
    }
}
