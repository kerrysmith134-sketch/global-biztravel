import SwiftUI
import os

@main
struct GlobalTravelOSApp: App {
    @StateObject private var environment = AppEnvironment()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(environment)
                .task {
                    // UI 测试/验收钩子：GBT_UI_TEST_SEED=1 时本地模式载入示例数据。
                    let env = ProcessInfo.processInfo.environment
                    guard env["GBT_UI_TEST_SEED"] == "1" else { return }
                    let canSeed = await LocalDemoSeeder.canSeed(environment: environment)
                    Logger(subsystem: "com.anari.globaltravelos", category: "demo-seed")
                        .info("hook canSeed=\(canSeed, privacy: .public) runMode=\(environment.settings.runMode, privacy: .public)")
                    guard canSeed else { return }
                    do {
                        _ = try await LocalDemoSeeder.seed(environment: environment, generatePlan: false)
                    } catch {
                        Logger(subsystem: "com.anari.globaltravelos", category: "demo-seed")
                            .error("seed failed: \(String(describing: error), privacy: .public)")
                    }
                }
        }
    }
}
