import Foundation

/// 客户库（审计 5.5）：列表/详情/新建。云端走 API，本地写 SQLite。
protocol CustomerRepository {
    func listCustomers(query: String?, page: PageRequest) async throws -> Page<Customer>
    func customer(id: String) async throws -> Customer
    func createCustomer(_ request: CustomerCreateRequest) async throws -> Customer
}

final class RemoteCustomerRepository: CustomerRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func listCustomers(query: String?, page: PageRequest) async throws -> Page<Customer> {
        try await client.send(Endpoints.listCustomers(query: query, page: page), as: Page<Customer>.self)
    }

    func customer(id: String) async throws -> Customer {
        try await client.send(Endpoints.customerDetail(id: id), as: Customer.self)
    }

    func createCustomer(_ request: CustomerCreateRequest) async throws -> Customer {
        try await client.send(try Endpoints.createCustomer(request), as: Customer.self)
    }
}

final class LocalCustomerRepository: CustomerRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    func listCustomers(query: String?, page: PageRequest) async throws -> Page<Customer> {
        let offset = page.cursor.flatMap { Int($0.replacingOccurrences(of: "offset:", with: "")) } ?? 0
        var clause = "1 = 1"
        var bindings: [Any?] = []
        if let query, !query.isEmpty {
            clause += " AND name LIKE ?"
            bindings.append("%\(query)%")
        }
        let payloads = try database.loadPayloads(
            table: "customers", where: clause, bindings: bindings, order: "name"
        )
        let all = try payloads.map { try DateCoding.decoder().decode(Customer.self, from: $0) }
        let windowed = Array(all.dropFirst(offset).prefix(page.limit))
        let next = offset + page.limit < all.count ? "offset:\(offset + page.limit)" : nil
        return Page(items: windowed, nextCursor: next)
    }

    func customer(id: String) async throws -> Customer {
        let payloads = try database.loadPayloads(table: "customers", where: "id = ?", bindings: [id])
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "NOT_FOUND", message: "客户不存在", traceId: nil, details: [:])
        }
        return try DateCoding.decoder().decode(Customer.self, from: payload)
    }

    func createCustomer(_ request: CustomerCreateRequest) async throws -> Customer {
        let trimmed = request.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw APIError.serverError(code: "VALIDATION_ERROR", message: "客户名称不能为空", traceId: nil, details: [:])
        }
        let customer = Customer(
            id: UUID().uuidString,
            name: trimmed,
            externalRef: request.externalRef,
            tier: request.tier,
            countryCode: request.countryCode?.uppercased(),
            industry: request.industry,
            primaryContactName: request.primaryContactName,
            primaryContactEmail: request.primaryContactEmail,
            primaryContactPhone: request.primaryContactPhone,
            status: "ACTIVE",
            ownerUserId: "local-user",
            createdAt: Date(),
            updatedAt: Date()
        )
        try database.upsert(
            table: "customers", id: customer.id,
            columns: ["name": customer.name, "tier": customer.tier],
            payload: try DateCoding.encode(customer)
        )
        return customer
    }
}
