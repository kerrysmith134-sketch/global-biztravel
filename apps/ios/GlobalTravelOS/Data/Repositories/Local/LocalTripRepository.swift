import Foundation

/// 本地模式尚未实现的方法统一抛这个：UI 显示错误横幅，随阶段推进逐个替换。
struct LocalStorePendingError: LocalizedError {
    let method: String

    var errorDescription: String? {
        "本地模式暂未实现：\(method)（随本地版阶段推进补齐）"
    }
}

/// 本地模式仓储：SQLite document 表即真相，UI 协议零改动。
///
/// 实现范围随本地版阶段推进（ADR-0036）：
/// - P1 已本地化：trips/places/visits 读写、hold/resume/complete（版本令牌语义）
/// - P4/P7 逐步替换标 `pending` 的领域方法（费用/报销/预算/签证/导入/指南/复盘）
final class LocalTripRepository: TripRepository {
    let database: LocalDatabase
    private let receiptVault: ReceiptVault?

    init(database: LocalDatabase, receiptVault: ReceiptVault? = nil) {
        self.database = database
        self.receiptVault = receiptVault
    }

    // MARK: - 编解码

    private func encode<T: Encodable>(_ value: T) throws -> Data {
        try DateCoding.encoder().encode(value)
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try DateCoding.decoder().decode(type, from: data)
    }

    // MARK: - 行程读写（P1 本地化）

    func listTrips(page: PageRequest) async throws -> Page<Trip> {
        let offset = page.cursor.flatMap { Int($0.replacingOccurrences(of: "offset:", with: "")) } ?? 0
        let payloads = try database.loadPayloads(table: "trips", order: "updated_at DESC")
        let all = try payloads.map { try decode(Trip.self, from: $0) }
        let windowed = Array(all.dropFirst(offset).prefix(page.limit))
        let next = offset + page.limit < all.count
            ? "offset:\(offset + page.limit)"
            : nil
        return Page(items: windowed, nextCursor: next)
    }

    func trip(id: String) async throws -> Trip {
        let payloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [id])
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        return try decode(Trip.self, from: payload)
    }

    /// 乐观并发：version 不匹配即冲突（与 `If-Match` 同语义）。
    private func loadForWrite(id: String, expectedVersion: Int) throws -> Trip {
        let payloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [id])
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try decode(Trip.self, from: payload)
        guard trip.version == expectedVersion else {
            throw APIError.serverError(
                code: "VERSION_CONFLICT",
                message: "出行已被其他操作更新（本地版本 \(trip.version) ≠ 请求 \(expectedVersion)）",
                traceId: nil,
                details: [:]
            )
        }
        return trip
    }

    private func save(_ trip: Trip) throws {
        var updated = trip
        updated.updatedAt = Date()
        let columns: [String: Any?] = [
            "name": updated.name,
            "status": updated.status ?? "DRAFT",
            "version": updated.version,
            "window_start": updated.window.map { SQLTimestamp.basics.string(from: $0.start) },
            "window_end": updated.window.map { SQLTimestamp.basics.string(from: $0.end) },
            "active_plan_id": updated.activePlanVersionId,
            "updated_at": SQLTimestamp.basics.string(from: updated.updatedAt ?? Date()),
        ]
        try database.upsert(
            table: "trips",
            id: updated.id,
            columns: columns,
            payload: try encode(updated)
        )
    }

    func holdTrip(id: String, version: Int) async throws -> Trip {
        var trip = try loadForWrite(id: id, expectedVersion: version)
        guard trip.status == "ACTIVE" else {
            throw APIError.serverError(code: "TRIP_NOT_HOLDABLE", message: "仅进行中的出行可以挂起", traceId: nil, details: [:])
        }
        trip.status = "HOLD"
        trip.version += 1
        try save(trip)
        return trip
    }

    func resumeTrip(id: String, version: Int) async throws -> Trip {
        var trip = try loadForWrite(id: id, expectedVersion: version)
        guard trip.status == "HOLD" else {
            throw APIError.serverError(code: "TRIP_NOT_RESUMABLE", message: "仅挂起中的出行可以恢复", traceId: nil, details: [:])
        }
        // 服务端语义：按生效计划回到 ACTIVE/READY/DRAFT。
        trip.status = trip.activePlanVersionId != nil ? "ACTIVE" : "DRAFT"
        trip.version += 1
        try save(trip)
        return trip
    }

    func completeTrip(id: String, version: Int) async throws -> Trip {
        var trip = try loadForWrite(id: id, expectedVersion: version)
        guard trip.status == "ACTIVE" || trip.status == "HOLD" else {
            throw APIError.serverError(code: "TRIP_NOT_COMPLETABLE", message: "仅进行中/挂起的出行可以完成", traceId: nil, details: [:])
        }
        trip.status = "COMPLETED"
        trip.version += 1
        try save(trip)
        return trip
    }

    /// 本地编排：建 Place + 建 Trip 在同一事务里（远端是两次 HTTP，本地吸收为原子操作）。
    func createTrip(_ request: CreateTripRequest) async throws -> Trip {
        let origin = request.originPlace
        let placeId = UUID().uuidString
        let tripId = UUID().uuidString
        let now = Date()

        let place = Place(
            id: placeId,
            name: origin.name,
            countryCode: origin.countryCode.uppercased(),
            timezone: origin.timezone,
            latitude: origin.latitude,
            longitude: origin.longitude,
            city: origin.name
        )
        let trip = Trip(
            id: tripId,
            organizationId: "local",
            name: request.name,
            purpose: request.purpose,
            status: "DRAFT",
            version: 1,
            window: request.window,
            originPlace: place,
            originPlaceId: placeId,
            activePlanVersionId: nil,
            createdAt: now,
            updatedAt: now,
            activePlan: nil,
            stats: nil,
            currency: nil,
            countries: request.countries
        )

        try database.transaction {
            let placeColumns: [String: Any?] = ["name": place.name, "city": place.city]
            try database.upsert(
                table: "places",
                id: placeId,
                columns: placeColumns,
                payload: try encode(place)
            )
            try save(trip)
            let visitStatus: String? = nil // 新行程无拜访
            _ = visitStatus
        }
        return trip
    }

    // MARK: - 拜访/地点查询（本地行程详情需要）

    func visits(tripId: String) throws -> [TripVisit] {
        let payloads = try database.loadPayloads(
            table: "visits",
            where: "trip_id = ?",
            bindings: [tripId],
            order: "id"
        )
        return try payloads.map { try decode(TripVisit.self, from: $0) }
    }

    func saveVisit(_ visit: TripVisit) throws {
        try database.upsert(
            table: "visits",
            id: visit.id,
            columns: [
                "trip_id": visit.tripId as Any?,
                "title": visit.title as Any?,
                "status": visit.status as Any?,
                "lat": nil as Any?,
                "lon": nil as Any?,
            ],
            payload: try encode(visit)
        )
    }

    // MARK: - P4/P7 逐个替换的领域方法（显式占位）

    func visaCases(tripId: String) async throws -> [VisaCase] {
        let payloads = try database.loadPayloads(
            table: "visa_cases", where: "trip_id = ?", bindings: [tripId], order: "rowid ASC"
        )
        return try payloads.map { try decode(VisaCase.self, from: $0) }
    }

    func createVisaCases(tripId: String, countries: [String]?) async throws -> [VisaCase] {
        try LocalVisaService.createCases(database: database, tripId: tripId, countries: countries)
    }

    func updateVisaCase(id: String, status: VisaCaseStatus) async throws -> VisaCase {
        try LocalVisaService.updateCase(database: database, id: id, status: status)
    }

    func expenses(tripId: String) async throws -> [TripExpense] {
        let payloads = try database.loadPayloads(
            table: "trip_expenses", where: "trip_id = ?", bindings: [tripId], order: "occurred_on"
        )
        return try payloads.map { try decode(TripExpense.self, from: $0) }
    }

    func createExpense(tripId: String, body: TripExpenseCreateRequest) async throws -> TripExpense {
        let occurredOn = ISO8601.day(from: body.occurredOn)
        let expense = TripExpense(
            id: UUID().uuidString,
            tripId: tripId,
            category: body.category.rawValue,
            amount: body.amount,
            currency: body.currency.uppercased(),
            occurredOn: occurredOn,
            note: body.note,
            receiptRef: nil,
            receiptAssetId: body.receiptAssetId
        )
        try database.upsert(
            table: "trip_expenses", id: expense.id,
            columns: [
                "trip_id": tripId, "category": expense.category,
                "currency": expense.currency,
                "occurred_on": body.occurredOn,
            ],
            payload: try DateCoding.encode(expense)
        )
        return expense
    }

    /// 预算折算（ADR-0019 语义直译）：按币种聚合；同币种直接差；
    /// base 折算逐币种取最新汇率（含逆率）；缺汇率 conversion_complete=false 并列缺口。
    func budgetSummary(tripId: String, baseCurrency: String?) async throws -> BudgetSummary {
        let trip = try await self.trip(id: tripId)
        let expenses = try await self.expenses(tripId: tripId)

        var byCurrency: [String: (amount: Decimal, count: Int)] = [:]
        for expense in expenses {
            let key = expense.currency.uppercased()
            let current = byCurrency[key] ?? (Decimal.zero, 0)
            byCurrency[key] = (current.amount + expense.amount, current.count + 1)
        }
        let totals = byCurrency.keys.sorted().map { key in
            CurrencyTotal(
                currency: key,
                amount: (byCurrency[key]!.amount as NSDecimalNumber).doubleValue,
                count: byCurrency[key]!.count
            )
        }

        let base = (baseCurrency ?? trip.currency ?? "EUR").uppercased()
        var unconverted: [String] = []
        var convertedTotal = Decimal.zero
        var conversions: [FxConversion] = []
        var conversionComplete = true

        for total in totals {
            if total.currency == base {
                convertedTotal += Decimal(string: String(total.amount)) ?? Decimal.zero
                conversions.append(
                    FxConversion(currency: total.currency, rate: 1.0, asOf: nil, source: nil, viaInverse: false)
                )
                continue
            }
            guard let quote = fxConversionFor(base: base, currency: total.currency) else {
                unconverted.append(total.currency)
                conversionComplete = false
                continue
            }
            let rate = Decimal(string: String(quote.rate)) ?? 0
            convertedTotal += (Decimal(string: String(total.amount)) ?? Decimal.zero) * rate
            conversions.append(
                FxConversion(
                    currency: total.currency, rate: quote.rate, asOf: quote.asOf,
                    source: quote.source, viaInverse: quote.viaInverse
                )
            )
        }

        // 本地 Trip DTO 未暴露预算字段（服务端 TripRead 亦未解码）⇒ 预算差额留空，折算照常。
        let budgetAmount: Decimal? = nil
        let variance = budgetAmount.map { $0 - convertedTotal }
        return BudgetSummary(
            budgetAmount: budgetAmount,
            budgetCurrency: base,
            plannedCostTotal: nil,
            plannedCostCurrency: nil,
            plannedCostUnknownCount: nil,
            actualByCurrency: totals,
            actualTotalInBudgetCurrency: conversionComplete ? convertedTotal : nil,
            varianceVsBudget: conversionComplete ? variance : nil,
            varianceVsPlan: nil,
            conversionBaseCurrency: baseCurrency != nil ? base : nil,
            convertedActualTotal: conversionComplete ? convertedTotal : nil,
            convertedPlannedCostTotal: nil,
            convertedVarianceVsBudget: conversionComplete ? variance : nil,
            conversionComplete: baseCurrency != nil ? conversionComplete : nil,
            unconvertedCurrencies: conversionComplete ? nil : unconverted,
            fxConversions: conversions
        )
    }

    /// 取 base→currency 最新汇率；支持反向记录（取倒数）。
    private func fxConversionFor(base: String, currency: String)
        -> (rate: Double, asOf: Date?, source: String?, viaInverse: Bool)?
    {
        let direct = try? database.store.query(
            "SELECT rate, as_of, source FROM fx_rates WHERE base = ? AND quote = ? ORDER BY as_of DESC LIMIT 1",
            bindings: [base, currency]
        ) { row -> (Double, Date?, String?, Bool) in
            (Double(row.string(0)) ?? 0, row.stringOpt(1).flatMap { ISO8601.date(from: $0) }, row.stringOpt(2), false)
        }
        if let hit = direct?.first {
            return (rate: hit.0, asOf: hit.1, source: hit.2, viaInverse: hit.3)
        }
        let inverse = try? database.store.query(
            "SELECT rate, as_of, source FROM fx_rates WHERE base = ? AND quote = ? ORDER BY as_of DESC LIMIT 1",
            bindings: [currency, base]
        ) { row -> (Double, Date?, String?, Bool) in
            (Double(row.string(0)) ?? 0, row.stringOpt(1).flatMap { ISO8601.date(from: $0) }, row.stringOpt(2), true)
        }
        if let hit = inverse?.first, hit.0 > 0 {
            return (rate: 1.0 / hit.0, asOf: hit.1, source: hit.2, viaInverse: hit.3)
        }
        return nil
    }

    /// Read-only assessment from the current local facts. A local receipt ID means the
    /// original was captured in the private vault before expense submission; it does not
    /// claim that the asset has been uploaded to a server.
    func closeoutReadiness(tripId: String) async throws -> TripCloseoutReadiness {
        let trip = try await self.trip(id: tripId)
        let visits = try self.visits(tripId: tripId)
        let expenses = try await self.expenses(tripId: tripId)
        let reports = try await self.expenseReports(tripId: tripId)

        var recordedVisitIds = Set<String>()
        for visit in visits {
            let payloads = try database.loadPayloads(
                table: "visit_records",
                where: "visit_id = ?",
                bindings: [visit.id]
            )
            let records = try payloads.map { try decode(VisitRecord.self, from: $0) }
            if !records.isEmpty { recordedVisitIds.insert(visit.id) }
        }

        var blockers: [TripCloseoutIssue] = []
        var warnings: [TripCloseoutIssue] = []

        for visit in visits {
            let status = (visit.status ?? "PLANNED").uppercased()
            if status == "PLANNED" || status == "SCHEDULED" {
                blockers.append(
                    closeoutIssue(
                        code: "VISIT_NOT_CLOSED",
                        severity: .blocker,
                        title: "拜访尚未完成",
                        detail: "拜访状态为 \(status)，请完成、取消或更新真实拜访结果",
                        sourceType: "VISIT",
                        sourceId: visit.id,
                        actionHint: "打开该拜访并记录真实结果；不要为结账虚构完成状态"
                    )
                )
            } else if (status == "DONE" || status == "MISSED")
                        && !recordedVisitIds.contains(visit.id) {
                blockers.append(
                    closeoutIssue(
                        code: "VISIT_OUTCOME_MISSING",
                        severity: .blocker,
                        title: "缺少拜访结果记录",
                        detail: "该拜访已标记完成或未能拜访，但没有本地现场记录",
                        sourceType: "VISIT",
                        sourceId: visit.id,
                        actionHint: "补录并保存真实现场拜访结果"
                    )
                )
            }
        }

        let reportItems = reports.flatMap(\.itemList)
        let snapshottedExpenseIds = Set(reportItems.compactMap(\.expenseId))
        let unreportedExpenses = expenses.filter { !snapshottedExpenseIds.contains($0.id) }
        for expense in unreportedExpenses {
            blockers.append(
                closeoutIssue(
                    code: "EXPENSE_NOT_IN_REPORT",
                    severity: .blocker,
                    title: "费用尚未纳入报销快照",
                    detail: "\(expense.category) · \(expense.amount) \(expense.currency)",
                    sourceType: "EXPENSE",
                    sourceId: expense.id,
                    actionHint: "将该费用加入报销单并重新检查报销快照"
                )
            )
        }

        let latestReport = reports.max {
            ($0.createdAt ?? .distantPast) < ($1.createdAt ?? .distantPast)
        }
        if let latestReport, latestReport.status == .rejected {
            blockers.append(
                closeoutIssue(
                    code: "LATEST_REPORT_REJECTED",
                    severity: .blocker,
                    title: "最新报销单已被驳回",
                    detail: "需要修正费用或凭证并创建新的报销快照",
                    sourceType: "EXPENSE_REPORT",
                    sourceId: latestReport.id,
                    actionHint: "查看驳回原因，修正费用/票据后创建新的报销单"
                )
            )
        }

        var grouped: [String: (amount: Decimal, count: Int)] = [:]
        for expense in expenses {
            let currency = expense.currency.uppercased()
            let current = grouped[currency] ?? (Decimal.zero, 0)
            grouped[currency] = (current.amount + expense.amount, current.count + 1)
        }
        let totals = grouped.keys.sorted().map { currency in
            let value = grouped[currency] ?? (Decimal.zero, 0)
            return CurrencyTotal(
                currency: currency,
                amount: NSDecimalNumber(decimal: value.amount).doubleValue,
                count: value.count
            )
        }
        if totals.count > 1 {
            warnings.append(
                closeoutIssue(
                    code: "MULTI_CURRENCY_REVIEW",
                    severity: .warning,
                    title: "费用包含多种币种",
                    detail: "各币种金额分别核对；本评估不会使用估算或缺失汇率合并总额",
                    sourceType: "TRIP",
                    sourceId: trip.id,
                    actionHint: "按原始币种分别核对金额与报销政策"
                )
            )
        }

        let expensesWithoutReceipt = expenses.filter { $0.receiptAssetId == nil }
        for expense in expensesWithoutReceipt {
            warnings.append(
                closeoutIssue(
                    code: "RECEIPT_MISSING",
                    severity: .warning,
                    title: "费用缺少原始票据",
                    detail: "\(expense.category) · \(expense.amount) \(expense.currency)",
                    sourceType: "EXPENSE",
                    sourceId: expense.id,
                    actionHint: "补充原始票据，或按组织适用政策记录例外"
                )
            )
        }

        var expensesWithUnavailableReceipt: [TripExpense] = []
        for expense in expenses where expense.receiptAssetId != nil {
            do {
                guard let receiptVault, let assetId = expense.receiptAssetId else {
                    throw ReceiptVaultError.assetNotFound
                }
                try receiptVault.verifyOriginal(assetId: assetId, tripId: tripId)
            } catch {
                expensesWithUnavailableReceipt.append(expense)
            }
        }
        for expense in expensesWithUnavailableReceipt {
            warnings.append(
                closeoutIssue(
                    code: "RECEIPT_UNAVAILABLE",
                    severity: .warning,
                    title: "原始票据不可用",
                    detail: "\(expense.category) · \(expense.amount) \(expense.currency)",
                    sourceType: "EXPENSE",
                    sourceId: expense.id,
                    actionHint: "重新采集原始票据，确认文件完整且归属于当前行程"
                )
            )
        }

        // LocalFollowUp data is already persisted by the same local visit repository.
        var openFollowUps: [VisitFollowUp] = []
        let localVisits = LocalVisitRepository(database: database)
        for visit in visits {
            let feed = try await localVisits.followUps(visitId: visit.id)
            openFollowUps.append(contentsOf: feed.items.filter {
                $0.status == "OPEN" || $0.status == "IN_PROGRESS"
            })
        }
        for followUp in openFollowUps {
            warnings.append(
                closeoutIssue(
                    code: "BUSINESS_FOLLOW_UP_OPEN",
                    severity: .warning,
                    title: "仍有商务跟进事项未完成",
                    detail: followUp.title,
                    sourceType: "VISIT_FOLLOW_UP",
                    sourceId: followUp.id,
                    actionHint: "打开拜访记录更新商务跟进进度；该事项不会阻断财务结账"
                )
            )
        }

        blockers.sort {
            $0.code == $1.code ? $0.sourceId < $1.sourceId : $0.code < $1.code
        }
        warnings.sort {
            $0.code == $1.code ? $0.sourceId < $1.sourceId : $0.code < $1.code
        }

        let status: TripCloseoutStatus
        if !blockers.isEmpty {
            status = .blocked
        } else if latestReport?.status == .approved {
            status = .reportApproved
        } else if latestReport?.status == .submitted {
            status = .reportSubmitted
        } else if !warnings.isEmpty {
            status = .readyWithExceptions
        } else {
            status = .readyForReview
        }

        let receiptAssetIds = Set(expenses.compactMap(\.receiptAssetId))
        let missingOutcomeCount = visits.filter {
            let value = ($0.status ?? "PLANNED").uppercased()
            return (value == "DONE" || value == "MISSED") && !recordedVisitIds.contains($0.id)
        }.count

        return TripCloseoutReadiness(
            tripId: trip.id,
            status: status,
            generatedAt: Date(),
            summary: TripCloseoutSummary(
                visitCount: visits.count,
                visitOutcomeCount: recordedVisitIds.count,
                visitsOpenCount: visits.filter { $0.isOpen }.count,
                visitsMissingOutcomeCount: missingOutcomeCount,
                expenseCount: expenses.count,
                expensesInReportCount: expenses.count - unreportedExpenses.count,
                expensesNotInReportCount: unreportedExpenses.count,
                expensesWithoutReceiptCount: expensesWithoutReceipt.count,
                receiptAssetCount: receiptAssetIds.count,
                receiptAssetUnavailableCount: expensesWithUnavailableReceipt.count,
                ocrNeedsReviewCount: 0,
                openBusinessFollowUpCount: openFollowUps.count,
                latestReportId: latestReport?.id,
                latestReportStatus: latestReport?.status,
                blockerCount: blockers.count,
                warningCount: warnings.count
            ),
            totalsByCurrency: totals,
            blockers: blockers,
            warnings: warnings
        )
    }

    private func closeoutIssue(
        code: String,
        severity: TripCloseoutIssueSeverity,
        title: String,
        detail: String,
        sourceType: String,
        sourceId: String,
        actionHint: String
    ) -> TripCloseoutIssue {
        TripCloseoutIssue(
            id: "\(code):\(sourceType):\(sourceId)",
            code: code,
            severity: severity,
            title: title,
            detail: detail,
            sourceType: sourceType,
            sourceId: sourceId,
            actionHint: actionHint
        )
    }

    func expenseReports(tripId: String) async throws -> [ExpenseReport] {
        let payloads = try database.loadPayloads(
            table: "expense_reports", where: "trip_id = ?", bindings: [tripId], order: "rowid ASC"
        )
        return try payloads.map { try decode(ExpenseReport.self, from: $0) }
    }

    /// 报销单 = 费用快照（不可变）；提交后状态机 DRAFT→SUBMITTED。
    func createExpenseReport(tripId: String) async throws -> ExpenseReport {
        let expenses = try await self.expenses(tripId: tripId)
        let reportId = UUID().uuidString
        let now = Date()
        var byCurrency: [String: (Double, Int)] = [:]
        for expense in expenses {
            let key = expense.currency.uppercased()
            let amountDouble = (expense.amount as NSDecimalNumber).doubleValue
            let current = byCurrency[key] ?? (0.0, 0)
            byCurrency[key] = (current.0 + amountDouble, current.1 + 1)
        }
        let totals = byCurrency.keys.sorted().map {
            CurrencyTotal(currency: $0, amount: byCurrency[$0]!.0, count: byCurrency[$0]!.1)
        }
        let items = expenses.map { expense in
            ExpenseReportItem(
                id: UUID().uuidString,
                reportId: reportId,
                expenseId: expense.id,
                category: expense.category,
                amount: expense.amount,
                currency: expense.currency,
                occurredOn: expense.occurredOn,
                note: expense.note,
                receiptRef: expense.receiptAssetId ?? expense.receiptRef
            )
        }
        let report = ExpenseReport(
            id: reportId,
            tripId: tripId,
            status: .draft,
            items: items,
            totals: totals,
            createdAt: now,
            updatedAt: now
        )
        try database.upsert(
            table: "expense_reports", id: reportId,
            columns: [
                "trip_id": tripId, "status": ExpenseReportStatus.draft.rawValue,
            ],
            payload: try DateCoding.encode(report)
        )
        return report
    }

    func submitExpenseReport(id: String) async throws -> ExpenseReport {
        let payloads = try database.loadPayloads(table: "expense_reports", where: "id = ?", bindings: [id])
        guard let payload = payloads.first,
              var report = try? DateCoding.decoder().decode(ExpenseReport.self, from: payload) else {
            throw APIError.serverError(code: "NOT_FOUND", message: "报销单不存在", traceId: nil, details: [:])
        }
        guard report.status == .draft else {
            throw APIError.serverError(
                code: "CONFLICT", message: "仅草稿状态可提交（当前 \(report.status.rawValue)）",
                traceId: nil, details: [:]
            )
        }
        report.status = .submitted
        report.submittedAt = Date()
        report.updatedAt = Date()
        try database.upsert(
            table: "expense_reports", id: id,
            columns: ["trip_id": report.tripId, "status": "SUBMITTED"],
            payload: try DateCoding.encode(report)
        )
        return report
    }

    func entryAssessments(tripId: String) async throws -> [EntryAssessment] {
        let payloads = try database.loadPayloads(
            table: "entry_assessments", where: "trip_id = ?", bindings: [tripId], order: "rowid ASC"
        )
        return try payloads.map { try decode(EntryAssessment.self, from: $0) }
    }

    func refreshEntryAssessments(tripId: String) async throws -> [EntryAssessment] {
        let nationality = LocalTravelerProfile.load(database: database).nationality
        return try LocalVisaService.assessEntry(
            database: database, tripId: tripId, nationality: nationality
        )
    }

    func localGuide(tripId: String) async throws -> LocalGuide {
        try LocalGuideBuilder.build(database: database, tripId: tripId)
    }

    func tripOutcome(tripId: String) async throws -> TripOutcome {
        try LocalAggregations.tripOutcome(database: database, tripId: tripId)
    }

    func parseImport(tripId: String, fileData: Data, filename: String) async throws -> ImportParseResult {
        throw LocalStorePendingError(method: "parseImport")
    }

    func confirmImport(tripId: String, request: ConfirmImportRequest) async throws -> ConfirmImportResult {
        throw LocalStorePendingError(method: "confirmImport")
    }
}
