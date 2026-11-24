import SwiftUI

struct TripCloseoutPanel: View {
    let readiness: TripCloseoutReadiness?
    let isLoading: Bool
    let errorMessage: String?
    let isLocalMode: Bool
    let onRefresh: () -> Void
    let onOpenSource: (TripCloseoutIssue) -> Void

    var body: some View {
        Group {
            Section("出差结账准备度") {
                if isLocalMode {
                    Label("本地模式不提供云端结账评估", systemImage: "info.circle")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                } else if isLoading && readiness == nil {
                    ProgressView("正在评估结账条件…")
                } else if let errorMessage, readiness == nil {
                    Label("结账评估暂不可用", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                    Text(errorMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    refreshButton
                } else if let readiness {
                    readinessSummary(readiness)
                    if let errorMessage {
                        Label("刷新失败，当前显示上一次评估", systemImage: "wifi.exclamationmark")
                            .font(.caption)
                            .foregroundStyle(.orange)
                        Text(errorMessage)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ContentUnavailableView(
                        "尚无结账评估",
                        systemImage: "checkmark.shield",
                        description: Text("重新评估后查看拜访结果、费用证据与报销状态。")
                    )
                    refreshButton
                }
            }

            if let readiness {
                if !readiness.blockers.isEmpty {
                    Section("必须先处理 · \(readiness.blockers.count)") {
                        ForEach(readiness.blockers) { issue in
                            issueRow(issue)
                        }
                    }
                }
                if !readiness.warnings.isEmpty {
                    Section("需要确认 · \(readiness.warnings.count)") {
                        ForEach(readiness.warnings) { issue in
                            issueRow(issue)
                        }
                    }
                }
                if readiness.issues.isEmpty {
                    Section("结账检查") {
                        Label("没有发现待处理问题", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                }
            }
        }
    }

    private var refreshButton: some View {
        Button(action: onRefresh) {
            Label("重新评估", systemImage: "arrow.clockwise")
        }
        .accessibilityIdentifier("closeout-refresh")
        .disabled(isLoading || isLocalMode)
    }

    private func readinessSummary(_ readiness: TripCloseoutReadiness) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: readiness.status.symbolName)
                    .foregroundStyle(statusColor(readiness.status))
                VStack(alignment: .leading, spacing: 2) {
                    Text(readiness.status.label)
                        .font(.headline)
                    Text("评估于 \(ISO8601.shortDisplay(from: readiness.generatedAt))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button(action: onRefresh) {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("重新评估结账条件")
                .accessibilityIdentifier("closeout-refresh")
                .disabled(isLoading)
            }

            HStack(spacing: 14) {
                Label(
                    "\(readiness.summary.blockerCount) 阻断",
                    systemImage: "xmark.octagon"
                )
                .foregroundStyle(
                    readiness.summary.blockerCount > 0 ? Color.red : Color.secondary
                )
                Label(
                    "\(readiness.summary.warningCount) 提醒",
                    systemImage: "exclamationmark.triangle"
                )
                .foregroundStyle(
                    readiness.summary.warningCount > 0 ? Color.orange : Color.secondary
                )
            }
            .font(.caption.weight(.medium))

            LabeledContent(
                "拜访结果",
                value: "\(readiness.summary.visitOutcomeCount) / \(readiness.summary.visitCount)"
            )
            LabeledContent(
                "费用已纳入报销",
                value: "\(readiness.summary.expensesInReportCount) / \(readiness.summary.expenseCount)"
            )
            LabeledContent(
                "未纳入报销",
                value: String(readiness.summary.expensesNotInReportCount)
            )
            LabeledContent(
                "缺少票据",
                value: String(readiness.summary.expensesWithoutReceiptCount)
            )
            LabeledContent(
                "票据不可用 / OCR 待复核",
                value: "\(readiness.summary.receiptAssetUnavailableCount) / " +
                    "\(readiness.summary.ocrNeedsReviewCount)"
            )
            LabeledContent(
                "未完成商务跟进",
                value: String(readiness.summary.openBusinessFollowUpCount)
            )

            ForEach(readiness.totalsByCurrency) { total in
                LabeledContent(
                    "费用 · \(total.currency)",
                    value: "\(String(format: "%.2f", total.amount)) · \(total.count) 笔"
                )
            }

            Text("只读评估：不会自动更改拜访、费用或报销状态。")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func statusColor(_ status: TripCloseoutStatus) -> Color {
        switch status {
        case .blocked: return .red
        case .readyWithExceptions: return .orange
        case .readyForReview, .reportSubmitted, .reportApproved: return .green
        case .unknown: return .secondary
        }
    }

    private func issueRow(_ issue: TripCloseoutIssue) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Label(issue.severity.label, systemImage: issue.severity.symbolName)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(issue.severity == .blocker ? Color.red : Color.orange)
                Spacer()
                Text(issue.sourceLabel)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text(issue.title)
                .font(.subheadline.weight(.semibold))
            Text(issue.detail)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(issue.actionHint)
                .font(.caption)
                .foregroundStyle(.secondary)
            Button {
                onOpenSource(issue)
            } label: {
                Label("定位来源记录", systemImage: "arrow.up.right.square")
                    .font(.caption.weight(.semibold))
            }
            .accessibilityIdentifier("closeout-source-\(issue.sourceId)")
        }
        .padding(.vertical, 5)
    }
}
