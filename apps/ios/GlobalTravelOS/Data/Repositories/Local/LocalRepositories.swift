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
        // 本地只存详情载荷（days/legs），列表的停靠/天数在服务端是独立列；
        // 这里按详情回算，避免列表显示 "0 停靠 / 0 天"。
        let plans = try payloads.map { data -> PlanVersion in
            var plan = try DateCoding.decoder().decode(PlanVersion.self, from: data)
            if plan.stopCount == nil || plan.dayCount == nil,
               let detail = try? DateCoding.decoder().decode(PlanVersionDetail.self, from: data) {
                let days = detail.days ?? []
                plan.stopCount = plan.stopCount ?? days.reduce(0) { $0 + $1.stops.count }
                plan.dayCount = plan.dayCount ?? days.count
            }
            return plan
        }
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
        // Customer tier is the only available local commercial-value signal (not actual revenue).
        let customerPayloads = try database.loadPayloads(table: "customers")
        let customers = try customerPayloads.map {
            try DateCoding.decoder().decode(Customer.self, from: $0)
        }

        let problem = EngineMapping.makeProblem(
            trip: trip, visits: visits, places: places,
            customers: customers, budgetSeconds: request.solverBudgetSeconds ?? 10
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
            from: result.days,
            places: places,
            visits: visits,
            timeZone: trip.originPlace?.timeZone() ?? .current
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
                "local-job-\(planId)", value: String(decoding: jobData, as: UTF8.self)
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
        try await LocalPlanChangeService.createPlanChange(
            database: database, tripId: tripId, request: request, planRepository: self
        )
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
            customerId: request.customerId,
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
        let taskData = try DateCoding.encoder().encode(request.followUpTasks)
        try database.setKeyValue(
            "visit-follow-up-source:\(record.id)",
            value: String(decoding: taskData, as: UTF8.self)
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
        // 需要记录所属 trip/visit 信息；从记录 payload 读。
        let payloads = try database.loadPayloads(
            table: "visit_records", where: "id = ?", bindings: [recordId]
        )
        guard let payload = payloads.first,
              let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            throw APIError.serverError(code: "NOT_FOUND", message: "现场记录不存在", traceId: nil, details: [:])
        }
        let visitId = (object["visit_id"] as? String) ?? ""
        let tripId = (try? database.loadPayloads(
            table: "visits", where: "id = ?", bindings: [visitId]
        ).first.flatMap { try? DateCoding.decoder().decode(TripVisit.self, from: $0) })?.tripId ?? ""
        return try LocalCrmService.syncToCRM(
            database: database, recordId: recordId, visitId: visitId, tripId: tripId
        )
    }

    func crmSyncJob(id: String) async throws -> CrmSyncJob {
        guard let raw = database.keyValue("crm_job:\(id)"),
              let data = raw.data(using: .utf8),
              let job = try? DateCoding.decoder().decode(CrmSyncJob.self, from: data) else {
            throw APIError.serverError(code: "NOT_FOUND", message: "CRM 任务不存在", traceId: nil, details: [:])
        }
        return job
    }

    func briefing(visitId: String) async throws -> VisitBriefing {
        try LocalAggregations.visitBriefing(database: database, visitId: visitId)
    }

    func updateObjective(visitId: String, objective: String?) async throws -> TripVisit {
        let payloads = try database.loadPayloads(table: "visits", where: "id = ?", bindings: [visitId])
        guard let payload = payloads.first,
              var visit = try? DateCoding.decoder().decode(TripVisit.self, from: payload) else {
            throw APIError.serverError(code: "NOT_FOUND", message: "拜访不存在", traceId: nil, details: [:])
        }
        visit.objective = objective
        try database.upsert(
            table: "visits", id: visitId,
            columns: ["title": visit.title],
            payload: try DateCoding.encode(visit)
        )
        return visit
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

        // Local approval prepares deterministic domain records; it never performs external actions.
        let tripId = object["trip_id"] as? String
        var execution: [String: Any] = ["status": "PREPARED"]
        var updated = object
        updated["status"] = "EXECUTED"
        updated["executed_at"] = SQLTimestamp.basics.string(from: Date())
        updated["approved_by"] = "local-user"

        try database.transaction {
            if type == "VISA_REQUIREMENT_REMINDER" {
                guard let tripId, !tripId.isEmpty else {
                    throw APIError.serverError(
                        code: "VALIDATION_ERROR",
                        message: "签证提醒缺少行程关联",
                        traceId: nil,
                        details: [:]
                    )
                }
                let cases = try LocalVisaService.createCases(
                    database: database,
                    tripId: tripId,
                    countries: nil
                )
                execution["visa_cases"] = cases.map(\.id)
            }
            updated["execution_result"] = execution
            try database.upsert(
                table: "proactive_suggestions",
                id: suggestionId,
                columns: ["status": "EXECUTED"],
                payload: try JSONSerialization.data(withJSONObject: updated)
            )
        }
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


private extension LocalVisitRepository {
    func storedFollowUpItems(visitId: String) -> [VisitFollowUp] {
        guard let raw = database.keyValue("visit-follow-ups:\(visitId)"),
              let data = raw.data(using: .utf8) else { return [] }
        return (try? DateCoding.decoder().decode([VisitFollowUp].self, from: data)) ?? []
    }

    func saveFollowUpItems(_ items: [VisitFollowUp], visitId: String) throws {
        let data = try DateCoding.encoder().encode(items)
        try database.setKeyValue(
            "visit-follow-ups:\(visitId)",
            value: String(decoding: data, as: UTF8.self)
        )
        for item in items {
            try database.setKeyValue("visit-follow-up-visit:\(item.id)", value: visitId)
        }
    }

    func localTripId(visitId: String) -> String {
        let rows = try? database.loadPayloads(
            table: "visits", where: "id = ?", bindings: [visitId]
        )
        guard let payload = rows?.first,
              let visit = try? DateCoding.decoder().decode(TripVisit.self, from: payload) else {
            return ""
        }
        return visit.tripId ?? ""
    }

    func localTaskList(_ raw: Any?) -> [FollowUpTask] {
        guard let values = raw as? [Any] else { return [] }
        return values.compactMap { value in
            if let title = value as? String {
                let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
                return clean.isEmpty ? nil : FollowUpTask(title: clean)
            }
            guard let object = value as? [String: Any],
                  let title = object["title"] as? String else { return nil }
            let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !clean.isEmpty else { return nil }
            return FollowUpTask(title: clean, dueDate: object["due_date"] as? String)
        }
    }

    func localFollowUpSourceKey(
        sourceType: String,
        sourceId: String,
        sourceIndex: Int
    ) -> String {
        "\(sourceType):\(sourceId):\(sourceIndex)"
    }
}

extension LocalVisitRepository {
    func followUps(visitId: String) async throws -> VisitFollowUpFeed {
        let items = storedFollowUpItems(visitId: visitId)
        let consumed = Set(items.compactMap { item -> String? in
            guard item.sourceType != "MANUAL",
                  let sourceId = item.sourceRecordId ?? item.sourceAgentRunId,
                  let index = item.sourceIndex else { return nil }
            return localFollowUpSourceKey(
                sourceType: item.sourceType,
                sourceId: sourceId,
                sourceIndex: index
            )
        })

        var suggestions: [VisitFollowUpSuggestion] = []
        let records = try database.loadPayloads(
            table: "visit_records", where: "visit_id = ?", bindings: [visitId], order: "id"
        )
        for payload in records {
            guard let record = try? DateCoding.decoder().decode(VisitRecord.self, from: payload),
                  let rawTasks = database.keyValue("visit-follow-up-source:\(record.id)"),
                  let taskData = rawTasks.data(using: .utf8),
                  let tasks = try? DateCoding.decoder().decode([FollowUpTask].self, from: taskData) else {
                continue
            }
            for (index, task) in tasks.enumerated() {
                let key = localFollowUpSourceKey(
                    sourceType: "RECORD",
                    sourceId: record.id,
                    sourceIndex: index
                )
                guard !consumed.contains(key) else { continue }
                suggestions.append(
                    VisitFollowUpSuggestion(
                        sourceType: "RECORD",
                        sourceRecordId: record.id,
                        sourceAgentRunId: nil,
                        sourceIndex: index,
                        title: task.title,
                        dueDate: task.dueDate,
                        sourceCreatedAt: Date()
                    )
                )
            }
        }

        if let rawMinutes = database.keyValue("minutes:\(visitId)"),
           let data = rawMinutes.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let draftId = object["id"] as? String {
            for (index, task) in localTaskList(object["follow_up_tasks"]).enumerated() {
                let key = localFollowUpSourceKey(
                    sourceType: "MINUTES_DRAFT",
                    sourceId: draftId,
                    sourceIndex: index
                )
                guard !consumed.contains(key) else { continue }
                suggestions.append(
                    VisitFollowUpSuggestion(
                        sourceType: "MINUTES_DRAFT",
                        sourceRecordId: nil,
                        sourceAgentRunId: draftId,
                        sourceIndex: index,
                        title: task.title,
                        dueDate: task.dueDate,
                        sourceCreatedAt: Date()
                    )
                )
            }
        }

        return VisitFollowUpFeed(
            visitId: visitId,
            tripId: localTripId(visitId: visitId),
            items: items.sorted { $0.createdAt > $1.createdAt },
            suggestions: suggestions
        )
    }

    func decideFollowUp(
        visitId: String,
        request: VisitFollowUpDecisionRequest
    ) async throws -> VisitFollowUp {
        var items = storedFollowUpItems(visitId: visitId)
        let now = Date()
        let sourceId = request.sourceRecordId ?? request.sourceAgentRunId
        let sourceSuggestion: VisitFollowUpSuggestion?
        if request.sourceType == "MANUAL" {
            sourceSuggestion = nil
            guard request.decision == "CREATE",
                  let title = request.title,
                  !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR", message: "手工跟进必须提供标题",
                    traceId: nil, details: [:]
                )
            }
        } else {
            guard let sourceId, let sourceIndex = request.sourceIndex else {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR", message: "来源跟进项缺少来源标识",
                    traceId: nil, details: [:]
                )
            }
            let feed = try await followUps(visitId: visitId)
            sourceSuggestion = feed.suggestions.first {
                $0.sourceType == request.sourceType
                    && ($0.sourceRecordId ?? $0.sourceAgentRunId) == sourceId
                    && $0.sourceIndex == sourceIndex
            }
            guard sourceSuggestion != nil,
                  request.decision == "ACCEPT" || request.decision == "DISMISS" else {
                throw APIError.serverError(
                    code: "NOT_FOUND", message: "该来源跟进建议不存在或已经处理",
                    traceId: nil, details: [:]
                )
            }
        }

        let sourceTitle = sourceSuggestion?.title ?? request.title ?? ""
        let title = sourceTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else {
            throw APIError.serverError(
                code: "VALIDATION_ERROR", message: "跟进标题不能为空",
                traceId: nil, details: [:]
            )
        }
        let status = request.decision == "DISMISS" ? "DISMISSED" : "OPEN"
        let item = VisitFollowUp(
            id: UUID().uuidString.lowercased(),
            visitId: visitId,
            tripId: localTripId(visitId: visitId),
            sourceType: request.sourceType,
            sourceRecordId: request.sourceRecordId,
            sourceAgentRunId: request.sourceAgentRunId,
            sourceIndex: request.sourceIndex,
            sourceTitle: sourceSuggestion?.title ?? request.title,
            sourceDueDate: sourceSuggestion?.dueDate,
            title: title,
            dueDate: request.dueDate ?? sourceSuggestion?.dueDate,
            status: status,
            createdByUserId: nil,
            confirmedByUserId: nil,
            confirmedAt: request.sourceType == "MANUAL" ? nil : now,
            completedByUserId: nil,
            completedAt: nil,
            createdAt: now,
            updatedAt: now,
            events: [
                VisitFollowUpEvent(
                    id: UUID().uuidString.lowercased(),
                    eventType: request.decision == "DISMISS" ? "DISMISSED" : (
                        request.sourceType == "MANUAL" ? "CREATED_MANUAL" : "ACCEPTED"
                    ),
                    fromStatus: nil,
                    toStatus: status,
                    actorUserId: nil,
                    note: request.note,
                    createdAt: now
                )
            ]
        )
        items.append(item)
        try saveFollowUpItems(items, visitId: visitId)
        return item
    }

    func updateFollowUp(
        id: String,
        request: VisitFollowUpUpdateRequest
    ) async throws -> VisitFollowUp {
        guard let visitId = database.keyValue("visit-follow-up-visit:\(id)") else {
            throw APIError.serverError(
                code: "NOT_FOUND", message: "本地拜访跟进项不存在",
                traceId: nil, details: [:]
            )
        }
        var items = storedFollowUpItems(visitId: visitId)
        guard let index = items.firstIndex(where: { $0.id == id }) else {
            throw APIError.serverError(
                code: "NOT_FOUND", message: "本地拜访跟进项不存在",
                traceId: nil, details: [:]
            )
        }

        var item = items[index]
        let oldStatus = item.status
        let newStatus = request.status ?? oldStatus
        if newStatus != oldStatus {
            let allowed: [String: Set<String>] = [
                "OPEN": ["IN_PROGRESS", "COMPLETED", "DISMISSED"],
                "IN_PROGRESS": ["OPEN", "COMPLETED", "DISMISSED"],
                "COMPLETED": ["OPEN"],
                "DISMISSED": ["OPEN"],
            ]
            guard allowed[oldStatus, default: []].contains(newStatus) else {
                throw APIError.serverError(
                    code: "CONFLICT", message: "不允许该跟进状态转换",
                    traceId: nil,
                    details: ["from_status": .string(oldStatus), "to_status": .string(newStatus)]
                )
            }
            if (oldStatus == "COMPLETED" || oldStatus == "DISMISSED")
                && newStatus == "OPEN"
                && (request.note?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true) {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR", message: "重新打开事项时必须填写原因",
                    traceId: nil, details: [:]
                )
            }
            item.status = newStatus
            if newStatus == "COMPLETED" {
                item.completedAt = Date()
            } else if newStatus == "OPEN" {
                item.completedAt = nil
                item.completedByUserId = nil
            }
        }

        if let title = request.title {
            let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !clean.isEmpty else {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR", message: "跟进标题不能为空",
                    traceId: nil, details: [:]
                )
            }
            item.title = clean
        }
        if let dueDate = request.dueDate {
            item.dueDate = dueDate
        }

        guard item.status != oldStatus || request.title != nil || request.dueDate != nil else {
            throw APIError.serverError(
                code: "VALIDATION_ERROR", message: "至少需要更新状态、标题或截止日期",
                traceId: nil, details: [:]
            )
        }

        let now = Date()
        item.updatedAt = now
        let eventType: String
        if newStatus != oldStatus {
            switch newStatus {
            case "IN_PROGRESS": eventType = "STARTED"
            case "COMPLETED": eventType = "COMPLETED"
            case "DISMISSED": eventType = "DISMISSED"
            case "OPEN": eventType = "REOPENED"
            default: eventType = "UPDATED"
            }
        } else {
            eventType = "UPDATED"
        }
        item.events.append(
            VisitFollowUpEvent(
                id: UUID().uuidString.lowercased(),
                eventType: eventType,
                fromStatus: oldStatus,
                toStatus: item.status,
                actorUserId: nil,
                note: request.note,
                createdAt: now
            )
        )
        items[index] = item
        try saveFollowUpItems(items, visitId: visitId)
        return item
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
        let visitPayloads = (try? database.loadPayloads(table: "visits", order: "id")) ?? []
        var activeFollowUps: [ActionItem] = []
        var completedFollowUps: [ActionItem] = []
        let completedSince = Date().addingTimeInterval(-7 * 24 * 60 * 60)
        for payload in visitPayloads {
            guard let visit = try? DateCoding.decoder().decode(TripVisit.self, from: payload),
                  tripId == nil || visit.tripId == tripId,
                  let raw = database.keyValue("visit-follow-ups:\(visit.id)"),
                  let data = raw.data(using: .utf8),
                  let items = try? DateCoding.decoder().decode([VisitFollowUp].self, from: data) else {
                continue
            }
            for item in items {
                let isActive = item.status == "OPEN" || item.status == "IN_PROGRESS"
                let isRecentCompletion = item.status == "COMPLETED"
                    && (item.completedAt ?? item.updatedAt) >= completedSince
                guard isActive || isRecentCompletion else { continue }
                let state = item.status == "IN_PROGRESS" ? "进行中" : (
                    item.status == "COMPLETED" ? "已完成" : "待开始"
                )
                let due = item.dueDate.map { " · 截止 \($0)" } ?? ""
                let action = ActionItem(
                    category: isActive ? "PREPARATION" : "COMPLETED",
                    kind: "VISIT_FOLLOW_UP",
                    entityId: item.id,
                    title: item.title,
                    detail: state + due,
                    status: item.status,
                    severity: "LOW",
                    tripId: item.tripId,
                    tripName: nil,
                    actionHint: "打开对应行程，在拜访工作台更新进度",
                    availableActions: ["OPEN_TRIP"],
                    createdAt: item.createdAt,
                    updatedAt: item.updatedAt
                )
                if isActive {
                    activeFollowUps.append(action)
                } else {
                    completedFollowUps.append(action)
                }
            }
        }

        var sections: [ActionSection] = []
        if !decisionItems.isEmpty {
            sections.append(ActionSection(key: "DECISION", title: "待决定", items: decisionItems))
        }
        if !approvalItems.isEmpty {
            sections.append(ActionSection(key: "APPROVAL", title: "待审批", items: approvalItems))
        }
        if !activeFollowUps.isEmpty {
            sections.append(
                ActionSection(key: "PREPARATION", title: "待跟进", items: activeFollowUps)
            )
        }
        if !completedFollowUps.isEmpty {
            sections.append(
                ActionSection(key: "COMPLETED", title: "已完成", items: completedFollowUps)
            )
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
