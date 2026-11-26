import XCTest
@testable import GlobalTravelOS

/// Customer, shared-place and trip-link persistence workflows for the local-first store.
final class LocalCustomerRepositoryTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var database: LocalDatabase!

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("local-customers-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
        database = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("test.sqlite3")
        )
    }

    override func tearDownWithError() throws {
        database = nil
        try? FileManager.default.removeItem(at: temporaryDirectory)
    }

    private func makeRepository() -> LocalTripRepository {
        LocalTripRepository(database: database)
    }

    private func makeRequest(name: String) -> CreateTripRequest {
        CreateTripRequest(
            name: name,
            purpose: "客户拜访",
            originPlace: PlaceRef(
                name: "上海浦东",
                countryCode: "cn",
                timezone: "Asia/Shanghai",
                latitude: 31.23,
                longitude: 121.47
            ),
            window: TripWindow(
                start: Date(timeIntervalSinceNow: 86_400),
                end: Date(timeIntervalSinceNow: 86_400 * 3)
            )
        )
    }

    func testLocalCustomerPlacesPersistAndEnforceSinglePrimaryAddress() async throws {
        let repository = LocalCustomerRepository(database: database)
        let customer = try await repository.createCustomer(
            CustomerCreateRequest(name: "Local Customer", tier: "A")
        )
        let headOffice = Place(
            id: "place-local-hq",
            name: "Head Office",
            countryCode: "DE",
            latitude: 52.5200,
            longitude: 13.4050,
            city: "Berlin",
            address: "Industriestrasse 12"
        )
        let factory = Place(
            id: "place-local-factory",
            name: "Factory",
            countryCode: "DE",
            latitude: 51.4416,
            longitude: 5.4697,
            city: "Eindhoven"
        )
        for place in [headOffice, factory] {
            try database.upsert(
                table: "places",
                id: place.id ?? "",
                columns: ["name": place.name, "city": place.city],
                payload: try DateCoding.encode(place)
            )
        }

        let firstLink = try await repository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: headOffice.id ?? "",
                label: "总部",
                isPrimary: true
            )
        )
        XCTAssertTrue(firstLink.isPrimary)
        XCTAssertTrue(firstLink.hasValidCoordinates)
        XCTAssertEqual(firstLink.geocodeStatus, "RESOLVED")
        XCTAssertNil(firstLink.geocodeSource) // 本地存储不伪造地理编码来源

        _ = try await repository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: factory.id ?? "",
                label: "工厂",
                isPrimary: true
            )
        )

        // Re-linking an existing pair updates it instead of creating a duplicate row.
        _ = try await repository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: factory.id ?? "",
                label: "生产基地",
                isPrimary: true
            )
        )

        let places = try await repository.customerPlaces(customerId: customer.id)
        XCTAssertEqual(places.count, 2)
        XCTAssertEqual(places.first?.placeId, factory.id)
        XCTAssertEqual(places.first?.label, "生产基地")
        XCTAssertTrue(places.first?.isPrimary == true)
        XCTAssertFalse(places.first(where: { $0.placeId == headOffice.id })?.isPrimary ?? true)

        let storedCount = try database.count(
            table: "customer_places",
            where: "customer_id = ?",
            bindings: [customer.id]
        )
        XCTAssertEqual(storedCount, 2)
    }

    func testCustomerAddressBookCreateSearchAndUnlinkKeepsSharedPlace() async throws {
        let repository = LocalCustomerRepository(database: database)
        let customer = try await repository.createCustomer(
            CustomerCreateRequest(name: "Address Book Customer", tier: "A")
        )
        let request = PlaceCreateRequest(
            name: "Rotterdam Factory",
            addressLine: "Harbor Road 8",
            city: "Rotterdam",
            region: "South Holland",
            countryCode: "NL",
            timezone: "Europe/Amsterdam",
            lat: 51.9244,
            lon: 4.4777
        )

        let created = try await repository.createAndLinkCustomerPlace(
            customerId: customer.id,
            place: request,
            label: "工厂",
            isPrimary: true
        )
        XCTAssertTrue(created.isPrimary)
        XCTAssertEqual(created.region, "South Holland")
        XCTAssertEqual(created.geocodeStatus, "RESOLVED")
        XCTAssertEqual(created.geocodeSource, "client")
        XCTAssertTrue(created.hasValidCoordinates)

        let catalog = try await repository.listPlaces(
            query: "Rotterdam",
            countryCode: "NL",
            page: PageRequest(limit: 20)
        )
        XCTAssertEqual(catalog.items.count, 1)
        XCTAssertEqual(catalog.items.first?.id, created.placeId)
        XCTAssertEqual(catalog.items.first?.displayAddress, "Harbor Road 8 · Rotterdam · South Holland · NL")

        try await repository.unlinkCustomerPlace(customerId: customer.id, placeId: created.placeId)
        let remainingLinks = try await repository.customerPlaces(customerId: customer.id)
        XCTAssertTrue(remainingLinks.isEmpty)

        // Removing a customer relation must not delete the organization-level shared place.
        let retained = try await repository.listPlaces(
            query: "Rotterdam",
            countryCode: "NL",
            page: PageRequest(limit: 20)
        )
        XCTAssertEqual(retained.items.map(\.id), [created.placeId])
    }

    func testLocalGlobalCustomerMapAggregatesAndFiltersAddresses() async throws {
        let repository = LocalCustomerRepository(database: database)
        let customerA = try await repository.createCustomer(
            CustomerCreateRequest(name: "Map Customer A", tier: "A", countryCode: "DE", industry: "Energy")
        )
        let customerB = try await repository.createCustomer(
            CustomerCreateRequest(name: "Map Customer B", tier: "C", countryCode: "NL")
        )
        let berlin = Place(
            id: "map-berlin-hq", name: "Berlin HQ", countryCode: "DE",
            latitude: 52.52, longitude: 13.405, city: "Berlin",
            address: "Industriestrasse 12", region: "Berlin",
            geocodeStatus: "RESOLVED", geocodeSource: "client"
        )
        let rotterdam = Place(
            id: "map-rotterdam-factory", name: "Rotterdam Factory",
            countryCode: "NL", city: "Rotterdam", geocodeStatus: "MISSING"
        )
        let hamburg = Place(
            id: "map-hamburg-office", name: "Hamburg Office", countryCode: "DE",
            latitude: 53.5511, longitude: 9.9937, city: "Hamburg",
            geocodeStatus: "RESOLVED", geocodeSource: "client"
        )
        for place in [berlin, rotterdam, hamburg] {
            try database.upsert(
                table: "places", id: place.id ?? "",
                columns: ["name": place.name, "city": place.city],
                payload: try DateCoding.encode(place)
            )
        }
        _ = try await repository.linkCustomerPlace(
            customerId: customerA.id,
            request: CustomerPlaceLinkRequest(placeId: berlin.id ?? "", label: "总部", isPrimary: true)
        )
        _ = try await repository.linkCustomerPlace(
            customerId: customerA.id,
            request: CustomerPlaceLinkRequest(placeId: rotterdam.id ?? "", label: "工厂", isPrimary: false)
        )
        _ = try await repository.linkCustomerPlace(
            customerId: customerA.id,
            request: CustomerPlaceLinkRequest(placeId: hamburg.id ?? "", label: "区域办公室", isPrimary: false)
        )
        _ = try await repository.linkCustomerPlace(
            customerId: customerB.id,
            request: CustomerPlaceLinkRequest(placeId: berlin.id ?? "", label: "合作办公室", isPrimary: true)
        )

        let all = try await repository.listGlobalCustomerMapLocations(
            filters: .all, page: PageRequest(limit: 50)
        )
        XCTAssertEqual(all.items.count, 4)
        XCTAssertEqual(all.summary.customersWithLocations, 2)
        XCTAssertEqual(all.summary.customersWithoutLocations, 0)
        XCTAssertEqual(all.summary.locationsMapped, 3)
        XCTAssertEqual(all.summary.locationsMissingCoordinates, 1)
        let germany = try XCTUnwrap(all.summary.countryStats.first { $0.countryCode == "DE" })
        XCTAssertEqual(germany.customersTotal, 2)
        XCTAssertEqual(germany.locationsTotal, 3)
        XCTAssertEqual(germany.locationsMapped, 3)
        XCTAssertEqual(germany.tierACustomers, 1)
        XCTAssertEqual(germany.tierCCustomers, 1)

        let aTier = try await repository.listGlobalCustomerMapLocations(
            filters: GlobalCustomerMapFilters(tier: "A"), page: PageRequest(limit: 50)
        )
        XCTAssertEqual(aTier.items.count, 3)
        XCTAssertTrue(aTier.items.allSatisfy { $0.customer.tier == "A" })

        let missing = try await repository.listGlobalCustomerMapLocations(
            filters: GlobalCustomerMapFilters(geocodeStatus: "MISSING"), page: PageRequest(limit: 50)
        )
        XCTAssertEqual(missing.items.map(\.place.id), ["map-rotterdam-factory"])
    }

    func testAddingExistingCustomerAddressToTripReusesPlace() async throws {
        let trip = try await makeRepository().createTrip(makeRequest(name: "地图客户加入 Mission"))
        let customerRepository = LocalCustomerRepository(database: database)
        let customer = try await customerRepository.createCustomer(
            CustomerCreateRequest(name: "Map Mission Customer", tier: "A")
        )
        let place = Place(
            id: "map-mission-existing-place",
            name: "Customer Berlin HQ",
            countryCode: "DE",
            latitude: 52.5200,
            longitude: 13.4050,
            city: "Berlin",
            address: "Industriestrasse 12",
            geocodeStatus: "RESOLVED",
            geocodeSource: "client"
        )
        try database.upsert(
            table: "places",
            id: place.id ?? "",
            columns: ["name": place.name, "city": place.city],
            payload: try DateCoding.encode(place)
        )
        _ = try await customerRepository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: place.id ?? "",
                label: "总部",
                isPrimary: true
            )
        )
        let placesBefore = try database.count(table: "places")

        let visit = try await LocalVisitRepository(database: database).createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                placeId: place.id,
                customerId: customer.id,
                title: "客户现场沟通",
                serviceMinutes: 90,
                priority: 1,
                movementMode: "GROUND"
            )
        )

        XCTAssertEqual(visit.customerId, customer.id)
        XCTAssertEqual(visit.placeId, place.id)
        XCTAssertEqual(try database.count(table: "places"), placesBefore)
        let storedVisits = try await LocalVisitRepository(database: database).listVisits(tripId: trip.id)
        XCTAssertEqual(storedVisits.first?.placeId, place.id)
        XCTAssertEqual(storedVisits.first?.customerId, customer.id)
    }

    func testLocalVisitKeepsCRMCustomerAssociation() async throws {
        let trip = try await makeRepository().createTrip(makeRequest(name: "CRM 关联行程"))
        let customer = try await LocalCustomerRepository(database: database).createCustomer(
            CustomerCreateRequest(name: "Visit Customer")
        )
        let visit = try await LocalVisitRepository(database: database).createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                customerId: customer.id,
                placeName: "Customer Site",
                city: "Berlin",
                countryCode: "DE",
                latitude: 52.52,
                longitude: 13.405,
                title: "Customer review"
            )
        )

        let stored = try await LocalVisitRepository(database: database).listVisits(tripId: trip.id)
        XCTAssertEqual(stored.first?.id, visit.id)
        XCTAssertEqual(stored.first?.customerId, customer.id)
        XCTAssertNotNil(stored.first?.placeId)
    }
}
