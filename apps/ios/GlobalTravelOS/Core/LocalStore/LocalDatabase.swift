import Foundation

/// 本地模式数据库：schema 迁移 + document 表读写。
///
/// 表设计（ADR-0036）：每张业务表 = 少量**查询标量列**（id/状态/时间/外键）+
/// `payload_json`（完整 DTO，复用现有 Codable 模型——模型即真相）。
/// 这样迁移面小、DTO 解码测试直接覆盖存储内容。
struct LocalDatabase {
    let store: SQLiteStore

    static func open(path: URL = SQLiteStore.defaultPath()) throws -> LocalDatabase {
        let database = LocalDatabase(store: try SQLiteStore(path: path))
        try database.migrate()
        return database
    }

    /// 迁移列表：只追加，不改写历史（与 Alembic 同一心智模型）。
    private static let migrations: [(name: String, script: String)] = [
        ("0001_core_tables", """
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS places (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            city       TEXT,
            payload    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trips (
            id             TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'DRAFT',
            version        INTEGER NOT NULL DEFAULT 1,
            window_start   TEXT,
            window_end     TEXT,
            active_plan_id TEXT,
            countries      TEXT,
            updated_at     TEXT NOT NULL,
            payload        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS visits (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            place_id   TEXT,
            title      TEXT,
            status     TEXT NOT NULL DEFAULT 'PLANNED',
            lat        REAL,
            lon        REAL,
            payload    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_visits_trip ON visits (trip_id);
        CREATE TABLE IF NOT EXISTS plan_versions (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            version    INTEGER NOT NULL,
            status     TEXT NOT NULL DEFAULT 'DRAFT',
            created_at TEXT NOT NULL,
            payload    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_plans_trip ON plan_versions (trip_id);
        """),
        ("0002_proactive_tables", """
        CREATE TABLE IF NOT EXISTS proactive_suggestions (
            id            TEXT PRIMARY KEY,
            trip_id       TEXT,
            type          TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'PENDING',
            push_status   TEXT NOT NULL DEFAULT 'WITHHELD',
            dedupe_key    TEXT NOT NULL,
            severity      TEXT NOT NULL DEFAULT 'LOW',
            created_at    TEXT NOT NULL,
            payload       TEXT NOT NULL,
            UNIQUE (trip_id, type, dedupe_key)
        );
        CREATE INDEX IF NOT EXISTS ix_suggestions_status ON proactive_suggestions (status, created_at);
        CREATE TABLE IF NOT EXISTS proactive_triggers (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            rule_id    TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'TRIGGERED',
            created_at TEXT NOT NULL,
            UNIQUE (trip_id, rule_id, dedupe_key)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id          TEXT PRIMARY KEY,
            user_id     TEXT,
            kind        TEXT NOT NULL,
            priority    TEXT NOT NULL DEFAULT 'P2',
            status      TEXT NOT NULL DEFAULT 'PENDING',
            issue_key   TEXT NOT NULL,
            title       TEXT NOT NULL,
            body        TEXT,
            recipient   TEXT,
            attempts    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            payload     TEXT NOT NULL,
            UNIQUE (issue_key)
        );
        """),
        ("0003_business_tables", """
        CREATE TABLE IF NOT EXISTS trip_expenses (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            category   TEXT NOT NULL,
            currency   TEXT NOT NULL,
            occurred_on TEXT NOT NULL,
            payload    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_expenses_trip ON trip_expenses (trip_id);
        CREATE TABLE IF NOT EXISTS expense_reports (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'DRAFT',
            payload    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS visa_cases (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            country    TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'NEW',
            payload    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entry_assessments (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            country    TEXT NOT NULL,
            payload    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fx_rates (
            base  TEXT NOT NULL,
            quote TEXT NOT NULL,
            as_of TEXT NOT NULL,
            rate  TEXT NOT NULL,
            source TEXT,
            PRIMARY KEY (base, quote, as_of)
        );
        CREATE TABLE IF NOT EXISTS assistant_conversations (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            payload         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_assistant_messages ON assistant_messages (conversation_id);
        CREATE TABLE IF NOT EXISTS visit_records (
            id               TEXT PRIMARY KEY,
            visit_id         TEXT NOT NULL,
            client_record_id TEXT NOT NULL,
            payload          TEXT NOT NULL,
            UNIQUE (client_record_id)
        );
        CREATE TABLE IF NOT EXISTS approval_decisions (
            id           TEXT PRIMARY KEY,
            subject_id   TEXT NOT NULL,
            approver_id  TEXT NOT NULL,
            decision     TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            UNIQUE (subject_id, approver_id)
        );
        """),
        ("0004_customers", """
        CREATE TABLE IF NOT EXISTS customers (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'C',
            payload TEXT NOT NULL
        );
        """),
        ("0005_customer_places", """
        CREATE TABLE IF NOT EXISTS customer_places (
            id          TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            place_id    TEXT NOT NULL,
            label       TEXT,
            is_primary  INTEGER NOT NULL DEFAULT 0,
            payload     TEXT NOT NULL,
            UNIQUE (customer_id, place_id)
        );
        CREATE INDEX IF NOT EXISTS ix_customer_places_customer
            ON customer_places (customer_id, is_primary DESC, place_id);
        """),
        ("0006_customer_single_primary", """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_customer_places_one_primary
            ON customer_places (customer_id)
            WHERE is_primary = 1;
        """),
        ("0007_receipt_assets", """
        CREATE TABLE IF NOT EXISTS receipt_assets (
            id              TEXT PRIMARY KEY,
            trip_id         TEXT NOT NULL,
            server_asset_id TEXT,
            upload_status   TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            payload         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_receipt_assets_trip_created
            ON receipt_assets (trip_id, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_receipt_assets_server_id
            ON receipt_assets (server_asset_id)
            WHERE server_asset_id IS NOT NULL;
        """),
        ("0008_pending_trip_expenses", """
        CREATE TABLE IF NOT EXISTS pending_trip_expenses (
            id         TEXT PRIMARY KEY,
            trip_id    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_pending_trip_expenses_trip_created
            ON pending_trip_expenses (trip_id, created_at);
        """),
        ("0009_pending_trip_expense_fifo", """
        ALTER TABLE pending_trip_expenses
            ADD COLUMN queue_order INTEGER NOT NULL DEFAULT 0;
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY trip_id
                       ORDER BY created_at ASC, id ASC
                   ) AS position
            FROM pending_trip_expenses
        )
        UPDATE pending_trip_expenses
        SET queue_order = (
            SELECT position FROM ranked WHERE ranked.id = pending_trip_expenses.id
        );
        CREATE INDEX IF NOT EXISTS ix_pending_trip_expenses_trip_queue
            ON pending_trip_expenses (trip_id, queue_order);
        """),
    ]

    func migrate() throws {
        try store.run("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """)
        for migration in LocalDatabase.migrations {
            try applyMigration(name: migration.name, script: migration.script)
        }
    }

    /// Applies one local schema migration atomically.
    ///
    /// BEGIN IMMEDIATE serializes writers across SQLite connections. Re-checking the
    /// marker after the lock is acquired makes simultaneous app/database opens safe;
    /// the DDL and marker commit together, so a crash cannot leave a half-applied version.
    func applyMigration(name: String, script: String) throws {
        try store.transaction {
            let alreadyApplied = try store.query(
                "SELECT name FROM schema_migrations WHERE name = ?",
                bindings: [name]
            ) { $0.string(0) }.first != nil
            guard !alreadyApplied else { return }

            try store.executeScript(script)
            try store.run(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                bindings: [name, Date()]
            )
        }
    }

    // MARK: - 通用 document 操作

    func upsert(table: String, id: String, columns: [String: Any?], payload: Data) throws {
        let names = ["id"] + columns.keys.sorted() + ["payload"]
        let values: [Any?] = [id] + columns.keys.sorted().map { columns[$0] ?? nil } + [String(decoding: payload, as: UTF8.self)]
        let placeholders = names.map { _ in "?" }.joined(separator: ",")
        let updates = (columns.keys.sorted() + ["payload"])
            .map { "\($0) = excluded.\($0)" }
            .joined(separator: ", ")
        try store.run(
            "INSERT INTO \(table) (\(names.joined(separator: ", "))) VALUES (\(placeholders)) " +
                "ON CONFLICT(id) DO UPDATE SET \(updates)",
            bindings: values
        )
    }

    func loadPayloads(table: String, where clause: String? = nil, bindings: [Any?] = [], order: String? = nil, limit: Int? = nil) throws -> [Data] {
        var sql = "SELECT payload FROM \(table)"
        if let clause { sql += " WHERE \(clause)" }
        if let order { sql += " ORDER BY \(order)" }
        if let limit { sql += " LIMIT \(limit)" }
        return try store.query(sql, bindings: bindings) { $0.data(0) }
    }

    func run(_ sql: String, bindings: [Any?] = []) throws -> Int {
        try store.run(sql, bindings: bindings)
    }

    func transaction<T>(_ work: () throws -> T) throws -> T {
        try store.transaction(work)
    }

    /// Allocates a stable FIFO position for the next expense command in one Trip.
    /// Call inside `transaction(_:)` before inserting the queue item.
    func nextPendingTripExpenseQueueOrder(tripId: String) throws -> Int {
        try store.query(
            "SELECT COALESCE(MAX(queue_order), 0) + 1 FROM pending_trip_expenses WHERE trip_id = ?",
            bindings: [tripId]
        ) { $0.int(0) }.first ?? 1
    }

    func delete(table: String, id: String) throws {
        try store.run("DELETE FROM \(table) WHERE id = ?", bindings: [id])
    }

    func count(table: String, where clause: String? = nil, bindings: [Any?] = []) throws -> Int {
        var sql = "SELECT count(*) FROM \(table)"
        if let clause { sql += " WHERE \(clause)" }
        return try store.query(sql, bindings: bindings) { $0.int(0) }.first ?? 0
    }

    func keyValue(_ key: String) -> String? {
        try? store.query("SELECT value FROM kv WHERE key = ?", bindings: [key]) { $0.string(0) }.first
    }

    func setKeyValue(_ key: String, value: String) throws {
        try store.run(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            bindings: [key, value]
        )
    }
}
