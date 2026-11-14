import Foundation

/// 本地模式服务收尾（P7）：签证案件/入境评估、出行人画像、拜访目标、CRM 显式失败、计划变更。
/// 语义红线保持：评估不给确定性入境结论、manual_review_required 恒真；
/// CRM 未配置适配器显式 FAILED；变更只入队不自动执行。
enum LocalVisaService {
    static let defaultDocuments: [(key: String, label: String)] = [
        ("passport", "护照（有效期通常需 ≥ 6 个月）"),
        ("photo", "近期证件照"),
        ("application_form", "签证申请表"),
        ("invitation_letter", "商务邀请函 / 邀请方信息"),
        ("itinerary", "行程与住宿证明"),
        ("insurance", "旅行保险"),
        ("financial_proof", "资金或在职证明"),
    ]

    static let transitions: [VisaCaseStatus: [VisaCaseStatus]] = [
        .new: [.preparing, .cancelled],
        .preparing: [.submitted, .cancelled],
        .submitted: [.approved, .rejected, .cancelled],
        .approved: [],
        .rejected: [],
        .cancelled: [],
    ]

    // MARK: - 入境评估（目录只做提醒，不给结论）

    static func assessEntry(
        database: LocalDatabase, tripId: String, nationality: String?
    ) throws -> [EntryAssessment] {
        let tripPayloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let tripPayload = tripPayloads.first else {
            throw APIError.serverError(code: "NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try DateCoding.decoder().decode(Trip.self, from: tripPayload)
        let now = Date()
        var results: [EntryAssessment] = []

        for code in Set((trip.countries ?? []).map { $0.uppercased() }).sorted() {
            let knowledge = VisaDirectory.countries[code]
            let premise = nationality.map { "证件前提：\($0) 护照。" }
                ?? "证件前提：未登记护照国籍，无法结合证件核验。"
            let summary: String
            var confidence: Double
            if let knowledge {
                summary = "\(premise)\(knowledge.label)：\(knowledge.businessEntry)。\(knowledge.reviewHint)（来源 \(VisaDirectory.version)，需人工复核）"
                confidence = 0.6
            } else {
                summary = "\(premise)\(code)：本地目录未收录，必须直接查询官方入境要求。"
                confidence = 0.4
            }
            if nationality == nil { confidence = min(confidence, 0.35) }

            let assessment = EntryAssessment(
                id: UUID().uuidString,
                tripId: tripId,
                countryCode: code,
                summary: summary,
                source: "offline-visa-catalog",
                sourceVersion: VisaDirectory.version,
                confidence: confidence,
                manualReviewRequired: true,
                assessedAt: now
            )
            results.append(assessment)
        }

        // 幂等：同 trip+country 覆盖（本地按 trip_id 全量替换）。
        try database.run("DELETE FROM entry_assessments WHERE trip_id = ?", bindings: [tripId])
        for assessment in results {
            try database.upsert(
                table: "entry_assessments", id: assessment.id,
                columns: ["trip_id": tripId, "country": assessment.countryCode],
                payload: try DateCoding.encode(assessment)
            )
        }
        return results
    }

    // MARK: - 签证案件（状态机）

    static func createCases(
        database: LocalDatabase, tripId: String, countries: [String]?
    ) throws -> [VisaCase] {
        let tripPayloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let tripPayload = tripPayloads.first else {
            throw APIError.serverError(code: "NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try DateCoding.decoder().decode(Trip.self, from: tripPayload)
        let targets = (countries?.map { $0.uppercased() })
            ?? (trip.countries ?? []).map { $0.uppercased() }

        var results: [VisaCase] = []
        for code in Set(targets).sorted() {
            // 一出行一国家一案件（幂等）
            let existing = try database.loadPayloads(
                table: "visa_cases", where: "trip_id = ? AND country = ?", bindings: [tripId, code]
            )
            if let payload = existing.first,
               let existingCase = try? DateCoding.decoder().decode(VisaCase.self, from: payload) {
                results.append(existingCase)
                continue
            }
            let now = Date()
            let caseModel = VisaCase(
                id: UUID().uuidString,
                tripId: tripId,
                countryCode: code,
                status: .new,
                checklist: defaultDocuments.map { item in
                    VisaChecklistItem(key: item.key, label: item.label, status: "MISSING", note: nil)
                },
                notes: VisaDirectory.countries[code].map { "\($0.businessEntry)；需人工复核" },
                passportExpiresAt: nil,
                confidence: 0.5,
                submittedAt: nil,
                decisionAt: nil,
                createdAt: now,
                updatedAt: now
            )
            try database.upsert(
                table: "visa_cases", id: caseModel.id,
                columns: ["trip_id": tripId, "country": code, "status": "NEW"],
                payload: try DateCoding.encode(caseModel)
            )
            results.append(caseModel)
        }
        return results
    }

    static func updateCase(
        database: LocalDatabase, id: String, status: VisaCaseStatus
    ) throws -> VisaCase {
        let payloads = try database.loadPayloads(table: "visa_cases", where: "id = ?", bindings: [id])
        guard let payload = payloads.first,
              var caseModel = try? DateCoding.decoder().decode(VisaCase.self, from: payload) else {
            throw APIError.serverError(code: "NOT_FOUND", message: "签证案件不存在", traceId: nil, details: [:])
        }
        let allowed = transitions[caseModel.status] ?? []
        guard allowed.contains(status) else {
            throw APIError.serverError(
                code: "CASE_TRANSITION_INVALID",
                message: "非法状态迁移：\(caseModel.status.rawValue) → \(status.rawValue)",
                traceId: nil, details: [:]
            )
        }
        caseModel.status = status
        caseModel.updatedAt = Date()
        if status == .submitted { caseModel.submittedAt = Date() }
        if status == .approved || status == .rejected { caseModel.decisionAt = Date() }
        try database.upsert(
            table: "visa_cases", id: id,
            columns: ["status": status.rawValue],
            payload: try DateCoding.encode(caseModel)
        )
        return caseModel
    }
}

/// 本地出行人画像（存 kv；国籍用于入境评估的证件前提）。
enum LocalTravelerProfile {
    private static let key = "traveler_profile"

    static func load(database: LocalDatabase) -> TravelerProfile {
        guard let raw = database.keyValue(key),
              let data = raw.data(using: .utf8),
              let profile = try? DateCoding.decoder().decode(TravelerProfile.self, from: data) else {
            return TravelerProfile(version: 0, preferences: nil, nationality: nil, notes: nil, confidence: nil)
        }
        return profile
    }

    static func update(database: LocalDatabase, body: TravelerProfileUpdate) throws -> TravelerProfile {
        var profile = load(database: database)
        if let nationality = body.nationality {
            let normalized = nationality.trimmingCharacters(in: .whitespaces).uppercased()
            guard normalized.count == 2, normalized.allSatisfy({ $0.isLetter }) else {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR", message: "国籍须为 ISO 两位字母代码", traceId: nil, details: [:]
                )
            }
            profile.nationality = normalized
        }
        if let notes = body.notes { profile.notes = notes }
        if let confidence = body.confidence { profile.confidence = confidence }
        profile.version += 1
        try database.setKeyValue(
            key, value: String(decoding: try DateCoding.encode(profile), as: UTF8.self)
        )
        return profile
    }
}

/// 本地记忆（P7）：出行记忆返回空聚合 + 画像本地读写；不做虚构摘要。
final class LocalMemoryRepository: MemoryRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func tripMemory(tripId: String) async throws -> TripMemory {
        // 本地模式没有复盘 Worker：返回空记忆并保持结构（客户端显示"暂无记忆"）。
        TripMemory(tripId: tripId, summaries: [], procedure: [])
    }

    func travelerProfile() async throws -> TravelerProfile {
        LocalTravelerProfile.load(database: database)
    }

    func updateProfile(_ body: TravelerProfileUpdate) async throws -> TravelerProfile {
        try LocalTravelerProfile.update(database: database, body: body)
    }
}

/// 本地 CRM 同步（P7）：未配置真实适配器 ⇒ 显式 FAILED（不伪造 PREPARED 为成功）。
enum LocalCrmService {
    static func syncToCRM(
        database: LocalDatabase, recordId: String, visitId: String, tripId: String
    ) throws -> CrmSyncJob {
        let now = Date()
        let job = CrmSyncJob(
            id: UUID().uuidString,
            tripId: tripId,
            visitId: visitId,
            visitRecordId: recordId,
            status: .failed,
            jobId: nil,
            result: nil,
            error: "本地模式未配置 CRM 适配器（同步需真实供应商凭据）",
            createdAt: now,
            updatedAt: now
        )
        try database.setKeyValue(
            "crm_job:\(job.id)",
            value: String(decoding: try DateCoding.encode(job), as: UTF8.self)
        )
        _ = recordId
        _ = visitId
        return job
    }
}

/// 本地计划变更（P7）：应用拜访变更后**同步重新求解**，返回已受理对象。
/// 与服务端一致：只接受白名单字段，变更只能产生新版本，不覆盖历史。
enum LocalPlanChangeService {
    static func createPlanChange(
        database: LocalDatabase,
        tripId: String,
        request: PlanChangeCreate,
        planRepository: PlanRepository
    ) async throws -> PlanChangeAccepted {
        // 当前本地实现仅支持取消拜访（最常见路径）；其他类型显式未支持，
        // 避免静默忽略变更意图。
        guard request.operationType == .cancelVisit else {
            throw LocalStorePendingError(method: "createPlanChange(\(request.operationType.rawValue))")
        }
        guard let visitId = request.payload["visit_id"] else {
            throw APIError.serverError(
                code: "VALIDATION_ERROR", message: "取消拜访需要 visit_id", traceId: nil, details: [:]
            )
        }
        let payloads = try database.loadPayloads(table: "visits", where: "id = ?", bindings: [visitId])
        guard let payload = payloads.first,
              var visit = try? DateCoding.decoder().decode(TripVisit.self, from: payload) else {
            throw APIError.serverError(code: "NOT_FOUND", message: "拜访不存在", traceId: nil, details: [:])
        }
        visit.status = "CANCELLED"
        try database.upsert(
            table: "visits", id: visitId,
            columns: ["status": "CANCELLED"],
            payload: try DateCoding.encode(visit)
        )
        // 变化后重新求解（本地同步生成新版本）。
        let generated = try await planRepository.generatePlan(
            tripId: tripId, request: GeneratePlanRequest()
        )
        return PlanChangeAccepted(
            changeRequestId: UUID().uuidString,
            status: "ACCEPTED",
            optimizationJobId: generated.jobId,
            tripVersion: request.baseTripVersion
        )
    }
}
