import SwiftUI

struct ExpenseEntrySheet: View {
    @Environment(\.dismiss) private var dismiss

    let defaultCurrency: String
    let onSave: (TripExpenseCategory, String, String, Date, String) async -> Bool

    @State private var category: TripExpenseCategory = .meals
    @State private var amount = ""
    @State private var currency: String
    @State private var occurredOn = Date()
    @State private var note = ""
    @State private var saving = false

    init(
        defaultCurrency: String,
        onSave: @escaping (TripExpenseCategory, String, String, Date, String) async -> Bool
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
            }
            .navigationTitle("记一笔费用")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        saving = true
                        Task {
                            let ok = await onSave(category, amount, currency, occurredOn, note)
                            saving = false
                            if ok { dismiss() }
                        }
                    } label: {
                        Text(saving ? "保存中…" : "保存")
                    }
                    .disabled(saving || amount.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}
