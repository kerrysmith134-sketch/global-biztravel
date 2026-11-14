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
        try LocalAggregations.planDiff(
            database: database, tripId: tripId, basePlanId: against, targetPlanId: planId
        )
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

    /// 本地求解：进程内跑规划引擎（启发式），同步产出计划版本 + 已完成 job。
    /// GeneratePlanUseCase 的轮询逻辑对"立即 SUCCEEDED 的 job"零改动兼容。
    func generatePlan(tripId: String, request: GeneratePlanRequest) async throws -> GeneratePlanResponse {
        let trips = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let tripPayload = trips.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try DateCoding.decoder().decode(Trip.self, from: tripPayload)
        let visitPayloads = try database.loadPayloads(
            table: "visits", where: "trip_id = ?", bindings: [tripId]
        )
        let visits = try visitPayloads.map { try DateCoding.decoder().decode(TripVisit.self, from: $0) }
        let placePayloads = try database.loadPayloads(table: "places")
        let places = try placePayloads.map { try DateCoding.decoder().decode(Place.self, from: $0) }

        let problem = EngineMapping.makeProblem(
            trip: trip, visits: visits, places: places,
            budgetSeconds: request.solverBudgetSeconds ?? 10
        )
        guard !problem.stops.isEmpty else {
            throw APIError.serverError(
                code: "NO_STOPS", message: "没有可规划的拜访（先添加带地点的拜访）",
                traceId: nil, details: [:]
            )
        }

        let result = try EnginePipeline.planTrip(problem, solver: .heuristic)
        let planId = UUID().uuidString
        let versionNumber = try database.count(table: "plan_versions", where: "trip_id = ?", bindings: [tripId]) + 1
        let now = Date()

        // 摘要行（列表用）+ 详情 payload（详情页用）同写。
        let summary = PlanVersion(
            id: planId,
            versionNumber: versionNumber,
            status: .draft,
            solver: result.solver.rawValue,
            feasible: result.validation.feasible,
            degraded: result.degraded,
            objectiveValue: result.objectiveValue,
            solutionStatus: result.status.rawValue,
            costTotal: nil,
            costCurrency: trip.currency,
            costUnknownCount: 0,
            policyViolations: nil,
            stopCount: result.placedVisitIds.count,
            dayCount: result.days.count,
            createdAt: now
        )
        let detailDays: [PlanDay] = EngineMapping.makePlanDays(
            from: result.days, places: places, visits: visits
        )
        // StopPlan 与 visit 对齐后重映射会丢序号 → 直接用 makePlanDays 输出。
        let detail = PlanVersionDetail(
            id: planId,
            tripId: tripId,
            versionNumber: versionNumber,
            status: .draft,
            solver: result.solver.rawValue,
            feasible: result.validation.feasible,
            degraded: result.degraded,
            objectiveValue: result.objectiveValue,
            problemHash: result.problemHash,
            createdAt: now,
            validation: EngineMapping.makeValidationReport(from: result.validation),
            unplaced: EngineMapping.makeUnplaced(
                from: result.unplaced, places: places, visits: visits
            ),
            days: detailDays,
            legs: EngineMapping.makeLegs(from: result.legs),
            solutionStatus: result.status.rawValue,
            costTotal: nil,
            costCurrency: trip.currency,
            costUnknownCount: 0,
            policyViolations: nil
        )
        try database.transaction {
            try database.upsert(
                table: "plan_versions", id: planId,
                columns: [
                    "trip_id": tripId,
                    "version": versionNumber,
                    "status": summary.status?.rawValue ?? "DRAFT",
                    "created_at": SQLTimestamp.basics.string(from: now),
                ],
                payload: try DateCoding.encode(detail)
            )
            // 合成 job（本地模式无后台队列）：GeneratePlanUseCase 轮询立即命中 SUCCEEDED。
            let jobPayload: [String: Any] = [
                "id": "local-job-\(planId)",
                "job_type": "plan_trip",
                "status": "SUCCEEDED",
                "plan_version_id": planId,
            ]
            let jobData = try JSONSerialization.data(withJSONObject: jobPayload)
            try database.setKeyValue(
                "local-job:\(planId)", value: String(decoding: jobData, as: UTF8.self)
            )
        }

        return GeneratePlanResponse(jobId: "local-job-\(planId)", status: .succeeded)
    }

    func job(id: String) async throws -> PlanJob {
        let rows = try database.store.query(
            "SELECT value FROM kv WHERE key = ?", bindings: [id]
        ) { $0.string(0) }
        guard let raw = rows.first,
              let object = try? JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any] else {
            throw APIError.serverError(code: "JOB_NOT_FOUND", message: "任务不存在", traceId: nil, details: [:])
        }
        return PlanJob(
            id: id,
            jobType: object["job_type"] as? String,
            status: JobStatus(rawValue: object["status"] as? String ?? "UNKNOWN"),
            attempts: 1,
            result: JobResult(planVersionId: object["plan_version_id"] as? String),
            lastError: nil,
            createdAt: nil,
            updatedAt: nil
        )
    }

    func createPlanChange(tripId: String, request: PlanChangeCreate) async throws -> PlanChangeAccepted {
        throw LocalStorePendingError(method: "createPlanChange（P7）")
    }
}

// MARK: - VisitRepository（P1：列表/记录本地化；纪要/CRM P5）

final class LocalVisitRepository: VisitRepository {
    let database: LocalDatabase
    var environmentSettings: AppSettings?

    init(database: LocalDatabase, settings: AppSettings? = nil) {
        self.database = database
        self.environmentSettings = settings
    }

    func listVisits(tripId: String) async throws -> [TripVisit] {
        let payloads = try database.loadPayloads(
            table: "visits", where: "trip_id = ?", bindings: [tripId], order: "id"
        )
        return try payloads.map { try DateCoding.decoder().decode(TripVisit.self, from: $0) }
    }

    /// 现场记录：clientRecordId 幂等写入本地（LWW 冲突矩阵 P3 补）。
    func createVisit(tripId: String, request: VisitCreateRequest) async throws -> TripVisit {
        var placeId = request.placeId
        if placeId == nil {
            let newPlaceId = UUID().uuidString
            let place = Place(
                id: newPlaceId,
                name: request.placeName ?? request.title,
                countryCode: request.countryCode?.uppercased(),
                timezone: request.timezone,
                latitude: request.latitude,
                longitude: request.longitude,
                city: request.city
            )
            try database.upsert(
                table: "places", id: newPlaceId,
                columns: ["name": place.name, "city": place.city],
                payload: try DateCoding.encode(place)
            )
            placeId = newPlaceId
        }
        let visit = TripVisit(
            id: UUID().uuidString,
            tripId: tripId,
            placeId: placeId,
            title: request.title,
            objective: nil,
            serviceMinutes: request.serviceMinutes,
            timeWindowStart: nil,
            timeWindowEnd: nil,
            priority: request.priority,
            movementMode: request.movementMode,
            status: "PLANNED",
            createdAt: Date()
        )
        try database.upsert(
            table: "visits", id: visit.id,
            columns: [
                "trip_id": tripId, "place_id": placeId, "title": visit.title,
                "status": "PLANNED",
                "lat": request.latitude, "lon": request.longitude,
            ],
            payload: try DateCoding.encode(visit)
        )
        return visit
    }

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

    /// 纪要草稿：LLM 直连（设置页配置）或离线模板降级——功能不断。
    func draftMinutes(visitId: String) async throws -> MinutesDraft {
        let visitRows = try database.loadPayloads(
            table: "visits", where: "id = ?", bindings: [visitId]
        )
        guard let visitPayload = visitRows.first,
              let visit = try? DateCoding.decoder().decode(TripVisit.self, from: visitPayload) else {
            throw APIError.serverError(code: "NOT_FOUND", message: "拜访不存在", traceId: nil, details: [:])
        }
        // 找最近一条现场记录作输入（本地模式：records 表）。
        let recordPayloads = (try? database.loadPayloads(
            table: "visit_records", where: "visit_id = ?", bindings: [visitId]
        )) ?? []
        var notes = ""
        var followUps: [String] = []
        for payload in recordPayloads {
            if let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] {
                notes = (object["notes"] as? String) ?? notes
                if let tasks = object["follow_up_tasks"] as? [[String: Any]] {
                    followUps.append(contentsOf: tasks.compactMap { $0["title"] as? String })
                }
            }
        }

        let settings = environmentSettings
        let service = settings.flatMap { LLMService.fromSettings($0) }
        var output: [String: Any]
        var degraded = true
        if let service {
            do {
                output = try LLMService.completeJson(
                    service: service,
                    systemPrompt: LLMPrompts.meetingMinutesSystem,
                    userPayload: [
                        "visit_id": visitId,
                        "visit_title": visit.title ?? "",
                        "outcome": "MET",
                        "notes": notes,
                        "follow_up_tasks": followUps,
                        "tone": "professional",
                    ]
                )
                degraded = false
            } catch {
                output = LLMDegrade.fallbackMinutes(notes: notes, followUps: followUps)
            }
        } else {
            output = LLMDegrade.fallbackMinutes(notes: notes, followUps: followUps)
        }

        let draftId = UUID().uuidString
        let now = Date()
        var insights = (output["insights"] as? [[String: Any]]) ?? []
        // 非 SUMMARY 洞察强制人工确认（服务端语义）。
        insights = insights.map { insight in
            var item = insight
            if let kind = item["kind"] as? String, kind != "SUMMARY" {
                item["requires_confirmation"] = true
            }
            return item
        }
        var body: [String: Any] = [
            "id": draftId,
            "visit_id": visitId,
            "title": output["title"] as? String ?? "会议纪要",
            "summary": output["summary"] as? String ?? "",
            "decisions": output["decisions"] ?? [],
            "follow_up_tasks": output["follow_up_tasks"] ?? [],
            "insights": insights,
            "degraded": degraded,
            "created_at": SQLTimestamp.basics.string(from: now),
        ]
        if degraded { body["degraded_note"] = "LLM 未配置或失败，已用离线模板生成" }

        try database.setKeyValue(
            "minutes:\(visitId)",
            value: String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
        )
        return MinutesDraft(
            draftId: draftId,
            llmInvocationId: nil,
            title: body["title"] as? String ?? "会议纪要",
            summary: body["summary"] as? String ?? "",
            decisions: output["decisions"] as? [String],
            followUpTasks: followUps.map { FollowUpTask(title: $0, dueDate: nil) },
            insights: insights.compactMap { insight in
                guard let text = insight["text"] as? String,
                      let kind = insight["kind"] as? String else { return nil }
                let requiresConfirmation = (insight["requires_confirmation"] as? Bool) ?? (kind != "SUMMARY")
                return MinutesInsight(kind: kind, text: text, requiresConfirmation: requiresConfirmation)
            },
            degraded: degraded
        )
    }

    func syncToCRM(recordId: String) async throws -> CrmSyncJob {
        throw LocalStorePendingError(method: "syncToCRM")
    }

    func crmSyncJob(id: String) async throws -> CrmSyncJob {
        throw LocalStorePendingError(method: "crmSyncJob")
    }

    func briefing(visitId: String) async throws -> VisitBriefing {
        try LocalAggregations.visitBriefing(database: database, visitId: visitId)
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
        // 扫描与读取一体：幂等去重保证重复扫描不产生重复建议。
        LocalProactiveEngine.scan(database: database, userId: "local-user")
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

    /// 本地审批：单用户会签阈值恒为 1（本地无组织策略），批准即受控执行（只准备式）。
    func approve(suggestionId: String) async throws -> SuggestionApprovalResponse {
        let payloads = try database.loadPayloads(
            table: "proactive_suggestions", where: "id = ?", bindings: [suggestionId]
        )
        guard let payload = payloads.first,
              let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
              let type = object["suggestion_type"] as? String else {
            throw APIError.serverError(code: "NOT_FOUND", message: "主动建议不存在", traceId: nil, details: [:])
        }
        let status = object["status"] as? String ?? "PENDING"
        if status == "APPROVED" || status == "EXECUTED" {
            return SuggestionApprovalResponse(id: suggestionId, status: status)
        }
        guard status == "PENDING" else {
            throw APIError.serverError(
                code: "CONFLICT", message: "该建议当前不可批准（状态 \(status)）",
                traceId: nil, details: [:]
            )
        }

        // 执行器（本地只准备，不对外）：建签证案草稿 / 标记准备完成。
        let tripId = object["trip_id"] as? String
        var execution: [String: Any] = ["status": "PREPARED"]
        if type == "VISA_REQUIREMENT_REMINDER", let tripId,
           let countries = (try? database.loadPayloads(
               table: "trips", where: "id = ?", bindings: [tripId]
           ))?.first
               .flatMap({ try? DateCoding.decoder().decode(Trip.self, from: $0) })?.countries {
            for code in countries {
                let caseId = UUID().uuidString
                let casePayload: [String: Any] = [
                    "id": caseId,
                    "trip_id": tripId,
                    "country": code,
                    "status": "NEW",
                    "note": "MANUAL_REVIEW_REQUIRED：按官方渠道核验签证要求",
                    "created_at": SQLTimestamp.basics.string(from: Date()),
                ]
                try? database.upsert(
                    table: "visa_cases", id: caseId,
                    columns: [
                        "trip_id": tripId, "country": code, "status": "NEW",
                        "created_at": SQLTimestamp.basics.string(from: Date()),
                    ],
                    payload: try JSONSerialization.data(withJSONObject: casePayload)
                )
                execution["visa_cases"] = (execution["visa_cases"] as? [String]) ?? [] + [caseId]
            }
        }

        var updated = object
        updated["status"] = "EXECUTED"
        updated["execution_result"] = execution
        updated["executed_at"] = SQLTimestamp.basics.string(from: Date())
        updated["approved_by"] = "local-user"
        try database.upsert(
            table: "proactive_suggestions", id: suggestionId,
            columns: ["status": "EXECUTED"],
            payload: try JSONSerialization.data(withJSONObject: updated)
        )
        return SuggestionApprovalResponse(id: suggestionId, status: "EXECUTED")
    }

    /// 驳回：DISMISSED + 负反馈折算（重复驳回幂等）。
    func dismiss(suggestionId: String) async throws -> SuggestionDismissResponse {
        let payloads = try database.loadPayloads(
            table: "proactive_suggestions", where: "id = ?", bindings: [suggestionId]
        )
        guard let payload = payloads.first,
              let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            throw APIError.serverError(code: "NOT_FOUND", message: "主动建议不存在", traceId: nil, details: [:])
        }
        let status = object["status"] as? String ?? "PENDING"
        if status == "DISMISSED" {
            return SuggestionDismissResponse(id: suggestionId, status: "DISMISSED")
        }
        guard status == "PENDING" else {
            throw APIError.serverError(
                code: "CONFLICT", message: "该建议当前不可驳回（状态 \(status)）",
                traceId: nil, details: [:]
            )
        }
        var updated = object
        updated["status"] = "DISMISSED"
        updated["dismissed_at"] = SQLTimestamp.basics.string(from: Date())
        try database.upsert(
            table: "proactive_suggestions", id: suggestionId,
            columns: ["status": "DISMISSED"],
            payload: try JSONSerialization.data(withJSONObject: updated)
        )
        return SuggestionDismissResponse(id: suggestionId, status: "DISMISSED")
    }
}

// MARK: - ActionCenterRepository（P3 充实聚合；P1 返回空视图）

final class LocalActionCenterRepository: ActionCenterRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func actionCenter(tripId: String?) async throws -> ActionCenter {
        // 行动中心 = 已有事实的聚合视图（ADR-0020）。
        let suggestionPayloads = (try? database.loadPayloads(
            table: "proactive_suggestions",
            where: "status = 'PENDING' AND push_status = 'PUSHED'",
            order: "created_at DESC"
        )) ?? []
        var decisionItems: [ActionItem] = []
        for payload in suggestionPayloads {
            guard let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
                  let id = object["id"] as? String,
                  let title = object["title"] as? String else { continue }
            decisionItems.append(
                ActionItem(
                    category: "DECISION", kind: "SUGGESTION", entityId: id,
                    title: title, detail: (object["rationale"] as? String) ?? "",
                    status: "PENDING", severity: object["severity"] as? String,
                    tripId: object["trip_id"] as? String, tripName: nil,
                    actionHint: "批准或驳回该建议", availableActions: ["APPROVE", "DISMISS"],
                    createdAt: nil, updatedAt: nil
                )
            )
        }
        let reportRows = (try? database.loadPayloads(
            table: "expense_reports", where: "status = 'SUBMITTED'"
        )) ?? []
        var approvalItems: [ActionItem] = []
        for payload in reportRows {
            guard let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
                  let id = object["id"] as? String else { continue }
            approvalItems.append(
                ActionItem(
                    category: "APPROVAL", kind: "EXPENSE_REPORT", entityId: id,
                    title: "报销单待审批", detail: "已提交待审批", status: "SUBMITTED",
                    severity: nil, tripId: object["trip_id"] as? String, tripName: nil,
                    actionHint: "审批该报销单", availableActions: ["APPROVE", "REJECT"],
                    createdAt: nil, updatedAt: nil
                )
            )
        }
        var sections: [ActionSection] = []
        if !decisionItems.isEmpty {
            sections.append(ActionSection(key: "DECISION", title: "待决定", items: decisionItems))
        }
        if !approvalItems.isEmpty {
            sections.append(ActionSection(key: "APPROVAL", title: "待审批", items: approvalItems))
        }
        let total = sections.reduce(0) { $0 + $1.items.count }
        return ActionCenter(generatedAt: Date(), total: total, counts: nil, sections: sections)
    }
}

// MARK: - SyncTransport（本地模式无需推送；outbox 保留作草稿队列）

struct LocalSyncTransport: SyncTransport {
    func push(_ request: SyncPushRequest) async throws -> SyncPushResult {
        SyncPushResult(results: [], summary: SyncSummary(applied: 0, conflict: 0, rejected: 0))
    }
}
