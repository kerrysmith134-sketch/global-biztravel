import SwiftUI

struct RootView: View {
    @EnvironmentObject private var environment: AppEnvironment

    var body: some View {
        TabView {
            TodayView(environment: environment)
                .tabItem { Label("今日", systemImage: "sparkles") }
            TripsView(environment: environment)
                .tabItem { Label("出行", systemImage: "airplane") }
            SyncCenterView(environment: environment)
                .tabItem { Label("同步", systemImage: "arrow.triangle.2.circlepath") }
        }
    }
}
