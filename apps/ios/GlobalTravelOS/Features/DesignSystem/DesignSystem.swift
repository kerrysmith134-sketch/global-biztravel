import SwiftUI

/// 设计系统（审计 5.6）：颜色/字体/间距/圆角/状态徽标统一口径。
/// 页面只组合令牌与组件，不再各自发明视觉规范。
enum DS {
    // MARK: - 颜色

    enum ColorToken {
        /// 品牌主色（今日/进行中/主操作）
        static let brand = Color.blue
        /// 状态色
        static let success = Color.green
        static let warning = Color.orange
        static let danger = Color.red
        static let neutral = Color.secondary
        /// 分级（客户 A/B/C）
        static func tier(_ tier: String) -> Color {
            switch tier.uppercased() {
            case "A": return .purple
            case "B": return .blue
            default: return .gray
            }
        }
        /// 建议严重度
        static func severity(_ severity: String) -> Color {
            switch severity.uppercased() {
            case "HIGH": return .orange
            case "MEDIUM": return .blue
            case "LOW": return .secondary
            default: return .gray
            }
        }
        /// 通知优先级（P0–P3）
        static func priority(_ priority: String) -> Color {
            switch priority.uppercased() {
            case "P0": return .red
            case "P1": return .orange
            case "P2": return .blue
            default: return .secondary
            }
        }
        /// 计划/行程状态
        static func status(_ status: String) -> Color {
            switch status.uppercased() {
            case "ACTIVE": return .green
            case "READY", "PLANNING": return .orange
            case "HOLD": return .yellow
            case "COMPLETED": return .secondary
            case "CANCELLED": return .red
            default: return .secondary
            }
        }
    }

    // MARK: - 间距 / 圆角

    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
    }

    enum Radius {
        static let card: CGFloat = 14
        static let badge: CGFloat = 6
        static let sheet: CGFloat = 20
    }

    // MARK: - 字体

    enum Typography {
        static let pageTitle = Font.title2.weight(.bold)
        static let sectionTitle = Font.headline
        static let body = Font.body
        static let caption = Font.caption
        static let captionMono = Font.caption.monospacedDigit()
        static let badge = Font.caption2.weight(.bold)
    }
}

/// 状态徽标：分级/严重度/优先级/状态统一渲染（替代各页面手写 Capsule 样式）。
struct DSBadge: View {
    let text: String
    var color: Color = .blue

    var body: some View {
        Text(text)
            .font(DS.Typography.badge)
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.12), in: Capsule())
            .accessibilityLabel(text)
    }
}

/// 数据质量标注（审计 5.3 要求：估算/过期/缺口必须可辨识）。
struct DSDataQualityTag: View {
    enum Kind {
        case estimate
        case stale
        case gap
        case live
    }

    let kind: Kind
    let text: String

    private var color: Color {
        switch kind {
        case .estimate: return .secondary
        case .stale: return .orange
        case .gap: return .orange
        case .live: return .green
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
    }
}

/// 指标行：标签 + 值 + 可选强调色（预算/复盘等页面统一）。
struct DSMetricRow: View {
    let label: String
    let value: String
    var valueColor: Color = .primary

    var body: some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(valueColor)
                .monospacedDigit()
        }
    }
}
