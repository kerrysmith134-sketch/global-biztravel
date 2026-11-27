import SwiftUI

struct AppointmentRecoveryReassessmentSheet: View {
    @Environment(\.dismiss) private var dismiss

    let item: VisitFollowUp
    let onSubmit: (AppointmentRecoveryReassessmentRequest) -> Void

    @State private var includeCustomerWindow = false
    @State private var windowStart = Date().addingTimeInterval(86_400)
    @State private var windowEnd = Date().addingTimeInterval(86_400 + 7_200)
    @State private var timezone = ""
    @State private var confirmationMethod = "EMAIL"
    @State private var confirmationReference = ""
    @State private var replacementFlightSegmentId = ""

    private var actionCode: String {
        item.sourceActionContext?.actionCode ?? ""
    }

    private var requiresCustomerWindow: Bool {
        [
            "REQUEST_CUSTOMER_RESCHEDULE_WINDOW",
            "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW",
            "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM"
        ].contains(actionCode)
    }

    private var requiresReplacementFlight: Bool {
        actionCode == "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM"
    }

    private var shouldSubmitCustomerWindow: Bool {
        requiresCustomerWindow || includeCustomerWindow
    }

    private var selectedTimeZone: TimeZone {
        TimeZone(identifier: timezone.trimmingCharacters(in: .whitespacesAndNewlines)) ?? .current
    }

    private var validTimeZone: Bool {
        TimeZone(identifier: timezone.trimmingCharacters(in: .whitespacesAndNewlines)) != nil
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("航班与交通证据") {
                    Text("系统会读取晚于原始恢复事件且仍在有效期内的航班更新，并按当前生效计划重新传播。缺失 ETA、过期交通段或计划绑定不一致会保留为待复核。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                if requiresReplacementFlight {
                    Section("替代航班") {
                        TextField("机票段 ID", text: $replacementFlightSegmentId)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Text("只能选择已录入、属于本次出行，并且 BOOKED/TICKETED 且绑定当前 ACTIVE 计划的替代航班。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("客户预约窗口") {
                    if !requiresCustomerWindow {
                        Toggle("同时提供客户已确认的新预约窗口", isOn: $includeCustomerWindow)
                    }
                    if shouldSubmitCustomerWindow {
                        TextField("客户所在地 IANA 时区，例如 Europe/Berlin", text: $timezone)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        DatePicker(
                            "窗口开始",
                            selection: $windowStart,
                            displayedComponents: [.date, .hourAndMinute]
                        )
                        .environment(\.timeZone, selectedTimeZone)
                        DatePicker(
                            "窗口结束",
                            selection: $windowEnd,
                            displayedComponents: [.date, .hourAndMinute]
                        )
                        .environment(\.timeZone, selectedTimeZone)
                        Picker("确认方式", selection: $confirmationMethod) {
                            Text("邮件").tag("EMAIL")
                            Text("电话").tag("PHONE")
                            Text("WhatsApp").tag("WHATSAPP")
                            Text("CRM").tag("CRM")
                            Text("当面确认").tag("IN_PERSON")
                            Text("其他").tag("OTHER")
                        }
                        TextField("邮件/CRM 活动参考编号（可选）", text: $confirmationReference)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Text("只记录方式与参考编号，不上传通信正文；该信息仍属于出差人陈述，不代表系统已独立核实客户。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if !validTimeZone && !timezone.isEmpty {
                            Text("请输入有效的 IANA 时区标识")
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                    } else {
                        Text("本次只重新评估航班与交通证据，不提交新的预约窗口。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("重新评估预约")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("开始评估") {
                        let reference = confirmationReference.trimmingCharacters(in: .whitespacesAndNewlines)
                        let customerWindow = shouldSubmitCustomerWindow
                            ? CustomerAppointmentWindowAttestationRequest(
                                startAt: windowStart,
                                endAt: windowEnd,
                                timezone: timezone.trimmingCharacters(in: .whitespacesAndNewlines),
                                confirmationMethod: confirmationMethod,
                                confirmationReference: reference.isEmpty ? nil : reference
                            )
                            : nil
                        let replacementId = replacementFlightSegmentId
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                        onSubmit(
                            AppointmentRecoveryReassessmentRequest(
                                customerWindow: customerWindow,
                                replacementFlightSegmentId: replacementId.isEmpty ? nil : replacementId
                            )
                        )
                    }
                    .disabled(
                        (shouldSubmitCustomerWindow
                            && (!validTimeZone || windowStart <= Date() || windowEnd <= windowStart))
                            || (requiresReplacementFlight
                                && replacementFlightSegmentId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    )
                }
            }
        }
    }
}
