import Foundation

private struct LocalImportStagedRow: Codable {
    var rowNumber: Int
    var status: ImportRowStatus
    var normalized: LocalImportNormalizedRow?
    var confirmationStatus: String?
}

private struct LocalImportCustomerPlaceLink: Codable {
    let customerId: String
    let placeId: String
    let label: String?
    let isPrimary: Bool
}

private struct LocalImportBatch: Codable {
    let id: String
    let tripId: String
    let filename: String
    var status: String
    var result: ImportParseResult
    var rows: [LocalImportStagedRow]
}

extension LocalTripRepository {
    func performParseImport(
        tripId: String,
        fileData: Data,
        filename: String
    ) async throws -> ImportParseResult {
        let trip = try await self.trip(id: tripId)
        let document = try LocalSpreadsheetImportParser.readDocument(data: fileData, filename: filename)
        let missingColumns = ["customer_name"].filter { !document.recognizedColumns.contains($0) }
        guard missingColumns.isEmpty else {
            throw APIError.serverError(
                code: "MISSING_COLUMN",
                message: "表格缺少必需列：客户名称",
                traceId: nil,
                details: ["missing_columns": .array(missingColumns.map(JSONValue.string))]
            )
        }

        let timezone = trip.originPlace?.timezone.flatMap(TimeZone.init(identifier:)) ?? .current
        let existingKeys = try existingVisitFingerprints(tripId: tripId)
        var seen: [String: Int] = [:]
        var importedRows: [ImportedRow] = []
        var stagedRows: [LocalImportStagedRow] = []

        for row in document.rows {
            let validation = LocalSpreadsheetImportParser.validate(row: row, timezone: timezone)
            var status = validation.status
            var issues = validation.issues
            if !document.unknownColumns.isEmpty {
                issues.append(LocalSpreadsheetImportParser.issue(
                    "UNKNOWN_COLUMNS", nil, "WARNING",
                    "以下列无法识别并会被忽略：" + document.unknownColumns.joined(separator: "、")
                ))
            }

            if let normalized = validation.normalized {
                let timeKey = normalized.timeWindowStart.map {
                    String(Int64($0.timeIntervalSince1970))
                } ?? ""
                let duplicateKey = [
                    normalized.customerName.lowercased(),
                    (normalized.city ?? "").lowercased(),
                    (normalized.countryCode ?? "").uppercased(),
                    timeKey,
                ].joined(separator: "|")
                if let first = seen[duplicateKey] {
                    issues.append(LocalSpreadsheetImportParser.issue(
                        "DUPLICATE_ROW", nil, "WARNING",
                        "与第 \(first) 行重复（同客户、同城市、同国家、同时间窗）"
                    ))
                    if status == .valid { status = .duplicate }
                } else {
                    seen[duplicateKey] = row.rowNumber
                }

                let fingerprint = Self.visitFingerprint(
                    name: normalized.customerName,
                    city: normalized.city,
                    countryCode: normalized.countryCode
                )
                if existingKeys.contains(fingerprint) {
                    issues.append(LocalSpreadsheetImportParser.issue(
                        "ALREADY_IN_TRIP", nil, "WARNING",
                        "该出行中已存在同客户、同城市、同国家的拜访；请核对是否重复安排"
                    ))
                }
            }

            importedRows.append(ImportedRow(
                rowNumber: row.rowNumber,
                status: status,
                issues: issues,
                raw: row.values.mapValues(JSONValue.string),
                normalized: validation.normalized?.jsonFields
            ))
            stagedRows.append(LocalImportStagedRow(
                rowNumber: row.rowNumber,
                status: status,
                normalized: validation.normalized,
                confirmationStatus: nil
            ))
        }

        let validCount = importedRows.filter { $0.status == .valid }.count
        let invalidCount = importedRows.filter { $0.status == .invalid }.count
        let duplicateCount = importedRows.filter { $0.status == .duplicate }.count
        let importId = UUID().uuidString
        let report: [String: JSONValue] = [
            "schema_version": .string("1"),
            "counts": .object([
                "total": .number(Double(importedRows.count)),
                "valid": .number(Double(validCount)),
                "invalid": .number(Double(invalidCount)),
                "duplicate": .number(Double(duplicateCount)),
            ]),
            "unknown_columns": .array(document.unknownColumns.map(JSONValue.string)),
        ]
        let result = ImportParseResult(
            importId: importId,
            tripId: tripId,
            filename: filename,
            status: "PARSED",
            totalRows: importedRows.count,
            validRows: validCount,
            invalidRows: invalidCount,
            duplicateRows: duplicateCount,
            columns: document.columns,
            missingColumns: [],
            unknownColumns: document.unknownColumns,
            rows: importedRows,
            report: report
        )
        let batch = LocalImportBatch(
            id: importId,
            tripId: tripId,
            filename: filename,
            status: "PARSED",
            result: result,
            rows: stagedRows
        )
        let encoded = try DateCoding.encoder().encode(batch)
        guard let encodedText = String(bytes: encoded, encoding: .utf8) else {
            throw APIError.invalidResponse("无法暂存导入预览")
        }
        try database.setKeyValue("local-import:\(importId)", value: encodedText)
        return result
    }

    func performConfirmImport(
        tripId: String,
        request: ConfirmImportRequest
    ) async throws -> ConfirmImportResult {
        _ = try await self.trip(id: tripId)
        let storageKey = "local-import:\(request.importId)"
        let requested = request.acceptedRowNumbers
        let requestedSet = Set(requested)
        guard requestedSet.count == requested.count else {
            throw APIError.serverError(code: "VALIDATION_ERROR", message: "确认行号包含重复值", traceId: nil, details: [:])
        }
        let autoAcceptValidRows = requestedSet.isEmpty
        var acceptedRows = 0
        var rejectedRows = 0
        var createdCustomers = 0
        var createdPlaces = 0
        var createdVisits = 0
        var reusedCustomers = 0
        var reusedPlaces = 0
        var warnings: [String] = []

        try database.transaction {
            // BEGIN IMMEDIATE serializes concurrent confirmations. Always re-read the batch only
            // after acquiring the write lock so a second caller observes CONFIRMED and cannot
            // create a duplicate set of customer/visit rows.
            var batch = try loadConfirmableImportBatch(
                storageKey: storageKey,
                tripId: tripId,
                importId: request.importId
            )
            let knownRows = Set(batch.rows.map(\.rowNumber))
            guard requestedSet.isSubset(of: knownRows) else {
                throw APIError.serverError(
                    code: "VALIDATION_ERROR",
                    message: "确认行号不属于该导入批次",
                    traceId: nil,
                    details: [:]
                )
            }

            // Decode each business table once per import, then update the in-memory snapshots as
            // rows are created. Avoid a full SQLite table scan for every spreadsheet row.
            var customerCache = try database.loadPayloads(table: "customers")
                .map { try DateCoding.decoder().decode(Customer.self, from: $0) }
            var placeCache = try database.loadPayloads(table: "places")
                .map { try DateCoding.decoder().decode(Place.self, from: $0) }

            for index in batch.rows.indices {
                var row = batch.rows[index]
                let explicitlySelected = requestedSet.contains(row.rowNumber)
                let selected = autoAcceptValidRows ? row.status == .valid : explicitlySelected

                if row.status == .invalid {
                    if explicitlySelected {
                        row.confirmationStatus = "REJECTED"
                        rejectedRows += 1
                        warnings.append("第 \(row.rowNumber) 行存在阻断性问题，已拒绝入库")
                    } else {
                        row.confirmationStatus = "SKIPPED"
                    }
                    batch.rows[index] = row
                    continue
                }
                guard selected, let normalized = row.normalized else {
                    row.confirmationStatus = "SKIPPED"
                    batch.rows[index] = row
                    continue
                }

                let customerResult = try resolveImportedCustomer(normalized, cache: &customerCache)
                if customerResult.reused { reusedCustomers += 1 } else { createdCustomers += 1 }
                let customer = customerResult.customer

                let placeResult = try resolveImportedPlace(normalized, cache: &placeCache)
                if placeResult.reused { reusedPlaces += 1 } else { createdPlaces += 1 }
                let place = placeResult.place
                guard let placeId = place.id else {
                    throw APIError.invalidResponse("导入地点保存后缺少编号")
                }
                try linkImportedCustomerPlace(customerId: customer.id, placeId: placeId)

                let visit = TripVisit(
                    id: UUID().uuidString,
                    tripId: tripId,
                    placeId: placeId,
                    customerId: customer.id,
                    title: normalized.visitTitle,
                    serviceMinutes: normalized.serviceMinutes,
                    timeWindowStart: normalized.timeWindowStart,
                    timeWindowEnd: normalized.timeWindowEnd,
                    priority: normalized.priority,
                    businessValue: Double(normalized.businessValue),
                    isImmovable: normalized.isImmovable,
                    movementMode: normalized.movementMode,
                    status: "PLANNED",
                    createdAt: Date()
                )
                try database.upsert(
                    table: "visits",
                    id: visit.id,
                    columns: [
                        "trip_id": tripId,
                        "place_id": placeId,
                        "title": visit.title,
                        "status": "PLANNED",
                        "lat": normalized.latitude,
                        "lon": normalized.longitude,
                    ],
                    payload: try DateCoding.encode(visit)
                )
                row.confirmationStatus = "ACCEPTED"
                batch.rows[index] = row
                acceptedRows += 1
                createdVisits += 1

                if normalized.latitude == nil || normalized.longitude == nil {
                    warnings.append("第 \(row.rowNumber) 行缺少坐标；该拜访暂不会进入路线优化")
                }
                if row.status == .duplicate {
                    warnings.append("第 \(row.rowNumber) 行原本标记为批内重复，但已按用户明确选择导入")
                }
            }

            batch.status = "CONFIRMED"
            minimizeConfirmedBatch(&batch)
            let updated = try DateCoding.encoder().encode(batch)
            guard let updatedText = String(bytes: updated, encoding: .utf8) else {
                throw APIError.invalidResponse("无法保存导入确认状态")
            }
            try database.setKeyValue(storageKey, value: updatedText)
            // Match the server contract: every confirmed batch invalidates the current plan once,
            // including batches where all selected rows are rejected or skipped.
            try touchTripAfterImport(tripId: tripId)
        }

        return ConfirmImportResult(
            importId: request.importId,
            status: "CONFIRMED",
            acceptedRows: acceptedRows,
            rejectedRows: rejectedRows,
            createdCustomers: createdCustomers,
            createdPlaces: createdPlaces,
            createdVisits: createdVisits,
            reusedCustomers: reusedCustomers,
            reusedPlaces: reusedPlaces,
            warnings: Array(warnings.prefix(50))
        )
    }

    private func loadConfirmableImportBatch(
        storageKey: String,
        tripId: String,
        importId: String
    ) throws -> LocalImportBatch {
        guard let encoded = database.keyValue(storageKey), let data = encoded.data(using: .utf8) else {
            throw APIError.serverError(
                code: "NOT_FOUND",
                message: "导入批次不存在",
                traceId: nil,
                details: ["import_id": .string(importId)]
            )
        }
        let batch: LocalImportBatch
        do {
            batch = try DateCoding.decoder().decode(LocalImportBatch.self, from: data)
        } catch {
            throw APIError.invalidResponse("本地导入批次损坏，无法确认")
        }
        guard batch.tripId == tripId else {
            throw APIError.serverError(
                code: "NOT_FOUND",
                message: "导入批次不属于当前出行",
                traceId: nil,
                details: ["import_id": .string(importId)]
            )
        }
        guard batch.status == "PARSED" else {
            throw APIError.serverError(
                code: "IMPORT_ALREADY_CONFIRMED",
                message: "该导入批次已处理，不能重复确认",
                traceId: nil,
                details: ["import_id": .string(importId), "status": .string(batch.status)]
            )
        }
        return batch
    }

    private func minimizeConfirmedBatch(_ batch: inout LocalImportBatch) {
        batch.result.rows = []
        batch.rows = batch.rows.map { row in
            LocalImportStagedRow(
                rowNumber: row.rowNumber,
                status: row.status,
                normalized: nil,
                confirmationStatus: row.confirmationStatus
            )
        }
    }

    private func resolveImportedCustomer(
        _ value: LocalImportNormalizedRow,
        cache: inout [Customer]
    ) throws -> (customer: Customer, reused: Bool) {
        let customers = cache
        if let externalRef = value.externalRef,
           let match = customers.first(where: {
               $0.externalRef?.localizedCaseInsensitiveCompare(externalRef) == .orderedSame
           }) {
            return (match, true)
        }
        if let match = customers.first(where: {
            $0.name.localizedCaseInsensitiveCompare(value.customerName) == .orderedSame
                && $0.countryCode?.uppercased() == value.countryCode?.uppercased()
        }) {
            return (match, true)
        }

        let now = Date()
        let customer = Customer(
            id: UUID().uuidString,
            name: value.customerName,
            externalRef: value.externalRef,
            tier: value.tier,
            countryCode: value.countryCode,
            industry: value.industry,
            primaryContactName: value.contactName,
            primaryContactEmail: value.contactEmail,
            primaryContactPhone: value.contactPhone,
            status: "ACTIVE",
            ownerUserId: "local-user",
            createdAt: now,
            updatedAt: now
        )
        try database.upsert(
            table: "customers",
            id: customer.id,
            columns: ["name": customer.name, "tier": customer.tier],
            payload: try DateCoding.encode(customer)
        )
        cache.append(customer)
        return (customer, false)
    }

    private func resolveImportedPlace(
        _ value: LocalImportNormalizedRow,
        cache: inout [Place]
    ) throws -> (place: Place, reused: Bool) {
        let name = value.addressLine ?? value.customerName
        let places = cache
        if let match = places.first(where: {
            $0.name.localizedCaseInsensitiveCompare(name) == .orderedSame
                && sameOptionalText($0.city, value.city)
                && $0.countryCode?.uppercased() == value.countryCode?.uppercased()
        }) {
            return (match, true)
        }

        let hasCoordinates = value.latitude != nil && value.longitude != nil
        let place = Place(
            id: UUID().uuidString,
            name: name,
            countryCode: value.countryCode,
            timezone: value.timezone,
            latitude: value.latitude,
            longitude: value.longitude,
            city: value.city,
            address: value.addressLine,
            region: value.region,
            geocodeStatus: hasCoordinates ? "RESOLVED" : "MISSING",
            geocodeSource: hasCoordinates ? "user_import" : nil
        )
        try database.upsert(
            table: "places",
            id: place.id ?? "",
            columns: ["name": place.name, "city": place.city],
            payload: try DateCoding.encode(place)
        )
        cache.append(place)
        return (place, false)
    }

    private func sameOptionalText(_ lhs: String?, _ rhs: String?) -> Bool {
        switch (lhs, rhs) {
        case (nil, nil): return true
        case let (left?, right?): return left.localizedCaseInsensitiveCompare(right) == .orderedSame
        default: return false
        }
    }

    private func linkImportedCustomerPlace(customerId: String, placeId: String) throws {
        let existing = try database.loadPayloads(
            table: "customer_places",
            where: "customer_id = ? AND is_primary = 1",
            bindings: [customerId]
        ).map { try DateCoding.decoder().decode(LocalImportCustomerPlaceLink.self, from: $0) }

        for current in existing where current.placeId != placeId {
            let demoted = LocalImportCustomerPlaceLink(
                customerId: current.customerId,
                placeId: current.placeId,
                label: current.label,
                isPrimary: false
            )
            try database.upsert(
                table: "customer_places",
                id: "\(customerId)::\(current.placeId)",
                columns: [
                    "customer_id": customerId,
                    "place_id": current.placeId,
                    "label": current.label,
                    "is_primary": 0,
                ],
                payload: try DateCoding.encode(demoted)
            )
        }

        let link = LocalImportCustomerPlaceLink(
            customerId: customerId,
            placeId: placeId,
            label: nil,
            isPrimary: true
        )
        try database.upsert(
            table: "customer_places",
            id: "\(customerId)::\(placeId)",
            columns: [
                "customer_id": customerId,
                "place_id": placeId,
                "label": nil,
                "is_primary": 1,
            ],
            payload: try DateCoding.encode(link)
        )
    }

    private func touchTripAfterImport(tripId: String) throws {
        let payloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        var trip = try DateCoding.decoder().decode(Trip.self, from: payload)
        trip.version += 1
        trip.updatedAt = Date()
        try database.upsert(
            table: "trips",
            id: trip.id,
            columns: [
                "name": trip.name,
                "status": trip.status ?? "DRAFT",
                "version": trip.version,
                "window_start": trip.window.map { SQLTimestamp.basics.string(from: $0.start) },
                "window_end": trip.window.map { SQLTimestamp.basics.string(from: $0.end) },
                "active_plan_id": trip.activePlanVersionId,
                "updated_at": SQLTimestamp.basics.string(from: trip.updatedAt ?? Date()),
            ],
            payload: try DateCoding.encode(trip)
        )
    }

    private func existingVisitFingerprints(tripId: String) throws -> Set<String> {
        let customers = try database.loadPayloads(table: "customers")
            .map { try DateCoding.decoder().decode(Customer.self, from: $0) }
        let places = try database.loadPayloads(table: "places")
            .map { try DateCoding.decoder().decode(Place.self, from: $0) }
        let customerById = Dictionary(uniqueKeysWithValues: customers.map { ($0.id, $0) })
        let placeById = Dictionary(uniqueKeysWithValues: places.compactMap { value in
            value.id.map { ($0, value) }
        })
        let visits = try database.loadPayloads(
            table: "visits", where: "trip_id = ?", bindings: [tripId]
        ).map { try DateCoding.decoder().decode(TripVisit.self, from: $0) }

        return Set(visits.compactMap { visit in
            guard let customerId = visit.customerId,
                  let customer = customerById[customerId],
                  let placeId = visit.placeId,
                  let place = placeById[placeId] else { return nil }
            return Self.visitFingerprint(
                name: customer.name,
                city: place.city,
                countryCode: place.countryCode ?? customer.countryCode
            )
        })
    }

    private static func visitFingerprint(name: String, city: String?, countryCode: String?) -> String {
        "\(name.lowercased())|\((city ?? "").lowercased())|\((countryCode ?? "").uppercased())"
    }
}
