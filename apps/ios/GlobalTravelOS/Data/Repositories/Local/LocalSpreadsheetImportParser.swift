import CoreXLSX
import Foundation

struct LocalSpreadsheetRow {
    let rowNumber: Int
    let values: [String: String]
}

struct LocalSpreadsheetDocument {
    let columns: [String]
    let recognizedColumns: [String]
    let unknownColumns: [String]
    let rows: [LocalSpreadsheetRow]
}

struct LocalImportNormalizedRow: Codable {
    let customerName: String
    let visitTitle: String
    let externalRef: String?
    let tier: String
    let countryCode: String?
    let city: String?
    let addressLine: String?
    let region: String?
    let latitude: Double?
    let longitude: Double?
    let timezone: String?
    let serviceMinutes: Int
    let priority: Int
    let businessValue: String
    let movementMode: String
    let isImmovable: Bool
    let timeWindowStart: Date?
    let timeWindowEnd: Date?
    let contactName: String?
    let contactEmail: String?
    let contactPhone: String?
    let industry: String?

    var jsonFields: [String: JSONValue] {
        [
            "customer_name": .string(customerName),
            "visit_title": .string(visitTitle),
            "external_ref": externalRef.map(JSONValue.string) ?? .null,
            "tier": .string(tier),
            "country_code": countryCode.map(JSONValue.string) ?? .null,
            "city": city.map(JSONValue.string) ?? .null,
            "address_line": addressLine.map(JSONValue.string) ?? .null,
            "region": region.map(JSONValue.string) ?? .null,
            "latitude": latitude.map(JSONValue.number) ?? .null,
            "longitude": longitude.map(JSONValue.number) ?? .null,
            "timezone": timezone.map(JSONValue.string) ?? .null,
            "service_minutes": .number(Double(serviceMinutes)),
            "priority": .number(Double(priority)),
            "business_value": .string(businessValue),
            "movement_mode": .string(movementMode),
            "is_immovable": .bool(isImmovable),
            "time_window_start": timeWindowStart.map { .string(ISO8601DateFormatter().string(from: $0)) } ?? .null,
            "time_window_end": timeWindowEnd.map { .string(ISO8601DateFormatter().string(from: $0)) } ?? .null,
            "contact_name": contactName.map(JSONValue.string) ?? .null,
            "contact_email": contactEmail.map(JSONValue.string) ?? .null,
            "contact_phone": contactPhone.map(JSONValue.string) ?? .null,
            "industry": industry.map(JSONValue.string) ?? .null,
        ]
    }
}

struct LocalImportValidation {
    let normalized: LocalImportNormalizedRow?
    let status: ImportRowStatus
    let issues: [ImportIssue]
}

/// Native, read-only OOXML parser. The preview stage never writes customer, place or visit rows.
enum LocalSpreadsheetImportParser {
    static let maximumRows = 2_000
    static let maximumFileBytes = 20 * 1_024 * 1_024

    private static let aliases: [String: String] = [
        "customername": "customer_name", "customer": "customer_name", "company": "customer_name",
        "客户": "customer_name", "客户名称": "customer_name", "公司": "customer_name", "公司名称": "customer_name",
        "visittitle": "visit_title", "title": "visit_title", "topic": "visit_title", "拜访主题": "visit_title", "主题": "visit_title",
        "externalref": "external_ref", "customercode": "external_ref", "客户编号": "external_ref",
        "tier": "tier", "grade": "tier", "客户等级": "tier",
        "countrycode": "country_code", "country": "country_code", "国家": "country_code", "国家代码": "country_code",
        "city": "city", "城市": "city",
        "addressline": "address_line", "address": "address_line", "地址": "address_line", "详细地址": "address_line",
        "region": "region", "state": "region", "省": "region", "省份": "region",
        "latitude": "latitude", "lat": "latitude", "纬度": "latitude",
        "longitude": "longitude", "lon": "longitude", "lng": "longitude", "经度": "longitude",
        "timezone": "timezone", "tz": "timezone", "时区": "timezone",
        "serviceminutes": "service_minutes", "durationminutes": "service_minutes",
        "visitminutes": "service_minutes", "服务时长": "service_minutes", "停留时长": "service_minutes",
        "priority": "priority", "优先级": "priority",
        "businessvalue": "business_value", "value": "business_value", "商业价值": "business_value",
        "movementmode": "movement_mode", "transport": "movement_mode", "交通方式": "movement_mode",
        "isimmovable": "is_immovable", "fixed": "is_immovable", "时间固定": "is_immovable",
        "timewindowstart": "time_window_start", "windowstart": "time_window_start",
        "earliest": "time_window_start", "最早到达": "time_window_start", "时间窗开始": "time_window_start",
        "timewindowend": "time_window_end", "windowend": "time_window_end",
        "latest": "time_window_end", "最晚离开": "time_window_end", "时间窗结束": "time_window_end",
        "contactname": "contact_name", "联系人": "contact_name",
        "contactemail": "contact_email", "email": "contact_email", "邮箱": "contact_email",
        "contactphone": "contact_phone", "phone": "contact_phone", "电话": "contact_phone",
        "industry": "industry", "行业": "industry",
    ]

    static func readDocument(data: Data, filename: String) throws -> LocalSpreadsheetDocument {
        let suffix = URL(fileURLWithPath: filename).pathExtension.lowercased()
        guard ["xlsx", "xlsm"].contains(suffix) else {
            throw APIError.invalidResponse("只支持 .xlsx / .xlsm 文件；旧版 .xls 请另存为 .xlsx 后重试")
        }
        guard !data.isEmpty, data.count <= maximumFileBytes else {
            throw APIError.invalidResponse("Excel 文件为空或超过 20 MB 限制")
        }

        do {
            let file = try XLSXFile(data: data, bufferSize: 4 * 1_024 * 1_024)
            guard let workbook = try file.parseWorkbooks().first else {
                throw APIError.invalidResponse("Excel 工作簿中没有可读取的工作表")
            }
            guard let sheet = try file.parseWorksheetPathsAndNames(workbook: workbook).first else {
                throw APIError.invalidResponse("Excel 工作簿中没有可读取的工作表")
            }
            let worksheet = try file.parseWorksheet(at: sheet.path)
            let sharedStrings = try file.parseSharedStrings()
            let sheetRows = (worksheet.data?.rows ?? []).sorted { $0.reference < $1.reference }

            var headerLabels: [String] = []
            var headerMap: [Int: String] = [:]
            var seenHeader = false
            var rows: [LocalSpreadsheetRow] = []
            var unknownColumns: [String] = []

            for sheetRow in sheetRows {
                var cells: [Int: String] = [:]
                for cell in sheetRow.cells {
                    let column = columnNumber(cell.reference.column.value)
                    guard column > 0 else { continue }
                    cells[column] = cellText(cell, sharedStrings: sharedStrings)
                }
                guard cells.values.contains(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else {
                    continue
                }

                if !seenHeader {
                    let lastColumn = cells.keys.max() ?? 0
                    headerLabels = (1...max(1, lastColumn)).map { cells[$0] ?? "" }
                    var usedFields = Set<String>()
                    for column in 1...max(1, lastColumn) {
                        let label = cells[column] ?? ""
                        let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty else { continue }
                        guard let canonical = aliases[normalizeHeader(trimmed)] else {
                            if !unknownColumns.contains(trimmed) { unknownColumns.append(trimmed) }
                            continue
                        }
                        if usedFields.insert(canonical).inserted {
                            headerMap[column] = canonical
                        }
                    }
                    seenHeader = true
                    continue
                }

                var values: [String: String] = [:]
                for (column, canonical) in headerMap {
                    if let value = cells[column],
                       !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        values[canonical] = value
                    }
                }
                rows.append(LocalSpreadsheetRow(rowNumber: Int(sheetRow.reference), values: values))
                guard rows.count <= maximumRows else {
                    throw APIError.invalidResponse("数据行超过 2,000 行上限，请拆分文件后分批导入")
                }
            }

            guard seenHeader else { throw APIError.invalidResponse("Excel 工作表为空，未找到表头") }
            guard !rows.isEmpty else { throw APIError.invalidResponse("Excel 表格没有数据行") }

            return LocalSpreadsheetDocument(
                columns: headerLabels,
                recognizedColumns: Array(Set(headerMap.values)).sorted(),
                unknownColumns: unknownColumns,
                rows: rows
            )
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.invalidResponse("无法解析 Excel 文件：\(error.localizedDescription)")
        }
    }

    static func validate(row: LocalSpreadsheetRow, timezone: TimeZone) -> LocalImportValidation {
        func value(_ key: String) -> String? {
            guard let raw = row.values[key] else { return nil }
            let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            return text.isEmpty ? nil : text
        }

        var issues: [ImportIssue] = []
        let customerName = value("customer_name")
        if customerName == nil {
            issues.append(issue("MISSING_CUSTOMER_NAME", "customer_name", "ERROR", "客户名称为空"))
        }

        var countryCode = value("country_code")?.uppercased()
        if let code = countryCode,
           !(code.count == 2 && code.unicodeScalars.allSatisfy { (65...90).contains(Int($0.value)) }) {
            issues.append(issue("INVALID_COUNTRY_CODE", "country_code", "WARNING", "国家代码不是两位字母；使用 ISO 3166-1 alpha-2"))
            countryCode = nil
        }

        let latitude = coordinate(
            raw: value("latitude"), lower: -90, upper: 90,
            code: "INVALID_LATITUDE", field: "latitude", issues: &issues
        )
        let longitude = coordinate(
            raw: value("longitude"), lower: -180, upper: 180,
            code: "INVALID_LONGITUDE", field: "longitude", issues: &issues
        )
        var validLatitude = latitude
        var validLongitude = longitude
        if (validLatitude == nil) != (validLongitude == nil) {
            issues.append(issue("COORDINATES_PARTIAL", nil, "WARNING", "只填了纬度或经度其中一项；请两项都填或都留空"))
            validLatitude = nil
            validLongitude = nil
        }
        if validLatitude == nil {
            issues.append(issue("COORDINATES_MISSING", "city", "WARNING", "没有精确坐标；请补充经纬度后再进行路线优化"))
        }

        let serviceMinutes = integer(
            value("service_minutes"), range: 1...1440, fallback: 60,
            code: "INVALID_SERVICE_MINUTES", field: "service_minutes",
            message: "停留时长不是 1–1440 之间的正整数分钟；已使用 60 分钟", issues: &issues
        )
        let priority = integer(
            value("priority"), range: 1...3, fallback: 2,
            code: "INVALID_PRIORITY", field: "priority",
            message: "优先级不是 1–3；已使用默认优先级 2", issues: &issues
        )

        var businessValue = "0"
        if let raw = value("business_value") {
            let cleaned = raw.replacingOccurrences(of: ",", with: "")
                .replacingOccurrences(of: "￥", with: "")
                .replacingOccurrences(of: "$", with: "")
                .replacingOccurrences(of: "¥", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let amount = Decimal(string: cleaned, locale: Locale(identifier: "en_US_POSIX")),
               amount >= 0,
               NSDecimalNumber(decimal: amount).doubleValue.isFinite {
                businessValue = NSDecimalNumber(decimal: amount).stringValue
            } else {
                issues.append(issue("INVALID_BUSINESS_VALUE", "business_value", "WARNING", "商业价值不是有效的非负金额；已使用 0"))
            }
        }

        var movementMode = "GROUND"
        if let raw = value("movement_mode")?.uppercased() {
            if ["GROUND", "RAIL", "AIR", "SEA"].contains(raw) {
                movementMode = raw
            } else {
                issues.append(issue("INVALID_MOVEMENT_MODE", "movement_mode", "WARNING", "交通方式不在允许集合内；已使用 GROUND"))
            }
        }

        let email = value("contact_email")
        if let email,
           (!email.contains("@") || !(email.components(separatedBy: "@").last ?? "").contains(".")) {
            issues.append(issue("INVALID_EMAIL", "contact_email", "WARNING", "邮箱格式可疑；建议修正或留空"))
        }

        let startRaw = value("time_window_start")
        let endRaw = value("time_window_end")
        let start = startRaw.flatMap { parseDate($0, timezone: timezone) }
        var end = endRaw.flatMap { parseDate($0, timezone: timezone) }
        if startRaw != nil && start == nil {
            issues.append(issue("INVALID_TIME_WINDOW_FORMAT", "time_window_start", "WARNING", "时间窗开始无法解析；使用 YYYY-MM-DD HH:MM 格式"))
        }
        if endRaw != nil && end == nil {
            issues.append(issue("INVALID_TIME_WINDOW_FORMAT", "time_window_end", "WARNING", "时间窗结束无法解析；使用 YYYY-MM-DD HH:MM 格式"))
        }
        if let start, let endDate = end, endDate <= start {
            issues.append(issue("INVALID_TIME_WINDOW", "time_window_end", "WARNING", "时间窗起止顺序错误；已清除结束时间"))
            end = nil
        }

        let tierValue = (value("tier") ?? "C").uppercased()
        let tier = ["A", "B", "C"].contains(tierValue) ? tierValue : "C"
        let normalized = customerName.map {
            LocalImportNormalizedRow(
                customerName: $0,
                visitTitle: value("visit_title") ?? "拜访 \($0)",
                externalRef: value("external_ref"),
                tier: tier,
                countryCode: countryCode,
                city: value("city"),
                addressLine: value("address_line"),
                region: value("region"),
                latitude: validLatitude,
                longitude: validLongitude,
                timezone: value("timezone"),
                serviceMinutes: serviceMinutes,
                priority: priority,
                businessValue: businessValue,
                movementMode: movementMode,
                isImmovable: isTruthy(value("is_immovable")),
                timeWindowStart: start,
                timeWindowEnd: end,
                contactName: value("contact_name"),
                contactEmail: email,
                contactPhone: value("contact_phone"),
                industry: value("industry")
            )
        }
        let status: ImportRowStatus = issues.contains(where: { $0.severity == "ERROR" }) ? .invalid : .valid
        return LocalImportValidation(normalized: normalized, status: status, issues: issues)
    }

    private static func issue(_ code: String, _ field: String?, _ severity: String, _ message: String) -> ImportIssue {
        ImportIssue(code: code, field: field, message: message, severity: severity)
    }

    private static func coordinate(
        raw: String?,
        lower: Double,
        upper: Double,
        code: String,
        field: String,
        issues: inout [ImportIssue]
    ) -> Double? {
        guard let raw else { return nil }
        guard let number = Double(raw.replacingOccurrences(of: ",", with: "")),
              number.isFinite, (lower...upper).contains(number) else {
            issues.append(issue(code, field, "ERROR", code == "INVALID_LATITUDE"
                ? "纬度不是有效数值或超出 ±90" : "经度不是有效数值或超出 ±180"))
            return nil
        }
        return number
    }

    private static func integer(
        _ raw: String?,
        range: ClosedRange<Int>,
        fallback: Int,
        code: String,
        field: String,
        message: String,
        issues: inout [ImportIssue]
    ) -> Int {
        guard let raw else { return fallback }
        guard let value = Double(raw), value.isFinite, value.rounded() == value,
              value >= Double(range.lowerBound), value <= Double(range.upperBound) else {
            issues.append(issue(code, field, "WARNING", message))
            return fallback
        }
        return Int(value)
    }

    private static func cellText(_ cell: Cell, sharedStrings: SharedStrings?) -> String {
        if cell.type == .sharedString,
           let index = cell.value.flatMap(Int.init),
           let sharedStrings,
           sharedStrings.items.indices.contains(index) {
            let item = sharedStrings.items[index]
            return item.text ?? item.richText.compactMap(\.text).joined()
        }
        if let inline = cell.inlineString?.text { return inline }
        return cell.value ?? ""
    }

    private static func normalizeHeader(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .filter { !$0.isWhitespace && $0 != "_" && $0 != "-" }
    }

    private static func columnNumber(_ value: String) -> Int {
        value.unicodeScalars.reduce(0) { partial, scalar in
            guard (65...90).contains(Int(scalar.value)) else { return partial }
            return partial * 26 + Int(scalar.value) - 64
        }
    }

    private static func isTruthy(_ value: String?) -> Bool {
        guard let value else { return false }
        return ["1", "true", "yes", "y", "是", "固定", "immovable"]
            .contains(value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
    }

    private static func parseDate(_ raw: String, timezone: TimeZone) -> Date? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let serial = Double(value), serial >= 20_000, serial <= 100_000 {
            return excelSerialDate(serial, timezone: timezone)
        }
        let candidate = value.replacingOccurrences(of: "/", with: "-")
        for format in [
            "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm",
            "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm",
            "yyyy-MM-dd", "yyyy年MM月dd日 HH:mm", "yyyy年MM月dd日",
        ] {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = timezone
            formatter.isLenient = false
            formatter.dateFormat = format
            if let parsed = formatter.date(from: candidate) { return parsed }
        }
        return ISO8601DateFormatter().date(from: candidate)
    }

    private static func excelSerialDate(_ serial: Double, timezone: TimeZone) -> Date? {
        let wholeDays = Int(floor(serial))
        let seconds = Int(((serial - floor(serial)) * 86_400).rounded())
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timezone
        guard let epoch = calendar.date(from: DateComponents(year: 1899, month: 12, day: 30)),
              let targetDay = calendar.date(byAdding: .day, value: wholeDays, to: epoch) else {
            return nil
        }
        let day = calendar.dateComponents([.year, .month, .day], from: targetDay)
        return calendar.date(from: DateComponents(
            year: day.year, month: day.month, day: day.day,
            hour: seconds / 3_600, minute: (seconds % 3_600) / 60, second: seconds % 60
        ))
    }
}
