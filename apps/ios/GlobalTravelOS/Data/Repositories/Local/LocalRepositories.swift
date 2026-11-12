import Foundation

/// 本地模式骨架仓储集合（P1 地基，ADR-0036）。
///
/// 每个类型实现对应协议；已能本地化的方法真实实现，其余显式抛
/// `LocalStorePendingError` 并随 P2–P7 阶段替换——绝不静默伪造数据。

// MARK: - PlanRepository（P2 充实：进程内求解）

final class LocalPlanRepository: PlanRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func listPlans(tripId: String, page: PageRequest) async throws -> Page<PlanVersion> {
        let payloads = try database.loadPayloads(
            table: "plan_versions",
            where: "trip_id = ?",
            bindings: [tripId],
            order: "created_at DESC"
        )
        let plans = try payloads.map { try DateCoding.decoder().decode(PlanVersion.self, from: $0) }
        return Page(items: Array(plans.prefix(page.limit)))
    }

    func planDetail(tripId: String, planId: String) async throws -> PlanVersionDetail {
        let payloads = try database.loadPayloads(
            table: "plan_versions", where: "id = ?", bindings: [planId]
        )
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "PLAN_NOT_FOUND", message: "计划版本不存在", traceId: nil, details: [:])
        }
        return try DateCoding.decoder().decode(PlanVersionDetail.self, from: payload)
    }

    func planDiff(tripId: String, planId: String, against: String) async throws -> PlanDiff {
        throw LocalStorePendingError(method: "planDiff（P2）")
    }

    /// 本地激活：版本令牌校验 + 置 ACTIVE + 行程回写 active_plan_id。
    func activatePlan(tripId: String, planId: String, tripVersion: Int) async throws -> PlanActivation {
        let tripPayloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let tripPayload = tripPayloads.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        var trip = try DateCoding.decoder().decode(Trip.self, from: tripPayload)
        guard trip.version == tripVersion else {
            throw APIError.serverError(
                code: "VERSION_CONFLICT",
                message: "出行版本已变化（本地 \(trip.version) ≠ 请求 \(tripVersion)）",
                traceId: nil,
                details: [:]
            )
        }
        let planPayloads = try database.loadPayloads(table: "plan_versions", where: "id = ?", bindings: [planId])
        guard let planPayload = planPayloads.first else {
            throw APIError.serverError(code: "PLAN_NOT_FOUND", message: "计划版本不存在", traceId: nil, details: [:])
        }
        var plan = try DateCoding.decoder().decode(PlanVersionDetail.self, from: planPayload)

        plan.status = .active
        trip.status = "ACTIVE"
        trip.version += 1
        trip.activePlanVersionId = planId

        try database.transaction {
            try database.upsert(
                table: "plan_versions", id: planId,
                columns: ["status": "ACTIVE"],
                payload: try DateCoding.encoder().encode(plan)
            )
            try database.upsert(
                table: "trips", id: trip.id,
                columns: [
                    "status": trip.status ?? "ACTIVE",
                    "version": trip.version,
                    "active_plan_id": trip.activePlanVersionId,
                    "updated_at": SQLTimestamp.basics.string(from: Date()),
                ],
                payload: try DateCoding.encoder().encode(trip)
            )
        }
        return PlanActivation(
            tripId: tripId,
            activePlanVersionId: planId,
            tripVersion: trip.version,
            previousPlanVersionId: nil,
            activatedAt: Date()
        )
    }

    /// 本地求解（P2 接入 Swift 规划引擎）；P1 阶段显式未就绪。
    func generatePlan(tripId: String, request: GeneratePlanRequest) async throws -> GeneratePlanResponse {
        throw LocalStorePendingError(method: "generatePlan（P2 接入本地规划引擎）")
    }

    func job(id: String) async throws -> PlanJob {
        let payloads = try database.loadPayloads(table: "plan_versions", where: "id = ?", bindings: [id])
        guard payloads.first != nil else {
            throw APIError.serverError(code: "JOB_NOT_FOUND", message: "任务不存在", traceId: nil, details: [:])
        }
        throw LocalStorePendingError(method: "job（P2）")
    }

    func createPlanChange(tripId: String, request: PlanChangeCreate) async throws -> PlanChangeAccepted {
        throw LocalStorePendingError(method: "createPlanChange（P7）")
    }
}

// MARK: - VisitRepository（P1：列表/记录本地化；纪要/CRM P5）

final class LocalVisitRepository: VisitRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func listVisits(tripId: String) async throws -> [TripVisit] {
        let payloads = try database.loadPayloads(
            table: "visits", where: "trip_id = ?", bindings: [tripId], order: "created_at"
        )
        return try payloads.map { try DateCoding.decoder().decode(TripVisit.self, from: $0) }
    }

    /// 现场记录：clientRecordId 幂等写入本地（LWW 冲突矩阵 P3 补）。
    func submitRecord(visitId: String, request: VisitRecordRequest) async throws -> VisitRecord {
        let recordId = UUID().uuidString
        // 本地记录即最终事实（服务端 serverVersion 概念不适用）。
        let record = VisitRecord(id: recordId, visitId: visitId, outcome: request.outcome)
        try database.upsert(
            table: "visit_records",
            id: record.id,
            columns: [
                "visit_id": visitId,
                "client_record_id": request.clientRecordId,
            ],
            payload: try DateCoding.encoder().encode(record)
        )
        return record
    }

    func draftMinutes(visitId: String) async throws -> MinutesDraft {
        throw LocalStorePendingError(method: "draftMinutes（P5 接入 LLMService）")
    }

    func syncToCRM(recordId: String) async throws -> CrmSyncJob {
        throw LocalStorePendingError(method: "syncToCRM")
    }

    func crmSyncJob(id: String) async throws -> CrmSyncJob {
        throw LocalStorePendingError(method: "crmSyncJob")
    }

    func briefing(visitId: String) async throws -> VisitBriefing {
        throw LocalStorePendingError(method: "briefing（P7）")
    }

    func updateObjective(visitId: String, objective: String?) async throws -> TripVisit {
        throw LocalStorePendingError(method: "updateObjective（P7）")
    }
}

// MARK: - ProactiveRepository（P3 充实：规则扫描/审批；P1 先读本地表）

final class LocalProactiveRepository: ProactiveRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func suggestions(
        tripId: String?,
        status: String?,
        limit: Int,
        cursor: String?
    ) async throws -> SuggestionFeed {
        var clause = "1 = 1"
        var bindings: [Any?] = []
        if let tripId {
            clause += " AND trip_id = ?"
            bindings.append(tripId)
        }
        if let status {
            clause += " AND status = ?"
            bindings.append(status)
        }
        let payloads = try database.loadPayloads(
            table: "proactive_suggestions", where: clause, bindings: bindings,
            order: "created_at DESC", limit: limit
        )
        let items = try payloads.map { try DateCoding.decoder().decode(ProactiveSuggestion.self, from: $0) }
        return SuggestionFeed(items: items, dailyCap: nil, nextCursor: nil)
    }

    func approve(suggestionId: String) async throws -> SuggestionApprovalResponse {
        throw LocalStorePendingError(method: "approve（P3 审批状态机）")
    }

    func dismiss(suggestionId: String) async throws -> SuggestionDismissResponse {
        throw LocalStorePendingError(method: "dismiss（P3 审批状态机）")
    }
}

// MARK: - ActionCenterRepository（P3 充实聚合；P1 返回空视图）

final class LocalActionCenterRepository: ActionCenterRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func actionCenter(tripId: String?) async throws -> ActionCenter {
        // 行动中心 = 已有事实的聚合视图（ADR-0020）：P1 先返回真实的空态，P3 接入聚合。
        return ActionCenter(generatedAt: Date(), total: 0, counts: nil, sections: [])
    }
}

// MARK: - SyncTransport（本地模式无需推送；outbox 保留作草稿队列）

struct LocalSyncTransport: SyncTransport {
    func push(_ request: SyncPushRequest) async throws -> SyncPushResult {
        SyncPushResult(results: [], summary: SyncSummary(applied: 0, conflict: 0, rejected: 0))
    }
}
