import CryptoKit
import Foundation

/// User-selected receipt bytes. The original filename is display metadata only; never used as a path.
struct ReceiptDraft {
    let filename: String
    let data: Data
}

/// Receipt state is deliberately explicit: a local copy is not a server-confirmed asset.
enum ReceiptUploadState: String, Codable, Equatable {
    case localOnly = "LOCAL_ONLY"
    case pendingUpload = "PENDING_UPLOAD"
    case uploaded = "UPLOADED"
}

struct LocalReceiptAsset: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String
    var serverAssetId: String?
    var originalFilename: String
    var mimeType: String
    var byteLength: Int
    var sha256: String
    /// Opaque filename inside the app-private vault, never sent to the API.
    var storageKey: String
    var uploadState: ReceiptUploadState
    var lastError: String?
    var createdAt: Date
    var updatedAt: Date

    var expenseReceiptAssetId: String? {
        if let serverAssetId { return serverAssetId }
        return uploadState == .localOnly ? id : nil
    }
}

private struct ReceiptAssetUploadResponse: Decodable {
    let id: String
    let tripId: String
    let originalFilename: String
    let mimeType: String
    let byteLength: Int
    let sha256: String
    let storageStatus: String
    let uploadedAt: Date
}

/// Local-first receipt vault. Original bytes are written to the account's private Application Support
/// directory before any network request. SQLite stores metadata and upload state, never file bytes.
final class ReceiptVault {
    private static let maximumBytes = 10 * 1024 * 1024

    private let database: LocalDatabase
    private let fileDirectory: URL
    private let client: APIClient?
    private let uploadsEnabled: Bool
    private let fileManager: FileManager

    init(
        database: LocalDatabase,
        fileDirectory: URL,
        client: APIClient?,
        uploadsEnabled: Bool,
        fileManager: FileManager = .default
    ) throws {
        self.database = database
        self.fileDirectory = fileDirectory
        self.client = client
        self.uploadsEnabled = uploadsEnabled
        self.fileManager = fileManager
        try fileManager.createDirectory(
            at: fileDirectory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete]
        )
    }

    static func open(
        directory: URL,
        client: APIClient?,
        uploadsEnabled: Bool
    ) throws -> ReceiptVault {
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete]
        )
        let database = try LocalDatabase.open(
            path: directory.appendingPathComponent("receipt-vault.sqlite3")
        )
        return try ReceiptVault(
            database: database,
            fileDirectory: directory.appendingPathComponent("receipt-vault", isDirectory: true),
            client: client,
            uploadsEnabled: uploadsEnabled
        )
    }

    /// Saves the exact original file and its hash. Re-selecting the same file for the same Trip
    /// reuses the existing asset rather than creating duplicate local copies.
    func capture(tripId: String, draft: ReceiptDraft) throws -> LocalReceiptAsset {
        guard !draft.data.isEmpty else {
            throw ReceiptVaultError.emptyFile
        }
        guard draft.data.count <= Self.maximumBytes else {
            throw ReceiptVaultError.fileTooLarge(maximumBytes: Self.maximumBytes)
        }
        let detected = try Self.detectType(draft.data)
        let digest = Self.sha256(draft.data)

        let existing = try database.loadPayloads(
            table: "receipt_assets",
            where: "trip_id = ?",
            bindings: [tripId],
            order: "created_at DESC"
        )
        for payload in existing {
            let asset = try DateCoding.decode(LocalReceiptAsset.self, from: payload)
            if asset.sha256 == digest,
               let storedBytes = try? originalData(assetId: asset.id),
               storedBytes == draft.data {
                return asset
            }
        }

        let id = UUID().uuidString.lowercased()
        let storageKey = "\(id).\(detected.fileExtension)"
        let destination = fileDirectory.appendingPathComponent(storageKey, isDirectory: false)
        let now = Date()
        let asset = LocalReceiptAsset(
            id: id,
            tripId: tripId,
            serverAssetId: nil,
            originalFilename: Self.sanitizedDisplayName(draft.filename, fallbackExtension: detected.fileExtension),
            mimeType: detected.mimeType,
            byteLength: draft.data.count,
            sha256: digest,
            storageKey: storageKey,
            uploadState: uploadsEnabled ? .pendingUpload : .localOnly,
            lastError: nil,
            createdAt: now,
            updatedAt: now
        )

        try draft.data.write(to: destination, options: [.atomic, .completeFileProtection])
        do {
            try database.upsert(
                table: "receipt_assets",
                id: asset.id,
                columns: [
                    "trip_id": tripId,
                    "server_asset_id": nil as String?,
                    "upload_status": asset.uploadState.rawValue,
                    "created_at": SQLTimestamp.basics.string(from: now),
                    "updated_at": SQLTimestamp.basics.string(from: now),
                ],
                payload: try DateCoding.encode(asset)
            )
        } catch {
            try? fileManager.removeItem(at: destination)
            throw error
        }
        return asset
    }

    /// Returns an asset ID safe to attach to the expense. Capture is a separate, earlier action:
    /// the original has already been persisted locally before the traveler submits the expense.
    func prepareForExpense(assetId: String) async throws -> LocalReceiptAsset {
        let saved = try requireAsset(assetId)
        guard uploadsEnabled else { return saved }
        let uploaded = try await upload(assetId: saved.id)
        guard uploaded.serverAssetId != nil else {
            throw ReceiptVaultError.serverAssetNotConfirmed
        }
        return uploaded
    }

    func upload(assetId: String) async throws -> LocalReceiptAsset {
        guard uploadsEnabled else {
            throw ReceiptVaultError.uploadUnavailable
        }
        var asset = try requireAsset(assetId)
        guard let client else {
            asset.lastError = ReceiptVaultError.uploadUnavailable.localizedDescription
            asset.updatedAt = Date()
            try? save(asset)
            throw ReceiptVaultError.uploadUnavailable
        }
        if asset.uploadState == .uploaded, asset.serverAssetId != nil {
            return asset
        }
        let sourceURL = fileDirectory.appendingPathComponent(asset.storageKey, isDirectory: false)
        let bytes: Data
        do {
            bytes = try Data(contentsOf: sourceURL, options: .mappedIfSafe)
        } catch {
            throw ReceiptVaultError.originalFileUnavailable
        }
        guard Self.sha256(bytes) == asset.sha256 else {
            throw ReceiptVaultError.integrityCheckFailed
        }

        let boundary = "GBTReceipt-\(UUID().uuidString)"
        var body = Data()
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"file\"; filename=\"\(Self.multipartSafeName(asset.originalFilename))\"\r\n")
        body.appendUTF8("Content-Type: \(asset.mimeType)\r\n\r\n")
        body.append(bytes)
        body.appendUTF8("\r\n--\(boundary)--\r\n")

        let request = APIRequest(
            method: .post,
            path: "/v1/trips/\(asset.tripId)/receipt-assets",
            body: body,
            contentType: "multipart/form-data; boundary=\(boundary)",
            idempotencyKey: asset.id
        )

        do {
            let response = try await client.send(request, as: ReceiptAssetUploadResponse.self)
            guard response.tripId == asset.tripId,
                  response.sha256 == asset.sha256,
                  response.byteLength == asset.byteLength,
                  response.mimeType == asset.mimeType,
                  response.storageStatus == "AVAILABLE" else {
                throw ReceiptVaultError.serverAssetMismatch
            }
            asset.serverAssetId = response.id
            asset.uploadState = .uploaded
            asset.lastError = nil
            asset.updatedAt = Date()
            try save(asset)
            return asset
        } catch {
            asset.uploadState = .pendingUpload
            asset.lastError = String(error.localizedDescription.prefix(500))
            asset.updatedAt = Date()
            try? save(asset)
            throw error
        }
    }

    /// Best-effort retry for a Trip's durable pending uploads. A failure leaves each original in
    /// place with PENDING_UPLOAD state and a visible diagnostic; no state is marked uploaded early.
    func retryPendingUploads(tripId: String, excluding assetIDs: Set<String> = []) async {
        guard uploadsEnabled else { return }
        let payloads = (try? database.loadPayloads(
            table: "receipt_assets",
            where: "trip_id = ? AND upload_status = ?",
            bindings: [tripId, ReceiptUploadState.pendingUpload.rawValue],
            order: "created_at"
        )) ?? []
        for payload in payloads {
            guard let asset = try? DateCoding.decode(LocalReceiptAsset.self, from: payload) else { continue }
            guard !assetIDs.contains(asset.id) else { continue }
            _ = try? await upload(assetId: asset.id)
        }
    }

    func makeTripExpenseOutbox() -> TripExpenseOutbox {
        TripExpenseOutbox(database: database)
    }

    func assets(tripId: String) throws -> [LocalReceiptAsset] {
        try database.loadPayloads(
            table: "receipt_assets",
            where: "trip_id = ?",
            bindings: [tripId],
            order: "created_at DESC"
        ).map { try DateCoding.decode(LocalReceiptAsset.self, from: $0) }
    }

    func originalData(assetId: String) throws -> Data {
        let asset = try requireAsset(assetId)
        let url = fileDirectory.appendingPathComponent(asset.storageKey, isDirectory: false)
        do {
            let data = try Data(contentsOf: url, options: .mappedIfSafe)
            guard Self.sha256(data) == asset.sha256 else {
                throw ReceiptVaultError.integrityCheckFailed
            }
            return data
        } catch let error as ReceiptVaultError {
            throw error
        } catch {
            throw ReceiptVaultError.originalFileUnavailable
        }
    }

    private func requireAsset(_ id: String) throws -> LocalReceiptAsset {
        let payloads = try database.loadPayloads(
            table: "receipt_assets",
            where: "id = ?",
            bindings: [id]
        )
        guard let payload = payloads.first else { throw ReceiptVaultError.assetNotFound }
        return try DateCoding.decode(LocalReceiptAsset.self, from: payload)
    }

    private func save(_ asset: LocalReceiptAsset) throws {
        // 必须补齐所有 NOT NULL 列：SQLite 在 upsert 处理**之前**先校验 NOT NULL，
        // 漏传 created_at 会直接 `NOT NULL constraint failed`，ON CONFLICT 根本没机会生效
        // （表现为上传失败的 lastError 永远写不进库）。capture() 首次插入带 created_at，
        // save() 只更新时也不能省——否则同 id 的二次写入会整行失败。
        try database.upsert(
            table: "receipt_assets",
            id: asset.id,
            columns: [
                "trip_id": asset.tripId,
                "server_asset_id": asset.serverAssetId,
                "upload_status": asset.uploadState.rawValue,
                "created_at": SQLTimestamp.basics.string(from: asset.createdAt),
                "updated_at": SQLTimestamp.basics.string(from: asset.updatedAt),
            ],
            payload: try DateCoding.encode(asset)
        )
    }

    private static func detectType(_ data: Data) throws -> (mimeType: String, fileExtension: String) {
        let bytes = [UInt8](data.prefix(12))
        if bytes.starts(with: [0xFF, 0xD8, 0xFF]) { return ("image/jpeg", "jpg") }
        if bytes.starts(with: [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) {
            return ("image/png", "png")
        }
        if bytes.starts(with: Array("%PDF-".utf8)) { return ("application/pdf", "pdf") }
        if bytes.starts(with: Array("GIF87a".utf8)) || bytes.starts(with: Array("GIF89a".utf8)) {
            return ("image/gif", "gif")
        }
        if bytes.count >= 12,
           Array(bytes[0 ..< 4]) == Array("RIFF".utf8),
           Array(bytes[8 ..< 12]) == Array("WEBP".utf8) {
            return ("image/webp", "webp")
        }
        throw ReceiptVaultError.unsupportedFileType
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func sanitizedDisplayName(_ input: String, fallbackExtension: String) -> String {
        let leaf = input.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? ""
        let cleaned = leaf.unicodeScalars.filter {
            !CharacterSet.controlCharacters.contains($0) && $0 != "\r" && $0 != "\n"
        }
        let name = String(String.UnicodeScalarView(cleaned)).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return "receipt.\(fallbackExtension)" }
        return String(name.prefix(200))
    }

    private static func multipartSafeName(_ filename: String) -> String {
        filename.replacingOccurrences(of: "\"", with: "_")
            .replacingOccurrences(of: "\r", with: "_")
            .replacingOccurrences(of: "\n", with: "_")
    }
}

/// Durable command log for cloud-mode expense creation. The local receipt reference is stored
/// separately because only a server-confirmed asset ID can be sent to the expense endpoint.
struct PendingTripExpense: Codable, Equatable, Identifiable {
    let id: String
    let tripId: String
    let body: TripExpenseCreateRequest
    let localReceiptAssetId: String?
    var attempts: Int
    var lastError: String?
    let createdAt: Date
    var updatedAt: Date
}

final class TripExpenseOutbox {
    private let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    @discardableResult
    func enqueue(
        tripId: String,
        body: TripExpenseCreateRequest,
        localReceiptAssetId: String?
    ) throws -> PendingTripExpense {
        let now = Date()
        let item = PendingTripExpense(
            id: UUID().uuidString.lowercased(),
            tripId: tripId,
            body: body,
            localReceiptAssetId: localReceiptAssetId,
            attempts: 0,
            lastError: nil,
            createdAt: now,
            updatedAt: now
        )
        try database.transaction {
            let queueOrder = try database.nextPendingTripExpenseQueueOrder(tripId: tripId)
            try save(item, queueOrder: queueOrder)
        }
        return item
    }

    func pending(tripId: String) throws -> [PendingTripExpense] {
        try database.loadPayloads(
            table: "pending_trip_expenses",
            where: "trip_id = ?",
            bindings: [tripId],
            order: "queue_order ASC, created_at ASC, id ASC"
        ).map { try DateCoding.decode(PendingTripExpense.self, from: $0) }
    }

    func recordFailure(id: String, message: String) throws {
        guard let payload = try database.loadPayloads(
            table: "pending_trip_expenses",
            where: "id = ?",
            bindings: [id]
        ).first else { return }
        var item = try DateCoding.decode(PendingTripExpense.self, from: payload)
        item.attempts += 1
        item.lastError = String(message.prefix(500))
        item.updatedAt = Date()
        try save(item)
    }

    func remove(id: String) throws {
        try database.delete(table: "pending_trip_expenses", id: id)
    }

    private func save(_ item: PendingTripExpense, queueOrder: Int? = nil) throws {
        var columns: [String: Any?] = [
            "trip_id": item.tripId,
            "created_at": SQLTimestamp.basics.string(from: item.createdAt),
            "updated_at": SQLTimestamp.basics.string(from: item.updatedAt),
        ]
        // Updates after a failed attempt deliberately omit this column so FIFO position is immutable.
        if let queueOrder { columns["queue_order"] = queueOrder }
        try database.upsert(
            table: "pending_trip_expenses",
            id: item.id,
            columns: columns,
            payload: try DateCoding.encode(item)
        )
    }
}

enum ReceiptVaultError: LocalizedError, Equatable {
    case emptyFile
    case fileTooLarge(maximumBytes: Int)
    case unsupportedFileType
    case originalFileUnavailable
    case integrityCheckFailed
    case assetNotFound
    case uploadUnavailable
    case serverAssetNotConfirmed
    case serverAssetMismatch

    var errorDescription: String? {
        switch self {
        case .emptyFile: return "票据文件为空"
        case .fileTooLarge(let maximumBytes): return "票据超过大小限制（\(maximumBytes / 1024 / 1024) MB）"
        case .unsupportedFileType: return "仅支持 PDF、JPEG、PNG、GIF 或 WebP"
        case .originalFileUnavailable: return "本地原始票据文件不可读取"
        case .integrityCheckFailed: return "票据完整性校验失败，原始文件未上传"
        case .assetNotFound: return "本地票据资产不存在"
        case .uploadUnavailable: return "当前未配置票据上传服务"
        case .serverAssetNotConfirmed: return "服务端尚未确认票据资产，费用未保存"
        case .serverAssetMismatch: return "服务端返回的票据内容与本地原件不一致"
        }
    }
}

private extension Data {
    mutating func appendUTF8(_ value: String) {
        append(Data(value.utf8))
    }
}
