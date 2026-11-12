import SwiftUI

struct RootView: View {
    @EnvironmentObject private var environment: AppEnvironment

    var body: some View {
        TabView {
            TodayView(environment: environment)
                .tabItem { Label("今日", systemImage: "sparkles") }
            TripsView(environment: environment)
                .tabItem { Label("出行", systemImage: "airplane") }
            ActionCenterView(environment: environment)
                .tabItem { Label("行动中心", systemImage: "tray.full") }
            SettingsView()
                .tabItem { Label("我的", systemImage: "person.crop.circle") }
        }
    }
}
