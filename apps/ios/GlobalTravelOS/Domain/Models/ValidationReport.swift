import Foundation

enum ValidationCheckStatus: String, Codable, CaseIterable, Identifiable {
    case pass = "PASS"
    case fail = "FAIL"
    case warn = "WARN"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .pass: return "通过"
        case .fail: return "失败"
        case .warn: return "警告"
        }
    }

    var glyph: String {
        switch self {
        case .pass: return "checkmark.circle.fill"
        case .fail: return "xmark.octagon.fill"
        case .warn: return "exclamationmark.triangle.fill"
        }
    }
}

struct ValidationCheck: Codable, Equatable, Identifiable {
    var rule: String
    var status: ValidationCheckStatus?
    var detail: String?

    init(rule: String, status: ValidationCheckStatus? = nil, detail: String? = nil) {
        self.rule = rule
        self.status = status
        self.detail = detail
    }

    var id: String { rule }

    var statusLabel: String { status?.label ?? "未知" }

    var glyph: String { status?.glyph ?? "questionmark.circle" }
}

struct ValidationViolation: Codable, Equatable, Identifiable {
    var rule: String
    var visitId: String?
    var detail: String?

    init(rule: String, visitId: String? = nil, detail: String? = nil) {
        self.rule = rule
        self.visitId = visitId
        self.detail = detail
    }

    var id: String { "\(rule)-\(visitId ?? "-")" }
}

/// `validation:{feasible, checks:[{rule,status,detail}], violations:[{rule,visit_id,detail}]}`.
struct ValidationReport: Codable, Equatable {
    var feasible: Bool?
    var checks: [ValidationCheck]?
    var violations: [ValidationViolation]?

    init(
        feasible: Bool? = nil,
        checks: [ValidationCheck]? = nil,
        violations: [ValidationViolation]? = nil
    ) {
        self.feasible = feasible
        self.checks = checks
        self.violations = violations
    }

    var checkList: [ValidationCheck] { checks ?? [] }

    var violationList: [ValidationViolation] { violations ?? [] }

    var failingChecks: [ValidationCheck] {
        checkList.filter { $0.status == .fail }
    }

    var warningChecks: [ValidationCheck] {
        checkList.filter { $0.status == .warn }
    }

    var passingChecks: [ValidationCheck] {
        checkList.filter { $0.status == .pass }
    }

    var feasibilityLabel: String {
        guard let feasible else { return "未评估" }
        if feasible && violationList.isEmpty { return "通过全部校验" }
        if feasible { return "可行，但存在 \(violationList.count) 条违规记录" }
        return "不可行"
    }

    var summaryLine: String {
        "通过 \(passingChecks.count) · 警告 \(warningChecks.count) · 失败 \(failingChecks.count)"
    }
}