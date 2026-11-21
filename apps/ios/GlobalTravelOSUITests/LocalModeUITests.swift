import XCTest

/// 本地模式端到端冒烟（不依赖任何后端）：
/// 首启自动注入示例数据 → 今日卡 → 出行列表 → 行程详情五个分区可切换。
/// 与 `GlobalTravelOSUITests`（真实后端）互补，覆盖 ADR-0036 的本地路径。
final class LocalModeUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        // 本地模式会在首启请求通知权限（主动建议推送）：系统弹窗会挡住后续点击，
        // 用中断监视器统一允许，避免用例在真机/模拟器首次运行时假失败。
        addUIInterruptionMonitor(withDescription: "通知权限") { alert in
            for label in ["允许", "Allow"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                return true
            }
            return false
        }
        // 不注入 GBT_UI_TEST_BASE_URL：保持默认的本地模式（App 内 SQLite）。
        app.launch()
        app.tap() // 触发中断监视器（若有系统弹窗在此关闭）。
    }

    func testFivePrimaryWorkspacesAreReachable() throws {
        let mapTab = app.buttons["地图"]
        XCTAssertTrue(mapTab.waitForExistence(timeout: 10), "缺少地图一级入口")
        mapTab.tap()
        XCTAssertTrue(app.navigationBars["地图"].waitForExistence(timeout: 10), "地图工作区未打开")

        let briefTab = app.buttons["简报"]
        XCTAssertTrue(briefTab.waitForExistence(timeout: 10), "缺少目的地简报一级入口")
        briefTab.tap()
        XCTAssertTrue(
            app.navigationBars["目的地简报"].waitForExistence(timeout: 10),
            "目的地简报工作区未打开"
        )

        let customersTab = app.buttons["客户"]
        XCTAssertTrue(customersTab.waitForExistence(timeout: 10), "缺少客户一级入口")
        customersTab.tap()
        XCTAssertTrue(app.navigationBars["客户"].waitForExistence(timeout: 10), "客户工作区未打开")

        XCTAssertTrue(app.buttons["任务"].exists, "缺少 Mission 一级入口")
        XCTAssertTrue(app.buttons["我的"].exists, "缺少个人设置一级入口")
    }

    func testLocalModeSeededTripSections() throws {
        XCTAssertTrue(
            app.navigationBars["任务"].waitForExistence(timeout: 20),
            "本地模式首屏未出现"
        )
        // 本地模式标识（不是"数据来自服务端"）。
        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label BEGINSWITH '本地模式'")
            ).firstMatch.waitForExistence(timeout: 10),
            "首屏未显示本地模式标识"
        )

        // 差旅驾驶舱（审计 5.1）：英雄卡倒计时 + 今日分区 + 待处理风险。
        XCTAssertTrue(
            app.buttons.matching(
                NSPredicate(format: "label CONTAINS '出发' OR label CONTAINS '第 '")
            ).firstMatch.waitForExistence(timeout: 15),
            "驾驶舱缺少倒计时/进度"
        )
        XCTAssertTrue(app.staticTexts["今日拜访"].waitForExistence(timeout: 10), "驾驶舱缺少今日分区")
        XCTAssertTrue(app.staticTexts["AI 洞察与待决策"].waitForExistence(timeout: 10), "驾驶舱缺少待处理分区")
        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS '签证' OR label CONTAINS '计划'")
            ).firstMatch.exists,
            "待处理分区没有风险条目"
        )
        let cockpitShot = XCTAttachment(screenshot: app.screenshot())
        cockpitShot.name = "今日驾驶舱"
        cockpitShot.lifetime = .keepAlways
        add(cockpitShot)

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
