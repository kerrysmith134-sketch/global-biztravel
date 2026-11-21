import Foundation

/// Customer and organization-place access. Address relations are distinct from the shared place
/// catalog: unlinking a customer never deletes a place that may be referenced elsewhere.
protocol CustomerRepository {
    func listCustomers(query: String?, page: PageRequest) async throws -> Page<Customer>
    func customer(id: String) async throws -> Customer
    func createCustomer(_ request: CustomerCreateRequest) async throws -> Customer
    func customerPlaces(customerId: String) async throws -> [CustomerPlace]
    func listPlaces(query: String?, countryCode: String?, page: PageRequest) async throws -> Page<PlaceCatalogItem>
    func createAndLinkCustomerPlace(
        customerId: String,
        place: PlaceCreateRequest,
        label: String?,
        isPrimary: Bool
    ) async throws -> CustomerPlace
    func linkCustomerPlace(customerId: String, request: CustomerPlaceLinkRequest) async throws -> CustomerPlace
    func unlinkCustomerPlace(customerId: String, placeId: String) async throws
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

    func listPlaces(
        query: String?,
        countryCode: String?,
        page: PageRequest
    ) async throws -> Page<PlaceCatalogItem> {
        try await client.send(
            Endpoints.listPlaces(query: query, countryCode: countryCode, page: page),
            as: Page<PlaceCatalogItem>.self
        )
    }

    func createAndLinkCustomerPlace(
        customerId: String,
        place: PlaceCreateRequest,
        label: String?,
        isPrimary: Bool
    ) async throws -> CustomerPlace {
        if let validationMessage = place.validationMessage {
            throw APIError.serverError(
                code: "VALIDATION_ERROR",
                message: validationMessage,
                traceId: nil,
                details: [:]
            )
        }

        // POST /places is idempotent by normalized name + city + country on the server.
        // If the following relation write fails, a retry reuses the same organization place.
        let created = try await client.send(
            try Endpoints.createPlace(place),
            as: PlaceCreated.self
        )
        return try await linkCustomerPlace(
            customerId: customerId,
            request: CustomerPlaceLinkRequest(
                placeId: created.id,
                label: label,
                isPrimary: isPrimary
            )
        )
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

    func unlinkCustomerPlace(customerId: String, placeId: String) async throws {
        try await client.sendWithoutResponse(
            Endpoints.unlinkCustomerPlace(customerId: customerId, placeId: placeId)
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

    func listPlaces(
        query: String?,
        countryCode: String?,
        page: PageRequest
    ) async throws -> Page<PlaceCatalogItem> {
        let normalizedQuery = query?.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedCountry = countryCode?.trimmingCharacters(in: .whitespacesAndNewlines)
            .uppercased()
        let payloads = try database.loadPayloads(table: "places", order: "name")
        let places = try payloads.map { try DateCoding.decoder().decode(Place.self, from: $0) }
        let filtered = places.compactMap { place -> PlaceCatalogItem? in
            guard let id = place.id, !id.isEmpty else { return nil }
            if let normalizedCountry, !normalizedCountry.isEmpty,
               place.countryCode?.uppercased() != normalizedCountry {
                return nil
            }
            if let normalizedQuery, !normalizedQuery.isEmpty {
                let searchable = [place.name, place.address, place.city, place.region]
                    .compactMap { $0 }
                guard searchable.contains(where: {
                    $0.localizedCaseInsensitiveContains(normalizedQuery)
                }) else {
                    return nil
                }
            }
            return placeCatalogItem(from: place, id: id)
        }
        let offset = page.cursor.flatMap { Int($0.replacingOccurrences(of: "offset:", with: "")) } ?? 0
        let items = Array(filtered.dropFirst(offset).prefix(page.limit))
        let next = offset + page.limit < filtered.count ? "offset:\(offset + page.limit)" : nil
        return Page(items: items, nextCursor: next)
    }

    func createAndLinkCustomerPlace(
        customerId: String,
        place request: PlaceCreateRequest,
        label: String?,
        isPrimary: Bool
    ) async throws -> CustomerPlace {
        if let validationMessage = request.validationMessage {
            throw APIError.serverError(
                code: "VALIDATION_ERROR",
                message: validationMessage,
                traceId: nil,
                details: [:]
            )
        }
        _ = try await customer(id: customerId)

        let normalizedName = request.name.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedCity = cleaned(request.city)
        let normalizedCountry = request.countryCode?.uppercased()
        let normalizedLabel = label?.trimmingCharacters(in: .whitespacesAndNewlines)
        let storedLabel = normalizedLabel?.isEmpty == false ? normalizedLabel : nil

        var result: (LocalCustomerPlaceLink, Place)?
        try database.transaction {
            // Match the server's shared-place identity rule: name + city + country.
            // Reuse an existing place instead of creating duplicate pins in the map workspace.
            let existingPayloads = try database.loadPayloads(table: "places")
            let existingPlaces = try existingPayloads.map {
                try DateCoding.decoder().decode(Place.self, from: $0)
            }
            let reused = existingPlaces.first { candidate in
                let sameCity: Bool
                switch (cleaned(candidate.city), normalizedCity) {
                case (nil, nil):
                    sameCity = true
                case let (stored?, requested?):
                    sameCity = stored.localizedCaseInsensitiveCompare(requested) == .orderedSame
                default:
                    sameCity = false
                }
                return candidate.name.localizedCaseInsensitiveCompare(normalizedName) == .orderedSame
                    && sameCity
                    && candidate.countryCode?.uppercased() == normalizedCountry
            }

            let place: Place
            if let reused {
                place = reused
            } else {
                let hasCoordinates = request.lat != nil && request.lon != nil
                place = Place(
                    id: UUID().uuidString,
                    name: normalizedName,
                    countryCode: normalizedCountry,
                    timezone: cleaned(request.timezone),
                    latitude: request.lat,
                    longitude: request.lon,
                    city: normalizedCity,
                    address: cleaned(request.addressLine),
                    region: cleaned(request.region),
                    geocodeStatus: hasCoordinates ? "RESOLVED" : "MISSING",
                    geocodeSource: hasCoordinates ? "client" : nil
                )
                try database.upsert(
                    table: "places",
                    id: place.id ?? "",
                    columns: ["name": place.name, "city": place.city],
                    payload: try DateCoding.encode(place)
                )
            }

            guard let placeID = place.id, !placeID.isEmpty else {
                throw APIError.serverError(
                    code: "PLACE_ID_MISSING",
                    message: "地点保存后缺少编号",
                    traceId: nil,
                    details: [:]
                )
            }
            let link = LocalCustomerPlaceLink(
                customerId: customerId,
                placeId: placeID,
                label: storedLabel,
                isPrimary: isPrimary
            )
            try persistCustomerPlaceLink(link)
            result = (link, place)
        }

        guard let (link, place) = result else {
            throw APIError.invalidResponse("企业地址已保存，但未形成客户关联")
        }
        return customerPlace(from: link, place: place)
    }

    func linkCustomerPlace(
        customerId: String,
        request: CustomerPlaceLinkRequest
    ) async throws -> CustomerPlace {
        // Verify both records locally; never create a dangling customer-place link.
        _ = try await customer(id: customerId)
        let place = try localPlace(id: request.placeId)
        let normalizedLabel = request.label?.trimmingCharacters(in: .whitespacesAndNewlines)
        let label = normalizedLabel?.isEmpty == false ? normalizedLabel : nil
        let link = LocalCustomerPlaceLink(
            customerId: customerId,
            placeId: request.placeId,
            label: label,
            isPrimary: request.isPrimary
        )
        try database.transaction {
            try persistCustomerPlaceLink(link)
        }
        return customerPlace(from: link, place: place)
    }

    func unlinkCustomerPlace(customerId: String, placeId: String) async throws {
        _ = try await customer(id: customerId)
        let existing = try database.count(
            table: "customer_places",
            where: "customer_id = ? AND place_id = ?",
            bindings: [customerId, placeId]
        )
        guard existing > 0 else {
            throw APIError.serverError(
                code: "NOT_FOUND",
                message: "客户与地点未关联",
                traceId: nil,
                details: ["customer_id": .string(customerId), "place_id": .string(placeId)]
            )
        }
        let linkID = Self.customerPlaceLinkID(customerId: customerId, placeId: placeId)
        try database.delete(table: "customer_places", id: linkID)
        // Deliberately retain the places row: it is an organization-level shared resource.
    }

    private func persistCustomerPlaceLink(_ link: LocalCustomerPlaceLink) throws {
        if link.isPrimary {
            let existingPayloads = try database.loadPayloads(
                table: "customer_places",
                where: "customer_id = ? AND is_primary = 1",
                bindings: [link.customerId]
            )
            let decoder = DateCoding.decoder()
            for payload in existingPayloads {
                let existing = try decoder.decode(LocalCustomerPlaceLink.self, from: payload)
                guard existing.placeId != link.placeId else { continue }
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
            id: Self.customerPlaceLinkID(customerId: link.customerId, placeId: link.placeId),
            columns: [
                "customer_id": link.customerId,
                "place_id": link.placeId,
                "label": link.label,
                "is_primary": link.isPrimary ? 1 : 0,
            ],
            payload: try DateCoding.encode(link)
        )
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
            region: place.region,
            countryCode: place.countryCode,
            lat: place.latitude,
            lon: place.longitude,
            geocodeStatus: place.geocodeStatus ?? (hasCoordinates ? "RESOLVED" : "MISSING"),
            geocodeSource: place.geocodeSource,
            geocodeConfidence: place.geocodeConfidence
        )
    }

    private func placeCatalogItem(from place: Place, id: String) -> PlaceCatalogItem {
        let hasCoordinates = place.latitude.map { (-90.0...90.0).contains($0) } == true
            && place.longitude.map { (-180.0...180.0).contains($0) } == true
        return PlaceCatalogItem(
            id: id,
            name: place.name,
            addressLine: place.address,
            city: place.city,
            region: place.region,
            countryCode: place.countryCode,
            timezone: place.timezone,
            lat: place.latitude,
            lon: place.longitude,
            geocodeStatus: place.geocodeStatus ?? (hasCoordinates ? "RESOLVED" : "MISSING"),
            geocodeSource: place.geocodeSource,
            geocodeConfidence: place.geocodeConfidence
        )
    }

    private func cleaned(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
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
