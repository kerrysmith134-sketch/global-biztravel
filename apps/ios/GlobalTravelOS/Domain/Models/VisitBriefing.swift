import Foundation

struct VisitObjectiveUpdate: Codable, Equatable {
    var objective: String?
}

struct VisitBriefingCustomer: Codable, Equatable {
    var id: String
    var name: String
    var externalRef: String?
    var tier: String?
    var countryCode: String?
    var industry: String?
    var status: String?
    var primaryContactName: String?
    var primaryContactEmail: String?
    var primaryContactPhone: String?
    var notes: String?

    var tierLabel: String {
        switch (tier ?? "").uppercased() {
        case "A": return "A 级客户"
        case "B": return "B 级客户"
        case "C": return "C 级客户"
        default: return tier ?? "未分级"
        }
    }

    var contactLine: String? {
        let parts = [primaryContactName, primaryContactEmail, primaryContactPhone]
            .compactMap { $0?.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

struct VisitBriefingPlace: Codable, Equatable {
    var id: String
    var name: String
    var addressLine: String?
    var city: String?
    var region: String?
    var countryCode: String?
    var timezone: String?
    var lat: Double?
    var lon: Double?

    var displayRegion: String {
        [city, countryCode].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

struct VisitBriefingHistory: Codable, Equatable, Identifiable {
    var visitId: String
    var tripId: String
    var tripName: String
    var date: Date?
    var title: String?
    var status: String?
    var outcome: String?
    var notes: String?

    var id: String { visitId }

    var dateLabel: String {
        guard let date else { return "日期未知" }
        return ISO8601.dayString(from: date)
    }

    var outcomeLabel: String {
        switch (outcome ?? "").uppercased() {
        case "MET": return "已会面"
        case "NOT_MET": return "未会面"
        case "RESCHEDULED": return "改期"
        case "CANCELLED": return "取消"
        default: return status ?? "无记录"
        }
    }
}

struct VisitBriefingFollowUp: Codable, Equatable, Identifiable {
    var title: String
    var dueDate: String?
    var tripId: String
    var tripName: String
    var visitId: String

    var id: String { "\(visitId):\(title):\(dueDate ?? "")" }

    var displayText: String {
        guard let dueDate, !dueDate.isEmpty else { return title }
        return "\(title) · 截止 \(dueDate)"
    }
}

struct VisitBriefingMemory: Codable, Equatable, Identifiable {
    var id: String
    var kind: String
    var summary: String
    var decisions: [String]?
    var followUps: [String]?
    var lessons: [String]?
    var confidence: Double?
    var validFrom: Date?
}

struct VisitBriefingGap: Codable, Equatable, Identifiable {
    var code: String
    var message: String

    var id: String { code }
}

struct VisitBriefing: Codable, Equatable {
    var visit: TripVisit
    var customer: VisitBriefingCustomer?
    var place: VisitBriefingPlace?
    var history: [VisitBriefingHistory]?
    var followUps: [VisitBriefingFollowUp]?
    var memories: [VisitBriefingMemory]?
    var dataGaps: [VisitBriefingGap]?

    var historyList: [VisitBriefingHistory] { history ?? [] }
    var followUpList: [VisitBriefingFollowUp] { followUps ?? [] }
    var memoryList: [VisitBriefingMemory] { memories ?? [] }
    var gapList: [VisitBriefingGap] { dataGaps ?? [] }

    var objective: String? {
        let value = visit.objective?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? nil : value
    }
}
