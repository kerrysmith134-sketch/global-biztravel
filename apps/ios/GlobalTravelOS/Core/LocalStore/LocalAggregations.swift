import Foundation

/// 本地模式聚合视图（P7）：复盘与拜访简报——直接由本地 SQLite 事实聚合，
/// 语义与服务端只读聚合一致（审计 5.x：复盘/简报是已有事实的视图）。
enum LocalAggregations {
    // MARK: - 出行复盘（TripOutcome）

    struct OutcomeVisitSummary {
        var statusCounts: [String: Int]
        var recordOutcomes: [String: Int]
    }

    static func tripOutcome(
        database: LocalDatabase, tripId: String
    ) throws -> TripOutcome {
        let tripPayloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let tripPayload = tripPayloads.first else {
            throw APIError.serverError(code: "NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try DateCoding.decoder().decode(Trip.self, from: tripPayload)

        // 拜访按状态计数
        var visitStatus: [String: Int] = [:]
        let visitPayloads = try database.loadPayloads(table: "visits", where: "trip_id = ?", bindings: [tripId])
        for payload in visitPayloads {
            let visit = try DateCoding.decoder().decode(TripVisit.self, from: payload)
            visitStatus[visit.status ?? "PLANNED", default: 0] += 1
        }
        var visitLabels: [String: String] = [:]
        for status in visitStatus.keys {
            visitLabels[status] = statusLabel(status)
        }

        // 现场记录结果计数
        var recordOutcomes: [String: Int] = [:]
        var insightSignals: [OutcomeSignal] = []
        let visitIds = try visitPayloads.map { try DateCoding.decoder().decode(TripVisit.self, from: $0).id }
        for visitId in visitIds {
            let recordPayloads = try database.loadPayloads(
                table: "visit_records", where: "visit_id = ?", bindings: [visitId]
            )
            for payload in recordPayloads {
                guard let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else { continue }
                let outcome = (object["outcome"] as? String) ?? "MET"
                recordOutcomes[outcome, default: 0] += 1
            }
            // 纪要洞察（非 SUMMARY 且待确认 → 机会信号）
            if let minutesRow = database.keyValue("minutes:\(visitId)"),
               let data = minutesRow.data(using: .utf8),
               let minutes = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let insights = minutes["insights"] as? [[String: Any]] {
                for insight in insights {
                    guard let kind = insight["kind"] as? String,
                          let text = insight["text"] as? String else { continue }
                    insightSignals.append(
                        OutcomeSignal(
                            kind: kind,
                            kindLabel: insightLabel(kind),
                            text: text,
                            visitId: visitId,
                            requiresConfirmation: (insight["requires_confirmation"] as? Bool) ?? (kind != "SUMMARY")
                        )
                    )
                }
            }
        }
        var recordLabels: [String: String] = [:]
        for outcome in recordOutcomes.keys { recordLabels[outcome] = outcomeLabel(outcome) }
        var insightKinds: [String: Int] = [:]
        for signal in insightSignals { insightKinds[signal.kind, default: 0] += 1 }
        var insightKindLabels: [String: String] = [:]
        for kind in insightKinds.keys { insightKindLabels[kind] = insightLabel(kind) }

        // 费用：实际按币种 + 计划成本
        var actualByCurrency: [String: (amount: Double, count: Int)] = [:]
        let expensePayloads = try database.loadPayloads(
            table: "trip_expenses", where: "trip_id = ?", bindings: [tripId]
        )
        for payload in expensePayloads {
            let expense = try DateCoding.decoder().decode(TripExpense.self, from: payload)
            let key = expense.currency.uppercased()
            let current = actualByCurrency[key] ?? (0.0, 0)
            actualByCurrency[key] = (
                current.0 + (expense.amount as NSDecimalNumber).doubleValue, current.count + 1
            )
        }

        var planCost: OutcomeCost?
        if let planId = trip.activePlanVersionId,
           let planPayload = try database.loadPayloads(
               table: "plan_versions", where: "id = ?", bindings: [planId]
           ).first,
           let plan = try? DateCoding.decoder().decode(PlanVersionDetail.self, from: planPayload) {
            let legs = plan.legs ?? []
            let known = legs.compactMap { $0.costAmount }
            let unknown = legs.filter { $0.costAmount == nil }.count
            planCost = OutcomeCost(
                total: known.isEmpty ? nil : known.reduce(0, +),
                currency: trip.currency,
                unknownCount: unknown
            )
        }

        let window: [String: String]? = trip.window.map { window in
            [
                "start": SQLTimestamp.basics.string(from: window.start),
                "end": SQLTimestamp.basics.string(from: window.end),
            ]
        }

        return TripOutcome(
            tripId: tripId,
            status: trip.status,
            window: window,
            visits: visitStatus,
            visitLabels: visitLabels,
            recordOutcomes: recordOutcomes,
            recordOutcomeLabels: recordLabels,
            planCost: planCost,
            actualCost: actualByCurrency.keys.sorted().map { key in
                OutcomeCurrencyTotal(
                    currency: key,
                    amount: actualByCurrency[key]!.0,
                    count: actualByCurrency[key]!.1
                )
            },
            insightsByKind: insightKinds,
            insightKindLabels: insightKindLabels,
            opportunitySignals: insightSignals,
            memorySummaries: nil
        )
    }

    // MARK: - 拜访简报（VisitBriefing）

    static func visitBriefing(
        database: LocalDatabase, visitId: String
    ) throws -> VisitBriefing {
        let visitPayloads = try database.loadPayloads(table: "visits", where: "id = ?", bindings: [visitId])
        guard let visitPayload = visitPayloads.first else {
            throw APIError.serverError(code: "NOT_FOUND", message: "拜访不存在", traceId: nil, details: [:])
        }
        let visit = try DateCoding.decoder().decode(TripVisit.self, from: visitPayload)

        // 地点
        var place: VisitBriefingPlace?
        if let placeId = visit.placeId,
           let placePayload = try database.loadPayloads(
               table: "places", where: "id = ?", bindings: [placeId]
           ).first,
           let placeModel = try? DateCoding.decoder().decode(Place.self, from: placePayload) {
            place = VisitBriefingPlace(
                id: placeModel.id ?? placeId,
                name: placeModel.name,
                addressLine: placeModel.address,
                city: placeModel.city,
                region: nil,
                countryCode: placeModel.countryCode,
                timezone: placeModel.timezone,
                lat: placeModel.latitude,
                lon: placeModel.longitude
            )
        }

        // 同客户历史拜访（本地无 customers 关联，按地点名称匹配历史 trips 中的同地点拜访）
        var history: [VisitBriefingHistory] = []
        if let placeId = visit.placeId {
            let siblingVisits = try database.loadPayloads(
                table: "visits", where: "place_id = ? AND id <> ?", bindings: [placeId, visitId]
            )
            for payload in siblingVisits.prefix(10) {
                guard let sibling = try? DateCoding.decoder().decode(TripVisit.self, from: payload),
                      let siblingTripId = sibling.tripId else { continue }
                let tripName = (try? database.loadPayloads(
                    table: "trips", where: "id = ?", bindings: [siblingTripId]
                ).first.flatMap { try? DateCoding.decoder().decode(Trip.self, from: $0) })?.name ?? "未知出行"
                var outcome: String?
                var notes: String?
                if let recordPayload = try database.loadPayloads(
                    table: "visit_records", where: "visit_id = ?", bindings: [sibling.id]
                ).first,
                   let object = try? JSONSerialization.jsonObject(with: recordPayload) as? [String: Any] {
                    outcome = object["outcome"] as? String
                    notes = object["notes"] as? String
                }
                history.append(
                    VisitBriefingHistory(
                        visitId: sibling.id,
                        tripId: siblingTripId,
                        tripName: tripName,
                        date: sibling.timeWindowStart ?? sibling.createdAt,
                        title: sibling.title,
                        status: sibling.status,
                        outcome: outcome,
                        notes: notes
                    )
                )
            }
        }

        var gaps: [VisitBriefingGap] = []
        if visit.placeId == nil {
            gaps.append(VisitBriefingGap(code: "PLACE_MISSING", message: "该拜访没有关联地点"))
        }
        if place?.lat == nil || place?.lon == nil {
            gaps.append(VisitBriefingGap(code: "COORDINATES_MISSING", message: "地点缺少坐标，地图与行程估算不可用"))
        }
        if history.isEmpty {
            gaps.append(VisitBriefingGap(code: "HISTORY_EMPTY", message: "该客户没有历史拜访记录"))
        }

        return VisitBriefing(
            visit: visit,
            customer: nil,
            place: place,
            history: history,
            followUps: nil,
            memories: nil,
            dataGaps: gaps
        )
    }

    // MARK: - 标签

    private static func statusLabel(_ status: String) -> String {
        switch status.uppercased() {
        case "PLANNED": return "计划中"
        case "SCHEDULED": return "已排期"
        case "COMPLETED": return "已完成"
        case "CANCELLED": return "已取消"
        default: return status
        }
    }

    private static func outcomeLabel(_ outcome: String) -> String {
        switch outcome.uppercased() {
        case "MET": return "已会面"
        case "NOT_MET": return "未会面"
        case "RESCHEDULED": return "改期"
        case "CANCELLED": return "取消"
        default: return outcome
        }
    }

    private static func insightLabel(_ kind: String) -> String {
        switch kind.uppercased() {
        case "SUMMARY": return "摘要"
        case "CUSTOMER_NEED": return "客户需求"
        case "OPPORTUNITY_SIGNAL": return "机会信号"
        case "DEAL_TERM": return "交易条款"
        case "COMMITMENT": return "承诺事项"
        case "STAGE_CHANGE": return "阶段变化"
        default: return kind
        }
    }
}
