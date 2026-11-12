import Foundation

/// 本地签证目录（与服务端 app/data/visa_catalog.json 同源，version 2026.10）。
/// 只用于"出发前需核验"提醒，不代表任何国家官方结论。
enum VisaDirectory {
    struct Entry {
        var label: String
        var businessEntry: String
        var reviewHint: String
    }

    static let version = "2026.10"

    static let countries: [String: Entry] = [
        "DE": Entry(label: "德国", businessEntry: "申根短期商务访问", reviewHint: "需核验申根商务签证的受理周期与保险/邀请函要求"),
        "FR": Entry(label: "法国", businessEntry: "申根短期商务访问", reviewHint: "需核验申根商务签证的受理周期与保险/邀请函要求"),
        "NL": Entry(label: "荷兰", businessEntry: "申根短期商务访问", reviewHint: "需核验申根商务签证的受理周期与保险/邀请函要求"),
        "GB": Entry(label: "英国", businessEntry: "标准访客签证（商务）", reviewHint: "需核验标准访客签证的受理周期与资金/邀请材料"),
        "US": Entry(label: "美国", businessEntry: "B-1 商务访问", reviewHint: "需核验 B-1 面签预约周期与 DS-160 材料"),
        "SG": Entry(label: "新加坡", businessEntry: "短期商务访问", reviewHint: "需核验免签/签证条件与邀请方信息"),
        "TH": Entry(label: "泰国", businessEntry: "短期商务访问", reviewHint: "需核验免签条件与停留期限"),
        "JP": Entry(label: "日本", businessEntry: "短期商务签证", reviewHint: "需核验短期商务停留的招请状与保证人材料"),
        "AE": Entry(label: "阿联酋", businessEntry: "短期商务访问", reviewHint: "需核验阿联酋商务访问的担保与许可要求"),
        "BR": Entry(label: "巴西", businessEntry: "短期商务访问", reviewHint: "需核验 VITEM-II/商务签证的受理周期与材料"),
    ]
}

/// 通知分级（直译 notification_policy_service.py，纯函数）。
/// 入队时按顺序命中即停：HIGH × {FLIGHT_STATUS_ALERT, PLAN_UNPLACED_VISITS} ⇒ P0；
/// HIGH ⇒ P1；MEDIUM ⇒ P2；LOW ⇒ P3；未知组合保守退回 P2。
enum NotificationPolicy {
    static func classify(suggestionType: String, severity: String) -> String {
        if severity == "HIGH" {
            if suggestionType == "FLIGHT_STATUS_ALERT" || suggestionType == "PLAN_UNPLACED_VISITS" {
                return "P0"
            }
            return "P1"
        }
        if severity == "MEDIUM" { return "P2" }
        if severity == "LOW" { return "P3" }
        return "P2"
    }
}
