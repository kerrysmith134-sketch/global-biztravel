import XCTest
@testable import GlobalTravelOS

/// EngineMapping 的映射语义测试（审计整改：本地规划此前把 businessValue 硬编码为 0，
/// 引擎下游全部支持价值目标，唯独映射层没传——本地规划因此完全不感知客户重要性）。
///
/// 锁死的行为：
/// 1. 拜访 → 客户 tier（A/B/C）推导商业价值（3/2/1）；
/// 2. 无客户关联的拜访取缺省值，不参与价值竞争；
/// 3. 地点缺坐标的拜访不进规划输入（0,0 会把路线拖到海里）。
final class EngineMappingTests: XCTestCase {
    private func makeTrip() -> Trip {
        Trip(
            id: "trip-1",
            name: "映射测试行程",
            version: 1,
            window: TripWindow(
                start: Date(timeIntervalSince1970: 1_794_000_000),
                end: Date(timeIntervalSince1970: 1_794_400_000)
            ),
            originPlace: Place(id: "p0", name: "出发地", latitude: 31.2304, longitude: 121.4737),
            originPlaceId: "p0"
        )
    }

    private func makePlace(_ id: String, lat: Double? = 52.52, lon: Double? = 13.405) -> Place {
        Place(id: id, name: "地点 \(id)", latitude: lat, longitude: lon)
    }

    private func makeVisit(_ id: String, placeId: String, customerId: String? = nil) -> TripVisit {
        TripVisit(id: id, placeId: placeId, customerId: customerId)
    }

    private func makeCustomer(_ id: String, tier: String) -> Customer {
        Customer(id: id, name: "客户 \(id)", tier: tier)
    }

    func testBusinessValueDerivedFromCustomerTier() throws {
        let trip = makeTrip()
        let places = [makePlace("p1"), makePlace("p2"), makePlace("p3")]
        let visits = [
            makeVisit("v1", placeId: "p1", customerId: "cA"),
            makeVisit("v2", placeId: "p2", customerId: "cB"),
            makeVisit("v3", placeId: "p3", customerId: "cC"),
        ]
        let customers = [
            makeCustomer("cA", tier: "A"),
            makeCustomer("cB", tier: "B"),
            makeCustomer("cC", tier: "C"),
        ]
        let problem = EngineMapping.makeProblem(
            trip: trip, visits: visits, places: places,
            customers: customers, budgetSeconds: 1
        )
        // visit.id 不是合法 UUID，映射层会做确定性派生——按输入顺序对应断言。
        XCTAssertEqual(problem.stops.count, 3)
        XCTAssertEqual(problem.stops[0].businessValue, 3.0, "A 级客户价值 3")
        XCTAssertEqual(problem.stops[1].businessValue, 2.0, "B 级客户价值 2")
        XCTAssertEqual(problem.stops[2].businessValue, 1.0, "C 级客户价值 1")
    }

    func testVisitWithoutCustomerGetsDefaultWeight() throws {
        let trip = makeTrip()
        let places = [makePlace("p1")]
        let visits = [makeVisit("v1", placeId: "p1", customerId: nil)]
        let problem = EngineMapping.makeProblem(
            trip: trip, visits: visits, places: places,
            customers: [], budgetSeconds: 1
        )
        XCTAssertEqual(try XCTUnwrap(problem.stops.first).businessValue, 1.0)
    }

    func testUnknownCustomerFallsBackToDefaultWeight() throws {
        let trip = makeTrip()
        let places = [makePlace("p1")]
        let visits = [makeVisit("v1", placeId: "p1", customerId: "cGhost")]
        let problem = EngineMapping.makeProblem(
            trip: trip, visits: visits, places: places,
            customers: [makeCustomer("cA", tier: "A")], budgetSeconds: 1
        )
        XCTAssertEqual(try XCTUnwrap(problem.stops.first).businessValue, 1.0)
    }

    func testPlaceWithoutCoordinatesIsExcludedNotZeroFilled() {
        let trip = makeTrip()
        // 审计指认：缺坐标曾被补成 0,0（把他拖到几内亚湾），路线被静默污染。
        let places = [makePlace("p1", lat: nil, lon: nil), makePlace("p2", lat: 48.14, lon: 11.58)]
        let visits = [
            makeVisit("v1", placeId: "p1"),
            makeVisit("v2", placeId: "p2"),
        ]
        let problem = EngineMapping.makeProblem(
            trip: trip, visits: visits, places: places,
            customers: [], budgetSeconds: 1
        )
        XCTAssertEqual(problem.stops.count, 1, "缺坐标地点的拜访不进规划")
        // 剩余的是慕尼黑（48.14/11.58）——用坐标判别，避开 visitId 的 UUID 派生。
        XCTAssertEqual(problem.stops.first?.lat ?? 0, 48.14, accuracy: 0.001)
        XCTAssertEqual(problem.stops.first?.lon ?? 0, 11.58, accuracy: 0.001)
    }
}
