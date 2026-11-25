import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct ExpenseEntrySheet: View {
    @Environment(\.dismiss) private var dismiss

    let defaultCurrency: String
    let receiptRecognitionEnabled: Bool
    let onCaptureReceipt: (ReceiptDraft) throws -> LocalReceiptAsset
    let onRecognizeReceipt: (String) async throws -> ReceiptOcrExecution
    let onReviewReceipt: (String, ReceiptOcrReviewRequest) async throws -> ReceiptOcrReview
    let onSave: (TripExpenseCategory, String, String, Date, String, String?) async -> Bool

    @State private var category: TripExpenseCategory = .meals
    @State private var amount = ""
    @State private var currency: String
    @State private var occurredOn = Date()
    @State private var note = ""
    @State private var saving = false
    @State private var isLoadingReceipt = false
    @State private var showsFileImporter = false
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var localReceiptAsset: LocalReceiptAsset?
    @State private var receiptError: String?
    @State private var saveError: String?
    @State private var ocrRun: ReceiptOcrRun?
    @State private var isRecognizingReceipt = false
    @State private var isReviewingReceipt = false
    @State private var ocrMerchant = ""
    @State private var ocrAmount = ""
    @State private var ocrCurrency = ""
    @State private var ocrDate = Date()
    @State private var includeOcrDate = false
    @State private var ocrRejectNote = ""
    @State private var ocrError: String?

    init(
        defaultCurrency: String,
        receiptRecognitionEnabled: Bool,
        onCaptureReceipt: @escaping (ReceiptDraft) throws -> LocalReceiptAsset,
        onRecognizeReceipt: @escaping (String) async throws -> ReceiptOcrExecution,
        onReviewReceipt: @escaping (String, ReceiptOcrReviewRequest) async throws -> ReceiptOcrReview,
        onSave: @escaping (TripExpenseCategory, String, String, Date, String, String?) async -> Bool
    ) {
        self.defaultCurrency = defaultCurrency
        self.receiptRecognitionEnabled = receiptRecognitionEnabled
        self.onCaptureReceipt = onCaptureReceipt
        self.onRecognizeReceipt = onRecognizeReceipt
        self.onReviewReceipt = onReviewReceipt
        self.onSave = onSave
        _currency = State(initialValue: defaultCurrency.uppercased())
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("费用") {
                    Picker("类别", selection: $category) {
                        ForEach(TripExpenseCategory.allCases) { item in
                            Text(item.label).tag(item)
                        }
                    }
                    TextField("金额（如 88.50）", text: $amount)
                        .keyboardType(.decimalPad)
                    TextField("币种（如 EUR / CNY）", text: $currency)
                        .textInputAutocapitalization(.characters)
                    DatePicker("发生日期", selection: $occurredOn, displayedComponents: .date)
                    TextField("备注（可选）", text: $note, axis: .vertical)
                        .lineLimit(2 ... 4)
                }

                Section("原始票据") {
                    if let localReceiptAsset {
                        HStack(spacing: 12) {
                            Image(systemName: "doc.text.image")
                                .font(.title2)
                                .foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(localReceiptAsset.originalFilename)
                                    .lineLimit(1)
                                Text("\(ByteCountFormatter.string(fromByteCount: Int64(localReceiptAsset.byteLength), countStyle: .file)) · \(receiptStateLabel(localReceiptAsset))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("移除", role: .destructive) {
                                self.localReceiptAsset = nil
                                selectedPhoto = nil
                                receiptError = nil
                            }
                            .font(.caption)
                        }
                    } else {
                        HStack {
                            PhotosPicker(selection: $selectedPhoto, matching: .images) {
                                Label("照片图库", systemImage: "photo")
                            }
                            Spacer()
                            Button {
                                showsFileImporter = true
                            } label: {
                                Label("浏览文件", systemImage: "folder")
                            }
                        }
                        if isLoadingReceipt {
                            ProgressView("正在安全保存原件…")
                        }
                    }

                    if let receiptError {
                        Text(receiptError)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                    if let localReceiptAsset {
                        if receiptRecognitionEnabled {
                            Button {
                                recognizeReceipt(assetId: localReceiptAsset.id)
                            } label: {
                                Label(
                                    isRecognizingReceipt ? "正在识别…" : (ocrRun == nil ? "识别票据" : "重新识别"),
                                    systemImage: "text.viewfinder"
                                )
                            }
                            .disabled(isRecognizingReceipt || isReviewingReceipt || saving)
                            .accessibilityIdentifier("expense.receipt.ocr.start")
                        } else {
                            Text("本地模式暂不调用云端 OCR；原件已保存在本机，可以继续手工录入。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let ocrError {
                        Text(ocrError)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                    Text("选择后立即将原件写入本机私有票据仓。云端模式在费用提交前上传并核对原件哈希。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                if let ocrRun {
                    Section("OCR 识别 · 人工复核") {
                        HStack {
                            Label(ocrRun.status == "SUCCEEDED" ? "已识别候选" : "识别未完成", systemImage: "text.viewfinder")
                            Spacer()
                            Text(ocrRun.engineVersion)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if let error = ocrRun.error {
                            Text(error)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                        if let hint = ocrRun.candidates.merchantHint {
                            Text("商户候选：\(hint)")
                                .font(.subheadline)
                        }
                        if !ocrRun.candidates.amountCandidates.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("金额候选（点选后仍需人工核对）")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                ForEach(ocrRun.candidates.amountCandidates, id: \.self) { candidate in
                                    Button {
                                        ocrAmount = numericAmount(from: candidate)
                                    } label: {
                                        HStack {
                                            Text(candidate)
                                            Spacer()
                                            if ocrAmount == numericAmount(from: candidate) {
                                                Image(systemName: "checkmark.circle.fill")
                                            } else {
                                                Image(systemName: "plus.circle")
                                            }
                                        }
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        if !ocrRun.candidates.dateCandidates.isEmpty {
                            Text("日期候选：\(ocrRun.candidates.dateCandidates.joined(separator: "、"))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let review = ocrRun.review {
                            Label(
                                review.decision == "CONFIRMED" ? "已人工确认" : "已驳回",
                                systemImage: review.decision == "CONFIRMED" ? "checkmark.seal.fill" : "xmark.seal"
                            )
                            .foregroundStyle(review.decision == "CONFIRMED" ? .green : .secondary)
                            if let note = review.note, !note.isEmpty {
                                Text(note).font(.caption).foregroundStyle(.secondary)
                            }
                        } else if ocrRun.status == "SUCCEEDED" {
                            Text("OCR 不负责判断币种或财务真实性。请核对原票据后再确认；确认只记录人工审阅，不会自动保存费用。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            TextField("确认商户（可选）", text: $ocrMerchant)
                            TextField("确认金额（纯数字，如 18.50）", text: $ocrAmount)
                                .keyboardType(.decimalPad)
                            TextField("确认币种（金额必填时必须确认，如 EUR）", text: $ocrCurrency)
                                .textInputAutocapitalization(.characters)
                            Toggle("同时确认发生日期", isOn: $includeOcrDate)
                            if includeOcrDate {
                                DatePicker("确认日期", selection: $ocrDate, displayedComponents: .date)
                            }
                            TextField("驳回原因（驳回时必填）", text: $ocrRejectNote, axis: .vertical)
                                .lineLimit(2 ... 3)
                            HStack {
                                Button {
                                    submitOCRReview(decision: "CONFIRMED")
                                } label: {
                                    Label(isReviewingReceipt ? "提交中…" : "确认候选", systemImage: "checkmark.seal")
                                }
                                .disabled(isReviewingReceipt)
                                .accessibilityIdentifier("expense.receipt.ocr.confirm")
                                Spacer()
                                Button("驳回", role: .destructive) {
                                    submitOCRReview(decision: "REJECTED")
                                }
                                .disabled(isReviewingReceipt || ocrRejectNote.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                                .accessibilityIdentifier("expense.receipt.ocr.reject")
                            }
                        }
                    }
                }

                if let saveError {
                    Section {
                        Label(saveError, systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle("记一笔费用")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        saving = true
                        saveError = nil
                        Task {
                            let ok = await onSave(
                                category,
                                amount,
                                currency,
                                occurredOn,
                                note,
                                localReceiptAsset?.id
                            )
                            saving = false
                            if ok {
                                dismiss()
                            } else {
                                saveError = "费用尚未保存。请检查错误信息后重试；已选票据原件仍保留在本机。"
                            }
                        }
                    } label: {
                        Text(saving ? "保存中…" : "保存")
                    }
                    .disabled(
                        saving
                            || isLoadingReceipt
                            || amount.trimmingCharacters(in: .whitespaces).isEmpty
                    )
                }
            }
            .fileImporter(
                isPresented: $showsFileImporter,
                allowedContentTypes: [.image, .pdf],
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case .success(let urls):
                    guard let url = urls.first else { return }
                    let didStartAccess = url.startAccessingSecurityScopedResource()
                    defer {
                        if didStartAccess { url.stopAccessingSecurityScopedResource() }
                    }
                    do {
                        let data = try Data(contentsOf: url, options: .mappedIfSafe)
                        let draft = ReceiptDraft(filename: url.lastPathComponent, data: data)
                        localReceiptAsset = try onCaptureReceipt(draft)
                        receiptError = nil
                    } catch {
                        receiptError = "无法保存所选票据：\(error.localizedDescription)"
                    }
                case .failure(let error):
                    receiptError = "选择票据失败：\(error.localizedDescription)"
                }
            }
            .onChange(of: selectedPhoto) { _, newItem in
                guard let newItem else { return }
                isLoadingReceipt = true
                receiptError = nil
                Task {
                    defer {
                        isLoadingReceipt = false
                        selectedPhoto = nil
                    }
                    do {
                        guard let data = try await newItem.loadTransferable(type: Data.self) else {
                            receiptError = "无法读取照片原件"
                            return
                        }
                        let draft = ReceiptDraft(filename: "receipt-photo", data: data)
                        localReceiptAsset = try onCaptureReceipt(draft)
                    } catch {
                        receiptError = "读取或保存照片失败：\(error.localizedDescription)"
                    }
                }
            }
        }
    }

    private func recognizeReceipt(assetId: String) {
        isRecognizingReceipt = true
        ocrError = nil
        Task {
            defer { isRecognizingReceipt = false }
            do {
                let execution = try await onRecognizeReceipt(assetId)
                localReceiptAsset = execution.asset
                ocrRun = execution.run
                ocrMerchant = execution.run.candidates.merchantHint ?? ""
                ocrAmount = execution.run.candidates.amountCandidates.first.map(numericAmount(from:)) ?? ""
                ocrCurrency = currency
                includeOcrDate = false
                ocrRejectNote = ""
            } catch {
                ocrError = "票据识别失败：\(error.localizedDescription)"
            }
        }
    }

    private func submitOCRReview(decision: String) {
        guard let currentRun = ocrRun else { return }
        ocrError = nil

        let merchant = ocrMerchant.trimmingCharacters(in: .whitespacesAndNewlines)
        let amountText = ocrAmount.trimmingCharacters(in: .whitespacesAndNewlines)
        var confirmedAmount: Decimal?
        var confirmedCurrency: String?
        if decision == "CONFIRMED", !amountText.isEmpty {
            guard let parsed = Decimal(string: amountText.replacingOccurrences(of: ",", with: ".")), parsed > 0 else {
                ocrError = "确认金额必须是大于 0 的纯数字"
                return
            }
            let normalizedCurrency = ocrCurrency.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            guard normalizedCurrency.count == 3 else {
                ocrError = "确认金额时必须填写三位币种代码，例如 EUR"
                return
            }
            confirmedAmount = parsed
            confirmedCurrency = normalizedCurrency
        }

        let request = ReceiptOcrReviewRequest(
            decision: decision,
            merchant: decision == "CONFIRMED" && !merchant.isEmpty ? merchant : nil,
            amount: confirmedAmount,
            currency: confirmedCurrency,
            occurredOn: decision == "CONFIRMED" && includeOcrDate ? ISO8601.dayString(from: ocrDate) : nil,
            note: decision == "REJECTED" ? ocrRejectNote.trimmingCharacters(in: .whitespacesAndNewlines) : nil
        )
        if decision == "CONFIRMED",
           request.merchant == nil,
           request.amount == nil,
           request.occurredOn == nil {
            ocrError = "至少确认一个字段后才能提交审阅"
            return
        }
        if decision == "REJECTED", request.note == nil || request.note?.isEmpty == true {
            ocrError = "驳回时必须填写原因"
            return
        }

        isReviewingReceipt = true
        Task {
            defer { isReviewingReceipt = false }
            do {
                let review = try await onReviewReceipt(currentRun.id, request)
                var updatedRun = currentRun
                updatedRun.review = review
                ocrRun = updatedRun

                // Apply only fields the traveler explicitly confirmed. Expense persistence still
                // requires the separate Save action in the form.
                if decision == "CONFIRMED" {
                    if let merchant = request.merchant {
                        note = note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            ? merchant
                            : "\(note) · \(merchant)"
                    }
                    if let amount = request.amount {
                        self.amount = NSDecimalNumber(decimal: amount).stringValue
                    }
                    if let confirmedCurrency = request.currency {
                        currency = confirmedCurrency
                    }
                    if request.occurredOn != nil {
                        occurredOn = ocrDate
                    }
                }
            } catch {
                ocrError = "保存人工审阅失败：\(error.localizedDescription)"
            }
        }
    }

    private func numericAmount(from candidate: String) -> String {
        let numeric = candidate.filter { $0.isNumber || $0 == "." || $0 == "," }
        return numeric.replacingOccurrences(of: ",", with: ".")
    }

    private func receiptStateLabel(_ asset: LocalReceiptAsset) -> String {
        switch asset.uploadState {
        case .localOnly: return "仅保存在本机"
        case .pendingUpload: return "本机已保存 · 云端待上传"
        case .uploaded: return "云端已确认"
        }
    }
}
