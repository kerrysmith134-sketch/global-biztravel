import Foundation

/// 客户库（审计 5.5）：列表/详情/新建。云端走 API，本地写 SQLite。
protocol CustomerRepository {
    func listCustomers(query: String?, page: PageRequest) async throws -> Page<Customer>
    func customer(id: String) async throws -> Customer
    func createCustomer(_ request: CustomerCreateRequest) async throws -> Customer
    func customerPlaces(customerId: String) async throws -> [CustomerPlace]
    func linkCustomerPlace(customerId: String, request: CustomerPlaceLinkRequest) async throws -> CustomerPlace
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

    func customerPlaces(customerId: String) async throws -> [CustomerPlace] {
        try await client.send(Endpoints.customerPlaces(customerId: customerId), as: [CustomerPlace].self)
    }

    func linkCustomerPlace(
        customerId: String,
        request: CustomerPlaceLinkRequest
    ) async throws -> CustomerPlace {
        try await client.send(
            try Endpoints.linkCustomerPlace(customerId: customerId, request: request),
            as: CustomerPlace.self
        )
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

    func customerPlaces(customerId: String) async throws -> [CustomerPlace] {
        let payloads = try database.loadPayloads(
            table: "customer_places",
            where: "customer_id = ?",
            bindings: [customerId],
            order: "is_primary DESC, place_id"
        )
        let decoder = DateCoding.decoder()
        let links = try payloads.map { try decoder.decode(LocalCustomerPlaceLink.self, from: $0) }
        return try links.map { link in
            let place = try localPlace(id: link.placeId)
            return customerPlace(from: link, place: place)
        }
    }

    func linkCustomerPlace(
        customerId: String,
        request: CustomerPlaceLinkRequest
    ) async throws -> CustomerPlace {
        // Verify both records locally; never create a dangling customer-place link.
        _ = try await customer(id: customerId)
        let place = try localPlace(id: request.placeId)
        let normalizedLabel = request.label?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let label = normalizedLabel?.isEmpty == false ? normalizedLabel : nil
        let link = LocalCustomerPlaceLink(
            customerId: customerId,
            placeId: request.placeId,
            label: label,
            isPrimary: request.isPrimary
        )
        let linkID = Self.customerPlaceLinkID(customerId: customerId, placeId: request.placeId)

        try database.transaction {
            if request.isPrimary {
                let existingPayloads = try database.loadPayloads(
                    table: "customer_places",
                    where: "customer_id = ? AND is_primary = 1",
                    bindings: [customerId]
                )
                let decoder = DateCoding.decoder()
                for payload in existingPayloads {
                    let existing = try decoder.decode(LocalCustomerPlaceLink.self, from: payload)
                    guard existing.placeId != request.placeId else { continue }
                    let demoted = LocalCustomerPlaceLink(
                        customerId: existing.customerId,
                        placeId: existing.placeId,
                        label: existing.label,
                        isPrimary: false
                    )
                    try database.upsert(
                        table: "customer_places",
                        id: Self.customerPlaceLinkID(
                            customerId: demoted.customerId,
                            placeId: demoted.placeId
                        ),
                        columns: [
                            "customer_id": demoted.customerId,
                            "place_id": demoted.placeId,
                            "label": demoted.label,
                            "is_primary": 0,
                        ],
                        payload: try DateCoding.encode(demoted)
                    )
                }
            }
            try database.upsert(
                table: "customer_places",
                id: linkID,
                columns: [
                    "customer_id": customerId,
                    "place_id": request.placeId,
                    "label": label,
                    "is_primary": request.isPrimary ? 1 : 0,
                ],
                payload: try DateCoding.encode(link)
            )
        }
        return customerPlace(from: link, place: place)
    }

    private func localPlace(id: String) throws -> Place {
        let payloads = try database.loadPayloads(
            table: "places",
            where: "id = ?",
            bindings: [id]
        )
        guard let payload = payloads.first else {
            throw APIError.serverError(
                code: "NOT_FOUND",
                message: "地点不存在",
                traceId: nil,
                details: ["place_id": .string(id)]
            )
        }
        return try DateCoding.decoder().decode(Place.self, from: payload)
    }

    private func customerPlace(
        from link: LocalCustomerPlaceLink,
        place: Place
    ) -> CustomerPlace {
        let hasCoordinates = place.latitude.map { (-90.0...90.0).contains($0) } == true
            && place.longitude.map { (-180.0...180.0).contains($0) } == true
        return CustomerPlace(
            placeId: link.placeId,
            label: link.label,
            isPrimary: link.isPrimary,
            name: place.name,
            addressLine: place.address,
            city: place.city,
            countryCode: place.countryCode,
            lat: place.latitude,
            lon: place.longitude,
            geocodeStatus: hasCoordinates ? "RESOLVED" : "MISSING"
        )
    }

    private static func customerPlaceLinkID(customerId: String, placeId: String) -> String {
        "\(customerId)::\(placeId)"
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
