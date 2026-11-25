import Foundation
import UserNotifications

/// 本地主动式引擎（ADR-0036）：4 条规则 + 三层抑制，直译 proactive_service.py。
///
/// 语义红线：
/// - 抑制区分 SUPPRESSED（冷却，建议留痕）与 WITHHELD（低置信/每日上限，推送留痕）；
/// - 驳回负反馈回填置信度折扣（误报回收）；
/// - 同 (trip, type, dedupe_key) 已有建议 ⇒ 不再生成（AT-P02）；
/// - 扫描只存事实与建议，通知按分级产生。
struct ProactiveCandidate {
    var triggerType: String        // SCHEDULED / STATE / EVENT / EXTERNAL
    var ruleId: String
    var suggestionType: String
    var dedupeKey: String
    var title: String
    var rationale: String
    var evidence: [[String: Any]]
    var proposedAction: [String: Any]
    var confidence: Double
    var requiresApproval: Bool
    var severity: String            // LOW / MEDIUM / HIGH
}

struct ProactiveScanReport {
    var tripsScanned = 0
    var triggers = 0
    var suggestions = 0
    var deduped = 0
    var suppressed = 0
    var pushed = 0
    var withheld = 0
    var errors: [String] = []
}

/// 本地主动式配置（与服务端 GBT_ 前缀默认值一致）。
struct ProactiveConfig {
    var visaLookaheadHours = 72
    var cooldownHours = 24
    var confidenceThreshold = 0.6
    var dismissalPenalty = 0.05
    var maxFeedbackPenalty = 0.3
    var feedbackWindowDays = 90
    var dailySuggestionCap = 3
}

enum LocalProactiveEngine {
    // MARK: - 扫描入口

    /// 扫描全部非终态行程，把命中的规则变成建议（幂等）。
    static func scan(
        database: LocalDatabase,
        userId: String,
        config: ProactiveConfig = ProactiveConfig(),
        now: Date = Date()
    ) -> ProactiveScanReport {
        var report = ProactiveScanReport()
        let tripPayloads = (try? database.loadPayloads(
            table: "trips",
            where: "status NOT IN ('COMPLETED','CANCELLED')",
            order: "updated_at"
        )) ?? []
        let decoder = DateCoding.decoder()
        for payload in tripPayloads {
            guard let trip = try? decoder.decode(Trip.self, from: payload) else { continue }
            report.tripsScanned += 1
            for candidate in candidates(for: trip, database: database, config: config, now: now) {
                record(
                    candidate, trip: trip, userId: userId, database: database,
                    config: config, now: now, report: &report
                )
            }
        }
        return report
    }

    // MARK: - 规则

    /// 对单个行程求全部候选。
    static func candidates(
        for trip: Trip, database: LocalDatabase, config: ProactiveConfig, now: Date
    ) -> [ProactiveCandidate] {
        var result: [ProactiveCandidate] = []
        if let visa = visaCandidate(trip, config: config, now: now) { result.append(visa) }
        if let missing = missingCoordinatesCandidate(trip, database: database) { result.append(missing) }
        if let unplaced = unplacedCandidate(trip, database: database) { result.append(unplaced) }
        return result
    }

    /// 规则 1：出发前 T-72h 签证核验（SCHEDULED）。
    static func visaCandidate(
        _ trip: Trip, config: ProactiveConfig, now: Date
    ) -> ProactiveCandidate? {
        let countries = (trip.countries ?? []).map { $0.uppercased() }
        guard !countries.isEmpty, let windowStart = trip.window?.start else { return nil }
        let remaining = windowStart.timeIntervalSince(now)
        guard remaining > 0 else { return nil }
        guard remaining <= Double(config.visaLookaheadHours) * 3600 else { return nil }

        let hoursLeft = max(Int(remaining / 3600), 1)
        // 本地目录与服务端 visa_catalog.json 同源（8 国）；未收录国家走"必须人工核验"分支。
        let known = countries.filter { VisaDirectory.countries[$0] != nil }
        let unknown = countries.filter { VisaDirectory.countries[$0] == nil }
        var evidence: [[String: Any]] = [
            ["kind": "TRIP_WINDOW", "ref": "trip:\(trip.id)@v\(trip.version)", "retrieved_at": NSNull()],
            ["kind": "VISA_RULE_CATALOG", "ref": "\(VisaDirectory.version) (offline; manual confirmation required)", "retrieved_at": NSNull()],
        ]
        for code in known {
            evidence.append([
                "kind": "VISA_RULE",
                "ref": "\(code): \(VisaDirectory.countries[code]?.businessEntry ?? "")",
                "retrieved_at": NSNull(),
            ])
        }
        for code in unknown {
            evidence.append(["kind": "VISA_RULE_MISSING", "ref": "\(code): 本地目录未收录，必须人工核验", "retrieved_at": NSNull()])
        }

        var labels = known.compactMap { code -> String? in
            VisaDirectory.countries[code].map { "\(code)（\($0.label)）" }
        }
        if !unknown.isEmpty {
            labels.append(unknown.joined(separator: "、") + "（目录未收录）")
        }
        var rationale = "出行将于约 \(hoursLeft) 小时后开始，目的国：\(labels.joined(separator: "、"))。本地规则目录只用于提醒，需按官方渠道人工核验签证类型、受理周期与材料要求。"
        if !unknown.isEmpty { rationale += " 其中部分国家未被目录收录，必须直接查官方信息。" }

        return ProactiveCandidate(
            triggerType: "SCHEDULED",
            ruleId: "visa_check",
            suggestionType: "VISA_REQUIREMENT_REMINDER",
            dedupeKey: "visa_check:\(proactiveDayString(windowStart))",
            title: "出发前签证与证件核验",
            rationale: rationale,
            evidence: evidence,
            proposedAction: ["kind": "VISA_CHECKLIST", "title": "生成核验清单", "tool": "visa.prepare_checklist"],
            confidence: unknown.isEmpty ? 0.85 : 0.70,
            requiresApproval: true,
            severity: hoursLeft <= 48 ? "HIGH" : "MEDIUM"
        )
    }

    /// 规则 2：缺坐标提醒（STATE）。
    static func missingCoordinatesCandidate(
        _ trip: Trip, database: LocalDatabase
    ) -> ProactiveCandidate? {
        let visitPayloads = (try? database.loadPayloads(
            table: "visits", where: "trip_id = ?", bindings: [trip.id]
        )) ?? []
        let decoder = DateCoding.decoder()
        var missingIds: [String] = []
        for payload in visitPayloads {
            guard let visit = try? decoder.decode(TripVisit.self, from: payload),
                  visit.status == "PLANNED" || visit.status == "SCHEDULED",
                  let placeId = visit.placeId else { continue }
            let placeRows = (try? database.loadPayloads(
                table: "places", where: "id = ?", bindings: [placeId]
            )) ?? []
            if let placePayload = placeRows.first,
               let place = try? decoder.decode(Place.self, from: placePayload),
               place.latitude != nil, place.longitude != nil {
                continue
            }
            missingIds.append(placeId)
        }
        guard !missingIds.isEmpty else { return nil }
        let digest = suffix16(joined: missingIds.sorted().joined(separator: ","))
        return ProactiveCandidate(
            triggerType: "STATE",
            ruleId: "missing_coordinates",
            suggestionType: "PLACE_COORDINATES_MISSING",
            dedupeKey: "missing_coordinates:\(digest)",
            title: "\(missingIds.count) 个拜访地点缺少坐标",
            rationale: "缺少坐标的地点无法进入地图与规划估算，请在地点页补齐经纬度。",
            evidence: [["kind": "VISITS_MISSING_COORDINATES", "ref": missingIds.sorted().joined(separator: ",")]],
            proposedAction: ["kind": "OPEN_PLACES", "title": "打开地点页"],
            confidence: 0.95,
            requiresApproval: false,
            severity: "MEDIUM"
        )
    }

    /// 规则 3：激活计划存在未排入停靠（STATE，HIGH）。
    static func unplacedCandidate(
        _ trip: Trip, database: LocalDatabase
    ) -> ProactiveCandidate? {
        guard let planId = trip.activePlanVersionId else { return nil }
        let rows = (try? database.loadPayloads(
            table: "plan_versions", where: "id = ?", bindings: [planId]
        )) ?? []
        guard let payload = rows.first,
              let detail = try? DateCoding.decoder().decode(PlanVersionDetail.self, from: payload)
        else { return nil }
        let unplaced = detail.unplaced ?? []
        guard !unplaced.isEmpty else { return nil }
        let summary = unplaced.map { $0.visitId }.sorted().joined(separator: ",").suffix(32)
        return ProactiveCandidate(
            triggerType: "STATE",
            ruleId: "unplaced_visits",
            suggestionType: "PLAN_UNPLACED_VISITS",
            dedupeKey: "unplaced:\(planId):\(summary)",
            title: "有 \(unplaced.count) 个拜访未排入计划",
            rationale: "当前激活的计划存在未能排入的拜访，建议调整时间窗或减少当日停靠上限。",
            evidence: [["kind": "PLAN_UNPLACED", "ref": "plan:\(planId)"]],
            proposedAction: ["kind": "OPEN_PLAN", "title": "查看计划"],
            confidence: 0.90,
            requiresApproval: false,
            severity: "HIGH"
        )
    }

    // MARK: - 三层抑制 + 落库

    static func record(
        _ candidate: ProactiveCandidate,
        trip: Trip,
        userId: String,
        database: LocalDatabase,
        config: ProactiveConfig,
        now: Date,
        report: inout ProactiveScanReport
    ) {
        report.triggers += 1

        // 层 0：去重（同 trip+type+dedupe_key 已存在 ⇒ 跳过）
        let duplicateCount = (try? database.count(
            table: "proactive_suggestions",
            where: "trip_id = ? AND type = ? AND dedupe_key = ?",
            bindings: [trip.id, candidate.suggestionType, candidate.dedupeKey]
        )) ?? 0
        if duplicateCount > 0 {
            report.deduped += 1
            return
        }

        // 层 1：冷却（同 trip+type 在冷却窗口内 ⇒ SUPPRESSED/WITHHELD:COOLDOWN）
        let cooldownStart = SQLTimestamp.basics.string(from: now.addingTimeInterval(-Double(config.cooldownHours) * 3600))
        let cooldownCount = (try? database.count(
            table: "proactive_suggestions",
            where: "trip_id = ? AND type = ? AND created_at >= ?",
            bindings: [trip.id, candidate.suggestionType, cooldownStart]
        )) ?? 0
        let inCooldown = cooldownCount > 0

        // 层 2：负反馈折扣（窗口内同类型被驳回次数 × 单次惩罚，封顶）
        let windowStart = SQLTimestamp.basics.string(
            from: now.addingTimeInterval(-Double(config.feedbackWindowDays) * 86_400)
        )
        let dismissedCount = (try? database.count(
            table: "proactive_suggestions",
            where: "type = ? AND status = 'DISMISSED' AND created_at >= ?",
            bindings: [candidate.suggestionType, windowStart]
        )) ?? 0
        let penalty = min(
            config.maxFeedbackPenalty,
            Double(dismissedCount) * config.dismissalPenalty
        )
        let confidence = max(0.05, min(1.0, candidate.confidence - penalty))

        // 三层判定：冷却 > 低置信 > 每日上限。
        var status = "PENDING"
        var pushStatus = "PUSHED"
        var pushReason: String?
        if inCooldown {
            status = "SUPPRESSED"
            pushStatus = "WITHHELD"
            pushReason = "COOLDOWN"
        } else if confidence < config.confidenceThreshold {
            pushStatus = "WITHHELD"
            pushReason = "LOW_CONFIDENCE"
        } else if dailyCapReached(database: database, userId: userId, now: now, cap: config.dailySuggestionCap) {
            pushStatus = "WITHHELD"
            pushReason = "DAILY_CAP"
        }

        let suggestionId = UUID().uuidString
        var payload: [String: Any] = [
            "id": suggestionId,
            "trip_id": trip.id,
            "suggestion_type": candidate.suggestionType,
            "status": status,
            "title": candidate.title,
            "rationale": candidate.rationale,
            "evidence": candidate.evidence,
            "proposed_action": candidate.proposedAction,
            "confidence": confidence,
            "requires_approval": candidate.requiresApproval,
            "severity": candidate.severity,
            "dedupe_key": candidate.dedupeKey,
            "push_status": pushStatus,
            "push_reason": pushReason ?? NSNull(),
            "created_at": proactiveISO(now),
            "feedback_penalty": penalty,
        ]
        if pushStatus == "PUSHED" { payload["notified_at"] = proactiveISO(now) }
        guard let payloadData = try? JSONSerialization.data(withJSONObject: payload) else {
            report.errors.append("suggestion payload encode failed")
            return
        }

        do {
            try persistTriggerAndSuggestion(
                candidate: candidate,
                trip: trip,
                suggestionId: suggestionId,
                status: status,
                pushStatus: pushStatus,
                payload: payloadData,
                database: database,
                now: now
            )
        } catch {
            handlePersistenceFailure(
                error,
                candidate: candidate,
                tripId: trip.id,
                database: database,
                report: &report
            )
            return
        }

        report.suggestions += 1
        if status == "SUPPRESSED" {
            report.suppressed += 1
        } else if pushStatus == "PUSHED" {
            report.pushed += 1
            notifyPushed(title: candidate.title, body: candidate.rationale, suggestionId: suggestionId, severity: candidate.severity)
        } else {
            report.withheld += 1
        }
    }

    /// Trigger rows are index/audit records without a document payload column.
    /// Keep trigger and suggestion persistence atomic.
    private static func persistTriggerAndSuggestion(
        candidate: ProactiveCandidate,
        trip: Trip,
        suggestionId: String,
        status: String,
        pushStatus: String,
        payload: Data,
        database: LocalDatabase,
        now: Date
    ) throws {
        try database.transaction {
            try database.run(
                """
                INSERT INTO proactive_triggers (
                    id, trip_id, rule_id, dedupe_key, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trip_id, rule_id, dedupe_key) DO NOTHING
                """,
                bindings: [
                    UUID().uuidString,
                    trip.id,
                    candidate.ruleId,
                    candidate.dedupeKey,
                    status == "SUPPRESSED" ? "SUPPRESSED" : "PROPOSED",
                    proactiveISO(now),
                ]
            )
            try database.upsert(
                table: "proactive_suggestions",
                id: suggestionId,
                columns: [
                    "trip_id": trip.id,
                    "type": candidate.suggestionType,
                    "status": status,
                    "push_status": pushStatus,
                    "dedupe_key": candidate.dedupeKey,
                    "severity": candidate.severity,
                    "created_at": proactiveISO(now),
                ],
                payload: payload
            )
        }
    }

    private static func handlePersistenceFailure(
        _ error: Error,
        candidate: ProactiveCandidate,
        tripId: String,
        database: LocalDatabase,
        report: inout ProactiveScanReport
    ) {
        let existing = (try? database.count(
            table: "proactive_suggestions",
            where: "trip_id = ? AND type = ? AND dedupe_key = ?",
            bindings: [tripId, candidate.suggestionType, candidate.dedupeKey]
        )) ?? 0
        if existing > 0 {
            report.deduped += 1
        } else {
            report.errors.append("主动建议落库失败：\(error.localizedDescription)")
        }
    }

    /// 每出差人每日推送上限（本地单用户：按 userId 计）。
    static func dailyCapReached(database: LocalDatabase, userId: String, now: Date, cap: Int) -> Bool {
        guard let startOfDay = proactiveDayStart(now) else { return false }
        let used = (try? database.count(
            table: "proactive_suggestions",
            where: "push_status = 'PUSHED' AND notified_at >= ?",
            bindings: [startOfDay]
        )) ?? 0
        return used >= cap
    }

    /// 本地通知（UNUserNotificationCenter）：推送即本地通知。
    static func notifyPushed(title: String, body: String, suggestionId: String, severity: String) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.userInfo = ["suggestion_id": suggestionId, "severity": severity]
            let request = UNNotificationRequest(
                identifier: suggestionId, content: content, trigger: nil
            )
            center.add(request)
        }
    }

    static func proactiveISO(_ date: Date) -> String {
        SQLTimestamp.basics.string(from: date)
    }

    static func proactiveDayString(_ date: Date) -> String {
        SQLTimestamp.basics.string(from: date).prefix(10).description
    }

    static func proactiveDayStart(_ date: Date) -> String? {
        let components = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return Calendar.current.date(from: components).map { proactiveISO($0) }
    }

    static func suffix16(joined: String) -> String {
        let digest = CryptoDigest.sha256(Data(joined.utf8))
        return digest.prefix(8).map { String(format: "%02x", $0) }.joined()
    }
}
