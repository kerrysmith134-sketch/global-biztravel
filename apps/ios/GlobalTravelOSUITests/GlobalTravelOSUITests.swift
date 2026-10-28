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
}
