import SwiftUI
import UIKit

/// Global Travel OS visual language.
/// Keep semantic colour, spacing, typography, shape and icon decisions here.
enum DS {
    enum ColorToken {
        static let canvas = adaptive(light: 0xF4F6F4, dark: 0x10191E)
        static let surface = adaptive(light: 0xFFFFFF, dark: 0x17242A)
        static let surfaceSecondary = adaptive(light: 0xEDF1EF, dark: 0x203038)
        static let textPrimary = adaptive(light: 0x14242D, dark: 0xEDF4F1)
        static let textSecondary = adaptive(light: 0x5F6D73, dark: 0xA3B2B2)
        static let border = adaptive(light: 0xDCE4E0, dark: 0x304046)
        static let brand = adaptive(light: 0x126B65, dark: 0x63C2B5)
        static let brandPressed = adaptive(light: 0x0E514D, dark: 0x7ED2C7)
        static let brandSubtle = adaptive(light: 0xE3F1EE, dark: 0x1E3B38)
        static let success = adaptive(light: 0x24765D, dark: 0x78C8A3)
        static let warning = adaptive(light: 0x99601A, dark: 0xE8B66C)
        static let danger = adaptive(light: 0xB54745, dark: 0xF08077)
        static let info = adaptive(light: 0x426F91, dark: 0x83B5D4)

        // Route colours are deliberately separate from customer-tier/status colours.
        static let routePalette: [Color] = [
            Color(hex: 0x126B65), Color(hex: 0x426F91),
            Color(hex: 0x786A9A), Color(hex: 0xA17A37)
        ]

        static func tier(_ tier: String) -> Color {
            switch tier.uppercased() {
            case "A": return Color(hex: 0x786A9A)
            case "B": return info
            default: return textSecondary
            }
        }

        static func severity(_ severity: String) -> Color {
            switch severity.uppercased() {
            case "HIGH": return warning
            case "MEDIUM": return info
            case "LOW": return textSecondary
            default: return textSecondary
            }
        }

        static func priority(_ priority: String) -> Color {
            switch priority.uppercased() {
            case "P0": return danger
            case "P1": return warning
            case "P2": return info
            default: return textSecondary
            }
        }

        static func status(_ status: String) -> Color {
            switch status.uppercased() {
            case "ACTIVE", "COMPLETED", "APPROVED": return success
            case "READY", "PLANNING", "HOLD", "PENDING": return warning
            case "CANCELLED", "FAILED", "REJECTED": return danger
            default: return textSecondary
            }
        }

        private static func adaptive(light: UInt32, dark: UInt32) -> Color {
            Color(uiColor: UIColor { traits in
                UIColor(hex: traits.userInterfaceStyle == .dark ? dark : light)
            })
        }
    }

    enum Spacing {
        static let xxs: CGFloat = 2
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
        static let xxxl: CGFloat = 40
    }

    enum Radius {
        static let badge: CGFloat = 6
        static let control: CGFloat = 10
        static let card: CGFloat = 16
        static let prominentCard: CGFloat = 20
        static let sheet: CGFloat = 28
    }

    enum Typography {
        static let pageTitle = Font.title2.weight(.semibold)
        static let sectionTitle = Font.headline.weight(.semibold)
        static let body = Font.body
        static let supporting = Font.subheadline
        static let caption = Font.caption
        static let captionMono = Font.caption.monospacedDigit()
        static let metric = Font.title2.weight(.semibold).monospacedDigit()
        static let badge = Font.caption2.weight(.semibold)
    }

    enum Motion {
        static let micro: Double = 0.14
        static let standard: Double = 0.20
        static let emphasis: Double = 0.28
        static let routeDraw: Double = 0.32
    }
}

enum DSIconCatalog {
    static let mission = "scope"
    static let map = "map"
    static let customers = "building.2"
    static let brief = "text.book.closed"
    static let profile = "person.crop.circle"
    static let actions = "tray.full"
    static let assistant = "bubble.left.and.text.bubble.right"
    static let notifications = "bell"
    static let trip = "airplane"
    static let calendar = "calendar"
    static let hotel = "bed.double"
    static let rail = "tram"
    static let car = "car"
    static let walking = "figure.walk"
    static let microphone = "mic"
    static let budget = "chart.pie"
    static let weather = "cloud.sun"
    static let visa = "doc.text"
    static let evidence = "checkmark.seal"
    static let warning = "exclamationmark.triangle"
}

/// State badge for customer tiers, workflow states and recommendation priority.
struct DSBadge: View {
    let text: String
    var color: Color = DS.ColorToken.brand

    var body: some View {
        Text(text)
            .font(DS.Typography.badge)
            .foregroundStyle(color)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(color.opacity(0.12), in: Capsule())
            .accessibilityLabel(text)
    }
}

/// Data provenance must remain visible: estimates are never styled as confirmed facts.
struct DSDataQualityTag: View {
    enum Kind { case estimate, stale, gap, live }

    let kind: Kind
    let text: String

    private var color: Color {
        switch kind {
        case .estimate: return DS.ColorToken.textSecondary
        case .stale, .gap: return DS.ColorToken.warning
        case .live: return DS.ColorToken.success
        }
    }

    private var icon: String {
        switch kind {
        case .estimate: return "ruler"
        case .stale: return "clock.badge.exclamationmark"
        case .gap: return "clock.badge.questionmark"
        case .live: return "antenna.radiowaves.left.and.right"
        }
    }

    var body: some View {
        Label(text, systemImage: icon)
            .font(.caption2)
            .foregroundStyle(color)
            .accessibilityElement(children: .combine)
    }
}

/// Compact evidence/source marker used beside AI-generated claims.
struct DSEvidenceTag: View {
    let title: String
    var isVerified = false

    var body: some View {
        Label(title, systemImage: isVerified ? "checkmark.seal.fill" : "doc.text.magnifyingglass")
            .font(.caption2.weight(.medium))
            .foregroundStyle(isVerified ? DS.ColorToken.success : DS.ColorToken.textSecondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(DS.ColorToken.surfaceSecondary, in: Capsule())
    }
}

struct DSCard<Content: View>: View {
    var padding: CGFloat = DS.Spacing.lg
    var content: Content

    init(padding: CGFloat = DS.Spacing.lg, @ViewBuilder content: () -> Content) {
        self.padding = padding
        self.content = content()
    }

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.ColorToken.surface, in: RoundedRectangle(cornerRadius: DS.Radius.card))
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.card)
                    .stroke(DS.ColorToken.border, lineWidth: 0.8)
            )
    }
}

struct DSSectionHeader: View {
    let title: String
    var subtitle: String?
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.sm) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(DS.Typography.sectionTitle).foregroundStyle(DS.ColorToken.textPrimary)
                if let subtitle {
                    Text(subtitle).font(.caption).foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
            Spacer(minLength: DS.Spacing.sm)
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(DS.ColorToken.brand)
            }
        }
    }
}

struct DSMetricCard: View {
    let title: String
    let value: String
    var detail: String?
    var symbol: String?
    var tint: Color = DS.ColorToken.brand

    var body: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(spacing: 6) {
                    if let symbol {
                        Image(systemName: symbol).font(.caption).foregroundStyle(tint)
                    }
                    Text(title).font(.caption).foregroundStyle(DS.ColorToken.textSecondary)
                }
                Text(value)
                    .font(DS.Typography.metric)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                if let detail {
                    Text(detail).font(.caption2).foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }
}

struct DSButton: View {
    enum Kind { case primary, secondary, destructive }

    let title: String
    var systemImage: String?
    var kind: Kind = .primary
    var isLoading = false
    var isDisabled = false
    let action: () -> Void

    private var tint: Color {
        switch kind {
        case .primary: return DS.ColorToken.brand
        case .secondary: return DS.ColorToken.surfaceSecondary
        case .destructive: return DS.ColorToken.danger
        }
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if isLoading {
                    ProgressView().tint(kind == .secondary ? DS.ColorToken.brand : .white)
                } else if let systemImage {
                    Image(systemName: systemImage)
                }
                Text(title).font(.subheadline.weight(.semibold))
            }
            .frame(maxWidth: .infinity, minHeight: 46)
            .foregroundStyle(kind == .secondary ? DS.ColorToken.textPrimary : .white)
            .background(tint, in: RoundedRectangle(cornerRadius: DS.Radius.control))
        }
        .buttonStyle(.plain)
        .disabled(isDisabled || isLoading)
        .accessibilityHint(isDisabled ? "当前不可用" : "")
    }
}

struct DSIconButton: View {
    let systemImage: String
    let accessibilityLabel: String
    var tint: Color = DS.ColorToken.textPrimary
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 44, height: 44)
                .background(DS.ColorToken.surfaceSecondary, in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel)
    }
}

/// Metric row: label + value, with tabular digits for financial and travel figures.
struct DSMetricRow: View {
    let label: String
    let value: String
    var valueColor: Color = DS.ColorToken.textPrimary

    var body: some View {
        HStack(spacing: DS.Spacing.md) {
            Text(label).foregroundStyle(DS.ColorToken.textSecondary)
            Spacer(minLength: DS.Spacing.sm)
            Text(value).foregroundStyle(valueColor).monospacedDigit()
        }
        .font(.subheadline)
    }
}

struct DSEmptyState: View {
    let title: String
    let message: String
    var systemImage = "tray"

    var body: some View {
        ContentUnavailableView(title, systemImage: systemImage, description: Text(message))
            .padding(.vertical, DS.Spacing.xxxl)
            .frame(maxWidth: .infinity)
    }
}

struct DSLoadingState: View {
    var message = "正在加载…"

    var body: some View {
        VStack(spacing: DS.Spacing.md) {
            ProgressView()
            Text(message).font(.footnote).foregroundStyle(DS.ColorToken.textSecondary)
        }
        .frame(maxWidth: .infinity, minHeight: 140)
    }
}

struct DSErrorState: View {
    let message: String
    var retryTitle = "重试"
    var retry: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            Label("暂时无法加载", systemImage: "exclamationmark.triangle")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(DS.ColorToken.danger)
            Text(message).font(.footnote).foregroundStyle(DS.ColorToken.textSecondary)
            if let retry {
                Button(retryTitle, action: retry)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.brand)
                    .frame(minHeight: 44)
            }
        }
        .padding(DS.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.ColorToken.surfaceSecondary, in: RoundedRectangle(cornerRadius: DS.Radius.card))
    }
}

struct DSTimelineNode: View {
    let title: String
    let detail: String
    var time: String?
    var systemImage = "mappin"
    var tint: Color = DS.ColorToken.brand
    var isLast = false

    var body: some View {
        HStack(alignment: .top, spacing: DS.Spacing.md) {
            VStack(spacing: 0) {
                Image(systemName: systemImage)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(tint)
                    .frame(width: 28, height: 28)
                    .background(tint.opacity(0.12), in: Circle())
                if !isLast {
                    Rectangle().fill(DS.ColorToken.border).frame(width: 1, height: 34)
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(title).font(.subheadline.weight(.semibold))
                    Spacer()
                    if let time {
                        Text(time).font(.caption.monospacedDigit()).foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
                Text(detail).font(.caption).foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

enum DSChartTheme {
    static let series = [
        DS.ColorToken.brand,
        DS.ColorToken.info,
        Color(hex: 0x786A9A),
        Color(hex: 0xA17A37)
    ]
}

private extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}
