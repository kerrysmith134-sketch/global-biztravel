import Foundation

/// Backend row verdict: `VALID / INVALID / DUPLICATE`.
///
/// The server also returns `ACCEPTED/REJECTED/SKIPPED` on confirmed rows; the client only needs the
/// pre-confirm status, but an unknown value must not break decoding.
enum ImportRowStatus: String, Codable, CaseIterable, Identifiable {
    case valid = "VALID"
    case invalid = "INVALID"
    case duplicate = "DUPLICATE"
    case unknown = "UNKNOWN"

    var id: String { rawValue }

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = ImportRowStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .valid: return "有效"
        case .invalid: return "阻断"
        case .duplicate: return "重复"
        case .unknown: return "未知"
        }
    }

    var glyph: String {
        switch self {
        case .valid: return "checkmark.circle"
        case .invalid: return "xmark.octagon"
        case .duplicate: return "doc.on.doc"
        case .unknown: return "questionmark.circle"
        }
    }

    /// Only rows the server marked VALID are selected by default; the server re-validates anyway.
    var selectableForImport: Bool { self == .valid }
}

struct ImportIssue: Codable, Equatable, Identifiable {
    var code: String
    var field: String?
    var message: String
    var severity: String?

    init(code: String, field: String? = nil, message: String, severity: String? = nil) {
        self.code = code
        self.field = field
        self.message = message
        self.severity = severity
    }

    var id: String { "\(code)-\(field ?? "-")-\(message)" }

    var displayText: String {
        var parts = [code]
        if let field, !field.isEmpty { parts.append("字段 \(field)") }
        if !message.isEmpty { parts.append(message) }
        return parts.joined(separator: " · ")
    }
}

struct ImportedRow: Codable, Equatable, Identifiable {
    var rowNumber: Int
    var status: ImportRowStatus
    var issues: [ImportIssue]?
    var raw: [String: JSONValue]?
    var normalized: [String: JSONValue]?

    init(
        rowNumber: Int,
        status: ImportRowStatus = .unknown,
        issues: [ImportIssue]? = nil,
        raw: [String: JSONValue]? = nil,
        normalized: [String: JSONValue]? = nil
    ) {
        self.rowNumber = rowNumber
        self.status = status
        self.issues = issues
        self.raw = raw
        self.normalized = normalized
    }

    private enum CodingKeys: String, CodingKey {
        case rowNumber
        case status
        case issues
        case raw
        case normalized
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.rowNumber = try container.decode(Int.self, forKey: .rowNumber)
        self.status = try container.decodeIfPresent(ImportRowStatus.self, forKey: .status) ?? .unknown
        self.issues = try container.decodeIfPresent([ImportIssue].self, forKey: .issues)
        self.raw = try container.decodeIfPresent([String: JSONValue].self, forKey: .raw)
        self.normalized = try container.decodeIfPresent([String: JSONValue].self, forKey: .normalized)
    }

    var id: Int { rowNumber }

    var issueList: [ImportIssue] { issues ?? [] }

    var displayName: String {
        normalized?["customer_name"]?.stringValue
            ?? normalized?["name"]?.stringValue
            ?? raw?["customer_name"]?.stringValue
            ?? "第 \(rowNumber) 行"
    }

    var hasBlockingIssue: Bool { status == .invalid }

    var issueSummary: String? {
        let list = issueList
        guard !list.isEmpty else { return nil }
        return list.map(\.displayText).joined(separator: "\n")
    }
}

/// `POST /v1/trips/{id}/imports:parse` response.
struct ImportParseResult: Codable, Equatable {
    var importId: String
    var tripId: String?
    var filename: String?
    var status: String?
    var totalRows: Int?
    var validRows: Int?
    var invalidRows: Int?
    var duplicateRows: Int?
    var columns: [String]?
    var missingColumns: [String]?
    var unknownColumns: [String]?
    var rows: [ImportedRow]?
    var report: [String: JSONValue]?

    init(
        importId: String,
        tripId: String? = nil,
        filename: String? = nil,
        status: String? = nil,
        totalRows: Int? = nil,
        validRows: Int? = nil,
        invalidRows: Int? = nil,
        duplicateRows: Int? = nil,
        columns: [String]? = nil,
        missingColumns: [String]? = nil,
        unknownColumns: [String]? = nil,
        rows: [ImportedRow]? = nil,
        report: [String: JSONValue]? = nil
    ) {
        self.importId = importId
        self.tripId = tripId
        self.filename = filename
        self.status = status
        self.totalRows = totalRows
        self.validRows = validRows
        self.invalidRows = invalidRows
        self.duplicateRows = duplicateRows
        self.columns = columns
        self.missingColumns = missingColumns
        self.unknownColumns = unknownColumns
        self.rows = rows
        self.report = report
    }

    var rowList: [ImportedRow] { rows ?? [] }

    var importableRowNumbers: [Int] {
        rowList.filter(\.status.selectableForImport).map(\.rowNumber)
    }

    var summaryText: String {
        "共 \(totalRows ?? rowList.count) 行 · 有效 \(validRows ?? 0) · 阻断 \(invalidRows ?? 0) · 重复 \(duplicateRows ?? 0)"
    }
}

/// `POST /v1/trips/{id}/imports:confirm` request.
struct ConfirmImportRequest: Codable, Equatable {
    var importId: String
    var acceptedRowNumbers: [Int]

    init(importId: String, acceptedRowNumbers: [Int]) {
        self.importId = importId
        self.acceptedRowNumbers = acceptedRowNumbers
    }
}

/// `POST /v1/trips/{id}/imports:confirm` response.
struct ConfirmImportResult: Codable, Equatable {
    var importId: String
    var status: String?
    var acceptedRows: Int?
    var rejectedRows: Int?
    var createdCustomers: Int?
    var createdPlaces: Int?
    var createdVisits: Int?
    var reusedCustomers: Int?
    var reusedPlaces: Int?
    var warnings: [String]?

    init(
        importId: String,
        status: String? = nil,
        acceptedRows: Int? = nil,
        rejectedRows: Int? = nil,
        createdCustomers: Int? = nil,
        createdPlaces: Int? = nil,
        createdVisits: Int? = nil,
        reusedCustomers: Int? = nil,
        reusedPlaces: Int? = nil,
        warnings: [String]? = nil
    ) {
        self.importId = importId
        self.status = status
        self.acceptedRows = acceptedRows
        self.rejectedRows = rejectedRows
        self.createdCustomers = createdCustomers
        self.createdPlaces = createdPlaces
        self.createdVisits = createdVisits
        self.reusedCustomers = reusedCustomers
        self.reusedPlaces = reusedPlaces
        self.warnings = warnings
    }

    var warningList: [String] { warnings ?? [] }

    var summaryText: String {
        "接受 \(acceptedRows ?? 0) 行 · 拒绝 \(rejectedRows ?? 0) 行 · 新建拜访 \(createdVisits ?? 0) · 新建客户 \(createdCustomers ?? 0)"
    }
}
