import XCTest

/// 本地模式端到端冒烟（不依赖任何后端）：
/// 首启自动注入示例数据 → 今日卡 → 出行列表 → 行程详情五个分区可切换。
/// 与 `GlobalTravelOSUITests`（真实后端）互补，覆盖 ADR-0036 的本地路径。
final class LocalModeUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        // 不注入 GBT_UI_TEST_BASE_URL：保持默认的本地模式（App 内 SQLite）。
        app.launch()
    }

    func testLocalModeSeededTripSections() throws {
        XCTAssertTrue(
            app.navigationBars["今日"].waitForExistence(timeout: 20),
            "本地模式首屏未出现"
        )
        // 本地模式标识（不是"数据来自服务端"）。
        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label BEGINSWITH '本地模式'")
            ).firstMatch.waitForExistence(timeout: 10),
            "首屏未显示本地模式标识"
        )

        let tripsTab = app.buttons["出行"]
        XCTAssertTrue(tripsTab.waitForExistence(timeout: 10), "未找到出行标签")
        tripsTab.tap()
        XCTAssertTrue(
            app.navigationBars["出行"].waitForExistence(timeout: 10),
            "出行列表未加载"
        )

        // 首启自动注入示例行程：列表必须非空。
        let firstTrip = app.cells.firstMatch
        XCTAssertTrue(firstTrip.waitForExistence(timeout: 20), "本地库没有示例行程（自动注入失败）")
        firstTrip.tap()

        // 详情拆成五个分区（审计 5.2）：逐段切换，断言该分区独有的内容出现。
        let sections: [(segment: String, markers: [String])] = [
            ("概览", ["时间窗口"]),
            ("日程", ["拜访"]),
            ("计划", ["计划版本"]),
            ("费用", ["费用与预算", "报销单", "记一笔费用"]),
            ("签证", ["签证与入境"]),
        ]
        for item in sections {
            let segment = app.buttons[item.segment].firstMatch
            XCTAssertTrue(segment.waitForExistence(timeout: 15), "未找到分区：\(item.segment)")
            segment.tap()
            XCTAssertTrue(
                waitForAny(item.markers, timeout: 12),
                "分区 \(item.segment) 未渲染（缺少 \(item.markers.joined(separator: "/"))）"
            )
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.lifetime = .keepAlways
            attachment.name = item.segment
            add(attachment)
        }
    }

    /// 列表是懒加载的：任一标签出现即视为该分区已渲染。
    private func waitForAny(_ labels: [String], timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "label IN %@", labels)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if app.descendants(matching: .any).matching(predicate).firstMatch.exists {
                return true
            }
            Thread.sleep(forTimeInterval: 0.3)
        }
        return false
    }
}
