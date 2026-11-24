import SwiftUI

@MainActor
final class MarketOpportunityWorkspaceViewModel: ObservableObject {
    @Published private(set) var opportunities: [MarketOpportunitySummaryRead] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isLoadingMore = false
    @Published private(set) var isSubmitting = false
    @Published private(set) var nextCursor: String?
    @Published private(set) var statusFilter: String?
    @Published var errorMessage: String?

    let workspaceId: String
    let repository: ResearchRepository

    init(workspaceId: String, repository: ResearchRepository) {
        self.workspaceId = workspaceId
        self.repository = repository
    }

    var statusFilterLabel: String {
        guard let statusFilter else { return "全部机会" }
        return MarketOpportunityStatusLabel.label(for: statusFilter)
    }

    func load(reset: Bool = true) async {
        guard !isLoading, !isLoadingMore else { return }
        if reset {
            isLoading = true
        } else {
            guard nextCursor != nil else { return }
            isLoadingMore = true
        }
        errorMessage = nil
        defer {
            isLoading = false
            isLoadingMore = false
        }

        do {
            let page = try await repository.listMarketOpportunities(
                workspaceId: workspaceId,
                page: PageRequest(limit: 40, cursor: reset ? nil : nextCursor),
                statusFilter: statusFilter
            )
            if reset {
                opportunities = page.items
            } else {
                let existing = Set(opportunities.map(\.id))
                opportunities.append(contentsOf: page.items.filter { !existing.contains($0.id) })
            }
            nextCursor = page.nextCursor
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setStatusFilter(_ value: String?) async {
        guard statusFilter != value else { return }
        statusFilter = value
        nextCursor = nil
        opportunities = []
        await load()
    }

    func createOpportunity(title: String, description: String, nextAction: String) async -> Bool {
        guard !isSubmitting else { return false }
        let normalizedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedTitle.isEmpty else {
            errorMessage = "机会标题不能为空。"
            return false
        }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }

        do {
            _ = try await repository.createMarketOpportunity(
                workspaceId: workspaceId,
                request: MarketOpportunityCreateRequest(
                    title: normalizedTitle,
                    description: description.nilIfBlank,
                    nextAction: nextAction.nilIfBlank
                )
            )
            if statusFilter != nil && statusFilter != "DISCOVERED" {
                statusFilter = nil
            }
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

@MainActor
final class MarketOpportunityDetailViewModel: ObservableObject {
    @Published private(set) var detail: MarketOpportunityDetailRead?
    @Published private(set) var workspaceEvidence: [ResearchEvidence] = []
    @Published private(set) var execution: MarketOpportunityExecutionRead?
    @Published private(set) var isLoading = false
    @Published private(set) var isSubmitting = false
    @Published var errorMessage: String?

    let workspaceId: String
    let opportunityId: String
    let repository: ResearchRepository

    init(workspaceId: String, opportunityId: String, repository: ResearchRepository) {
        self.workspaceId = workspaceId
        self.opportunityId = opportunityId
        self.repository = repository
    }

    var availableEvidence: [ResearchEvidence] {
        let linked = Set(detail?.evidence.map { $0.evidence.id } ?? [])
        return workspaceEvidence.filter { !linked.contains($0.id) }
    }

    var hasSupportingEvidence: Bool {
        detail?.evidence.contains { $0.relationType.uppercased() == "SUPPORTS" } ?? false
    }

    var hasContradictingEvidence: Bool {
        detail?.evidence.contains { $0.relationType.uppercased() == "CONTRADICTS" } ?? false
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        async let detailRequest = repository.marketOpportunity(
            workspaceId: workspaceId,
            opportunityId: opportunityId
        )
        async let evidenceRequest = repository.listMarketEvidence(
            workspaceId: workspaceId,
            page: PageRequest(limit: 60)
        )
        async let executionRequest = repository.marketOpportunityExecution(
            workspaceId: workspaceId,
            opportunityId: opportunityId
        )

        do {
            detail = try await detailRequest
        } catch {
            errorMessage = error.localizedDescription
        }
        do {
            let evidencePage = try await evidenceRequest
            workspaceEvidence = evidencePage.items
        } catch {
            if errorMessage == nil { errorMessage = error.localizedDescription }
        }
        do {
            execution = try await executionRequest
        } catch {
            if errorMessage == nil { errorMessage = error.localizedDescription }
        }
    }

    func addEvidence(_ evidence: ResearchEvidence, relationType: String, rationale: String) async -> Bool {
        guard !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            _ = try await repository.addMarketOpportunityEvidence(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                request: MarketOpportunityEvidenceCreateRequest(
                    evidenceId: evidence.id,
                    relationType: relationType,
                    rationale: rationale.nilIfBlank
                )
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func transition(to status: String, note: String? = nil) async -> Bool {
        guard !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            _ = try await repository.transitionMarketOpportunity(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                request: MarketOpportunityTransitionRequest(
                    targetStatus: status,
                    note: note?.nilIfBlank
                )
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func qualify(reason: String) async -> Bool {
        guard !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            _ = try await repository.qualifyMarketOpportunity(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                request: MarketOpportunityQualifyRequest(
                    qualificationReason: reason.trimmingCharacters(in: .whitespacesAndNewlines)
                )
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func recordOutcome(_ outcome: String, note: String) async -> Bool {
        guard !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            _ = try await repository.recordMarketOpportunityOutcome(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                request: MarketOpportunityOutcomeRequest(
                    outcome: outcome,
                    outcomeNote: note.trimmingCharacters(in: .whitespacesAndNewlines)
                )
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func linkVisit(_ candidate: MarketOpportunityVisitCandidateRead, note: String) async -> Bool {
        guard !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            _ = try await repository.linkMarketOpportunityVisit(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                request: MarketOpportunityVisitLinkCreate(
                    tripId: candidate.tripId,
                    visitId: candidate.visitId,
                    linkNote: note.nilIfBlank
                )
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func unlinkVisit(_ link: MarketOpportunityVisitLinkRead) async -> Bool {
        guard link.isLinked, !isSubmitting else { return false }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }
        do {
            _ = try await repository.unlinkMarketOpportunityVisit(
                workspaceId: workspaceId,
                opportunityId: opportunityId,
                visitId: link.visitId,
                unlinkNote: "用户从商业机会工作台解除关联"
            )
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

enum MarketOpportunityStatusLabel {
    static func label(for status: String) -> String {
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
