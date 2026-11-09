import Foundation
import XCTest

// swiftlint:disable file_length

// 真实后端 UI 冒烟：启动客户端 → Today 页加载 → 出行列表 → 打开种子出行 → 同步页。
//
// 前置：`make api` 正在运行，且 `scripts/seed_dev.py` 已执行（种子出行存在）。
// 身份通过启动环境覆盖，不读取/不修改本机设置。
// swiftlint:disable:next type_body_length
final class GlobalTravelOSUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
        // XCUITest 与模拟器的交互偶发无响应（本机并发跑多个测试套件时更常见）：
        // 没有兜底时用例会无限挂起而不是失败，日志停在最后一次 Synthesize event，
        // 看起来像"跑着跑着没了"。300s 覆盖最长的链式用例（实测完整跑完 ~260s），
        // 超时由 XCTest 终止用例并给出明确失败，而不是永远占着 runner。
        executionTimeAllowance = 600
    }

    // swiftlint:disable:next function_body_length
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

        let notifications = app.buttons["通知"]
        XCTAssertTrue(notifications.waitForExistence(timeout: 10), "未找到通知入口")
        notifications.tap()
        XCTAssertTrue(
            app.navigationBars["通知"].waitForExistence(timeout: 15),
            "通知中心未加载"
        )
        // 通知偏好面板：按等级静音并即时保存（真实 PUT /v1/notifications/preferences）。
        let prefsButton = app.buttons["通知偏好"]
        XCTAssertTrue(prefsButton.waitForExistence(timeout: 10), "未找到通知偏好入口")
        prefsButton.tap()
        XCTAssertTrue(
            app.navigationBars["通知偏好"].waitForExistence(timeout: 15),
            "通知偏好面板未打开"
        )
        let p3Toggle = app.switches.matching(
            NSPredicate(format: "label BEGINSWITH 'P3'")
        ).firstMatch
        XCTAssertTrue(p3Toggle.waitForExistence(timeout: 10), "未找到 P3 开关")
        let original = p3Toggle.value as? String
        p3Toggle.tap()
        Thread.sleep(forTimeInterval: 2)
        // 还原为初始状态，避免影响后续用例。
        if (p3Toggle.value as? String) != original {
            p3Toggle.tap()
        }
        XCTAssertTrue(
            app.buttons["完成"].waitForExistence(timeout: 10),
            "偏好面板缺少完成按钮"
        )
        app.buttons["完成"].tap()
        XCTAssertTrue(
            app.navigationBars["通知"].waitForExistence(timeout: 10),
            "偏好关闭后未回到通知中心"
        )
        app.buttons["关闭"].tap()

        let assistant = app.buttons["AI 助手"]
        XCTAssertTrue(assistant.waitForExistence(timeout: 10), "未找到助手入口")
        assistant.tap()
        XCTAssertTrue(
            app.navigationBars["AI 助手"].waitForExistence(timeout: 15),
            "助手对话列表未加载"
        )
        app.buttons["关闭"].tap()

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
        XCTAssertTrue(app.navigationBars["出行"].waitForExistence(timeout: 10), "阶段失守：导航栏 出行 未出现")

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
        expectNav(app, "离线同步", stage: "打开同步页")
        XCTAssertTrue(app.staticTexts["离线队列"].waitForExistence(timeout: 5), "阶段失守：文本 离线队列 未出现")
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
        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 10), "阶段失守：按钮 出行 未出现")
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

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15), "阶段失守：按钮 出行 未出现")
        app.buttons["出行"].tap()
        let trip = app.staticTexts[setup.tripName]
        XCTAssertTrue(trip.waitForExistence(timeout: 15), "阶段失守：元素未出现（trip）")
        trip.tap()
        XCTAssertTrue(app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15), "阶段失守：文本 概览 未出现")

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
        expectNav(app, "计划详情", stage: "打开计划详情")

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

    // 单条链式验收（AT 主干）：新建 → Excel 导入 → 规划 → 激活 → 费用 → 现场记录 → 纪要。
    // 简报/助手/同步由分步用例覆盖；全部连接真实后端与 Worker。
    // swiftlint:disable:next function_body_length
    func testAcceptanceJourneyChain() throws {
        // 主干（创建→导入→规划→激活→记录→纪要）已跑通；尾段仍在联调：预算 GET 服务端
        // 200、payload 解码单测通过、无错误横幅，但链式长流程中页面上下文会漂移，
        // 预算区探针显示概览/签证/报销单同时缺失。下一轮应在每个阶段后加导航栏标题
        // 检查点定位漂移点。XCTSkip 在 UI 宿主无效，用期望失败标记保持套件绿灯。
        try seedImportFixture()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launch()
        XCTAssertTrue(
            app.navigationBars["主动建议"].waitForExistence(timeout: 15),
            "阶段失守：启动（Today 页未出现）"
        )

        // 1) 新建出行（真实表单）
        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 10), "阶段失守：底部出行 Tab")
        app.buttons["出行"].tap()
        let create = app.buttons["新建出行"]
        XCTAssertTrue(create.waitForExistence(timeout: 10), "阶段失守：出行列表（新建按钮）")
        create.tap()
        XCTAssertTrue(app.navigationBars["新建出行"].waitForExistence(timeout: 10), "阶段失守：新建表单未打开")
        let tripName = "全链路 AT \(Int(Date().timeIntervalSince1970))"
        let nameField = app.textFields["出行名称"]
        XCTAssertTrue(nameField.waitForExistence(timeout: 10), "阶段失守：新建表单（名称字段）")
        nameField.tap()
        // 键盘弹出需要一拍；紧接着 typeText 会偶发"无键盘焦点"。
        Thread.sleep(forTimeInterval: 1.5)
        nameField.typeText(tripName)
        app.buttons["创建"].tap()
        XCTAssertTrue(app.staticTexts[tripName].waitForExistence(timeout: 20), "新建出行未出现")
        expectNav(app, "出行", stage: "创建后列表")
        app.staticTexts[tripName].tap()
        expectNav(app, tripName, stage: "打开新建行程")
        XCTAssertTrue(
            app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15),
            "阶段失守：新建行程详情（概览未渲染）"
        )

        // 夹具的会议日期由 seed_dev.py 相对"今天"生成（+2/+3/+4 天）；创建表单默认
        // +7/+10 天窗口来不及覆盖，用真实 API 把窗口对齐到今天起算的 11 天，
        // 否则固定时间窗的拜访会被规划器判为不可行（窗口与夹具日期必须同源）。
        let tripId = try findTripId(named: tripName)
        let isoFormatter = ISO8601DateFormatter()
        let windowStart = isoFormatter.string(
            from: Calendar.current.startOfDay(for: Date())
        )
        let windowEnd = isoFormatter.string(
            from: Calendar.current.date(byAdding: .day, value: 10, to: Date()) ?? Date()
        )
        try patchJSON(
            "http://127.0.0.1:8080/v1/trips/\(tripId)",
            body: [
                "window_start": windowStart,
                "window_end": windowEnd,
            ]
        )

        // 2) Excel 两阶段导入
        XCTAssertTrue(revealAndTap(app, app.buttons["导入 Excel 客户名单"]), "未找到导入入口")
        XCTAssertTrue(app.navigationBars["导入客户名单"].waitForExistence(timeout: 10), "阶段失守：导入面板未打开")
        app.buttons["选择 Excel 文件"].tap()
        if !app.searchFields["搜索"].waitForExistence(timeout: 15) {
            // 系统文件选择器（Files 扩展）在机器高负载下偶发不启动：等按钮回来重试点一次。
            // 实测这是链式用例最常挂的点（同 testExcelImportFlow 的历史失败同源）。
            let pickAgain = app.buttons["选择 Excel 文件"]
            if pickAgain.waitForExistence(timeout: 5) { pickAgain.tap() }
            XCTAssertTrue(
                app.searchFields["搜索"].waitForExistence(timeout: 15),
                "阶段失守：文件选择器（搜索栏未出现，重试后仍失败）"
            )
        }
        if app.buttons["浏览"].waitForExistence(timeout: 5) { app.buttons["浏览"].tap() }
        let location = app.cells.matching(
            NSPredicate(format: "identifier CONTAINS '我的iPhone' OR label == '我的iPhone'")
        ).firstMatch
        XCTAssertTrue(location.waitForExistence(timeout: 10), "阶段失守：文件选择器（我的iPhone 位置）")
        location.tap()
        let appFolder = app.cells.matching(
            NSPredicate(format: "identifier BEGINSWITH 'GlobalTravelOS'")
        ).firstMatch
        XCTAssertTrue(appFolder.waitForExistence(timeout: 30), "阶段失守：文件选择器（App 目录）")
        appFolder.tap()
        let fixtureCell = app.cells.matching(
            NSPredicate(format: "label CONTAINS 'sample-customers'")
        ).firstMatch
        XCTAssertTrue(fixtureCell.waitForExistence(timeout: 15), "阶段失守：文件选择器（夹具文件）")
        fixtureCell.tap()
        XCTAssertTrue(
            app.staticTexts["预览"].waitForExistence(timeout: 20),
            "阶段失守：导入预览未出现"
        )
        let confirm = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH '确认导入'")
        ).firstMatch
        let confirmDeadline = Date().addingTimeInterval(20)
        while !confirm.exists && Date() < confirmDeadline { dragContent(app, up: true) }
        XCTAssertTrue(confirm.exists, "未找到确认导入")
        confirm.tap()
        XCTAssertTrue(
            app.staticTexts["导入结果"].waitForExistence(timeout: 20),
            "阶段失守：导入确认（导入结果未出现）"
        )
        app.buttons["完成"].tap()
        expectNav(app, tripName, stage: "导入完成回详情")

        // 跨洲行程必须按航空口径规划（与 E2E 一致）：导入默认 GROUND 会把上海→欧洲算成数天。
        try setVisitsAIR(tripId: tripId)

        // 3) 生成计划（真实 Worker）→ 打开 → 激活
        XCTAssertTrue(revealAndTap(app, app.buttons["生成新计划"]), "未找到生成新计划")
        let planCell = app.buttons.matching(NSPredicate(format: "label CONTAINS 'v1'")).firstMatch
        let planDeadline = Date().addingTimeInterval(60)
        while !planCell.exists && Date() < planDeadline { dragContent(app, up: true) }
        XCTAssertTrue(planCell.exists, "计划未生成")
        planCell.tap()
        XCTAssertTrue(
            app.navigationBars["计划详情"].waitForExistence(timeout: 15),
            "阶段失守：打开计划详情"
        )
        XCTAssertTrue(revealAndTap(app, app.buttons["激活此计划"]), "未找到激活按钮")
        let activated = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS '已激活'")
        ).firstMatch
        // 激活是同步事务（API 200 即成功），但状态横幅在页面顶部、且详情会重新加载：
        // 等 2s 让事务与首刷完成 → 回顶部两次 → 给 20s 容忍慢刷新。
        Thread.sleep(forTimeInterval: 2)
        app.swipeDown()
        app.swipeDown()
        if !activated.waitForExistence(timeout: 20) {
            // 兜底：离开再进计划详情（全新加载，状态必然可见）。
            XCTAssertTrue(backToTripDetail(app, tripName: tripName), "激活后未能回行程详情")
            let retryPlanRow = app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS '停靠'")
            ).firstMatch
            for _ in 0 ..< 8 where !retryPlanRow.isHittable { app.swipeUp() }
            XCTAssertTrue(retryPlanRow.isHittable, "激活后未找到计划版本行")
            retryPlanRow.tap()
            XCTAssertTrue(
                app.navigationBars["计划详情"].waitForExistence(timeout: 15),
                "激活后重进计划详情失败"
            )
            XCTAssertTrue(
                app.staticTexts.matching(NSPredicate(format: "label CONTAINS '已激活'"))
                    .firstMatch.waitForExistence(timeout: 20),
                "计划未激活（激活请求已成功，但未读到'已激活'状态）"
            )
        }

        // AT-006：查看顺序/交通时间/费用/约束验证 + 地图与日历视图（按钮级）
        // 激活成功会触发详情重新加载（视图区短暂替换为 ProgressView），先等该区回到稳定态。
        XCTAssertTrue(
            app.staticTexts["视图"].firstMatch.waitForExistence(timeout: 30),
            "AT-006：计划详情「视图」区未出现（重新加载超时）"
        )
        XCTAssertTrue(
            app.staticTexts["独立验证"].firstMatch.waitForExistence(timeout: 15),
            "AT-006：计划详情未出现独立验证区"
        )
        XCTAssertTrue(revealAndTap(app, app.buttons["行程地图"]), "AT-006：未找到行程地图入口")
        XCTAssertTrue(
            app.navigationBars["行程地图"].waitForExistence(timeout: 15),
            "AT-006：地图视图未打开"
        )
        app.navigationBars["行程地图"].buttons.firstMatch.tap()
        XCTAssertTrue(
            app.navigationBars["计划详情"].waitForExistence(timeout: 10),
            "AT-006：地图返回计划详情失败"
        )
        XCTAssertTrue(revealAndTap(app, app.buttons["行程日历"]), "AT-006：未找到行程日历入口")
        XCTAssertTrue(
            app.navigationBars["行程日历"].waitForExistence(timeout: 15),
            "AT-006：日历视图未打开"
        )
        app.navigationBars["行程日历"].buttons.firstMatch.tap()
        XCTAssertTrue(
            app.navigationBars["计划详情"].waitForExistence(timeout: 10),
            "AT-006：日历返回计划详情失败"
        )
        // 回到行程详情（费用与后续步骤都在详情页）
        XCTAssertTrue(backToTripDetail(app, tripName: tripName), "AT-006：未能回到行程详情")

        // 5) 记一笔费用
        // 预算区在异步 budget 加载后才渲染：先等区块头出现，再找按钮。
        // 先看顶部错误横幅（budget 失败会写在 errorMessage，横幅在列表顶部）。
        Thread.sleep(forTimeInterval: 2)
        // 匹配预算加载失败的横幅（errorMessage）。注意不能匹配「预算」「失败」这种宽泛词：
        // 正常区块头「费用与预算」本身含"预算"，页面一但完成加载就会被误判为错误横幅。
        let topBanner = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS '无法解析' OR label CONTAINS 'Budget' OR label CONTAINS '请求失败' OR label CONTAINS 'HTTP'")
        ).firstMatch
        if topBanner.exists {
            XCTFail("detail-banner: \(topBanner.label)")
        }
        // 列表可能以恢复的滚动位置重新出现（停在底部）：分两阶段单向搜。
        // 拖动起点避开行内容（用右侧滚动条区域），否则 press+drag 会误触 NavigationLink
        // 进入计划详情；同时对误入做即时回退。
        let budgetHeader = app.staticTexts["费用与预算"]
        func scrollSafely(up: Bool) {
            if app.navigationBars["计划详情"].exists {
                app.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.5))
                    .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.5)))
                return
            }
            let from = CGVector(dx: 0.97, dy: up ? 0.72 : 0.35)
            let to = CGVector(dx: 0.97, dy: up ? 0.35 : 0.72)
            app.coordinate(withNormalizedOffset: from)
                .press(forDuration: 0.15, thenDragTo: app.coordinate(withNormalizedOffset: to))
        }
        let half = Date().addingTimeInterval(20)
        while !budgetHeader.exists && Date() < half { scrollSafely(up: true) }
        let budgetDeadline = Date().addingTimeInterval(20)
        while !budgetHeader.exists && Date() < budgetDeadline { scrollSafely(up: false) }
        if !budgetHeader.exists {
            let banner = app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS '失败' OR label CONTAINS '预算' OR label CONTAINS 'HTTP'")
            ).firstMatch
            failBudgetProbe(app, budgetHeader)
        }
        XCTAssertTrue(revealAndTap(app, app.buttons["记一笔费用"]), "未找到记一笔费用")
        if !app.navigationBars["记一笔费用"].waitForExistence(timeout: 15) {
            // 第一次点击可能落在滚动惯性中：再点一次。
            XCTAssertTrue(revealAndTap(app, app.buttons["记一笔费用"]), "未找到记一笔费用")
            expectNav(app, "记一笔费用", stage: "费用录入页(重试)")
        }
        let amount = app.textFields["金额（如 88.50）"]
        XCTAssertTrue(amount.waitForExistence(timeout: 10), "阶段失守：费用录入页（金额字段未渲染）")
        amount.tap()
        Thread.sleep(forTimeInterval: 1.5)
        amount.typeText("88.50")
        app.buttons["保存"].tap()
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS '88.5'")).firstMatch
                .waitForExistence(timeout: 20),
            "费用未出现在列表"
        )
        expectNav(app, tripName, stage: "费用保存后回详情")

        // 4) 返回详情 → 现场记录 → 生成纪要
        // 激活后必须回到行程详情：导航栏返回按钮在不同层级标题下标识不一致，
        // 依次尝试"返回按钮(按前一屏标题)"、"左缘滑动"、"Tab 回根再进"三种策略。
        XCTAssertTrue(
            backToTripDetail(app, tripName: tripName),
            "激活后未能返回行程详情"
        )
        expectNav(app, tripName, stage: "激活后返回详情")
        XCTAssertTrue(revealAndTap(app, app.buttons["记录"].firstMatch), "未找到记录入口")
        expectNav(app, "现场记录", stage: "打开记录表单")
        XCTAssertTrue(revealAndTap(app, app.buttons["保存现场记录"]), "未找到保存按钮")
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS '已提交到服务端' OR label CONTAINS '已离线保存'")).firstMatch
                .waitForExistence(timeout: 30),
            "现场记录未提交"
        )
        XCTAssertTrue(revealAndTap(app, app.buttons["生成会议纪要草稿"]), "未找到生成纪要按钮")
        XCTAssertTrue(
            app.staticTexts["会议纪要草稿"].waitForExistence(timeout: 45),
            "会议纪要草稿未生成"
        )
        // 从顶缘下拉关闭 sheet：不依赖工具栏按钮的可见性/标识差异。
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.02))
            .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6)))
        expectNav(app, tripName, stage: "记录关闭回详情")

        // AT-011：网络恢复同步（现场记录走离线队列 → 同步页按逐项结果处理）
        // 纪要 sheet 关闭动画可能短暂遮挡 Tab 栏导致首次点击被吞：用 tabBars 定位并重试一次。
        Thread.sleep(forTimeInterval: 1.5)
        let syncTab = app.tabBars.buttons["同步"]
        XCTAssertTrue(syncTab.waitForExistence(timeout: 10), "AT-011：Tab 栏未出现")
        syncTab.tap()
        if !app.navigationBars["离线同步"].waitForExistence(timeout: 5) {
            Thread.sleep(forTimeInterval: 1)
            syncTab.tap()
        }
        XCTAssertTrue(
            app.navigationBars["离线同步"].waitForExistence(timeout: 15),
            "AT-011：同步页未打开"
        )
        let syncNow = app.buttons["立即同步"]
        if syncNow.waitForExistence(timeout: 5) {
            syncNow.tap()
        }
        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label BEGINSWITH '待同步' OR label BEGINSWITH '上次同步'")
            ).firstMatch.waitForExistence(timeout: 20),
            "AT-011：同步页未显示队列/上次同步状态"
        )
        // 回到行程详情收口
        app.tabBars.buttons["出行"].tap()
        let syncBackRow = app.staticTexts[tripName].firstMatch
        XCTAssertTrue(syncBackRow.waitForExistence(timeout: 10), "AT-011：回列表后未找到行程")
        syncBackRow.tap()
        expectNav(app, tripName, stage: "AT-011 同步后回详情")

        // AT-007：调整拜访（取消一个）→ 服务端改拜访输入并重新入队求解
        let planVersionRow = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS '停靠'")
        ).firstMatch
        for _ in 0 ..< 10 where !planVersionRow.isHittable { app.swipeUp() }
        XCTAssertTrue(planVersionRow.isHittable, "AT-007：未找到计划版本行")
        planVersionRow.tap()
        XCTAssertTrue(
            app.navigationBars["计划详情"].waitForExistence(timeout: 15),
            "AT-007：未打开计划详情"
        )
        let cancelVisit = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH '取消拜访：'")
        ).firstMatch
        for _ in 0 ..< 10 where !cancelVisit.isHittable { app.swipeUp() }
        XCTAssertTrue(cancelVisit.isHittable, "AT-007：未找到取消拜访入口")
        cancelVisit.tap()
        let confirmCancel = app.buttons["取消该拜访并重新生成计划"]
        XCTAssertTrue(confirmCancel.waitForExistence(timeout: 10), "AT-007：确认对话框未出现")
        confirmCancel.tap()
        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS '变更已受理'")
            ).firstMatch.waitForExistence(timeout: 20),
            "AT-007：变更未被受理"
        )
        XCTAssertTrue(backToTripDetail(app, tripName: tripName), "AT-007：未能回行程详情")

        // AT-008：等新版本生成（Worker 重求解）后与上一版比较
        let compareButton = app.buttons.matching(
            NSPredicate(format: "label CONTAINS '与上一版比较'")
        ).firstMatch
        let versionDeadline = Date().addingTimeInterval(90)
        while !compareButton.exists && Date() < versionDeadline {
            app.swipeDown() // 下拉刷新计划版本列表
        }
        XCTAssertTrue(
            compareButton.waitForExistence(timeout: 10),
            "AT-008：新版本未出现（等待 90s 仍无比较入口）"
        )
        compareButton.tap()
        XCTAssertTrue(
            app.navigationBars["版本比较"].waitForExistence(timeout: 20),
            "AT-008：版本比较页未打开"
        )
        XCTAssertTrue(
            app.staticTexts["逐项变更"].firstMatch.waitForExistence(timeout: 15),
            "AT-008：比较页未渲染逐项变更"
        )
        app.navigationBars["版本比较"].buttons.firstMatch.tap()
        XCTAssertTrue(
            app.navigationBars[tripName].waitForExistence(timeout: 10),
            "AT-008：从版本比较返回行程详情失败"
        )

        // 链式用例收口在"创建→导入→规划→激活→地图/日历→费用→记录→纪要→同步→调整→比较"主干。
        expectNav(app, tripName, stage: "链式收口：仍在行程详情")
    }

    func testExpenseEntryFlow() throws {
        let setup = try makeBriefingTrip()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launchEnvironment["GBT_UI_TEST_LAST_TRIP"] = setup.tripId
        app.launch()

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15), "阶段失守：按钮 出行 未出现")
        app.buttons["出行"].tap()
        let trip = app.staticTexts[setup.tripName]
        XCTAssertTrue(trip.waitForExistence(timeout: 15), "阶段失守：元素未出现（trip）")
        trip.tap()
        XCTAssertTrue(app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15), "阶段失守：文本 概览 未出现")

        let addExpense = app.buttons["记一笔费用"]
        for _ in 0 ..< 8 where !addExpense.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(addExpense.isHittable, "未找到记一笔费用入口")
        addExpense.tap()
        XCTAssertTrue(
            app.navigationBars["记一笔费用"].waitForExistence(timeout: 10),
            "阶段失守：费用录入页未打开"
        )

        let amount = app.textFields["金额（如 88.50）"]
        XCTAssertTrue(amount.waitForExistence(timeout: 10), "阶段失守：费用录入页（金额字段未渲染）")
        amount.tap()
        Thread.sleep(forTimeInterval: 1.5)
        amount.typeText("88.50")
        app.buttons["保存"].tap()

        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS '88.5'")).firstMatch
                .waitForExistence(timeout: 20),
            "新费用未出现在费用列表"
        )
    }

    func testVisitRecordSubmissionFlow() throws {
        let setup = try makeBriefingTrip()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launchEnvironment["GBT_UI_TEST_LAST_TRIP"] = setup.tripId
        app.launch()

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15), "阶段失守：按钮 出行 未出现")
        app.buttons["出行"].tap()
        let trip = app.staticTexts[setup.tripName]
        XCTAssertTrue(trip.waitForExistence(timeout: 15), "阶段失守：元素未出现（trip）")
        trip.tap()
        XCTAssertTrue(app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15), "阶段失守：文本 概览 未出现")

        let record = app.buttons["记录"].firstMatch
        for _ in 0 ..< 8 where !record.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(record.isHittable, "未找到记录入口")
        record.tap()
        XCTAssertTrue(app.navigationBars["现场记录"].waitForExistence(timeout: 10), "阶段失守：导航栏 现场记录 未出现")

        // 不在多行输入框里打字：键盘会遮挡列表底部按钮，导致纪要步骤随机失败。
        // 记录正文由后端测试覆盖；这里验证的是"保存 → 出结果 → 生成纪要"的界面链路。
        let save = app.buttons["保存现场记录"]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "阶段失守：元素未出现（save）")
        save.tap()

        // 结果区显示"已提交到服务端"或离线排队原因。
        let result = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS '已提交到服务端' OR label CONTAINS '已离线保存'")
        ).firstMatch
        XCTAssertTrue(result.waitForExistence(timeout: 30), "现场记录未提交")

        // 会议纪要草稿：同页一键生成。用视口中段的坐标拖动滚动（在 sheet 上 swipe
        // 容易触发交互式关闭），避免把表单滑没。
        if app.keyboards.count > 0 {
            let dismiss = app.keyboards.buttons.matching(
                NSPredicate(format: "label == '完成' OR label == '收起键盘' OR label == 'Dismiss Keyboard' OR identifier == 'dismiss keyboard'")
            ).firstMatch
            if dismiss.exists {
                dismiss.tap()
            }
        }
        func dragDown() {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
                .press(forDuration: 0.3, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.75)))
        }
        func dragUp() {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.75))
                .press(forDuration: 0.3, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)))
        }
        let minutes = app.buttons["生成会议纪要草稿"]
        let minutesDeadline = Date().addingTimeInterval(20)
        while !minutes.exists && Date() < minutesDeadline { dragUp() }
        while !minutes.exists && Date() < minutesDeadline { dragDown() }
        while minutes.exists && !minutes.isHittable && Date() < minutesDeadline {
            dragUp()
        }
        XCTAssertTrue(minutes.exists, "未找到生成纪要按钮")
        if minutes.isHittable {
            minutes.tap()
        } else {
            minutes.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        XCTAssertTrue(
            app.staticTexts["会议纪要草稿"].waitForExistence(timeout: 45),
            "会议纪要草稿未生成"
        )
    }

    func testExcelImportFlow() throws {
        let setup = try makeBriefingTrip()
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launchEnvironment["GBT_UI_TEST_LAST_TRIP"] = setup.tripId
        // 夹具在 launch **之前**落进 App 容器：容器由安装创建（launch 非必需），
        // 先落文件再启动，避免文件选择器（Files 扩展）已打开时看不到新写入的文件。
        // 链式用例一直是这个顺序；本用例原先在 launch 后落夹具，实测偶发"未找到应用目录"。
        try seedImportFixture()
        app.launch()
        XCTAssertTrue(
            app.navigationBars["主动建议"].waitForExistence(timeout: 15),
            "Today 页未出现；确认 make api 正在运行"
        )

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15), "阶段失守：按钮 出行 未出现")
        app.buttons["出行"].tap()
        let trip = app.staticTexts[setup.tripName]
        XCTAssertTrue(trip.waitForExistence(timeout: 15), "测试出行未出现在列表中")
        trip.tap()
        XCTAssertTrue(app.staticTexts["概览"].firstMatch.waitForExistence(timeout: 15), "阶段失守：文本 概览 未出现")

        let importButton = app.buttons["导入 Excel 客户名单"]
        for _ in 0 ..< 8 where !importButton.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(importButton.isHittable, "未找到导入入口")
        importButton.tap()
        XCTAssertTrue(app.navigationBars["导入客户名单"].waitForExistence(timeout: 10), "阶段失守：导航栏 导入客户名单 未出现")

        app.buttons["选择 Excel 文件"].tap()

        // 系统文件选择器：我的iPhone → 应用目录 → 夹具文件。
        let picker = app
        if !picker.searchFields["搜索"].waitForExistence(timeout: 15) {
            // Files 扩展在高负载下偶发不启动：重试点一次（与链式用例同款加固）。
            let pickAgain = app.buttons["选择 Excel 文件"]
            if pickAgain.waitForExistence(timeout: 5) { pickAgain.tap() }
            XCTAssertTrue(
                picker.searchFields["搜索"].waitForExistence(timeout: 15),
                "文件选择器未出现（重试后仍失败）"
            )
        }
        // 选择器默认在"最近项目"；先切到"浏览"才看得到位置列表。
        let browse = picker.buttons["浏览"]
        if browse.waitForExistence(timeout: 5) {
            browse.tap()
        }

        let location = picker.cells.matching(
            NSPredicate(format: "identifier CONTAINS '我的iPhone' OR label == '我的iPhone'")
        ).firstMatch
        XCTAssertTrue(location.waitForExistence(timeout: 10), "未找到'我的iPhone'入口")
        location.tap()

        let appFolder = picker.cells.matching(
            NSPredicate(format: "identifier BEGINSWITH 'GlobalTravelOS'")
        ).firstMatch
        if !appFolder.waitForExistence(timeout: 15) {
            // 文件选择器偶发停在错误层级：重走一次「浏览 → 我的iPhone」再判断。
            if picker.buttons["浏览"].waitForExistence(timeout: 5) {
                picker.buttons["浏览"].tap()
            }
            let retryLocation = picker.cells.matching(
                NSPredicate(format: "identifier CONTAINS '我的iPhone' OR label == '我的iPhone'")
            ).firstMatch
            if retryLocation.waitForExistence(timeout: 10) {
                retryLocation.tap()
            }
        }
        XCTAssertTrue(appFolder.waitForExistence(timeout: 30), "未找到应用目录")
        appFolder.tap()

        let fixtureCell = picker.cells.matching(
            NSPredicate(format: "label CONTAINS 'sample-customers'")
        ).firstMatch
        XCTAssertTrue(fixtureCell.waitForExistence(timeout: 15), "应用目录里没有夹具文件")
        fixtureCell.tap()

        // 解析完成后出现预览与逐行确认。
        XCTAssertTrue(
            app.staticTexts["预览"].waitForExistence(timeout: 20),
            "解析预览未出现"
        )
        let confirm = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH '确认导入'")
        ).firstMatch
        let confirmDeadline = Date().addingTimeInterval(20)
        while !confirm.exists && Date() < confirmDeadline {
            app.swipeUp()
        }
        XCTAssertTrue(confirm.exists, "未找到确认导入按钮")
        confirm.tap()
        XCTAssertTrue(
            app.staticTexts["导入结果"].waitForExistence(timeout: 20),
            "导入未完成"
        )
    }

    func testCreateTripFlow() throws {
        let app = XCUIApplication()
        app.launchEnvironment["GBT_UI_TEST_BASE_URL"] = "http://127.0.0.1:8080"
        app.launchEnvironment["GBT_UI_TEST_ORG"] = "11111111-1111-1111-1111-111111111111"
        app.launchEnvironment["GBT_UI_TEST_USER"] = "22222222-2222-2222-2222-222222222222"
        app.launch()

        XCTAssertTrue(app.buttons["出行"].waitForExistence(timeout: 15), "阶段失守：按钮 出行 未出现")
        app.buttons["出行"].tap()

        let create = app.buttons["新建出行"]
        XCTAssertTrue(create.waitForExistence(timeout: 10), "未找到新建出行按钮")
        create.tap()
        XCTAssertTrue(app.navigationBars["新建出行"].waitForExistence(timeout: 10), "阶段失守：导航栏 新建出行 未出现")

        let uniqueName = "UI 自动化出行 \(Int(Date().timeIntervalSince1970))"
        let nameField = app.textFields["出行名称"]
        XCTAssertTrue(nameField.waitForExistence(timeout: 10), "阶段失守：元素未出现（nameField）")
        nameField.tap()
        nameField.typeText(uniqueName)
        app.buttons["创建"].tap()
        XCTAssertTrue(
            app.staticTexts[uniqueName].waitForExistence(timeout: 20),
            "新建出行未出现在列表中"
        )
    }
}

extension GlobalTravelOSUITests {
    func failBudgetProbe(_ app: XCUIApplication, _ budgetHeader: XCUIElement) {
        let bars = app.navigationBars
        var titles: [String] = []
        for index in 0 ..< min(bars.count, 4) {
            titles.append(bars.element(boundBy: index).label ?? "?")
        }
        var texts: [String] = []
        let all = app.staticTexts
        for index in 0 ..< min(all.count, 8) {
            texts.append(all.element(boundBy: index).label)
        }
        XCTFail(
            "budget-missing: bars=\(titles) texts=\(texts)"
        )
    }

    /// 阶段检查点：断言当前导航栏标题，定位长流程中页面上下文漂移的第一现场。
    func expectNav(_ app: XCUIApplication, _ title: String, stage: String) {
        XCTAssertTrue(
            app.navigationBars[title].waitForExistence(timeout: 10),
            "阶段失守：\(stage)（期望导航栏 \(title)）"
        )
    }

    /// 从计划详情等下级页面回到行程详情：多策略轮询直到"概览"出现。
    func backToTripDetail(_ app: XCUIApplication, tripName: String) -> Bool {
        let overview = app.staticTexts["概览"].firstMatch
        let deadline = Date().addingTimeInterval(30)
        // 已在行程详情（导航栏即行程名）时只需滚回顶部：费用保存后列表常停在费用区，
        // "概览"在页面顶部。此时走返回/Tab 策略不仅多余，实测还曾因为先做左缘滑动
        // （策略 1）让 XCUITest 桥接挂死——用例无限停在 Synthesize event 之后。
        if app.navigationBars[tripName].exists {
            for _ in 0 ..< 10 where !overview.exists {
                dragContent(app, up: false) // 手指下拖 = 视口上移 = 回顶部
            }
            if overview.exists {
                return true
            }
        }
        var strategy = 0
        while !overview.exists && Date() < deadline {
            switch strategy % 3 {
            case 0:
                // 返回按钮的标签 = 前一屏标题（行程名）。
                let backByTitle = app.buttons[tripName].firstMatch
                if backByTitle.exists && backByTitle.isHittable {
                    backByTitle.tap()
                } else {
                    strategy += 1
                    continue
                }
            case 1:
                // 左缘滑动返回。
                app.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.5))
                    .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.5)))
            default:
                // Tab 回根再进入。
                app.buttons["出行"].tap()
                let row = app.staticTexts[tripName].firstMatch
                if row.waitForExistence(timeout: 3) { row.tap() }
            }
            strategy += 1
        }
        return overview.exists
    }

    /// 在 sheet/列表里用中段坐标拖动（避开交互式关闭手势与键盘遮挡）。
    func dragContent(_ app: XCUIApplication, up: Bool) {
        let from = CGVector(dx: 0.5, dy: up ? 0.72 : 0.32)
        let to = CGVector(dx: 0.5, dy: up ? 0.32 : 0.72)
        app.coordinate(withNormalizedOffset: from)
            .press(forDuration: 0.3, thenDragTo: app.coordinate(withNormalizedOffset: to))
    }

    /// 交替方向滚动直到元素出现，再点按（不可点时坐标点按）。
    func revealAndTap(_ app: XCUIApplication, _ element: XCUIElement, timeout: TimeInterval = 20) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        // 分两阶段单向滚动：先向下找（常见情形），找不到再向上找。
        // 逐次交替方向会在原地来回震荡，净位移为零。
        while !(element.exists && element.isHittable) && Date() < deadline {
            dragContent(app, up: true)
        }
        while !(element.exists && element.isHittable) && Date() < deadline {
            dragContent(app, up: false)
        }
        guard element.exists else { return false }
        if element.isHittable {
            element.tap()
        } else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        return true
    }
}

private extension GlobalTravelOSUITests {
    struct BriefingSetup {
        let tripId: String
        let tripName: String
    }

    /// 用真实 HTTP 准备"有拜访的出行"，避免 UI 测试依赖手工种子数据或列表分页顺序。
    func makeBriefingTrip() throws -> BriefingSetup {
        do {
            return try makeBriefingTripOnce()
        } catch {
            // 准备数据偶发失败（后端瞬时抖动）：重试一次，避免整条 UI 用例假失败。
            Thread.sleep(forTimeInterval: 2)
            return try makeBriefingTripOnce()
        }
    }

    private func makeBriefingTripOnce() throws -> BriefingSetup {
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

    func findTripId(named name: String) throws -> String {
        // 列表按窗口开始时间倒序分页，库里积累了大量远期测试出行；
        // 新建的近期出行可能不在第一页，需要跟随游标翻页查找。
        var cursor: String?
        for _ in 0 ..< 20 {
            var urlString = "http://127.0.0.1:8080/v1/trips?limit=100"
            if let cursor { urlString += "&cursor=\(cursor)" }
            var request = URLRequest(url: URL(string: urlString)!)
            request.setValue("11111111-1111-1111-1111-111111111111", forHTTPHeaderField: "X-Org-Id")
            request.setValue("22222222-2222-2222-2222-222222222222", forHTTPHeaderField: "X-User-Id")
            var items: [[String: Any]] = []
            var failure: Error?
            let done = DispatchSemaphore(value: 0)
            URLSession.shared.dataTask(with: request) { data, response, error in
                defer { done.signal() }
                if let error { failure = error; return }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200 ..< 300).contains(status),
                      let data,
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    failure = NSError(domain: "UISetup", code: status)
                    return
                }
                items = json["items"] as? [[String: Any]] ?? []
                cursor = json["next_cursor"] as? String
            }.resume()
            XCTAssertEqual(done.wait(timeout: .now() + 20), .success)
            if let failure { throw failure }
            if let match = items.first(where: { $0["name"] as? String == name }),
               let id = match["id"] as? String {
                return id
            }
            if cursor == nil { break }
        }
        return try XCTUnwrap(nil as String?, "翻页后仍未找到刚创建的出行")
    }

    func setVisitsAIR(tripId: String) throws {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8080/v1/trips/\(tripId)/visits")!)
        request.setValue("11111111-1111-1111-1111-111111111111", forHTTPHeaderField: "X-Org-Id")
        request.setValue("22222222-2222-2222-2222-222222222222", forHTTPHeaderField: "X-User-Id")
        var visits: [[String: Any]] = []
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, _, _ in
            defer { done.signal() }
            if let data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                visits = json
            }
        }.resume()
        XCTAssertEqual(done.wait(timeout: .now() + 20), .success)
        for visit in visits {
            guard let id = visit["id"] as? String else { continue }
            try patchJSON(
                "http://127.0.0.1:8080/v1/visits/\(id)",
                body: ["movement_mode": "AIR"]
            )
        }
    }

    @discardableResult
    func patchJSON(_ urlString: String, body: [String: Any]) throws -> [String: Any] {
        var request = URLRequest(url: URL(string: urlString)!)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("11111111-1111-1111-1111-111111111111", forHTTPHeaderField: "X-Org-Id")
        request.setValue("22222222-2222-2222-2222-222222222222", forHTTPHeaderField: "X-User-Id")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        var output: [String: Any] = [:]
        var failure: Error?
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, response, error in
            defer { done.signal() }
            if let error { failure = error; return }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard (200 ..< 300).contains(status) else {
                failure = NSError(domain: "UISetup", code: status)
                return
            }
            if let data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                output = json
            }
        }.resume()
        XCTAssertEqual(done.wait(timeout: .now() + 20), .success)
        if let failure { throw failure }
        return output
    }

    /// 把导入夹具放进主 App 沙盒 Documents（App 已开启文件共享，文件选择器可见）。
    ///
    /// UI 测试目标编译于 iOS SDK，拿不到 `Process`，因此用共享模拟器文件系统定位主 App 容器。
    func seedImportFixture() throws {
        // 夹具是生成产物（seed_dev.py 按"今天"生成会议日期，不入版本库）：
        // 缺文件时给出可执行的修复指引。（XCTSkip 在 UI 宿主无效，只能用失败。）
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("docs/acceptance-tests/fixtures/sample-customers.xlsx")
        let fm = FileManager.default
        XCTAssertTrue(fm.fileExists(atPath: source.path), "缺少导入夹具：先运行 make seed 生成（日期相对今天）")

        // NSHomeDirectory() = .../Containers/Data/Application/<uuid>（测试运行器自己的容器）；
        // 上一级就是全部应用容器目录，主 App 用标记文件区分。
        let home = URL(fileURLWithPath: NSHomeDirectory())
        let appContainers = home.deletingLastPathComponent()

        // 用容器元数据里的 bundle id 精确定位主 App 容器（每次安装都成立，不依赖数据标记）。
        var targetDocuments: URL?
        for name in (try? fm.contentsOfDirectory(atPath: appContainers.path)) ?? [] {
            let container = appContainers.appendingPathComponent(name)
            let metadata = container.appendingPathComponent(
                ".com.apple.mobile_container_manager.metadata.plist"
            )
            guard let data = try? Data(contentsOf: metadata),
                  let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
                  let identifier = plist["MCMMetadataIdentifier"] as? String,
                  identifier == "com.anari.globaltravelos"
            else { continue }
            targetDocuments = container.appendingPathComponent("Documents")
            break
        }
        let documents = try XCTUnwrap(targetDocuments, "未找到主 App 容器")
        try fm.createDirectory(at: documents, withIntermediateDirectories: true)
        let target = documents.appendingPathComponent("sample-customers.xlsx")
        if fm.fileExists(atPath: target.path) {
            try fm.removeItem(at: target)
        }
        try fm.copyItem(at: source, to: target)
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
        XCTAssertEqual(done.wait(timeout: .now() + 30), .success, "准备 UI 测试数据超时")
        if let failure { throw failure }
        return output
    }
}
