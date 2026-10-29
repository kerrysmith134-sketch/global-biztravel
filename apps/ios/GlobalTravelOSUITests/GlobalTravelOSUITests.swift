import XCTest

/// 真实后端 UI 冒烟：启动客户端 → Today 页加载 → 出行列表 → 打开种子出行 → 同步页。
///
/// 前置：`make api` 正在运行，且 `scripts/seed_dev.py` 已执行（种子出行存在）。
/// 身份通过启动环境覆盖，不读取/不修改本机设置。
final class GlobalTravelOSUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testLaunchBrowseSeedTripAndSyncCenter() throws {
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"

        addUIInterruptionMonitor(withDescription: "系统登录弹窗") { alert in
            let cancel = alert.buttons["取消"]
            if cancel.exists {
                cancel.tap()
                return true
            }
            return false
        }

        app.launch()

        XCTAssertTrue(
            app.navigationBars["主动建议"].waitForExistence(timeout: 15),
            "Today 页未出现；确认 make api 正在运行"
        )
        // 触发一次中断监视器（若系统弹窗出现会在这里被关闭）。
        app.tap()

        let actionCenter = app.buttons["行动中心"]
        XCTAssertTrue(actionCenter.waitForExistence(timeout: 10), "未找到行动中心入口")
        actionCenter.tap()
        XCTAssertTrue(
            app.navigationBars["行动中心"].waitForExistence(timeout: 15),
            "行动中心未加载"
        )
        app.buttons["关闭"].tap()

        let tripsTab = app.buttons["出行"]
        XCTAssertTrue(tripsTab.waitForExistence(timeout: 10), "未找到出行标签")
        tripsTab.tap()
        XCTAssertTrue(app.navigationBars["出行"].waitForExistence(timeout: 10))

        // 开发库里出行会累积，列表按时间倒序分页；冒烟测试取第一行，验证"列表 → 详情"真实可用。
        let firstTrip = app.cells.firstMatch
        XCTAssertTrue(firstTrip.waitForExistence(timeout: 15), "出行列表为空；确认后端与种子数据可用")
        firstTrip.tap()

        XCTAssertTrue(
            app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15),
            "出行详情未加载"
        )

        let syncTab = app.buttons["同步"]
        XCTAssertTrue(syncTab.waitForExistence(timeout: 10), "未找到同步标签")
        syncTab.tap()
        XCTAssertTrue(app.navigationBars["离线同步"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["离线队列"].waitForExistence(timeout: 5))
    }

    func testPreparedTripBriefing() throws {
        let setup = try makeBriefingTrip()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launchEnvironment["GBT_UI_TEST_LAST_TRIP"] = setup.tripId
        app.launch()

        XCTAssertTrue(
            app.navigationBars["主动建议"].waitForExistence(timeout: 15),
            "Today 页未出现；确认 make api 正在运行"
        )
        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 10))
        app.buttons["出行"].tap()

        let seedTrip = app.staticTexts[setup.tripName]
        XCTAssertTrue(seedTrip.waitForExistence(timeout: 15), "测试出行未出现在列表中")
        seedTrip.tap()
        XCTAssertTrue(
            app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15),
            "种子出行详情未加载"
        )

        // 拜访可能在列表下方：向下滚动直到出现"简报"入口。
        let briefingButton = app.buttons["简报"].firstMatch
        for _ in 0 ..< 6 where !briefingButton.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(briefingButton.isHittable, "未找到拜访简报入口")
        briefingButton.tap()
        XCTAssertTrue(
            app.navigationBars["拜访简报"].waitForExistence(timeout: 15),
            "拜访简报未加载"
        )
        app.buttons["关闭"].tap()
        XCTAssertTrue(
            app.navigationBars[setup.tripName].waitForExistence(timeout: 10),
            "简报关闭后未回到出行详情"
        )
    }

    func testGenerateAndActivatePlan() throws {
        let setup = try makeBriefingTrip()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launchEnvironment["GBT_UI_TEST_LAST_TRIP"] = setup.tripId
        app.launch()

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15))
        app.buttons["出行"].tap()
        let trip = app.staticTexts[setup.tripName]
        XCTAssertTrue(trip.waitForExistence(timeout: 15))
        trip.tap()
        XCTAssertTrue(app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15))

        let generate = app.buttons["生成新计划"]
        for _ in 0 ..< 8 where !generate.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(generate.isHittable, "未找到生成新计划入口")
        generate.tap()

        // 后台 Worker 求解；给真实队列留出时间。
        // 计划区在拜访列表之后；求解是异步的，边等边向下滚。
        let planCell = app.buttons.matching(NSPredicate(format: "label CONTAINS 'v1'")).firstMatch
        let deadline = Date().addingTimeInterval(60)
        while !planCell.exists && Date() < deadline {
            app.swipeUp()
            usleep(1_000_000)
        }
        XCTAssertTrue(planCell.exists, "计划未生成；确认 Worker 正在运行")
        planCell.tap()
        XCTAssertTrue(app.navigationBars["计划详情"].waitForExistence(timeout: 15))

        let activate = app.buttons["激活此计划"]
        for _ in 0 ..< 8 where !activate.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(activate.isHittable, "未找到激活按钮")
        activate.tap()

        // 激活结果提示在页面顶部；先等请求完成，再回到顶部读取状态。
        let activated = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS '已激活'")
        ).firstMatch
        let activationDeadline = Date().addingTimeInterval(20)
        while !activated.exists && Date() < activationDeadline {
            app.swipeDown()
        }
        XCTAssertTrue(activated.exists, "计划未激活")
    }

    func testExpenseEntryFlow() throws {
        let setup = try makeBriefingTrip()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launchEnvironment["GBT_UI_TEST_LAST_TRIP"] = setup.tripId
        app.launch()

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15))
        app.buttons["出行"].tap()
        let trip = app.staticTexts[setup.tripName]
        XCTAssertTrue(trip.waitForExistence(timeout: 15))
        trip.tap()
        XCTAssertTrue(app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15))

        let addExpense = app.buttons["记一笔费用"]
        for _ in 0 ..< 8 where !addExpense.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(addExpense.isHittable, "未找到记一笔费用入口")
        addExpense.tap()
        XCTAssertTrue(app.navigationBars["记一笔费用"].waitForExistence(timeout: 10))

        let amount = app.textFields["金额（如 88.50）"]
        XCTAssertTrue(amount.waitForExistence(timeout: 10))
        amount.tap()
        amount.typeText("88.50")
        app.buttons["保存"].tap()

        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS '88.5'")).firstMatch
                .waitForExistence(timeout: 20),
            "新费用未出现在费用列表"
        )
    }

    func testCreateTripFlow() throws {
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launch()

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15))
        app.buttons["出行"].tap()

        let create = app.buttons["新建出行"]
        XCTAssertTrue(create.waitForExistence(timeout: 10), "未找到新建出行按钮")
        create.tap()
        XCTAssertTrue(app.navigationBars["新建出行"].waitForExistence(timeout: 10))

        let uniqueName = "UI 自动化出行 \(Int(Date().timeIntervalSince1970))"
        let nameField = app.textFields["出行名称"]
        XCTAssertTrue(nameField.waitForExistence(timeout: 10))
        nameField.tap()
        nameField.typeText(uniqueName)
        app.buttons["创建"].tap()
        XCTAssertTrue(
            app.staticTexts[uniqueName].waitForExistence(timeout: 20),
            "新建出行未出现在列表中"
        )
    }
}

private extension GlobalTravelOSUITests {
    struct BriefingSetup {
        let tripId: String
        let tripName: String
    }

    /// 用真实 HTTP 准备"有拜访的出行"，避免 UI 测试依赖手工种子数据或列表分页顺序。
    func makeBriefingTrip() throws -> BriefingSetup {
        let base = "http://127.0.0.1:8080"
        let suffix = Int(Date().timeIntervalSince1970)
        let place = try postJSON(
            "\(base)/v1/places",
            body: [
                "name": "UI 简报地点 \(suffix)",
                "city": "Berlin",
                "country_code": "DE",
                "lat": 52.52,
                "lon": 13.405,
            ]
        )
        let placeId = try XCTUnwrap(place["id"] as? String)

        let formatter = ISO8601DateFormatter()
        let start = Date().addingTimeInterval(86_400 * 7)
        let end = Date().addingTimeInterval(86_400 * 9)
        let tripName = "UI 简报出行 \(suffix)"
        let trip = try postJSON(
            "\(base)/v1/trips",
            body: [
                "name": tripName,
                "origin_place_id": placeId,
                "window_start": formatter.string(from: start),
                "window_end": formatter.string(from: end),
                "countries": ["DE"],
            ]
        )
        let tripId = try XCTUnwrap(trip["id"] as? String)

        _ = try postJSON(
            "\(base)/v1/trips/\(tripId)/visits",
            body: [
                "place_id": placeId,
                "title": "UI 简报拜访",
                "objective": "确认续约条款",
                "service_minutes": 60,
            ]
        )
        return BriefingSetup(tripId: tripId, tripName: tripName)
    }

    func postJSON(_ urlString: String, body: [String: Any]) throws -> [String: Any] {
        var request = URLRequest(url: URL(string: urlString)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("11111111-1111-1111-1111-111111111111", forHTTPHeaderField: "X-Org-Id")
        request.setValue("22222222-2222-2222-2222-222222222222", forHTTPHeaderField: "X-User-Id")
        request.setValue(UUID().uuidString.lowercased(), forHTTPHeaderField: "Idempotency-Key")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        var output: [String: Any] = [:]
        var failure: Error?
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, response, error in
            defer { done.signal() }
            if let error {
                failure = error
                return
            }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard (200 ..< 300).contains(status) else {
                failure = NSError(
                    domain: "UISetup",
                    code: status,
                    userInfo: [NSLocalizedDescriptionKey: "HTTP \(status): \(String(data: data ?? Data(), encoding: .utf8) ?? "")"]
                )
                return
            }
            if let data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                output = json
            }
        }.resume()
        XCTAssertEqual(done.wait(timeout: .now() + 20), .success, "准备 UI 测试数据超时")
        if let failure { throw failure }
        return output
    }
}
