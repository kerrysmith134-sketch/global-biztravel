import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct ExpenseEntrySheet: View {
    @Environment(\.dismiss) private var dismiss

    let defaultCurrency: String
    let onSave: (TripExpenseCategory, String, String, Date, String, ReceiptDraft?) async -> Bool

    @State private var category: TripExpenseCategory = .meals
    @State private var amount = ""
    @State private var currency: String
    @State private var occurredOn = Date()
    @State private var note = ""
    @State private var saving = false
    @State private var isLoadingReceipt = false
    @State private var showsFileImporter = false
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var receiptDraft: ReceiptDraft?
    @State private var receiptError: String?
    @State private var saveError: String?

    init(
        defaultCurrency: String,
        onSave: @escaping (TripExpenseCategory, String, String, Date, String, ReceiptDraft?) async -> Bool
    ) {
        self.defaultCurrency = defaultCurrency
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
                    if let receiptDraft {
                        HStack(spacing: 12) {
                            Image(systemName: "doc.text.image")
                                .font(.title2)
                                .foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(receiptDraft.filename)
                                    .lineLimit(1)
                                Text("\(ByteCountFormatter.string(fromByteCount: Int64(receiptDraft.data.count), countStyle: .file)) · 原件待保存")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("移除", role: .destructive) {
                                self.receiptDraft = nil
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
                            ProgressView("正在读取原始文件…")
                        }
                    }

                    if let receiptError {
                        Text(receiptError)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                    Text("票据原件会先保存在本机私有目录；云端模式只有收到服务端资产 ID 后才会关联到费用。")
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
                            let ok = await onSave(category, amount, currency, occurredOn, note, receiptDraft)
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
                        receiptDraft = ReceiptDraft(filename: url.lastPathComponent, data: data)
                        receiptError = nil
                    } catch {
                        receiptError = "无法读取所选票据：\(error.localizedDescription)"
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
                    defer { isLoadingReceipt = false }
                    do {
                        guard let data = try await newItem.loadTransferable(type: Data.self) else {
                            receiptError = "无法读取照片原件"
                            return
                        }
                        receiptDraft = ReceiptDraft(filename: "receipt-photo", data: data)
                    } catch {
                        receiptError = "读取照片失败：\(error.localizedDescription)"
                    }
                }
            }
        }
    }
}
