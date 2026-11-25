import Foundation
import SQLite3

/// 零依赖 SQLite3 薄封装（本地模式主存储，ADR-0036）。
///
/// 设计取舍：项目保持零第三方依赖（无 SPM），查询模式是简单的 document 表
/// （标量列 + payload_json），串行队列保护即可满足并发需求。
/// 迁移用显式有序列表（`migrations`），与 Alembic 同一心智模型。
final class SQLiteStore {
    private var handle: OpaquePointer?
    private let queue = DispatchQueue(label: "com.anari.globaltravelos.localstore")
    private let queueKey = DispatchSpecificKey<UInt8>()

    /// 当前线程是否已在该串行队列上（transaction 内嵌 run/query 时避免 sync 重入死锁）。
    private var isOnQueue: Bool {
        DispatchQueue.getSpecific(key: queueKey) != nil
    }

    static func defaultPath() -> URL {
        let directory = AppSettingsStore.defaultDirectory()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("local-store.sqlite3")
    }

    init(path: URL) throws {
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(path.path, &handle, flags, nil) == SQLITE_OK else {
            throw LocalStoreError.openFailed(path.path)
        }
        queue.setSpecific(key: queueKey, value: 1)
        // Separate SQLiteStore instances can open the same account database concurrently.
        // Wait for short-lived startup/migration writer locks instead of failing with SQLITE_BUSY.
        try run("PRAGMA busy_timeout=10000")
        try configureWriteAheadLogging()
        try run("PRAGMA foreign_keys=ON")
    }

    /// WAL mode changes require an exclusive database lock. SQLite may return SQLITE_BUSY
    /// immediately for this PRAGMA even when busy_timeout is configured, so concurrent
    /// first-open calls need a bounded retry that re-reads the current mode each time.
    private func configureWriteAheadLogging() throws {
        let maximumAttempts = 8
        var delay: TimeInterval = 0.025

        for attempt in 0..<maximumAttempts {
            do {
                let currentMode = try query("PRAGMA journal_mode") { $0.string(0).lowercased() }.first
                if currentMode == "wal" { return }

                try run("PRAGMA journal_mode=WAL")
                let confirmedMode = try query("PRAGMA journal_mode") { $0.string(0).lowercased() }.first
                guard confirmedMode == "wal" else {
                    throw LocalStoreError.queryFailed(
                        "SQLite refused WAL mode (current: \(confirmedMode ?? "unknown"))",
                        code: SQLITE_ERROR
                    )
                }
                return
            } catch let error as LocalStoreError {
                guard case let .queryFailed(_, code) = error,
                      code == SQLITE_BUSY,
                      attempt + 1 < maximumAttempts else {
                    throw error
                }
                Thread.sleep(forTimeInterval: delay)
                delay = min(delay * 2, 0.4)
            }
        }

        throw LocalStoreError.queryFailed(
            "SQLite remained busy while enabling WAL after \(maximumAttempts) attempts",
            code: SQLITE_BUSY
        )
    }

    deinit {
        if let handle { sqlite3_close_v2(handle) }
    }

    enum LocalStoreError: Error {
        case openFailed(String)
        case queryFailed(String, code: Int32)
        case bindingFailed(String)
    }

    // MARK: - 执行

    /// 执行无结果语句（DDL/DML）。`bindings` 支持 String/Int/Double/Bool/Data/nil。
    @discardableResult
    func run(_ sql: String, bindings: [Any?] = []) throws -> Int {
        if isOnQueue { return try runUnlocked(sql, bindings: bindings) }
        return try queue.sync {
            var statement: OpaquePointer?
            guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK else {
                throw LocalStoreError.queryFailed(lastError(), code: sqlite3_errcode(handle))
            }
            defer { sqlite3_finalize(statement) }
            try bind(bindings, to: statement)
            // 消费所有行（PRAGMA journal_mode 等带结果行），直到 DONE。
            while true {
                let result = sqlite3_step(statement)
                if result == SQLITE_ROW { continue }
                guard result == SQLITE_DONE else {
                    throw LocalStoreError.queryFailed("RUN[\(sql.prefix(80))]: \(lastError())", code: sqlite3_errcode(handle))
                }
                break
            }
            return Int(sqlite3_changes(handle))
        }
    }

    /// 执行多条语句（迁移脚本用）。脚本由本仓库维护（分号分隔、无嵌入分号字符串），
    /// 因此按分号拆分逐条执行——比 pzTail 指针推进简单且无内存生命周期风险。
    func executeScript(_ sql: String) throws {
        if isOnQueue {
            try executeScriptUnlocked(sql)
            return
        }
        try queue.sync { try executeScriptUnlocked(sql) }
    }

    private func executeScriptUnlocked(_ sql: String) throws {
        do {
            for statement in sql.split(separator: ";") {
                let trimmed = statement.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { continue }
                try runUnlocked(trimmed + ";")
            }
        }
    }

    /// 查询多行；`row` 把当前 statement 转成领域值。
    func query<T>(_ sql: String, bindings: [Any?] = [], row: (StatementRow) -> T) throws -> [T] {
        if isOnQueue { return try queryUnlocked(sql, bindings: bindings, row: row) }
        return try queue.sync {
            try queryUnlocked(sql, bindings: bindings, row: row)
        }
    }

    private func queryUnlocked<T>(
        _ sql: String, bindings: [Any?], row: (StatementRow) -> T
    ) throws -> [T] {
        do {
            var statement: OpaquePointer?
            guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK else {
                throw LocalStoreError.queryFailed("PREPARE[\(sql.prefix(80))]: \(lastError())", code: sqlite3_errcode(handle))
            }
            defer { sqlite3_finalize(statement) }
            try bind(bindings, to: statement)
            var results: [T] = []
            let cursor = StatementRow(statement: statement)
            while true {
                let result = sqlite3_step(statement)
                if result == SQLITE_ROW {
                    results.append(row(cursor))
                    continue
                }
                guard result == SQLITE_DONE else {
                    throw LocalStoreError.queryFailed(
                        "QUERY[\(sql.prefix(80))]: \(lastError())",
                        code: sqlite3_errcode(handle)
                    )
                }
                break
            }
            return results
        }
    }

    /// 事务内执行多条变更；抛错则回滚。
    func transaction<T>(_ work: () throws -> T) throws -> T {
        try queue.sync {
            try runUnlocked("BEGIN IMMEDIATE")
            do {
                let value = try work()
                try runUnlocked("COMMIT")
                return value
            } catch {
                try? runUnlocked("ROLLBACK")
                throw error
            }
        }
    }

    private func runUnlocked(_ sql: String, bindings: [Any?] = []) throws -> Int {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK else {
            throw LocalStoreError.queryFailed(lastError(), code: sqlite3_errcode(handle))
        }
        defer { sqlite3_finalize(statement) }
        try bind(bindings, to: statement)
        // 消费所有行：多数 DDL/DML 返回 DONE，但 PRAGMA（如 journal_mode）带结果行。
        while true {
            let result = sqlite3_step(statement)
            if result == SQLITE_ROW { continue }
            guard result == SQLITE_DONE else {
                throw LocalStoreError.queryFailed("STEP[\(sql.prefix(80))]: \(lastError())", code: sqlite3_errcode(handle))
            }
            break
        }
        return Int(sqlite3_changes(handle))
    }

    private func bind(_ bindings: [Any?], to statement: OpaquePointer?) throws {
        for (index, value) in bindings.enumerated() {
            let position = Int32(index + 1)
            switch value {
            case .none:
                sqlite3_bind_null(statement, position)
            case let value as NSNull:
                sqlite3_bind_null(statement, position)
            case let text as String:
                sqlite3_bind_text(statement, position, text, -1, SQLITE_TRANSIENT)
            case let number as Int:
                sqlite3_bind_int64(statement, position, Int64(number))
            case let number as Int64:
                sqlite3_bind_int64(statement, position, number)
            case let number as Double:
                sqlite3_bind_double(statement, position, number)
            case let flag as Bool:
                sqlite3_bind_int64(statement, position, flag ? 1 : 0)
            case let data as Data:
                let ok = data.withUnsafeBytes { bytes in
                    sqlite3_bind_blob(statement, position, bytes.baseAddress, Int32(data.count), SQLITE_TRANSIENT)
                }
                guard ok == SQLITE_OK else { throw LocalStoreError.bindingFailed("blob at \(index)") }
            case let date as Date:
                sqlite3_bind_text(statement, position, SQLTimestamp.basics.string(from: date), -1, SQLITE_TRANSIENT)
            default:
                throw LocalStoreError.bindingFailed("unsupported type at \(index): \(type(of: value))")
            }
        }
    }

    private func lastError() -> String {
        guard let message = sqlite3_errmsg(handle) else { return "unknown" }
        return String(cString: message)
    }
}

/// 一行查询结果的列读取器（列下标从 0 起，与 SQL SELECT 顺序一致）。
struct StatementRow {
    fileprivate let statement: OpaquePointer?

    func string(_ index: Int32) -> String {
        guard let cString = sqlite3_column_text(statement, index) else { return "" }
        return String(cString: cString)
    }

    func stringOpt(_ index: Int32) -> String? {
        guard sqlite3_column_type(statement, index) != SQLITE_NULL,
              let cString = sqlite3_column_text(statement, index) else { return nil }
        return String(cString: cString)
    }

    func int(_ index: Int32) -> Int {
        Int(sqlite3_column_int64(statement, index))
    }

    func intOpt(_ index: Int32) -> Int? {
        guard sqlite3_column_type(statement, index) != SQLITE_NULL else { return nil }
        return Int(sqlite3_column_int64(statement, index))
    }

    func double(_ index: Int32) -> Double {
        sqlite3_column_double(statement, index)
    }

    func doubleOpt(_ index: Int32) -> Double? {
        guard sqlite3_column_type(statement, index) != SQLITE_NULL else { return nil }
        return sqlite3_column_double(statement, index)
    }

    func data(_ index: Int32) -> Data {
        guard let bytes = sqlite3_column_blob(statement, index) else { return Data() }
        let count = Int(sqlite3_column_bytes(statement, index))
        return Data(bytes: bytes.assumingMemoryBound(to: UInt8.self), count: count)
    }
}

enum SQLTimestamp {
    static let basics: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}

private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
