import SwiftUI

private enum RootTab: Hashable {
    case mission
    case map
    case customers
    case brief
    case profile
}

struct RootView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @State private var selection: RootTab = .mission

    var body: some View {
        TabView(selection: $selection) {
            TodayView(environment: environment)
                .tabItem { Label("任务", systemImage: DSIconCatalog.mission) }
                .tag(RootTab.mission)

            MapWorkspaceView(environment: environment)
                .tabItem { Label("地图", systemImage: DSIconCatalog.map) }
                .tag(RootTab.map)

            CustomersView(environment: environment)
                .tabItem { Label("客户", systemImage: DSIconCatalog.customers) }
                .tag(RootTab.customers)

            DestinationBriefView(environment: environment)
                .tabItem { Label("简报", systemImage: DSIconCatalog.brief) }
                .tag(RootTab.brief)

            SettingsView()
                .tabItem { Label("我的", systemImage: DSIconCatalog.profile) }
                .tag(RootTab.profile)
        }
        .tint(DS.ColorToken.brand)
        .toolbarBackground(.visible, for: .tabBar)
        .toolbarBackground(DS.ColorToken.surface, for: .tabBar)
        // TodayView sequences local first-run seeding before its first cockpit read.
    }
}
