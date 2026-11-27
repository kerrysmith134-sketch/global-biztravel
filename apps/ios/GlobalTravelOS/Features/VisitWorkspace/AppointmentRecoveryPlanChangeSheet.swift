import SwiftUI

/// A guarded bridge from an evidence-backed appointment recommendation into the existing
/// Plan Change → Job → Plan Diff → explicit activation lifecycle.
struct AppointmentRecoveryPlanChangeSheet: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    let followUp: VisitFollowUp
    let proposal: AppointmentRecoveryPlanChangeRecommendation
    let onActivated: () -> Void

    @State private var currentTrip: Trip?
    @State private var accepted: PlanChangeAccepted?
    @State private var currentPlanningJobId: String?
    @State private var job: PlanJob?
    @State private var generatedPlan: PlanVersionDetail?
    @State private var diff: PlanDiff?
    @State private var isLoadingBasis = false
    @State private var isSubmitting = false
    @State private var isRefreshingJob = false
    @State private var isRetryingPlanning = false
    @State private var isActivating = false
    @State private var errorMessage: String?
    @State private var statusMessage: String?

    private var basisIsCurrent: Bool {
        guard let currentTrip else { return false }
        return currentTrip.activePlanVersionId == proposal.basePlanVersionId
            && currentTrip.version == proposal.baseTripVersion
    }

    private var canActivate: Bool {
        guard
            let accepted,
            let generatedPlan,
            job?.resolvedStatus == .succeeded,
            generatedPlan.tripId == nil || generatedPlan.tripId == followUp.tripId,
            generatedPlan.status == .draft,
            generatedPlan.feasible == true,
            generatedPlan.unplacedList.isEmpty,
            ["FEASIBLE", "OPTIMAL"].contains((generatedPlan.solutionStatus ?? "").uppercased()),
            diff != nil
        else {
            return false
        }
        return accepted.tripVersion > 0
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("预约时间变更") {
                    LabeledContent("拜访", value: followUp.title)
                    LabeledContent("客户窗口开始", value: customerLocalTime(proposal.newWindowStart))
                    LabeledContent("客户窗口结束", value: customerLocalTime(proposal.newWindowEnd))
                    if let timezone = proposal.timezone {
                        LabeledContent("客户所在地时区", value: timezone)
                    } else {
                        Label("缺少客户 IANA 时区，必须重新评估后才能提交", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                    Text("按客户所在地时区显示预约窗口。客户窗口仍属于出差人陈述，不代表已获外部独立验证。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if !proposal.reason.isEmpty {
                        Text(proposal.reason)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("计划版本保护") {
                    if isLoadingBasis && currentTrip == nil {
                        ProgressView("正在读取当前行程版本…")
                    } else if let currentTrip {
                        LabeledContent("当前行程版本", value: "v\(currentTrip.version)")
                        LabeledContent("建议基础版本", value: proposal.basePlanVersionId)
                        LabeledContent("当前生效计划", value: currentTrip.activePlanVersionId ?? "无")
                        if basisIsCurrent {
                            Label("建议仍基于当前行程和生效计划", systemImage: "checkmark.shield.fill")
                                .foregroundStyle(.green)
                        } else {
                            Label("该建议已过期，不能直接提交", systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                            Text("请回到预约恢复事项，使用最新航班证据重新评估。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Button("重新核对当前版本") {
                            Task { await loadCurrentTrip() }
                        }
                        .disabled(isLoadingBasis || isSubmitting || isActivating)
                    } else {
                        Text("尚未成功读取当前行程版本。必须在线核对后才能提交。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button("重试读取") {
                            Task { await loadCurrentTrip() }
                        }
                        .disabled(isLoadingBasis)
                    }
                }

                Section {
                    Text("提交后，服务端会更新拜访的规划输入并异步生成新计划。旧计划不会自动被激活；新计划必须经过独立验证、差异检查和你的再次确认。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Button {
                        Task { await submitPlanChange() }
                    } label: {
                        if isSubmitting {
                            Label("提交中…", systemImage: "hourglass")
                        } else {
                            Label("提交计划变更并重新规划", systemImage: "arrow.triangle.2.circlepath")
                        }
                    }
                    .disabled(
                        !basisIsCurrent
                            || customerTimeZone == nil
                            || accepted != nil
                            || isSubmitting
                            || isLoadingBasis
                    )
                } header: {
                    Text("人工提交")
                }

                if let accepted {
                    Section("规划任务") {
                        LabeledContent("变更状态", value: accepted.status)
                        LabeledContent("新行程版本", value: "v\(accepted.tripVersion)")
                        LabeledContent("任务 ID", value: currentPlanningJobId ?? accepted.optimizationJobId)
                        if let job {
                            LabeledContent("任务进度", value: job.statusLabel)
                            if let error = job.displayError {
                                Text(error)
                                    .font(.footnote)
                                    .foregroundStyle(.red)
                            }
                        } else {
                            Text("已受理。规划由服务端 Worker 异步执行。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        Button {
                            Task { await refreshPlanningJob() }
                        } label: {
                            if isRefreshingJob {
                                Label("查询中…", systemImage: "hourglass")
                            } else {
                                Label("刷新规划状态", systemImage: "arrow.clockwise")
                            }
                        }
                        .disabled(isRefreshingJob || isActivating || isRetryingPlanning)
                        if job?.resolvedStatus == .failed
                            || job?.resolvedStatus == .dead
                            || job?.resolvedStatus == .cancelled {
                            Button {
                                Task { await retryPlanning() }
                            } label: {
                                if isRetryingPlanning {
                                    Label("重新排队中…", systemImage: "hourglass")
                                } else {
                                    Label("在当前输入上重新生成计划", systemImage: "arrow.clockwise")
                                }
                            }
                            .disabled(isRetryingPlanning || isRefreshingJob || isActivating)
                            Text("只重新发起求解，不会再次修改拜访时间窗。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                if let generatedPlan {
                    Section("新计划验证") {
                        LabeledContent("计划版本", value: generatedPlan.versionLabel)
                        LabeledContent("求解状态", value: generatedPlan.solutionStatusLabel)
                        LabeledContent("可行性", value: generatedPlan.feasibilityLabel)
                        LabeledContent("未排拜访", value: "\(generatedPlan.unplacedList.count)")
                        if let violations = generatedPlan.policyViolations, !violations.isEmpty {
                            Text("差旅政策提示：\(violations.count) 项。最终激活仍由服务端执行严格政策校验。")
                                .font(.footnote)
                                .foregroundStyle(.orange)
                            ForEach(violations) { violation in
                                Text(violation.message)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if !canActivate {
                            Text("当前计划未满足自动开放激活按钮的门槛：必须有已验证的 FEASIBLE/OPTIMAL 结果、零未排拜访，并成功取得版本差异。")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                }

                if let diff {
                    Section("与原计划的差异") {
                        if let summary = diff.summary {
                            Text(summary.displayText)
                                .font(.subheadline.weight(.medium))
                        }
                        if let delta = diff.travelMinutesDelta {
                            LabeledContent("行驶时间变化", value: "\(delta > 0 ? "+" : "")\(delta) min")
                        }
                        if diff.materialStops.isEmpty {
                            Text("没有拜访顺序或时间变化。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(Array(diff.materialStops.prefix(8))) { stop in
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(stop.change.label)
                                        .font(.caption.weight(.semibold))
                                    Text(stop.travelDescription)
                                        .font(.footnote)
                                    Text(stop.visitId)
                                        .font(.caption2.monospaced())
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        if let unplaced = diff.unplaced, !unplaced.isEmpty {
                            Text("未排项变化：\(unplaced.count) 项")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                }

                if let statusMessage {
                    Section {
                        Label(statusMessage, systemImage: "checkmark.circle")
                            .font(.footnote)
                            .foregroundStyle(.green)
                    }
                }

                if let errorMessage {
                    Section("需要处理") {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }

                if accepted != nil {
                    Section {
                        Button(role: .destructive) {
                            dismiss()
                        } label: {
                            Text("关闭工作区")
                        }
                    } footer: {
                        Text("关闭不会撤销已提交的行程输入或后台规划任务。可稍后从行程计划页继续查看任务和计划版本。")
                    }
                }
            }
            .navigationTitle("预约计划变更")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await activateGeneratedPlan() }
                    } label: {
                        if isActivating {
                            Text("激活中…")
                        } else {
                            Text("确认激活")
                        }
                    }
                    .disabled(!canActivate || isActivating || isSubmitting || isRefreshingJob)
                }
            }
        }
        .task { await loadCurrentTrip() }
    }

    private var customerTimeZone: TimeZone? {
        guard let timezone = proposal.timezone else { return nil }
        return TimeZone(identifier: timezone)
    }

    private func customerLocalTime(_ date: Date) -> String {
        guard let customerTimeZone else { return "客户时区未知" }
        let formatter = DateFormatter()
        formatter.locale = .autoupdatingCurrent
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = customerTimeZone
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func loadCurrentTrip() async {
        guard !isLoadingBasis else { return }
        isLoadingBasis = true
        errorMessage = nil
        defer { isLoadingBasis = false }
        do {
            currentTrip = try await environment.tripRepository.trip(id: followUp.tripId)
        } catch {
            currentTrip = nil
            errorMessage = environment.errorMessage(error)
        }
    }

    private func submitPlanChange() async {
        guard !isSubmitting else { return }
        isSubmitting = true
        errorMessage = nil
        statusMessage = nil
        defer { isSubmitting = false }

        do {
            // Re-read immediately before the write. The server repeats both checks transactionally.
            let latestTrip = try await environment.tripRepository.trip(id: followUp.tripId)
            currentTrip = latestTrip
            guard
                latestTrip.activePlanVersionId == proposal.basePlanVersionId,
                latestTrip.version == proposal.baseTripVersion
            else {
                errorMessage = "预约重评建议对应的基础计划或行程版本已经变化。请重新评估后再提交。"
                return
            }
            guard proposal.operationType == "CHANGE_VISIT_TIME" else {
                errorMessage = "当前客户端只支持提交 CHANGE_VISIT_TIME 类型的预约计划建议。"
                return
            }
            guard let customerTimeZone else {
                errorMessage = "缺少有效的客户所在地 IANA 时区，请重新评估预约窗口。"
                return
            }

            let request = PlanChangeCreate(
                basePlanVersionId: proposal.basePlanVersionId,
                baseTripVersion: proposal.baseTripVersion,
                operationType: .changeVisitTime,
                payload: [
                    "visit_id": proposal.visitId,
                    "recovery_follow_up_id": followUp.id,
                    "new_window_start": apiTimestamp(proposal.newWindowStart, in: customerTimeZone),
                    "new_window_end": apiTimestamp(proposal.newWindowEnd, in: customerTimeZone)
                ],
                source: "APPOINTMENT_RECOVERY",
                reason: proposal.reason
            )
            let result = try await environment.planRepository.createPlanChange(
                tripId: followUp.tripId,
                request: request
            )
            accepted = result
            currentPlanningJobId = result.optimizationJobId
            currentTrip = try await environment.tripRepository.trip(id: followUp.tripId)
            statusMessage = "计划变更已受理。新计划生成后请检查差异并再次确认激活。"
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func refreshPlanningJob() async {
        guard let accepted, !isRefreshingJob else { return }
        let jobId = currentPlanningJobId ?? accepted.optimizationJobId
        isRefreshingJob = true
        errorMessage = nil
        defer { isRefreshingJob = false }

        do {
            let latestJob = try await environment.planRepository.job(id: jobId)
            job = latestJob
            guard latestJob.resolvedStatus == .succeeded else {
                generatedPlan = nil
                diff = nil
                if latestJob.resolvedStatus == .failed || latestJob.resolvedStatus == .dead {
                    errorMessage = latestJob.displayError ?? "规划任务未能生成新计划。请检查任务详情后再处理。"
                } else if latestJob.resolvedStatus == .cancelled {
                    errorMessage = "规划任务已取消。当前拜访输入已经更新，需重新发起计划生成。"
                } else {
                    statusMessage = "规划任务仍在\(latestJob.statusLabel)。"
                }
                return
            }
            guard let planId = latestJob.producedPlanVersionId else {
                errorMessage = "任务已完成，但没有返回新计划版本 ID，暂时不能继续激活。"
                return
            }

            let plan = try await environment.planRepository.planDetail(
                tripId: followUp.tripId,
                planId: planId
            )
            guard plan.tripId == nil || plan.tripId == followUp.tripId else {
                errorMessage = "任务返回的计划不属于当前行程，已阻止后续激活。"
                return
            }
            let comparison = try await environment.planRepository.planDiff(
                tripId: followUp.tripId,
                planId: planId,
                against: proposal.basePlanVersionId
            )
            generatedPlan = plan
            diff = comparison
            statusMessage = "新计划已生成，并已取得与原计划的差异。请检查验证结果后决定是否激活。"
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func retryPlanning() async {
        guard let accepted, !isRetryingPlanning else { return }
        isRetryingPlanning = true
        errorMessage = nil
        defer { isRetryingPlanning = false }

        do {
            let latestTrip = try await environment.tripRepository.trip(id: followUp.tripId)
            currentTrip = latestTrip
            guard
                latestTrip.version == accepted.tripVersion,
                latestTrip.activePlanVersionId == proposal.basePlanVersionId
            else {
                errorMessage = "行程或生效计划已再次变化。请先重新读取最新行程，再决定如何继续规划。"
                return
            }
            let retry = try await environment.planRepository.retryPlanChange(
                workflowId: accepted.changeRequestId
            )
            currentPlanningJobId = retry.optimizationJobId
            job = nil
            generatedPlan = nil
            diff = nil
            statusMessage = "已在原计划变更工作流下重新排队，任务关联和重试审计已保留。没有重复修改拜访时间窗。"
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func activateGeneratedPlan() async {
        guard canActivate, let accepted, let generatedPlan else { return }
        isActivating = true
        errorMessage = nil
        defer { isActivating = false }

        do {
            let latestTrip = try await environment.tripRepository.trip(id: followUp.tripId)
            currentTrip = latestTrip
            guard
                latestTrip.version == accepted.tripVersion,
                latestTrip.activePlanVersionId == proposal.basePlanVersionId
            else {
                errorMessage = "行程或生效计划在规划期间再次变化。为避免覆盖更新，已阻止激活；请基于最新版本重新规划。"
                return
            }
            // Fetch the candidate again immediately before activation; never trust the cached job result.
            let latestPlan = try await environment.planRepository.planDetail(
                tripId: followUp.tripId,
                planId: generatedPlan.id
            )
            guard
                latestPlan.feasible == true,
                latestPlan.unplacedList.isEmpty,
                ["FEASIBLE", "OPTIMAL"].contains((latestPlan.solutionStatus ?? "").uppercased()),
                latestPlan.status == .draft
            else {
                errorMessage = "新计划的可行性、未排项或状态已变化，不能激活。"
                return
            }
            _ = try await environment.planRepository.activatePlan(
                tripId: followUp.tripId,
                planId: generatedPlan.id,
                tripVersion: latestTrip.version
            )
            statusMessage = "新计划已激活。"
            onActivated()
            dismiss()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func apiTimestamp(_ date: Date, in timeZone: TimeZone) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        formatter.timeZone = timeZone
        return formatter.string(from: date)
    }
}
