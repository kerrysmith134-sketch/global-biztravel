import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct ExpenseEntrySheet: View {
    @Environment(\.dismiss) private var dismiss

    let defaultCurrency: String
    let onCaptureReceipt: (ReceiptDraft) throws -> LocalReceiptAsset
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

    init(
        defaultCurrency: String,
        onCaptureReceipt: @escaping (ReceiptDraft) throws -> LocalReceiptAsset,
        onSave: @escaping (TripExpenseCategory, String, String, Date, String, String?) async -> Bool
    ) {
        self.defaultCurrency = defaultCurrency
        self.onCaptureReceipt = onCaptureReceipt
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
                    Text("选择后立即将原件写入本机私有票据仓。云端模式在费用提交前上传并核对原件哈希。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
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
            .onChange(of: selectedPhoto) { newItem in
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

    private func receiptStateLabel(_ asset: LocalReceiptAsset) -> String {
        switch asset.uploadState {
        case .localOnly: return "仅保存在本机"
        case .pendingUpload: return "本机已保存 · 云端待上传"
        case .uploaded: return "云端已确认"
        }
    }
}
