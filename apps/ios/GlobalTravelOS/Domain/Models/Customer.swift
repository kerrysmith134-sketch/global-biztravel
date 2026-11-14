import Foundation

/// 客户（对齐服务端契约 CustomerRead，审计 5.5：客户端此前无客户管理）。
struct Customer: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var externalRef: String?
    var tier: String            // A / B / C
    var countryCode: String?
    var industry: String?
    var primaryContactName: String?
    var primaryContactEmail: String?
    var primaryContactPhone: String?
    var status: String          // ACTIVE / INACTIVE
    var ownerUserId: String?
    var createdAt: Date?
    var updatedAt: Date?

    init(
        id: String,
        name: String,
        externalRef: String? = nil,
        tier: String = "C",
        countryCode: String? = nil,
        industry: String? = nil,
        primaryContactName: String? = nil,
        primaryContactEmail: String? = nil,
        primaryContactPhone: String? = nil,
        status: String = "ACTIVE",
        ownerUserId: String? = nil,
        createdAt: Date? = nil,
        updatedAt: Date? = nil
    ) {
        self.id = id
        self.name = name
        self.externalRef = externalRef
        self.tier = tier
        self.countryCode = countryCode
        self.industry = industry
        self.primaryContactName = primaryContactName
        self.primaryContactEmail = primaryContactEmail
        self.primaryContactPhone = primaryContactPhone
        self.status = status
        self.ownerUserId = ownerUserId
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    var tierLabel: String {
        switch tier.uppercased() {
        case "A": return "A 级战略客户"
        case "B": return "B 级重点客户"
        default: return "C 级一般客户"
        }
    }

    var isActive: Bool { status.uppercased() == "ACTIVE" }

    var contactSummary: String? {
        [primaryContactName, primaryContactEmail, primaryContactPhone]
            .compactMap { $0 }.filter { !$0.isEmpty }.first
    }
}

/// 新建客户请求（对齐 CustomerCreate）。
struct CustomerCreateRequest: Codable, Equatable {
    var name: String
    var externalRef: String?
    var tier: String
    var countryCode: String?
    var industry: String?
    var primaryContactName: String?
    var primaryContactEmail: String?
    var primaryContactPhone: String?
    var notes: String?

    init(
        name: String,
        externalRef: String? = nil,
        tier: String = "C",
        countryCode: String? = nil,
        industry: String? = nil,
        primaryContactName: String? = nil,
        primaryContactEmail: String? = nil,
        primaryContactPhone: String? = nil,
        notes: String? = nil
    ) {
        self.name = name
        self.externalRef = externalRef
        self.tier = tier
        self.countryCode = countryCode
        self.industry = industry
        self.primaryContactName = primaryContactName
        self.primaryContactEmail = primaryContactEmail
        self.primaryContactPhone = primaryContactPhone
        self.notes = notes
    }
}
