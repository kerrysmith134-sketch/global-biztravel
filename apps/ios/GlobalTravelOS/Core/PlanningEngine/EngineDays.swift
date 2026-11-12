import Foundation
import CoreLocation

/// 几何与行程时间（直译 geometry.py）。速度/开销常量内嵌，保证跨机确定性。
enum EngineGeometry {
    static let earthRadiusKm = 6371.0088
    /// 视为"同一地点"的距离阈值（公里）。
    static let samePlaceKm = 0.05

    static let speedKmh: [EngineMovementMode: Double] = [
        .ground: 60.0, .rail: 160.0, .air: 700.0, .sea: 35.0,
    ]

    /// 固定开销（分钟）：安检、候车、登机、装卸等与距离无关的部分。
    static let overheadMinutes: [EngineMovementMode: Int] = [
        .ground: 20, .rail: 45, .air: 120, .sea: 60,
    ]

    static func haversineKm(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
        let phi1 = lat1 * .pi / 180.0
        let phi2 = lat2 * .pi / 180.0
        let dPhi = (lat2 - lat1) * .pi / 180.0
        let dLambda = (lon2 - lon1) * .pi / 180.0
        var a = pow(sin(dPhi / 2.0), 2) + cos(phi1) * cos(phi2) * pow(sin(dLambda / 2.0), 2)
        a = min(1.0, max(0.0, a))
        return 2.0 * earthRadiusKm * asin(sqrt(a))
    }

    /// 行程时间（分钟，向上取整）。距离 < samePlaceKm 返回 0。
    static func travelMinutes(
        lat1: Double, lon1: Double, lat2: Double, lon2: Double,
        mode: EngineMovementMode
    ) -> Int {
        let distance = haversineKm(lat1: lat1, lon1: lon1, lat2: lat2, lon2: lon2)
        guard distance >= samePlaceKm else { return 0 }
        let speed = speedKmh[mode] ?? 60.0
        let minutes = distance / speed * 60.0
        return Int(ceil(minutes)) + (overheadMinutes[mode] ?? 0)
    }
}

/// 时间与日界（直译 days.py）。Date 即瞬时（等价 aware datetime）；
/// "真实经过时间"运算 = Date 的 TimeInterval 算术，天然 DST 安全。
enum EngineDays {
    /// 防御性上限：出行窗口不应跨年。
    static let maxDays = 400

    struct DayWindow {
        var date: Date      // 本地日历日的零点瞬时
        var open: Date
        var close: Date

        /// 墙上分钟数（含首尾，与服务端口径一致）。
        var minutes: Int {
            let seconds = close.timeIntervalSince(open)
            return Int(seconds / 60) + 1
        }
    }

    /// 按本地时区把窗口切成逐日区间（升序、确定性）。
    static func enumerateDays(
        window: EngineProblemWindow, timezoneHome: String
    ) throws -> [DayWindow] {
        guard let zone = TimeZone(identifier: timezoneHome) else {
            throw EngineError.invalidTimezone(timezoneHome)
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone

        let first = calendar.startOfDay(for: window.start)
        let lastDay = calendar.startOfDay(for: window.end)
        guard let daySpan = calendar.dateComponents([.day], from: first, to: lastDay).day else {
            throw EngineError.emptyWindow
        }
        if daySpan + 1 > maxDays {
            throw EngineError.windowTooLong
        }

        var days: [DayWindow] = []
        var cursor = first
        while cursor <= lastDay {
            guard let nextMidnight = calendar.date(byAdding: .day, value: 1, to: cursor) else {
                throw EngineError.emptyWindow
            }
            let dayClose = nextMidnight.addingTimeInterval(-1)
            let dayOpen = window.start > cursor ? window.start : cursor
            let close = window.end < dayClose ? window.end : dayClose
            if close >= dayOpen {
                days.append(DayWindow(date: cursor, open: dayOpen, close: close))
            }
            guard let advanced = calendar.date(byAdding: .day, value: 1, to: cursor) else {
                break
            }
            cursor = advanced
        }
        if days.isEmpty {
            throw EngineError.emptyWindow
        }
        return days
    }

    /// 按**真实经过时间**推进（Date 即瞬时，天然 DST 安全）。
    static func addElapsed(_ moment: Date, minutes: Int) -> Date {
        moment.addingTimeInterval(Double(minutes) * 60.0)
    }

    /// 两个瞬时之间真实经过的整分钟数（向下取整）。
    static func elapsedMinutes(from start: Date, to end: Date) -> Int {
        Int(end.timeIntervalSince(start) / 60.0)
    }

    /// 相对 base 的分钟偏移（向上取整），映射到求解器整数时间轴。
    static func minutesBetween(base: Date, moment: Date) -> Int {
        let delta = moment.timeIntervalSince(base)
        guard delta > 0 else { return 0 }
        return Int(ceil(delta / 60.0))
    }
}

/// 引擎错误。
enum EngineError: Error, LocalizedError {
    case invalidTimezone(String)
    case emptyWindow
    case windowTooLong
    case noSolverProducedFullCoverage(String)
    case primaryFailure(String)

    var errorDescription: String? {
        switch self {
        case .invalidTimezone(let name): return "无效时区：\(name)"
        case .emptyWindow: return "出行窗口没有可用日期"
        case .windowTooLong: return "出行窗口超过 \(EngineDays.maxDays) 天"
        case .noSolverProducedFullCoverage(let detail): return detail
        case .primaryFailure(let detail): return detail
        }
    }
}
