import Foundation

/// `GET /v1/health` → `{status, db, org_isolation, version}`.
struct HealthStatus: Codable, Equatable {
    var status: String
    var db: String?
    var orgIsolation: String?
    var version: String?

    var isHealthy: Bool {
        status.lowercased() == "ok" || status.lowercased() == "healthy"
    }

    var displayText: String {
        var parts = [status]
        if let db { parts.append("db=\(db)") }
        if let orgIsolation { parts.append("org=\(orgIsolation)") }
        if let version { parts.append("v\(version)") }
        return parts.joined(separator: " · ")
    }
}
